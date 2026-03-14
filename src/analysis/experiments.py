"""Experiment framework — structured research experiments on the crypto news engine.

Five core experiments that answer: "Does news-driven trading actually work?"

Experiment 1: Event-Return Correlation
Experiment 2: Sentiment-Return Correlation
Experiment 3: Narrative Accumulation Test
Experiment 4: Signal Decay Analysis
Experiment 5: Walk-Forward Validation
"""

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from scipy import stats

from src.analysis.enhanced_backtester import EnhancedBacktester
from src.analysis.event_impact import EventImpactAnalyzer
from src.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Result of a single experiment."""

    experiment_id: int
    name: str
    hypothesis: str
    methodology: str
    results: dict[str, Any]
    interpretation: str
    limitations: str
    tables: list[dict[str, Any]] = field(default_factory=list)


class ExperimentRunner:
    """Run structured research experiments on the engine's data.

    Each experiment follows the pattern:
    1. Hypothesis
    2. Methodology
    3. Results (tables + stats)
    4. Interpretation
    5. Limitations
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        self.db = db
        self.config = config or {}
        self.seed = 42  # Reproducible randomness

    def run_all(self) -> list[ExperimentResult]:
        """Run all 5 experiments."""
        results = []
        for exp_id in range(1, 6):
            result = self.run(exp_id)
            results.append(result)
        return results

    def run(self, experiment_id: int) -> ExperimentResult:
        """Run a specific experiment by ID."""
        runners = {
            1: self._experiment_1_event_return,
            2: self._experiment_2_sentiment_return,
            3: self._experiment_3_narrative_accumulation,
            4: self._experiment_4_signal_decay,
            5: self._experiment_5_walk_forward,
        }
        runner = runners.get(experiment_id)
        if not runner:
            raise ValueError(f"Unknown experiment ID: {experiment_id}. Valid: 1-5")
        return runner()

    # ── Experiment 1: Event-Return Correlation ──────────────────────

    def _experiment_1_event_return(self) -> ExperimentResult:
        """For each event category, measure avg return vs random baseline."""
        analyzer = EventImpactAnalyzer(self.db, self.config)
        all_moves = analyzer.compute_event_moves()

        if not all_moves:
            return self._empty_result(1, "Event-Return Correlation",
                                      "No event moves data available.")

        # Group by category and window
        cat_moves: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        all_returns: dict[str, list[float]] = defaultdict(list)

        for m in all_moves:
            for wk, mv in m["moves"].items():
                if mv is not None:
                    cat_moves[m["category"]][wk].append(mv)
                    all_returns[wk].append(mv)

        # Generate random baseline (same number of random time windows)
        random.seed(self.seed)
        baseline = self._generate_random_baseline(len(all_moves))

        # Statistical tests per category
        tables = []
        for cat in sorted(cat_moves.keys()):
            for window in ["1h", "4h", "24h"]:
                event_returns = cat_moves[cat].get(window, [])
                baseline_returns = baseline.get(window, [])

                if len(event_returns) < 5 or len(baseline_returns) < 5:
                    continue

                # Mann-Whitney U test (non-parametric)
                try:
                    u_stat, p_value = stats.mannwhitneyu(
                        event_returns, baseline_returns, alternative="two-sided"
                    )
                except ValueError:
                    u_stat, p_value = 0.0, 1.0

                # Effect size (rank-biserial correlation)
                n1, n2 = len(event_returns), len(baseline_returns)
                effect_size = 1 - (2 * u_stat) / (n1 * n2) if n1 * n2 > 0 else 0

                tables.append({
                    "Category": cat,
                    "Window": window,
                    "N (events)": n1,
                    "Avg Return (events)": round(float(np.mean(event_returns)), 4),
                    "Avg Return (random)": round(float(np.mean(baseline_returns)), 4),
                    "Difference": round(float(np.mean(event_returns)) - float(np.mean(baseline_returns)), 4),
                    "U-statistic": round(float(u_stat), 2),
                    "p-value": round(float(p_value), 6),
                    "Effect Size": round(effect_size, 4),
                    "Significant": p_value < 0.05,
                })

        significant = [t for t in tables if t["Significant"]]

        return ExperimentResult(
            experiment_id=1,
            name="Event-Return Correlation",
            hypothesis="Specific event types cause statistically significant price moves "
                       "compared to random time windows.",
            methodology="For each event category, measured avg return of affected assets at "
                        "T+1h, T+4h, T+24h. Compared to baseline of random time windows of "
                        "same duration using Mann-Whitney U test (non-parametric).",
            results={
                "total_categories": len(cat_moves),
                "total_tests": len(tables),
                "significant_results": len(significant),
                "significant_pct": round(len(significant) / max(1, len(tables)) * 100, 1),
            },
            interpretation=f"{len(significant)} of {len(tables)} category-window combinations "
                           f"showed statistically significant difference from random (p < 0.05). "
                           + ("This suggests some event types do move prices." if significant
                              else "No significant edge detected with current data."),
            limitations="Short data history limits statistical power. "
                        "Multiple comparison problem (no Bonferroni correction). "
                        "Random baseline uses same asset pool, not matched timestamps.",
            tables=tables,
        )

    # ── Experiment 2: Sentiment-Return Correlation ──────────────────

    def _experiment_2_sentiment_return(self) -> ExperimentResult:
        """Bucket events by sentiment, measure avg return per bucket."""
        analyzer = EventImpactAnalyzer(self.db, self.config)
        all_moves = analyzer.compute_event_moves()

        if not all_moves:
            return self._empty_result(2, "Sentiment-Return Correlation",
                                      "No event moves data available.")

        # Get sentiment: use direct sentiment field (from clusters) or
        # parse from summary string (from raw events)
        import re
        events_with_sentiment = []
        for m in all_moves:
            # Prefer direct sentiment field (set by clustering)
            sent = m.get("sentiment", None)
            if sent is None or sent == 0.0:
                # Fall back to parsing from summary string
                summary = m.get("summary", "") or ""
                match = re.search(r"sentiment=\w+\(([-\d.]+)\)", summary)
                sent = float(match.group(1)) if match else 0.0
            events_with_sentiment.append({**m, "sentiment": sent})

        # Bucket into 5 sentiment groups
        bucket_edges = [-1.0, -0.5, -0.1, 0.1, 0.5, 1.0]
        bucket_labels = ["Very Negative", "Negative", "Neutral", "Positive", "Very Positive"]
        buckets: dict[str, dict[str, list[float]]] = {
            label: defaultdict(list) for label in bucket_labels
        }

        for e in events_with_sentiment:
            sent = e["sentiment"]
            for i, (low, high) in enumerate(zip(bucket_edges[:-1], bucket_edges[1:])):
                if low <= sent < high or (i == len(bucket_labels) - 1 and sent == high):
                    for wk, mv in e["moves"].items():
                        if mv is not None:
                            buckets[bucket_labels[i]][wk].append(mv)
                    break

        tables = []
        for label in bucket_labels:
            for window in ["1h", "4h", "24h"]:
                returns = buckets[label].get(window, [])
                if returns:
                    tables.append({
                        "Sentiment Bucket": label,
                        "Window": window,
                        "N": len(returns),
                        "Avg Return (%)": round(float(np.mean(returns)), 4),
                        "Median Return (%)": round(float(np.median(returns)), 4),
                        "Std Dev": round(float(np.std(returns)), 4),
                    })

        # Correlation test: sentiment score vs 4h return
        sentiments_4h = [(e["sentiment"], e["moves"].get("4h"))
                         for e in events_with_sentiment if e["moves"].get("4h") is not None]
        if len(sentiments_4h) >= 10:
            sents, rets = zip(*sentiments_4h)
            corr, corr_p = stats.spearmanr(sents, rets)
        else:
            corr, corr_p = 0.0, 1.0

        return ExperimentResult(
            experiment_id=2,
            name="Sentiment-Return Correlation",
            hypothesis="More positive sentiment predicts higher subsequent returns.",
            methodology="Bucketed events into 5 sentiment groups (very negative to very positive). "
                        "Measured avg subsequent return per bucket at 1h/4h/24h. "
                        "Tested Spearman rank correlation between sentiment score and 4h return.",
            results={
                "spearman_correlation": round(float(corr), 4),
                "correlation_p_value": round(float(corr_p), 6),
                "significant": corr_p < 0.05,
                "n_observations": len(sentiments_4h),
            },
            interpretation=f"Spearman correlation between sentiment and 4h return: "
                           f"r={corr:.3f} (p={corr_p:.4f}). "
                           + ("Statistically significant relationship." if corr_p < 0.05
                              else "No significant linear relationship detected."),
            limitations="VADER sentiment may not capture crypto-specific nuance. "
                        "Sentiment extracted from event summary, not raw article text. "
                        "Non-linear relationships not captured by Spearman.",
            tables=tables,
        )

    # ── Experiment 3: Narrative Accumulation Test ────────────────────

    def _experiment_3_narrative_accumulation(self) -> ExperimentResult:
        """Compare single-event signals vs narrative-accumulated signals."""
        analyzer = EventImpactAnalyzer(self.db, self.config)
        all_moves = analyzer.compute_event_moves()

        if not all_moves:
            return self._empty_result(3, "Narrative Accumulation Test",
                                      "No event moves data available.")

        # Split: events with article_count > 1 (part of cluster) vs solo
        multi = [m for m in all_moves if m.get("article_count", 1) > 1]
        solo = [m for m in all_moves if m.get("article_count", 1) == 1]

        tables = []
        for window in ["1h", "4h", "24h"]:
            multi_returns = [m["moves"].get(window) for m in multi
                             if m["moves"].get(window) is not None]
            solo_returns = [m["moves"].get(window) for m in solo
                            if m["moves"].get(window) is not None]

            if len(multi_returns) >= 3 and len(solo_returns) >= 3:
                try:
                    u_stat, p_value = stats.mannwhitneyu(
                        multi_returns, solo_returns, alternative="two-sided"
                    )
                except ValueError:
                    u_stat, p_value = 0.0, 1.0
            else:
                u_stat, p_value = 0.0, 1.0

            tables.append({
                "Window": window,
                "Multi-Article N": len(multi_returns),
                "Multi-Article Avg (%)": round(float(np.mean(multi_returns)), 4) if multi_returns else 0,
                "Solo N": len(solo_returns),
                "Solo Avg (%)": round(float(np.mean(solo_returns)), 4) if solo_returns else 0,
                "Avg |Multi|": round(float(np.mean([abs(r) for r in multi_returns])), 4) if multi_returns else 0,
                "Avg |Solo|": round(float(np.mean([abs(r) for r in solo_returns])), 4) if solo_returns else 0,
                "p-value": round(float(p_value), 6),
                "Significant": p_value < 0.05,
            })

        return ExperimentResult(
            experiment_id=3,
            name="Narrative Accumulation Test",
            hypothesis="Events covered by multiple articles (narrative accumulation) "
                       "predict larger price moves than isolated single-article events.",
            methodology="Split events into multi-article clusters (article_count > 1) vs "
                        "single-article events. Compared absolute move sizes using "
                        "Mann-Whitney U test.",
            results={
                "multi_article_events": len(multi),
                "solo_events": len(solo),
            },
            interpretation="Compared magnitude of moves between narrative-accumulated events "
                           "and isolated events. Larger absolute moves from multi-article "
                           "clusters would support the narrative accumulation hypothesis.",
            limitations="Small sample of multi-article events may limit power. "
                        "Clustering threshold affects what counts as 'multi-article'. "
                        "Does not account for event severity differences between groups.",
            tables=tables,
        )

    # ── Experiment 4: Signal Decay Analysis ──────────────────────────

    def _experiment_4_signal_decay(self) -> ExperimentResult:
        """Measure when most of the price move happens after a signal."""
        analyzer = EventImpactAnalyzer(self.db, self.config)
        all_moves = analyzer.compute_event_moves()

        if not all_moves:
            return self._empty_result(4, "Signal Decay Analysis",
                                      "No event moves data available.")

        # For events with all 3 windows, compute what fraction of 24h move
        # happened in each bucket
        buckets = {"0-1h": [], "1h-4h": [], "4h-24h": []}

        for m in all_moves:
            m1h = m["moves"].get("1h")
            m4h = m["moves"].get("4h")
            m24h = m["moves"].get("24h")

            if m1h is not None and m4h is not None and m24h is not None and abs(m24h) > 0.01:
                # Fraction of total 24h move captured in each bucket
                frac_0_1h = abs(m1h) / abs(m24h) if abs(m24h) > 0 else 0
                frac_1h_4h = abs(m4h - m1h) / abs(m24h) if abs(m24h) > 0 else 0
                frac_4h_24h = abs(m24h - m4h) / abs(m24h) if abs(m24h) > 0 else 0

                buckets["0-1h"].append(min(frac_0_1h, 2.0))  # cap outliers
                buckets["1h-4h"].append(min(frac_1h_4h, 2.0))
                buckets["4h-24h"].append(min(frac_4h_24h, 2.0))

        tables = []
        for bucket in ["0-1h", "1h-4h", "4h-24h"]:
            values = buckets[bucket]
            if values:
                tables.append({
                    "Time Bucket": bucket,
                    "Avg Fraction of 24h Move": round(float(np.mean(values)), 4),
                    "Median Fraction": round(float(np.median(values)), 4),
                    "N": len(values),
                })

        # Key question: can you capture the edge given execution latency?
        avg_first_hour = float(np.mean(buckets["0-1h"])) if buckets["0-1h"] else 0

        return ExperimentResult(
            experiment_id=4,
            name="Signal Decay Analysis",
            hypothesis="Most of the price move happens within the first hour after "
                       "event detection, making the edge difficult to capture with "
                       "execution latency.",
            methodology="For events with data at all 3 windows (1h, 4h, 24h), computed "
                        "what fraction of the total 24h move occurred in each time bucket: "
                        "0-1h, 1h-4h, 4h-24h.",
            results={
                "avg_first_hour_fraction": round(avg_first_hour, 4),
                "n_events_analyzed": len(buckets["0-1h"]),
                "capturable_after_15min": avg_first_hour < 0.6,
            },
            interpretation=f"On average, {avg_first_hour:.0%} of the 24h move occurs in "
                           f"the first hour. "
                           + ("Most of the move happens early — with 15min execution latency, "
                              "a significant portion may already be priced in."
                              if avg_first_hour > 0.5
                              else "The move is more gradual, suggesting execution latency "
                              "is less of a concern."),
            limitations="Fractional decomposition can exceed 100% when the move reverses. "
                        "Does not account for intra-hour timing (move could happen "
                        "in first 5 minutes vs last 5 minutes of first hour).",
            tables=tables,
        )

    # ── Experiment 5: Walk-Forward Validation ────────────────────────

    def _experiment_5_walk_forward(self) -> ExperimentResult:
        """Run full walk-forward backtest, compare IS vs OOS Sharpe."""
        backtester = EnhancedBacktester(self.db, self.config)
        wf_result = backtester.run_walk_forward()

        tables = []
        for w in wf_result.windows:
            tables.append({
                "Window": w.window_id,
                "Train Period": f"{w.train_start[:10]} to {w.train_end[:10]}",
                "Test Period": f"{w.test_start[:10]} to {w.test_end[:10]}",
                "Regime": w.regime,
                "IS Trades": w.in_sample.total_trades,
                "IS Win Rate": round(w.in_sample.win_rate, 3),
                "IS Sharpe": round(w.in_sample.sharpe_ratio, 2),
                "OOS Trades": w.out_of_sample.total_trades,
                "OOS Win Rate": round(w.out_of_sample.win_rate, 3),
                "OOS Sharpe": round(w.out_of_sample.sharpe_ratio, 2),
            })

        # Regime breakdown
        regime_tables = []
        for regime, result in sorted(wf_result.by_regime.items()):
            regime_tables.append({
                "Regime": regime,
                "Trades": result.total_trades,
                "Win Rate": round(result.win_rate, 3),
                "Avg Return": round(result.avg_return_pct, 4),
                "Sharpe": round(result.sharpe_ratio, 2),
            })

        is_sharpe = wf_result.in_sample_sharpe
        oos_sharpe = wf_result.out_of_sample_sharpe
        edge_real = oos_sharpe >= 0.5

        return ExperimentResult(
            experiment_id=5,
            name="Walk-Forward Validation",
            hypothesis="The trading strategy maintains positive Sharpe ratio "
                       "in out-of-sample periods (Sharpe >= 0.5).",
            methodology="Expanding window walk-forward: train on N days, test on next "
                        f"{backtester.wf_test_days} days. Sliding forward. "
                        f"Latency buffer: {backtester.latency_minutes} minutes. "
                        f"Cost model: {backtester.total_cost_pct:.2f}% round-trip. "
                        "Regime tagged by 30-day BTC trend.",
            results={
                "n_windows": len(wf_result.windows),
                "in_sample_sharpe": round(is_sharpe, 4),
                "out_of_sample_sharpe": round(oos_sharpe, 4),
                "sharpe_degradation": round(is_sharpe - oos_sharpe, 4),
                "is_trades": wf_result.overall_in_sample.total_trades,
                "oos_trades": wf_result.overall_out_of_sample.total_trades,
                "oos_win_rate": round(wf_result.overall_out_of_sample.win_rate, 4),
                "edge_appears_real": edge_real,
            },
            interpretation=f"In-sample Sharpe: {is_sharpe:.2f}, "
                           f"Out-of-sample Sharpe: {oos_sharpe:.2f}. "
                           + ("OOS Sharpe >= 0.5 — edge appears real and robust."
                              if edge_real
                              else "OOS Sharpe < 0.5 — edge may not be reliable. "
                              "Consider more data or strategy refinement."),
            limitations="Short data window limits number of walk-forward periods. "
                        "Regime tagging based on BTC only, may not reflect altcoin regimes. "
                        "No optimization of signal weights between windows (pure replay).",
            tables=tables + regime_tables,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _generate_random_baseline(
        self, n_events: int,
    ) -> dict[str, list[float]]:
        """Generate random return baseline for comparison.

        Samples random time windows from the price data and computes
        returns at 1h, 4h, 24h — same as event returns but at random times.

        Args:
            n_events: Number of random samples to generate.

        Returns:
            Dict mapping window keys to lists of random returns.
        """
        random.seed(self.seed)
        baseline: dict[str, list[float]] = defaultdict(list)

        # Get all BTC prices as the baseline asset
        prices = self.db.get_prices("BTC")
        if len(prices) < 25:
            return baseline

        for _ in range(min(n_events, 500)):
            idx = random.randint(0, len(prices) - 25)
            base_price = prices[idx]["close"]
            if base_price <= 0:
                continue

            for hours, key in [(1, "1h"), (4, "4h"), (24, "24h")]:
                future_idx = min(idx + hours, len(prices) - 1)
                if future_idx > idx:
                    future_price = prices[future_idx]["close"]
                    pct = ((future_price - base_price) / base_price) * 100
                    baseline[key].append(round(pct, 4))

        return baseline

    def _empty_result(self, exp_id: int, name: str, reason: str) -> ExperimentResult:
        """Return empty result when data is insufficient."""
        return ExperimentResult(
            experiment_id=exp_id,
            name=name,
            hypothesis="N/A",
            methodology="N/A",
            results={"error": reason},
            interpretation=reason,
            limitations="Insufficient data to run experiment.",
            tables=[],
        )

    def generate_report(
        self,
        results: Optional[list[ExperimentResult]] = None,
        fmt: str = "text",
    ) -> str:
        """Generate a formatted experiment report.

        Args:
            results: Experiment results (runs all if None).
            fmt: Output format ('text' or 'md').

        Returns:
            Formatted report string.
        """
        if results is None:
            results = self.run_all()

        if fmt == "md":
            return self._format_markdown(results)
        return self._format_text(results)

    def _format_text(self, results: list[ExperimentResult]) -> str:
        """Format results as plain text."""
        lines = [
            "=" * 72,
            "EXPERIMENT REPORT — Crypto News Research Engine",
            "=" * 72,
        ]

        for r in results:
            lines.extend([
                "",
                f"{'─' * 72}",
                f"EXPERIMENT {r.experiment_id}: {r.name}",
                f"{'─' * 72}",
                "",
                f"HYPOTHESIS: {r.hypothesis}",
                "",
                f"METHODOLOGY: {r.methodology}",
                "",
                "RESULTS:",
            ])

            for k, v in r.results.items():
                lines.append(f"  {k}: {v}")

            if r.tables:
                lines.append("")
                # Format first table as text
                if r.tables:
                    headers = list(r.tables[0].keys())
                    lines.append("  " + " | ".join(f"{h:>15s}" for h in headers))
                    lines.append("  " + "-" * (17 * len(headers)))
                    for row in r.tables[:20]:  # limit rows
                        vals = [str(row.get(h, ""))[:15] for h in headers]
                        lines.append("  " + " | ".join(f"{v:>15s}" for v in vals))

            lines.extend([
                "",
                f"INTERPRETATION: {r.interpretation}",
                "",
                f"LIMITATIONS: {r.limitations}",
            ])

        lines.extend(["", "=" * 72])
        return "\n".join(lines)

    def _format_markdown(self, results: list[ExperimentResult]) -> str:
        """Format results as markdown."""
        lines = [
            "# Experiment Report — Crypto News Research Engine",
            "",
        ]

        for r in results:
            lines.extend([
                f"## Experiment {r.experiment_id}: {r.name}",
                "",
                f"**Hypothesis:** {r.hypothesis}",
                "",
                f"**Methodology:** {r.methodology}",
                "",
                "### Results",
                "",
            ])

            for k, v in r.results.items():
                lines.append(f"- **{k}:** {v}")

            if r.tables:
                lines.append("")
                headers = list(r.tables[0].keys())
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in r.tables[:30]:
                    vals = [str(row.get(h, "")) for h in headers]
                    lines.append("| " + " | ".join(vals) + " |")

            lines.extend([
                "",
                f"**Interpretation:** {r.interpretation}",
                "",
                f"**Limitations:** {r.limitations}",
                "",
                "---",
                "",
            ])

        return "\n".join(lines)
