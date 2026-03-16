"""Backtest and optimize prediction parameters for higher accuracy.

Runs historical event clusters through the predictor with different
parameter configurations, grades against actual prices, and reports
which settings produce the best accuracy.

Tunable parameters:
- Category direction mapping (bullish/bearish per category)
- Base confidence per category
- Confidence formula weights (severity, source count, trust, novelty)
- Keyword overrides
- Minimum severity threshold for generating predictions
"""

from __future__ import annotations

import json
import itertools
import logging
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "news_crypto.db"

# Grade thresholds (same as predictions.py)
def assign_grade(directional_change_pct: float) -> str:
    if directional_change_pct >= 0.5:
        return "A"
    elif directional_change_pct >= 0.1:
        return "B"
    elif directional_change_pct >= -0.1:
        return "C"
    elif directional_change_pct >= -0.5:
        return "D"
    return "F"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ═══════════════════════════════════════════════════════════════
#  Parameter configurations to test
# ═══════════════════════════════════════════════════════════════

# Baseline — current defaults
BASELINE = {
    "name": "baseline",
    "direction_map": {
        "ADOPTION": "bullish",
        "PROTOCOL": "bullish",
        "EXCHANGE": "neutral",
        "SENTIMENT": "neutral",
        "MARKET_STRUCTURE": "neutral",
        "MACRO": "bearish",
        "REGULATORY": "bearish",
        "SECURITY": "bearish",
    },
    "min_severity": 1,
    "min_sources": 1,
    "severity_weight": 0.7,
    "trust_weight": 0.6,
    "novelty_weight": 0.5,
    "source_boost_per": 0.08,
    "source_boost_cap": 0.25,
}


def generate_configs() -> list[dict]:
    """Generate parameter configurations to test."""
    configs = [BASELINE]

    # Variant 1: Higher severity threshold (only predict on impactful events)
    v1 = deepcopy(BASELINE)
    v1["name"] = "high_severity_only"
    v1["min_severity"] = 3
    configs.append(v1)

    # Variant 2: Require multiple sources (confirmation required)
    v2 = deepcopy(BASELINE)
    v2["name"] = "multi_source_required"
    v2["min_sources"] = 2
    configs.append(v2)

    # Variant 3: Contrarian regulatory (regulatory = bullish, proven in experiments)
    v3 = deepcopy(BASELINE)
    v3["name"] = "contrarian_regulatory"
    v3["direction_map"]["REGULATORY"] = "bullish"
    configs.append(v3)

    # Variant 4: All bullish bias (crypto trends up long-term)
    v4 = deepcopy(BASELINE)
    v4["name"] = "bullish_bias"
    v4["direction_map"]["MACRO"] = "bullish"
    v4["direction_map"]["REGULATORY"] = "bullish"
    v4["direction_map"]["EXCHANGE"] = "bullish"
    v4["direction_map"]["SENTIMENT"] = "bullish"
    v4["direction_map"]["MARKET_STRUCTURE"] = "bullish"
    configs.append(v4)

    # Variant 5: Heavy source weighting
    v5 = deepcopy(BASELINE)
    v5["name"] = "source_heavy"
    v5["source_boost_per"] = 0.15
    v5["source_boost_cap"] = 0.40
    v5["trust_weight"] = 0.8
    configs.append(v5)

    # Variant 6: Novelty focused (only predict on fresh news)
    v6 = deepcopy(BASELINE)
    v6["name"] = "novelty_focused"
    v6["novelty_weight"] = 0.9
    configs.append(v6)

    # Variant 7: Contrarian sentiment (experiment showed contrarian sentiment is profitable)
    v7 = deepcopy(BASELINE)
    v7["name"] = "contrarian_sentiment"
    v7["direction_map"]["SENTIMENT"] = "bearish"  # fade sentiment events
    v7["direction_map"]["MACRO"] = "bullish"       # fade macro fear
    configs.append(v7)

    # Variant 8: High severity + multi source (strictest filter)
    v8 = deepcopy(BASELINE)
    v8["name"] = "strict_filter"
    v8["min_severity"] = 3
    v8["min_sources"] = 2
    configs.append(v8)

    # Variant 9: Security-focused contrarian (fade hack FUD)
    v9 = deepcopy(BASELINE)
    v9["name"] = "fade_security_fud"
    v9["direction_map"]["SECURITY"] = "bullish"
    configs.append(v9)

    # Variant 10: Severity weight emphasis
    v10 = deepcopy(BASELINE)
    v10["name"] = "severity_emphasis"
    v10["severity_weight"] = 0.9
    v10["min_severity"] = 2
    configs.append(v10)

    return configs


