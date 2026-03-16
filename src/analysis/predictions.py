"""Prediction engine — generates graded predictions from confirmed news events.

Three components:
1. Predictor: generates directional predictions with confidence from event clusters
2. Grader: checks predictions against actual price outcomes, assigns A-F grades
3. SourceTrust: tracks per-source accuracy to build trust ratings over time

Confidence is based on: source confirmation count, source credibility,
severity, novelty, and category-specific base rates.

Grading thresholds (for each timeframe):
  A = strong correct (>0.5% in predicted direction)
  B = weak correct (0.1-0.5%)
  C = flat (-0.1 to 0.1%)
  D = weak wrong (-0.5 to -0.1%)
  F = strong wrong (<-0.5%)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "news_crypto.db"

# Direction bias per event category
CATEGORY_DIRECTION = {
    "ADOPTION": "bullish",
    "PROTOCOL": "bullish",
    "EXCHANGE": "neutral",    # depends on context
    "SENTIMENT": "neutral",
    "MARKET_STRUCTURE": "neutral",
    "MACRO": "bearish",       # macro uncertainty = risk-off
    "REGULATORY": "bearish",  # regulatory = uncertainty
    "SECURITY": "bearish",
}

CATEGORY_BASE_CONFIDENCE = {
    "ADOPTION": 0.50,
    "PROTOCOL": 0.45,
    "EXCHANGE": 0.45,
    "SENTIMENT": 0.30,
    "MARKET_STRUCTURE": 0.35,
    "MACRO": 0.40,
    "REGULATORY": 0.50,
    "SECURITY": 0.55,
}

# Grade thresholds (price move % in predicted direction)
GRADE_THRESHOLDS = {
    "A": 0.5,    # strong correct: > +0.5%
    "B": 0.1,    # weak correct: +0.1% to +0.5%
    "C": -0.1,   # flat: -0.1% to +0.1%
    "D": -0.5,   # weak wrong: -0.5% to -0.1%
    # F = anything below -0.5%
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    predicted_at TEXT NOT NULL,
    price_at_prediction REAL,
    sources TEXT DEFAULT '[]',
    source_count INTEGER DEFAULT 1,
    category TEXT,
    severity INTEGER,
    reasoning TEXT,
    -- Outcome fields (filled by grader)
    price_1h REAL,
    price_4h REAL,
    price_24h REAL,
    change_1h_pct REAL,
    change_4h_pct REAL,
    change_24h_pct REAL,
    grade_1h TEXT,
    grade_4h TEXT,
    grade_24h TEXT,
    graded INTEGER DEFAULT 0,
    graded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_asset ON predictions(asset);
CREATE INDEX IF NOT EXISTS idx_predictions_time ON predictions(predicted_at);
CREATE INDEX IF NOT EXISTS idx_predictions_graded ON predictions(graded);

CREATE TABLE IF NOT EXISTS source_trust (
    source_name TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_1h INTEGER DEFAULT 0,
    correct_4h INTEGER DEFAULT 0,
    correct_24h INTEGER DEFAULT 0,
    wrong_1h INTEGER DEFAULT 0,
    wrong_4h INTEGER DEFAULT 0,
    wrong_24h INTEGER DEFAULT 0,
    accuracy_1h REAL DEFAULT 0,
    accuracy_4h REAL DEFAULT 0,
    accuracy_24h REAL DEFAULT 0,
    trust_score REAL DEFAULT 0.5,
    avg_confidence REAL DEFAULT 0,
    narrative_diversity INTEGER DEFAULT 0,
    is_noise_source INTEGER DEFAULT 0,
    last_updated TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_trust_score ON source_trust(trust_score);
"""


def _get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════════
#  PREDICTOR
# ═══════════════════════════════════════════════════════════════

