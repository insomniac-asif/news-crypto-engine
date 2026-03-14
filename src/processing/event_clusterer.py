"""Event clustering — deduplicate articles covering the same underlying event.

Groups articles with similar content, same event category, and close timestamps
into clusters. Each cluster represents one canonical event. Prevents the same
news story from generating multiple signals and inflating impact analysis.

Uses TF-IDF + cosine similarity for text comparison (lightweight, no GPU needed).
"""

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.processing.source_credibility import SourceCredibility
from src.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class EventCluster:
    """A canonical event representing one or more articles about the same story."""

    cluster_id: Optional[int]  # DB id, None before insertion
    category: str
    severity: int  # max severity across articles
    sentiment: float  # credibility-weighted average sentiment
    first_detected_at: str
    last_article_at: str
    article_count: int
    representative_headline: str
    novelty_score: float
    assets_affected: list[str]
    article_ids: list[int] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)


class EventClusterer:
    """Cluster articles covering the same underlying event.

    Pipeline position: runs AFTER article processing (events extracted)
    and BEFORE analysis (impact, signals, backtesting).

    Clustering criteria — two articles are in the same cluster if ALL of:
    1. Same event category
    2. Published within 48 hours of each other
    3. TF-IDF cosine similarity > threshold (default 0.6)
    """

    def __init__(
        self,
        db: Database,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize the clusterer.

        Args:
            db: Database instance.
            config: Full config dict. Looks for 'clustering' key.
        """
        self.db = db
        cfg = (config or {}).get("clustering", {})
        self.similarity_threshold: float = cfg.get("similarity_threshold", 0.6)
        self.time_window_hours: int = cfg.get("time_window_hours", 48)
        self.novelty_lambda: float = cfg.get("novelty_lambda", 0.1)
        self.credibility = SourceCredibility(config)

    def cluster_events(self) -> list[EventCluster]:
        """Run clustering on all events that haven't been clustered yet.

        Returns:
            List of EventCluster objects (new and updated).
        """
        # Get all events with article data
        events_with_articles = self._get_unclustered_events()

        if not events_with_articles:
            logger.info("No unclustered events to process")
            return []

        # Group by category first (clustering only within same category)
        by_category: dict[str, list[dict]] = defaultdict(list)
        for ea in events_with_articles:
            by_category[ea["category"]].append(ea)

        all_clusters: list[EventCluster] = []

        for category, items in by_category.items():
            clusters = self._cluster_within_category(items)
            all_clusters.extend(clusters)

        # Store clusters in DB
        for cluster in all_clusters:
            self._store_cluster(cluster)

        logger.info(
            "Clustered %d events into %d clusters",
            sum(c.article_count for c in all_clusters),
            len(all_clusters),
        )
        return all_clusters

    def update_novelty_scores(self) -> int:
        """Update novelty scores for all existing clusters.

        Returns:
            Number of clusters updated.
        """
        clusters = self.get_clusters()
        updated = 0
        now = datetime.now(timezone.utc)

        for c in clusters:
            first_dt = self._parse_timestamp(c["first_detected_at"])
            hours_since = (now - first_dt).total_seconds() / 3600
            new_novelty = math.exp(-self.novelty_lambda * hours_since)

            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE event_clusters SET novelty_score = ? WHERE id = ?",
                    (round(new_novelty, 6), c["id"]),
                )
                conn.commit()
            updated += 1

        return updated

    def get_clusters(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        asset: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch event clusters with optional filtering.

        Args:
            category: Filter by event category.
            min_severity: Minimum severity threshold.
            asset: Filter to clusters affecting this asset.
            since: Only clusters first detected after this timestamp.
            limit: Max rows.

        Returns:
            List of cluster dicts with parsed assets_affected.
        """
        query = "SELECT * FROM event_clusters WHERE severity >= ?"
        params: list[Any] = [min_severity]

        if category:
            query += " AND category = ?"
            params.append(category)
        if since:
            query += " AND first_detected_at >= ?"
            params.append(since)

        query += " ORDER BY first_detected_at DESC LIMIT ?"
        params.append(limit)

        with self.db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["assets_affected"] = json.loads(d.get("assets_affected", "[]"))
                if asset and asset not in d["assets_affected"]:
                    continue
                results.append(d)
            return results

    def get_cluster_articles(self, cluster_id: int) -> list[dict[str, Any]]:
        """Get all articles in a cluster.

        Args:
            cluster_id: The cluster's DB id.

        Returns:
            List of article dicts.
        """
        query = """
            SELECT a.* FROM articles a
            JOIN article_cluster_map acm ON a.id = acm.article_id
            WHERE acm.cluster_id = ?
            ORDER BY a.published_at ASC
        """
        with self.db.connect() as conn:
            rows = conn.execute(query, (cluster_id,)).fetchall()
            return [dict(r) for r in rows]

    def _get_unclustered_events(self) -> list[dict[str, Any]]:
        """Get events whose articles haven't been assigned to clusters yet.

        Returns:
            List of event+article dicts.
        """
        query = """
            SELECT e.id AS event_id, e.article_id, e.category, e.severity,
                   e.summary, e.assets_affected, e.detected_at,
                   a.title, a.content, a.source, a.published_at
            FROM events e
            JOIN articles a ON e.article_id = a.id
            LEFT JOIN article_cluster_map acm ON a.id = acm.article_id
            WHERE acm.article_id IS NULL
            ORDER BY e.detected_at ASC
        """
        with self.db.connect() as conn:
            rows = conn.execute(query).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["assets_affected"] = json.loads(d.get("assets_affected", "[]"))
                results.append(d)
            return results

    def _cluster_within_category(self, items: list[dict]) -> list[EventCluster]:
        """Cluster articles within the same event category.

        Uses TF-IDF cosine similarity + time window.

        Args:
            items: List of event+article dicts, all same category.

        Returns:
            List of EventCluster objects.
        """
        if not items:
            return []

        # Sort by publication time
        items.sort(key=lambda x: x["published_at"])

        # Build text corpus for TF-IDF
        texts = []
        for item in items:
            text = f"{item['title']} {item.get('content', '') or ''}"
            texts.append(text)

        # Compute TF-IDF similarity matrix
        similarity_matrix = self._compute_similarity_matrix(texts)

        # Union-Find clustering
        n = len(items)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                # Check time window
                t_i = self._parse_timestamp(items[i]["published_at"])
                t_j = self._parse_timestamp(items[j]["published_at"])
                if abs((t_j - t_i).total_seconds()) > self.time_window_hours * 3600:
                    continue

                # Check similarity
                if similarity_matrix[i][j] >= self.similarity_threshold:
                    union(i, j)

        # Group by cluster
        cluster_groups: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            cluster_groups[find(i)].append(i)

        # Build EventCluster objects
        clusters: list[EventCluster] = []
        for indices in cluster_groups.values():
            cluster_items = [items[i] for i in indices]
            cluster = self._build_cluster(cluster_items)
            clusters.append(cluster)

        return clusters

    def _compute_similarity_matrix(self, texts: list[str]) -> list[list[float]]:
        """Compute pairwise cosine similarity using TF-IDF.

        Falls back to word-overlap similarity (rescaled to match cosine range)
        if sklearn is not installed.

        Args:
            texts: List of document texts.

        Returns:
            N×N similarity matrix with values comparable to cosine similarity.
        """
        n = len(texts)
        if n <= 1:
            return [[1.0]]

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            # For small corpora (< 5 docs), disable max_df filtering
            # because with 2 docs, max_df=0.95 removes all shared terms
            max_df = 1.0 if n < 5 else 0.95

            vectorizer = TfidfVectorizer(
                max_features=5000,
                stop_words="english",
                min_df=1,
                max_df=max_df,
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
            sim_matrix = cosine_similarity(tfidf_matrix)
            return sim_matrix.tolist()

        except ImportError:
            logger.warning(
                "sklearn not available, falling back to word overlap similarity"
            )
            return self._fallback_similarity(texts)

    def _fallback_similarity(self, texts: list[str]) -> list[list[float]]:
        """Word-overlap similarity as fallback when sklearn is unavailable.

        Uses overlap coefficient (intersection / min_set_size) rather than
        Jaccard, because it better approximates cosine similarity behavior
        for documents of different lengths.

        Args:
            texts: List of document texts.

        Returns:
            N×N similarity matrix.
        """
        n = len(texts)
        # Simple stop words to filter out
        stop_words = {
            "the", "a", "an", "is", "was", "were", "are", "been", "be",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "shall", "can", "need",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "nor", "not", "so", "yet", "both", "either",
            "that", "this", "these", "those", "it", "its", "they", "them",
        }
        word_sets = []
        for t in texts:
            words = set(w.lower().strip(".,;:!?\"'()[]") for w in t.split())
            words = words - stop_words
            words = {w for w in words if len(w) > 2}
            word_sets.append(words)

        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            matrix[i][i] = 1.0
            for j in range(i + 1, n):
                if not word_sets[i] or not word_sets[j]:
                    continue
                intersection = len(word_sets[i] & word_sets[j])
                # Overlap coefficient: intersection / min(|A|, |B|)
                min_size = min(len(word_sets[i]), len(word_sets[j]))
                sim = intersection / min_size if min_size > 0 else 0.0
                matrix[i][j] = sim
                matrix[j][i] = sim
        return matrix

    def _build_cluster(self, items: list[dict]) -> EventCluster:
        """Build an EventCluster from a group of related articles.

        Args:
            items: List of event+article dicts in the same cluster.

        Returns:
            EventCluster object.
        """
        # Sort by time
        items.sort(key=lambda x: x["published_at"])

        # Category (all same)
        category = items[0]["category"]

        # Combined severity = max
        severity = max(item["severity"] for item in items)

        # Representative headline = from highest-credibility source
        best_item = max(items, key=lambda x: self.credibility.get_weight(x.get("source", "")))
        representative_headline = best_item["title"]

        # First and last timestamps
        first_detected = items[0]["published_at"]
        last_article = items[-1]["published_at"]

        # Combined assets (union)
        all_assets: set[str] = set()
        for item in items:
            for a in item.get("assets_affected", []):
                all_assets.add(a)

        # Sentiment: credibility-weighted average
        # Extract sentiment from summary if available
        sentiments: list[tuple[float, float]] = []  # (sentiment, weight)
        for item in items:
            summary = item.get("summary", "")
            sent_val = self._extract_sentiment_from_summary(summary)
            weight = self.credibility.get_weight(item.get("source", ""))
            sentiments.append((sent_val, weight))

        total_weight = sum(w for _, w in sentiments)
        if total_weight > 0:
            combined_sentiment = sum(s * w for s, w in sentiments) / total_weight
        else:
            combined_sentiment = 0.0

        # Novelty score
        first_dt = self._parse_timestamp(first_detected)
        now = datetime.now(timezone.utc)
        hours_since = (now - first_dt).total_seconds() / 3600
        novelty = math.exp(-self.novelty_lambda * hours_since)

        article_ids = [item["article_id"] for item in items]
        event_ids = [item["event_id"] for item in items]

        return EventCluster(
            cluster_id=None,
            category=category,
            severity=severity,
            sentiment=round(combined_sentiment, 4),
            first_detected_at=first_detected,
            last_article_at=last_article,
            article_count=len(items),
            representative_headline=representative_headline,
            novelty_score=round(novelty, 6),
            assets_affected=sorted(all_assets),
            article_ids=article_ids,
            event_ids=event_ids,
        )

    def _store_cluster(self, cluster: EventCluster) -> int:
        """Persist an EventCluster to the database.

        Args:
            cluster: The cluster to store.

        Returns:
            The cluster's DB id.
        """
        assets_json = json.dumps(cluster.assets_affected)

        with self.db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO event_clusters
                   (category, severity, sentiment, first_detected_at, last_article_at,
                    article_count, representative_headline, novelty_score, assets_affected)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster.category,
                    cluster.severity,
                    cluster.sentiment,
                    cluster.first_detected_at,
                    cluster.last_article_at,
                    cluster.article_count,
                    cluster.representative_headline,
                    cluster.novelty_score,
                    assets_json,
                ),
            )
            cluster_id = cursor.lastrowid
            cluster.cluster_id = cluster_id

            # Map articles to this cluster
            for article_id in cluster.article_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO article_cluster_map (article_id, cluster_id) VALUES (?, ?)",
                    (article_id, cluster_id),
                )

            conn.commit()

        logger.debug(
            "Stored cluster %d: %s (%d articles, severity=%d)",
            cluster_id, cluster.category, cluster.article_count, cluster.severity,
        )
        return cluster_id

    @staticmethod
    def _extract_sentiment_from_summary(summary: str) -> float:
        """Extract numeric sentiment value from event summary string.

        Summaries contain 'sentiment=label(score)' from the processing pipeline.

        Args:
            summary: Event summary string.

        Returns:
            Sentiment score, or 0.0 if not found.
        """
        import re

        match = re.search(r"sentiment=\w+\(([-\d.]+)\)", summary)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime:
        """Parse an ISO timestamp string to datetime.

        Args:
            ts: ISO 8601 timestamp string.

        Returns:
            Timezone-aware datetime.
        """
        ts_clean = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts_clean)
        except ValueError:
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)


def compute_novelty(hours_since_first_detection: float, decay_lambda: float = 0.1) -> float:
    """Compute novelty score using exponential decay.

    novelty = exp(-lambda * hours)

    At lambda=0.1:
    - 0 hours: 1.0 (brand new)
    - 7 hours: ~0.50
    - 23 hours: ~0.10
    - 48 hours: ~0.008

    Args:
        hours_since_first_detection: Hours since first article about this event.
        decay_lambda: Decay rate parameter.

    Returns:
        Novelty score between 0 and 1.
    """
    return math.exp(-decay_lambda * hours_since_first_detection)