# ═══════════════════════════════════════════════════════════════
#  Backtester
# ═══════════════════════════════════════════════════════════════

class PredictionBacktester:
    """Backtest prediction parameters against historical data."""

    def __init__(self) -> None:
        self.conn = _get_conn()

    def run_all(self) -> list[dict]:
        """Run all parameter configs and return ranked results."""
        configs = generate_configs()
        clusters = self._load_clusters()
        results = []

        for cfg in configs:
            result = self._test_config(cfg, clusters)
            results.append(result)

        # Sort by 4h accuracy (most actionable timeframe)
        results.sort(key=lambda r: r["accuracy_4h"], reverse=True)
        return results

    def _load_clusters(self) -> list[dict]:
        """Load all event clusters with source and price data."""
        rows = self.conn.execute(
            """SELECT ec.id, ec.category, ec.severity, ec.sentiment,
                      ec.representative_headline, ec.article_count,
                      ec.novelty_score, ec.assets_affected, ec.first_detected_at
               FROM event_clusters ec
               ORDER BY ec.first_detected_at ASC"""
        ).fetchall()

        clusters = []
        for r in rows:
            d = dict(r)
            try:
                d["assets_affected"] = json.loads(d["assets_affected"] or "[]")
            except (json.JSONDecodeError, TypeError):
                d["assets_affected"] = []

            # Get sources
            sources = self.conn.execute(
                """SELECT DISTINCT a.source
                   FROM articles a
                   JOIN article_cluster_map acm ON a.id = acm.article_id
                   WHERE acm.cluster_id = ?""",
                (d["id"],),
            ).fetchall()
            d["sources"] = [s["source"] for s in sources]
            d["source_count"] = len(d["sources"])

            clusters.append(d)

        return clusters

    def _test_config(self, cfg: dict, clusters: list[dict]) -> dict:
        """Test a single parameter config against all clusters."""
        predictions = []
        direction_map = cfg["direction_map"]
        min_sev = cfg["min_severity"]
        min_sources = cfg["min_sources"]

        bullish_kw = ["approved", "approval", "adoption", "partnership", "upgrade",
                      "bullish", "inflow", "ath", "record"]
        bearish_kw = ["hack", "exploit", "ban", "crash", "dump", "bearish",
                      "outflow", "liquidat", "vulnerability"]

        for cluster in clusters:
            category = cluster["category"]
            severity = cluster["severity"] or 1
            headline = (cluster["representative_headline"] or "").lower()
            source_count = cluster["source_count"]
            novelty = cluster["novelty_score"] or 0.5
            detected_at = cluster["first_detected_at"]
            assets = cluster["assets_affected"]

            if severity < min_sev:
                continue
            if source_count < min_sources:
                continue
            if not assets:
                continue

            # Determine direction
            direction = direction_map.get(category, "neutral")
            for kw in bullish_kw:
                if kw in headline:
                    direction = "bullish"
                    break
            if direction != "bullish":
                for kw in bearish_kw:
                    if kw in headline:
                        direction = "bearish"
                        break
            if direction == "neutral":
                continue

            # Compute confidence
            base = 0.45
            sev_factor = (severity / 5.0) * cfg["severity_weight"]
            source_boost = min(cfg["source_boost_cap"], (source_count - 1) * cfg["source_boost_per"])
            novelty_factor = novelty * cfg["novelty_weight"]
            confidence = base * (0.3 + 0.7 * sev_factor) * (0.5 + 0.5 * novelty_factor) + source_boost
            confidence = min(0.95, max(0.1, confidence))

            for asset in assets[:3]:
                predictions.append({
                    "asset": asset,
                    "direction": direction,
                    "confidence": confidence,
                    "detected_at": detected_at,
                    "category": category,
                    "severity": severity,
                    "source_count": source_count,
                })

        # Grade predictions
        graded = {"1h": {"correct": 0, "total": 0}, "4h": {"correct": 0, "total": 0}, "24h": {"correct": 0, "total": 0}}
        grade_dist = {tf: {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0} for tf in ["1h", "4h", "24h"]}

        for pred in predictions:
            entry_price = self._get_price(pred["asset"], pred["detected_at"])
            if not entry_price or entry_price <= 0:
                continue

            for hours, tf in [(1, "1h"), (4, "4h"), (24, "24h")]:
                target_ts = self._offset(pred["detected_at"], hours)
                future_price = self._get_future_price(pred["asset"], target_ts)
                if not future_price:
                    continue

                raw_chg = ((future_price - entry_price) / entry_price) * 100
                dir_chg = raw_chg if pred["direction"] == "bullish" else -raw_chg
                grade = assign_grade(dir_chg)

                graded[tf]["total"] += 1
                if grade in ("A", "B"):
                    graded[tf]["correct"] += 1
                grade_dist[tf][grade] += 1

        return {
            "name": cfg["name"],
            "total_predictions": len(predictions),
            "accuracy_1h": graded["1h"]["correct"] / graded["1h"]["total"] if graded["1h"]["total"] > 0 else 0,
            "accuracy_4h": graded["4h"]["correct"] / graded["4h"]["total"] if graded["4h"]["total"] > 0 else 0,
            "accuracy_24h": graded["24h"]["correct"] / graded["24h"]["total"] if graded["24h"]["total"] > 0 else 0,
            "graded_1h": graded["1h"]["total"],
            "graded_4h": graded["4h"]["total"],
            "graded_24h": graded["24h"]["total"],
            "grades_4h": grade_dist["4h"],
            "config": cfg,
        }

    def _get_price(self, asset: str, ts: str) -> float | None:
        row = self.conn.execute(
            "SELECT close FROM prices WHERE asset = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (asset, ts),
        ).fetchone()
        return row["close"] if row else None

    def _get_future_price(self, asset: str, ts: str) -> float | None:
        row = self.conn.execute(
            "SELECT close FROM prices WHERE asset = ? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1",
            (asset, ts),
        ).fetchone()
        return row["close"] if row else None

    @staticmethod
    def _offset(iso_ts: str, hours: int) -> str:
        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def close(self) -> None:
        self.conn.close()


