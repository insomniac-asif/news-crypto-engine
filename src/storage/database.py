"""SQLite database management — schema, CRUD operations, and migrations."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Articles ingested from RSS feeds, Reddit, etc.
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    published_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);

-- Classified events extracted from articles
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 5),
    summary TEXT,
    assets_affected TEXT DEFAULT '[]',
    detected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_detected_at ON events(detected_at);
CREATE INDEX IF NOT EXISTS idx_events_article_id ON events(article_id);

-- OHLCV price data
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    UNIQUE(asset, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_prices_asset_ts ON prices(asset, timestamp);

-- Trading signals generated from events
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('long', 'short', 'neutral')),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    entry_time TEXT NOT NULL,
    price_at_signal REAL,
    price_1h_later REAL,
    price_4h_later REAL,
    price_24h_later REAL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_signals_asset ON signals(asset);
CREATE INDEX IF NOT EXISTS idx_signals_entry_time ON signals(entry_time);

-- Narrative tracking over time
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    keywords TEXT DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    avg_price_impact REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_narratives_name ON narratives(name);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


class Database:
    """SQLite database manager with schema management and CRUD operations."""

    def __init__(self, db_path: str = "data/news_crypto.db") -> None:
        """Initialize database connection.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables and apply migrations if needed."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Record schema version
            existing = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if existing is None or existing < SCHEMA_VERSION:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            conn.commit()
        logger.info("Database initialized at %s (schema v%d)", self.db_path, SCHEMA_VERSION)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections with WAL mode and foreign keys."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # ── Articles CRUD ──────────────────────────────────────────────

    def insert_article(
        self,
        source: str,
        url: str,
        title: str,
        content: Optional[str],
        published_at: str,
    ) -> Optional[int]:
        """Insert an article, returning its ID. Returns None if URL already exists.

        Args:
            source: Feed/source name (e.g. 'CoinDesk', 'reddit/cryptocurrency').
            url: Unique article URL.
            title: Article title.
            content: Full article text (raw, for later NLP processing).
            published_at: ISO 8601 UTC timestamp.

        Returns:
            The new article's row ID, or None if duplicate.
        """
        with self.connect() as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO articles (source, url, title, content, published_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source, url, title, content, published_at),
                )
                conn.commit()
                article_id = cursor.lastrowid
                logger.debug("Inserted article %d: %s", article_id, title[:80])
                return article_id
            except sqlite3.IntegrityError:
                logger.debug("Duplicate article skipped: %s", url)
                return None

    def get_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch articles with optional filtering.

        Args:
            limit: Max rows to return.
            offset: Pagination offset.
            source: Filter by source name.
            since: Only articles published after this ISO timestamp.

        Returns:
            List of article dicts.
        """
        query = "SELECT * FROM articles WHERE 1=1"
        params: list[Any] = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if since:
            query += " AND published_at >= ?"
            params.append(since)

        query += " ORDER BY published_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_unprocessed_articles(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch articles that haven't been classified into events yet.

        Args:
            limit: Max rows to return.

        Returns:
            List of article dicts without associated events.
        """
        query = """
            SELECT a.* FROM articles a
            LEFT JOIN events e ON a.id = e.article_id
            WHERE e.id IS NULL
            ORDER BY a.published_at DESC
            LIMIT ?
        """
        with self.connect() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ── Events CRUD ────────────────────────────────────────────────

    def insert_event(
        self,
        article_id: int,
        category: str,
        severity: int,
        summary: Optional[str] = None,
        assets_affected: Optional[list[str]] = None,
        detected_at: Optional[str] = None,
    ) -> int:
        """Insert a classified event.

        Args:
            article_id: Foreign key to the source article.
            category: Event category from taxonomy.
            severity: Severity rating 1-5.
            summary: Brief event summary.
            assets_affected: List of affected asset symbols.
            detected_at: Optional ISO timestamp override (defaults to now).

        Returns:
            The new event's row ID.
        """
        assets_json = json.dumps(assets_affected or [])
        with self.connect() as conn:
            if detected_at:
                cursor = conn.execute(
                    """INSERT INTO events (article_id, category, severity, summary, assets_affected, detected_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (article_id, category, severity, summary, assets_json, detected_at),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO events (article_id, category, severity, summary, assets_affected)
                       VALUES (?, ?, ?, ?, ?)""",
                    (article_id, category, severity, summary, assets_json),
                )
            conn.commit()
            event_id = cursor.lastrowid
            logger.debug("Inserted event %d: %s (severity=%d)", event_id, category, severity)
            return event_id

    def get_events(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch events with optional filtering.

        Args:
            category: Filter by event category.
            min_severity: Minimum severity threshold.
            since: Only events detected after this ISO timestamp.
            limit: Max rows to return.

        Returns:
            List of event dicts with parsed assets_affected.
        """
        query = "SELECT * FROM events WHERE severity >= ?"
        params: list[Any] = [min_severity]

        if category:
            query += " AND category = ?"
            params.append(category)
        if since:
            query += " AND detected_at >= ?"
            params.append(since)

        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["assets_affected"] = json.loads(d["assets_affected"])
                results.append(d)
            return results

    # ── Prices CRUD ────────────────────────────────────────────────

    def insert_prices(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert price records, skipping duplicates.

        Args:
            records: List of dicts with keys: asset, timestamp, open, high, low, close, volume.

        Returns:
            Number of new rows inserted.
        """
        inserted = 0
        with self.connect() as conn:
            for rec in records:
                try:
                    conn.execute(
                        """INSERT INTO prices (asset, timestamp, open, high, low, close, volume)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            rec["asset"],
                            rec["timestamp"],
                            rec["open"],
                            rec["high"],
                            rec["low"],
                            rec["close"],
                            rec.get("volume", 0),
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass  # duplicate — skip silently
            conn.commit()
        if inserted:
            logger.info("Inserted %d price records (%d skipped)", inserted, len(records) - inserted)
        return inserted

    def get_prices(
        self,
        asset: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch price candles for an asset within a time range.

        Args:
            asset: Asset symbol (e.g. 'BTC').
            start: ISO timestamp lower bound (inclusive).
            end: ISO timestamp upper bound (inclusive).

        Returns:
            List of price dicts ordered by timestamp ascending.
        """
        query = "SELECT * FROM prices WHERE asset = ?"
        params: list[Any] = [asset]

        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)

        query += " ORDER BY timestamp ASC"

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_price_at(self, asset: str, timestamp: str) -> Optional[dict[str, Any]]:
        """Get the closest price candle at or before a given timestamp.

        Args:
            asset: Asset symbol.
            timestamp: ISO timestamp to look up.

        Returns:
            Price dict or None if no data available.
        """
        query = """
            SELECT * FROM prices
            WHERE asset = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self.connect() as conn:
            row = conn.execute(query, (asset, timestamp)).fetchone()
            return dict(row) if row else None

    # ── Signals CRUD ───────────────────────────────────────────────

    def insert_signal(
        self,
        event_id: int,
        asset: str,
        direction: str,
        confidence: float,
        entry_time: str,
        price_at_signal: Optional[float] = None,
    ) -> int:
        """Insert a trading signal.

        Args:
            event_id: Foreign key to the triggering event.
            asset: Asset symbol.
            direction: 'long', 'short', or 'neutral'.
            confidence: Confidence score 0-1.
            entry_time: ISO timestamp of signal generation.
            price_at_signal: Price at signal time.

        Returns:
            The new signal's row ID.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO signals (event_id, asset, direction, confidence,
                   entry_time, price_at_signal)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, asset, direction, confidence, entry_time, price_at_signal),
            )
            conn.commit()
            return cursor.lastrowid

    def update_signal_prices(
        self,
        signal_id: int,
        price_1h: Optional[float] = None,
        price_4h: Optional[float] = None,
        price_24h: Optional[float] = None,
    ) -> None:
        """Update a signal's follow-up prices as they become available.

        Args:
            signal_id: Signal row ID.
            price_1h: Price 1 hour after signal.
            price_4h: Price 4 hours after signal.
            price_24h: Price 24 hours after signal.
        """
        updates = []
        params: list[Any] = []
        if price_1h is not None:
            updates.append("price_1h_later = ?")
            params.append(price_1h)
        if price_4h is not None:
            updates.append("price_4h_later = ?")
            params.append(price_4h)
        if price_24h is not None:
            updates.append("price_24h_later = ?")
            params.append(price_24h)

        if not updates:
            return

        params.append(signal_id)
        query = f"UPDATE signals SET {', '.join(updates)} WHERE id = ?"
        with self.connect() as conn:
            conn.execute(query, params)
            conn.commit()

    # ── Narratives CRUD ────────────────────────────────────────────

    def upsert_narrative(
        self,
        name: str,
        keywords: list[str],
        event_count: int = 1,
        avg_price_impact: float = 0.0,
    ) -> int:
        """Insert or update a narrative.

        Args:
            name: Narrative name/label.
            keywords: Associated keywords.
            event_count: Number of events in this narrative.
            avg_price_impact: Average price impact percentage.

        Returns:
            The narrative's row ID.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        keywords_json = json.dumps(keywords)

        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM narratives WHERE name = ?", (name,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE narratives SET
                       keywords = ?, last_seen = ?, event_count = ?, avg_price_impact = ?
                       WHERE id = ?""",
                    (keywords_json, now, event_count, avg_price_impact, existing["id"]),
                )
                conn.commit()
                return existing["id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO narratives (name, keywords, first_seen, last_seen,
                       event_count, avg_price_impact)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (name, keywords_json, now, now, event_count, avg_price_impact),
                )
                conn.commit()
                return cursor.lastrowid

    # ── Maintenance ────────────────────────────────────────────────

    def enforce_retention(self, retention_days: int = 90) -> int:
        """Delete articles (and cascaded events) older than retention period.

        Args:
            retention_days: Number of days to retain data.

        Returns:
            Number of articles deleted.
        """
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Compute cutoff by subtracting days
        from datetime import timedelta

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM articles WHERE published_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            conn.commit()

        if deleted:
            logger.info("Retention policy: deleted %d articles older than %d days", deleted, retention_days)
        return deleted

    def get_stats(self) -> dict[str, int]:
        """Get row counts for all main tables.

        Returns:
            Dict mapping table names to row counts.
        """
        tables = ["articles", "events", "prices", "signals", "narratives"]
        stats = {}
        with self.connect() as conn:
            for table in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                stats[table] = count
        return stats
