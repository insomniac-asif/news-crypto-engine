"""Event impact analysis — the core research deliverable.

Measures how classified events affect crypto prices at various time horizons.
Answers: "Do specific event types reliably move prices?"

Operates on event_clusters (deduplicated) when available, falling back to
raw events for backward compatibility.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from scipy import stats

from src.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ImpactResult:
    """Price impact statistics for an event category."""

    category: str
    min_severity: int
    window_hours: int
    sample_size: int
    avg_move_pct: float
    median_move_pct: float
    std_dev: float
    win_rate: float  # fraction of positive moves
    t_statistic: float
    p_value: float
    significant: bool
    moves: list[float] = field(default_factory=list, repr=False)


class EventImpactAnalyzer:
    """Measure price response to classified events.

    For each event, looks up the price at detection time and at
    +1h, +4h, +24h, then aggregates statistics by category and severity.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the analyzer.

        Args:
            db: Database instance.
            config: Full config dict (expects 'analysis' key).
        """
        self.db = db
        cfg = (config or {}).get("analysis", {})
        self.impact_windows: list[int] = cfg.get("impact_windows", [1, 4, 24])
        self.min_sample_size: int = cfg.get("min_sample_size", 10)
        self.significance_level: float = cfg.get("significance_level", 0.05)

    def _has_clusters(self) -> bool:
        """Check if event_clusters table has data."""
        try:
            with self.db.connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM event_clusters").fetchone()[0]
                return count > 0
        except Exception:
            return False

    def _get_cluster_events(self) -> list[dict[str, Any]]:
        """Get events from event_clusters table.

        Returns:
            List of cluster dicts with same shape as events.
        """
        query = """
            SELECT id, category, severity, first_detected_at AS detected_at,
                   assets_affected, representative_headline AS summary,
                   article_count, novelty_score, sentiment
            FROM event_clusters
            ORDER BY first_detected_at DESC
            LIMIT 10000
        """
        with self.db.connect() as conn:
            rows = conn.execute(query).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["assets_affected"] = json.loads(d.get("assets_affected", "[]"))
                results.append(d)
            return results

    def compute_event_moves(self) -> list[dict[str, Any]]:
        """Compute price moves for all events that have price data.

        Uses event_clusters when available, falls back to raw events.

        For each event, finds the closest price at detection time and
        computes percentage change at each impact window.

        Returns:
            List of dicts with event info and price moves.
        """
        if self._has_clusters():
            events = self._get_cluster_events()
            logger.info("Using %d event clusters for impact analysis", len(events))
        else:
            events = self.db.get_events(limit=10000)
            logger.info("No clusters found, using %d raw events", len(events))

        results: list[dict[str, Any]] = []

        for event in events:
            assets = event.get("assets_affected", [])
            if not assets:
                continue

            detected_at = event["detected_at"]

            for asset in assets:
                base_price = self.db.get_price_at(asset, detected_at)
                if not base_price:
                    continue

                base_close = base_price["close"]
                if base_close <= 0:
                    continue

                moves: dict[str, Optional[float]] = {}
                for hours in self.impact_windows:
                    future_ts = self._offset_timestamp(detected_at, hours)
                    future_price = self.db.get_price_at(asset, future_ts)

                    if future_price and future_price["timestamp"] != base_price["timestamp"]:
                        pct_change = ((future_price["close"] - base_close) / base_close) * 100
                        moves[f"{hours}h"] = round(pct_change, 4)
                    else:
                        moves[f"{hours}h"] = None

                results.append({
                    "event_id": event["id"],
                    "category": event["category"],
                    "severity": event["severity"],
                    "asset": asset,
                    "detected_at": detected_at,
                    "base_price": base_close,
                    "moves": moves,
                    "summary": event.get("summary", ""),
                    "sentiment": event.get("sentiment", 0.0),
                    "article_count": event.get("article_count", 1),
                    "novelty_score": event.get("novelty_score", 1.0),
                })

        logger.info("Computed price moves for %d event-asset pairs", len(results))
        return results

    def analyze_by_category(
        self,
        min_severity: int = 1,
    ) -> list[ImpactResult]:
        """Analyze price impact grouped by event category.

        Args:
            min_severity: Only include events at or above this severity.

        Returns:
            List of ImpactResult for each category-window combination.
        """
        all_moves = self.compute_event_moves()

        # Group moves by category and window
        grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

        for entry in all_moves:
            if entry["severity"] < min_severity:
                continue
            cat = entry["category"]
            for window_key, move in entry["moves"].items():
                if move is not None:
                    grouped[cat][window_key].append(move)

        results: list[ImpactResult] = []
        for category, windows in sorted(grouped.items()):
            for window_key, moves in sorted(windows.items()):
                hours = int(window_key.replace("h", ""))
                result = self._compute_statistics(
                    category, min_severity, hours, moves,
                )
                results.append(result)

        logger.info("Analyzed impact for %d category-window combinations", len(results))
        return results

    def analyze_by_category_and_severity(self) -> list[ImpactResult]:
        """Analyze impact for each category at different severity thresholds.

        Returns:
            List of ImpactResult for high-severity (3+) and all events.
        """
        results: list[ImpactResult] = []
        results.extend(self.analyze_by_category(min_severity=1))
        results.extend(self.analyze_by_category(min_severity=3))
        return results

    def generate_report(self, min_severity: int = 1) -> str:
        """Generate a human-readable impact analysis report.

        Args:
            min_severity: Only include events at or above this severity.

        Returns:
            Formatted report string.
        """
        results = self.analyze_by_category(min_severity=min_severity)

        if not results:
            return "No event impact data available. Ingest and process articles first."

        lines = [
            "=" * 72,
            "EVENT IMPACT ANALYSIS REPORT",
            f"Minimum severity: {min_severity}",
            "=" * 72,
            "",
            f"{'Category':<20} {'Window':>6} {'Avg%':>8} {'Med%':>8} "
            f"{'WinR':>6} {'N':>5} {'p-val':>8} {'Sig':>4}",
            "-" * 72,
        ]

        for r in sorted(results, key=lambda x: (x.category, x.window_hours)):
            sig_mark = "***" if r.significant else ""
            lines.append(
                f"{r.category:<20} {r.window_hours:>4}h {r.avg_move_pct:>+7.2f}% "
                f"{r.median_move_pct:>+7.2f}% {r.win_rate:>5.1%} {r.sample_size:>5} "
                f"{r.p_value:>8.4f} {sig_mark:>4}"
            )

        lines.extend([
            "",
            "-" * 72,
            f"Significance level: p < {self.significance_level}",
            f"Min sample size: {self.min_sample_size}",
            "*** = statistically significant (vs. zero mean)",
            "=" * 72,
        ])

        # Highlight significant findings
        significant = [r for r in results if r.significant]
        if significant:
            lines.extend(["", "KEY FINDINGS (statistically significant):", ""])
            for r in significant:
                direction = "+" if r.avg_move_pct > 0 else ""
                lines.append(
                    f"  {r.category} → avg {r.window_hours}h move: "
                    f"{direction}{r.avg_move_pct:.1f}%, n={r.sample_size}, p={r.p_value:.4f}"
                )

        return "\n".join(lines)

    def _compute_statistics(
        self,
        category: str,
        min_severity: int,
        window_hours: int,
        moves: list[float],
    ) -> ImpactResult:
        """Compute statistical measures for a set of price moves.

        Uses one-sample t-test against zero (no move) as baseline.

        Args:
            category: Event category.
            min_severity: Severity threshold used.
            window_hours: Time window in hours.
            moves: List of percentage price changes.

        Returns:
            ImpactResult with full statistics.
        """
        n = len(moves)

        if n == 0:
            return ImpactResult(
                category=category, min_severity=min_severity,
                window_hours=window_hours, sample_size=0,
                avg_move_pct=0, median_move_pct=0, std_dev=0,
                win_rate=0, t_statistic=0, p_value=1.0,
                significant=False, moves=[],
            )

        import numpy as np

        arr = np.array(moves)
        avg = float(np.mean(arr))
        median = float(np.median(arr))
        std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        win_rate = float(np.sum(arr > 0) / n)

        # One-sample t-test: is the mean significantly different from 0?
        if n >= self.min_sample_size and std > 0:
            t_stat, p_val = stats.ttest_1samp(arr, 0)
            significant = p_val < self.significance_level
        else:
            t_stat, p_val = 0.0, 1.0
            significant = False

        return ImpactResult(
            category=category,
            min_severity=min_severity,
            window_hours=window_hours,
            sample_size=n,
            avg_move_pct=round(avg, 4),
            median_move_pct=round(median, 4),
            std_dev=round(std, 4),
            win_rate=round(win_rate, 4),
            t_statistic=round(float(t_stat), 4),
            p_value=round(float(p_val), 6),
            significant=significant,
            moves=moves,
        )

    @staticmethod
    def _offset_timestamp(iso_ts: str, hours: int) -> str:
        """Add hours to an ISO timestamp string.

        Args:
            iso_ts: ISO 8601 UTC timestamp.
            hours: Hours to add.

        Returns:
            New ISO timestamp string.
        """
        from datetime import datetime, timedelta, timezone

        # Handle both formats: with and without Z suffix
        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)

        new_dt = dt + timedelta(hours=hours)
        return new_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