def apply_best_config() -> dict:
    """Run backtest, find best config, update predictions.py defaults.

    Returns the winning config and its accuracy.
    """
    bt = PredictionBacktester()
    results = bt.run_all()
    bt.close()

    best = results[0]

    # Write optimized config to a JSON file the predictor can load
    config_path = Path(__file__).resolve().parent.parent.parent / "data" / "prediction_config.json"
    config_path.write_text(json.dumps(best["config"], indent=2))

    logger.info(
        "Best config: %s — 1h: %.1f%%, 4h: %.1f%%, 24h: %.1f%% (%d predictions)",
        best["name"],
        best["accuracy_1h"] * 100,
        best["accuracy_4h"] * 100,
        best["accuracy_24h"] * 100,
        best["total_predictions"],
    )

    return best


def run_backtest_report() -> str:
    """Run backtest and return formatted report."""
    bt = PredictionBacktester()
    results = bt.run_all()
    bt.close()

    lines = [
        "=" * 80,
        "PREDICTION PARAMETER OPTIMIZATION REPORT",
        "=" * 80,
        "",
        f"{'Config':<25} {'Preds':>6} {'1h Acc':>8} {'4h Acc':>8} {'24h Acc':>8}  4h Grades (A/B/C/D/F)",
        "-" * 80,
    ]

    for r in results:
        g = r["grades_4h"]
        grade_str = f"{g['A']}/{g['B']}/{g['C']}/{g['D']}/{g['F']}"
        lines.append(
            f"{r['name']:<25} {r['total_predictions']:>6} "
            f"{r['accuracy_1h']:>7.1%} {r['accuracy_4h']:>7.1%} {r['accuracy_24h']:>7.1%}  "
            f"{grade_str}"
        )

    winner = results[0]
    lines.extend([
        "",
        "-" * 80,
        f"WINNER: {winner['name']}",
        f"  4h Accuracy: {winner['accuracy_4h']:.1%} ({winner['graded_4h']} graded predictions)",
        f"  1h: {winner['accuracy_1h']:.1%} | 24h: {winner['accuracy_24h']:.1%}",
        "",
        "Config details:",
    ])
    for k, v in winner["config"].items():
        if k == "name":
            continue
        lines.append(f"  {k}: {v}")

    lines.append("=" * 80)
    return "\n".join(lines)