class Predictor:
    """Generate directional predictions from event clusters."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.conn = _get_conn(db_path)
        self._load_optimized_config()

    def _load_optimized_config(self) -> None:
        """Load optimized parameters from backtest if available."""
        config_path = Path(__file__).resolve().parent.parent.parent / "data" / "prediction_config.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                if "direction_map" in cfg:
                    CATEGORY_DIRECTION.update(cfg["direction_map"])
                if "min_severity" in cfg:
                    self._min_severity = cfg["min_severity"]
                else:
                    self._min_severity = 1
                if "min_sources" in cfg:
                    self._min_sources = cfg["min_sources"]
                else:
                    self._min_sources = 1
                self._severity_weight = cfg.get("severity_weight", 0.7)
                self._trust_weight = cfg.get("trust_weight", 0.6)
                self._novelty_weight = cfg.get("novelty_weight", 0.5)
                self._source_boost_per = cfg.get("source_boost_per", 0.08)
                self._source_boost_cap = cfg.get("source_boost_cap", 0.25)
                logger.info("Loaded optimized prediction config: %s", cfg.get("name", "custom"))
                return
            except Exception:
                logger.warning("Failed to load prediction_config.json, using defaults")

        self._min_severity = 1
        self._min_sources = 1
        self._severity_weight = 0.7
        self._trust_weight = 0.6
        self._novelty_weight = 0.5
        self._source_boost_per = 0.08
        self._source_boost_cap = 0.25

    def generate(self, since: str | None = None) -> list[dict]:
        """Generate predictions from recent event clusters.

        Args:
            since: Only process clusters after this timestamp.

        Returns:
            List of prediction dicts that were created.
        """
        query = """
            SELECT ec.id, ec.category, ec.severity, ec.sentiment,
                   ec.representative_headline, ec.article_count,
                   ec.novelty_score, ec.assets_affected, ec.first_detected_at
            FROM event_clusters ec
            WHERE 1=1
        """
        params: list[Any] = []
        if since:
            query += " AND ec.first_detected_at >= ?"
            params.append(since)
        query += " ORDER BY ec.first_detected_at DESC LIMIT 200"

        clusters = self.conn.execute(query, params).fetchall()
        predictions = []

        for cluster in clusters:
            preds = self._predict_from_cluster(dict(cluster))
            predictions.extend(preds)

        logger.info("Generated %d predictions from %d clusters", len(predictions), len(clusters))
        return predictions

    def _predict_from_cluster(self, cluster: dict) -> list[dict]:
        """Generate predictions for each asset in a cluster."""
        cluster_id = cluster["id"]
        category = cluster["category"]
        severity = cluster["severity"] or 1
        headline = cluster["representative_headline"] or ""
        article_count = cluster["article_count"] or 1
        novelty = cluster["novelty_score"] or 0.5
        detected_at = cluster["first_detected_at"]

        try:
            assets = json.loads(cluster["assets_affected"] or "[]")
        except (json.JSONDecodeError, TypeError):
            assets = []

        if not assets:
            return []

        # Check if prediction already exists for this cluster
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()[0]
        if existing > 0:
            return []

        # Determine direction from category + headline keywords
        direction = self._determine_direction(category, headline)
        if direction == "neutral":
            return []

        # Get sources involved
        sources = self._get_cluster_sources(cluster_id)
        source_count = len(sources)

        # Get average source trust
        avg_trust = self._get_avg_source_trust(sources)

        # Apply min thresholds from optimized config
        if severity < self._min_severity:
            return []
        if source_count < self._min_sources:
            return []

        # Compute confidence using optimized weights
        base = CATEGORY_BASE_CONFIDENCE.get(category, 0.4)
        severity_factor = severity / 5.0
        source_boost = min(self._source_boost_cap, (source_count - 1) * self._source_boost_per)
        credibility_factor = avg_trust
        novelty_factor = max(0.2, novelty)

        confidence = (
            base * (0.3 + self._severity_weight * severity_factor)
            * (0.4 + self._trust_weight * credibility_factor)
            * (0.5 + self._novelty_weight * novelty_factor)
            + source_boost
        )
        confidence = round(min(0.95, max(0.1, confidence)), 3)

        # Build reasoning (concise)
        parts = []
        parts.append(f"{source_count} source{'s' if source_count > 1 else ''}")
        parts.append(f"sev {severity}")
        if avg_trust >= 0.6:
            parts.append("trusted sources")
        elif avg_trust < 0.4:
            parts.append("low-trust sources")
        if article_count >= 3:
            parts.append(f"{article_count} articles confirming")
        reasoning = " · ".join(parts)

        predictions = []
        for asset in assets:
            price = self._get_price(asset, detected_at)
            if not price:
                continue

            pred = {
                "cluster_id": cluster_id,
                "asset": asset,
                "direction": direction,
                "confidence": confidence,
                "predicted_at": detected_at,
                "price_at_prediction": price,
                "sources": json.dumps(sources),
                "source_count": source_count,
                "category": category,
                "severity": severity,
                "reasoning": reasoning,
            }

            self.conn.execute(
                """INSERT INTO predictions
                   (cluster_id, asset, direction, confidence, predicted_at,
                    price_at_prediction, sources, source_count, category,
                    severity, reasoning)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster_id, asset, direction, confidence, detected_at,
                    price, json.dumps(sources), source_count, category,
                    severity, reasoning,
                ),
            )
            predictions.append(pred)

        self.conn.commit()
        return predictions

    def _determine_direction(self, category: str, headline: str) -> str:
        hl = headline.lower()
        base_dir = CATEGORY_DIRECTION.get(category, "neutral")

        # Keyword overrides
        bullish_kw = ["approved", "approval", "adoption", "partnership", "upgrade", "bullish", "inflow", "ath", "record"]
        bearish_kw = ["hack", "exploit", "ban", "crash", "dump", "bearish", "outflow", "liquidat", "vulnerability"]

        for kw in bullish_kw:
            if kw in hl:
                return "bullish"
        for kw in bearish_kw:
            if kw in hl:
                return "bearish"

        return base_dir

    def _get_cluster_sources(self, cluster_id: int) -> list[str]:
        rows = self.conn.execute(
            """SELECT DISTINCT a.source
               FROM articles a
               JOIN article_cluster_map acm ON a.id = acm.article_id
               WHERE acm.cluster_id = ?""",
            (cluster_id,),
        ).fetchall()
        return [r["source"] for r in rows]

    def _get_avg_source_trust(self, sources: list[str]) -> float:
        if not sources:
            return 0.5
        placeholders = ",".join("?" * len(sources))
        rows = self.conn.execute(
            f"SELECT trust_score FROM source_trust WHERE source_name IN ({placeholders})",
            sources,
        ).fetchall()
        if not rows:
            return 0.5  # default trust for unknown sources
        return sum(r["trust_score"] for r in rows) / len(rows)

    def _get_price(self, asset: str, timestamp: str) -> float | None:
        row = self.conn.execute(
            """SELECT close FROM prices
               WHERE asset = ? AND timestamp <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (asset, timestamp),
        ).fetchone()
        return row["close"] if row else None

    def close(self) -> None:
        self.conn.close()


# ═══════════════════════════════════════════════════════════════
#  GRADER
# ═══════════════════════════════════════════════════════════════

class PredictionGrader:
    """Grade predictions against actual price outcomes."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.conn = _get_conn(db_path)

    def grade_all(self) -> dict[str, int]:
        """Grade all ungraded predictions that have enough time elapsed.

        Returns:
            Dict with counts: {graded, skipped, already_graded}
        """
        ungraded = self.conn.execute(
            "SELECT * FROM predictions WHERE graded = 0 ORDER BY predicted_at ASC"
        ).fetchall()

        counts = {"graded": 0, "skipped": 0}

        for pred in ungraded:
            graded = self._grade_prediction(dict(pred))
            if graded:
                counts["graded"] += 1
            else:
                counts["skipped"] += 1

        # Update source trust scores after grading
        self._update_source_trust()

        logger.info("Graded %d predictions, skipped %d", counts["graded"], counts["skipped"])
        return counts

    def _grade_prediction(self, pred: dict) -> bool:
        """Grade a single prediction. Returns True if graded."""
        asset = pred["asset"]
        direction = pred["direction"]
        predicted_at = pred["predicted_at"]
        entry_price = pred["price_at_prediction"]

        if not entry_price or entry_price <= 0:
            return False

        # Get prices at 1h, 4h, 24h after prediction
        prices = {}
        for hours, label in [(1, "1h"), (4, "4h"), (24, "24h")]:
            target_ts = self._offset_ts(predicted_at, hours)
            row = self.conn.execute(
                """SELECT close FROM prices
                   WHERE asset = ? AND timestamp >= ?
                   ORDER BY timestamp ASC LIMIT 1""",
                (asset, target_ts),
            ).fetchone()
            if row:
                prices[label] = row["close"]

        # Need at least 1h price to grade
        if "1h" not in prices:
            return False

        # Calculate directional change
        updates = {"graded": 1, "graded_at": datetime.now(timezone.utc).isoformat()}
        grades = {}

        for label in ["1h", "4h", "24h"]:
            if label not in prices:
                continue
            price = prices[label]
            raw_change = ((price - entry_price) / entry_price) * 100

            # Directional change: positive = move in predicted direction
            if direction == "bullish":
                dir_change = raw_change
            else:
                dir_change = -raw_change

            grade = self._assign_grade(dir_change)
            grades[label] = grade

            updates[f"price_{label}"] = round(price, 2)
            updates[f"change_{label}_pct"] = round(raw_change, 3)
            updates[f"grade_{label}"] = grade

        # Update prediction record
        set_parts = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [pred["id"]]
        self.conn.execute(
            f"UPDATE predictions SET {set_parts} WHERE id = ?", vals
        )
        self.conn.commit()

        # Update source stats
        try:
            sources = json.loads(pred["sources"] or "[]")
        except (json.JSONDecodeError, TypeError):
            sources = []

        for source in sources:
            for label, grade in grades.items():
                correct = grade in ("A", "B")
                self._record_source_outcome(source, label, correct)

        return True

    def _assign_grade(self, directional_change_pct: float) -> str:
        if directional_change_pct >= GRADE_THRESHOLDS["A"]:
            return "A"
        elif directional_change_pct >= GRADE_THRESHOLDS["B"]:
            return "B"
        elif directional_change_pct >= GRADE_THRESHOLDS["C"]:
            return "C"
        elif directional_change_pct >= GRADE_THRESHOLDS["D"]:
            return "D"
        else:
            return "F"

    def _record_source_outcome(self, source: str, timeframe: str, correct: bool) -> None:
        # Ensure source exists
        self.conn.execute(
            "INSERT OR IGNORE INTO source_trust (source_name, last_updated) VALUES (?, ?)",
            (source, datetime.now(timezone.utc).isoformat()),
        )
        if correct:
            self.conn.execute(
                f"UPDATE source_trust SET correct_{timeframe} = correct_{timeframe} + 1, "
                f"total_predictions = total_predictions + 1, "
                f"last_updated = ? WHERE source_name = ?",
                (datetime.now(timezone.utc).isoformat(), source),
            )
        else:
            self.conn.execute(
                f"UPDATE source_trust SET wrong_{timeframe} = wrong_{timeframe} + 1, "
                f"total_predictions = total_predictions + 1, "
                f"last_updated = ? WHERE source_name = ?",
                (datetime.now(timezone.utc).isoformat(), source),
            )
        self.conn.commit()

    def _update_source_trust(self) -> None:
        """Recalculate trust scores for all sources."""
        sources = self.conn.execute("SELECT * FROM source_trust").fetchall()

        for s in sources:
            total = s["total_predictions"]
            if total == 0:
                continue

            correct_1h = s["correct_1h"]
            wrong_1h = s["wrong_1h"]
            correct_4h = s["correct_4h"]
            wrong_4h = s["wrong_4h"]
            correct_24h = s["correct_24h"]
            wrong_24h = s["wrong_24h"]

            acc_1h = correct_1h / (correct_1h + wrong_1h) if (correct_1h + wrong_1h) > 0 else 0
            acc_4h = correct_4h / (correct_4h + wrong_4h) if (correct_4h + wrong_4h) > 0 else 0
            acc_24h = correct_24h / (correct_24h + wrong_24h) if (correct_24h + wrong_24h) > 0 else 0

            # Weighted trust score (4h weighted highest — most actionable timeframe)
            weighted_acc = acc_1h * 0.25 + acc_4h * 0.45 + acc_24h * 0.30
            trust = round(min(1.0, max(0.0, weighted_acc)), 3)

            # Noise detection: high volume + low accuracy
            is_noise = 1 if total >= 15 and weighted_acc < 0.40 else 0

            # Narrative diversity: count distinct categories
            cats = self.conn.execute(
                """SELECT COUNT(DISTINCT p.category) as cnt
                   FROM predictions p
                   WHERE p.sources LIKE ? AND p.graded = 1""",
                (f'%"{s["source_name"]}"%',),
            ).fetchone()
            diversity = cats["cnt"] if cats else 0

            self.conn.execute(
                """UPDATE source_trust SET
                   accuracy_1h = ?, accuracy_4h = ?, accuracy_24h = ?,
                   trust_score = ?, is_noise_source = ?,
                   narrative_diversity = ?, last_updated = ?
                   WHERE source_name = ?""",
                (
                    round(acc_1h, 3), round(acc_4h, 3), round(acc_24h, 3),
                    trust, is_noise, diversity,
                    datetime.now(timezone.utc).isoformat(),
                    s["source_name"],
                ),
            )

        self.conn.commit()
        logger.info("Updated trust scores for %d sources", len(sources))

    @staticmethod
    def _offset_ts(iso_ts: str, hours: int) -> str:
        from datetime import timedelta
        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def close(self) -> None:
        self.conn.close()


# ═══════════════════════════════════════════════════════════════
#  CLI helpers
# ═══════════════════════════════════════════════════════════════

def run_predictions(since: str | None = None) -> int:
    """Generate predictions. Returns count."""
    p = Predictor()
    preds = p.generate(since=since)
    p.close()
    return len(preds)


def run_grading() -> dict[str, int]:
    """Grade all ungraded predictions. Returns counts."""
    g = PredictionGrader()
    counts = g.grade_all()
    g.close()
    return counts


def get_trust_leaderboard(db_path: Path | str | None = None) -> list[dict]:
    """Get source trust rankings."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM source_trust
           WHERE total_predictions > 0
           ORDER BY trust_score DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_predictions(db_path: Path | str | None = None, limit: int = 50) -> list[dict]:
    """Get recent predictions with grades."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM predictions ORDER BY predicted_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d["sources"] or "[]")
        except (json.JSONDecodeError, TypeError):
            d["sources"] = []
        results.append(d)
    return results


def get_accuracy_stats(db_path: Path | str | None = None) -> dict:
    """Get overall prediction accuracy by timeframe."""
    conn = _get_conn(db_path)

    stats = {}
    for tf in ["1h", "4h", "24h"]:
        total = conn.execute(
            f"SELECT COUNT(*) FROM predictions WHERE grade_{tf} IS NOT NULL"
        ).fetchone()[0]
        correct = conn.execute(
            f"SELECT COUNT(*) FROM predictions WHERE grade_{tf} IN ('A', 'B')"
        ).fetchone()[0]
        by_grade = {}
        for grade in ["A", "B", "C", "D", "F"]:
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE grade_{tf} = ?", (grade,)
            ).fetchone()[0]
            by_grade[grade] = cnt

        stats[tf] = {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 3) if total > 0 else 0,
            "by_grade": by_grade,
        }

    conn.close()
    return stats
