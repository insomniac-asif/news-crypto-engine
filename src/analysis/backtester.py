"""Simple event-driven backtester.

Simulates trading based on event signals and measures performance.
No live execution, no order book simulation — just: "if I entered
here and exited here, what happened?"

Operates on event_clusters when available (Phase 6), falling back
to raw events for backward compatibility.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from src.storage.database import Database

try:
    from dashboard_reporter import ProjectReporter
    _reporter: Optional[ProjectReporter] = None
except ImportError:
    _reporter = None


def _get_reporter() -> Optional["ProjectReporter"]:
    """Lazy-init the dashboard reporter for crypto backtested trades."""
    global _reporter
    if _reporter is None:
        try:
            from dashboard_reporter import ProjectReporter
            _reporter = ProjectReporter(project="crypto")
        except Exception:
            pass
    return _reporter

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """A single simulated trade."""

    event_id: int
    asset: str
    direction: str
    entry_time: str
    entry_price: float
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    category: str = ""
    severity: int = 0


@dataclass
class BacktestResult:
    """Aggregated backtest performance metrics."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    total_return_pct: float
    avg_return_pct: float
    median_return_pct: float
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    buy_hold_return_pct: float
    excess_return_pct: float
    trades: list[Trade] = field(default_factory=list, repr=False)
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)


class Backtester:
    """Event-driven backtester for crypto trading signals.

    For each event signal: simulate entry at detection price, exit after
    a configurable time window. Account for spread and slippage.
    Compare against buy-and-hold baseline.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the backtester.

        Args:
            db: Database instance.
            config: Full config dict (expects 'backtester' key).
        """
        self.db = db
        cfg = (config or {}).get("backtester", {})
        self.spread_pct: float = cfg.get("spread_pct", 0.10)
        self.slippage_pct: float = cfg.get("slippage_pct", 0.05)
        self.position_size: float = cfg.get("position_size", 0.10)
        self.exit_hours: int = cfg.get("default_exit_hours", 24)

    def run(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        min_confidence: float = 0.0,
        exit_hours: Optional[int] = None,
        asset: Optional[str] = None,
    ) -> BacktestResult:
        """Run a backtest on historical signals.

        Args:
            category: Filter signals by event category.
            min_severity: Minimum event severity.
            min_confidence: Minimum signal confidence.
            exit_hours: Override default exit window.
            asset: Filter by specific asset.

        Returns:
            BacktestResult with full performance metrics.
        """
        exit_h = exit_hours or self.exit_hours
        cost_pct = self.spread_pct + self.slippage_pct

        # Use clusters when available, fall back to raw events
        events = self._get_events_for_backtest(
            category=category,
            min_severity=min_severity,
        )

        trades: list[Trade] = []

        for event in events:
            assets_affected = event.get("assets_affected", [])
            if asset and asset not in assets_affected:
                continue

            target_assets = [asset] if asset else assets_affected
            detected_at = event["detected_at"]

            for target in target_assets:
                # Get entry price
                entry_price_data = self.db.get_price_at(target, detected_at)
                if not entry_price_data:
                    continue

                entry_price = entry_price_data["close"]
                if entry_price <= 0:
                    continue

                # Get exit price
                exit_ts = self._offset_timestamp(detected_at, exit_h)
                exit_price_data = self.db.get_price_at(target, exit_ts)
                if not exit_price_data:
                    continue

                exit_price = exit_price_data["close"]
                if exit_price_data["timestamp"] == entry_price_data["timestamp"]:
                    continue  # No future price data available

                # Determine direction from event category
                direction = self._direction_from_category(
                    event["category"], event.get("summary", "")
                )
                if direction == "neutral":
                    continue

                # Calculate P&L
                if direction == "long":
                    raw_pnl = ((exit_price - entry_price) / entry_price) * 100
                else:  # short
                    raw_pnl = ((entry_price - exit_price) / entry_price) * 100

                # Subtract transaction costs (entry + exit)
                pnl = raw_pnl - (cost_pct * 2)

                trade = Trade(
                    event_id=event["id"],
                    asset=target,
                    direction=direction,
                    entry_time=detected_at,
                    entry_price=entry_price,
                    exit_time=exit_ts,
                    exit_price=exit_price,
                    pnl_pct=round(pnl, 4),
                    category=event["category"],
                    severity=event["severity"],
                )
                trades.append(trade)

        result = self._compute_metrics(trades, asset=asset, exit_hours=exit_h)
        logger.info(
            "Backtest complete: %d trades, %.2f%% return, %.1f%% win rate",
            result.total_trades, result.total_return_pct, result.win_rate * 100,
        )

        # Report simulated trades to dashboard
        reporter = _get_reporter()
        if reporter and trades:
            for t in trades:
                try:
                    reporter.log_trade({
                        "instrument": t.asset,
                        "direction": t.direction,
                        "size": self.position_size,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "pnl": t.pnl_pct,
                        "entry_time": t.entry_time,
                        "exit_time": t.exit_time,
                        "metadata": {
                            "simulated": True,
                            "category": t.category,
                            "severity": t.severity,
                            "exit_hours": exit_h,
                            "cost_pct": cost_pct,
                        },
                    })
                except Exception:
                    logger.debug("Failed to report backtest trade to dashboard", exc_info=True)

        return result

    def _get_events_for_backtest(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
    ) -> list[dict[str, Any]]:
        """Get events for backtesting — clusters if available, else raw events.

        Args:
            category: Filter by event category.
            min_severity: Minimum severity.

        Returns:
            List of event-like dicts.
        """
        try:
            with self.db.connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM event_clusters").fetchone()[0]
                if count > 0:
                    query = """
                        SELECT id, category, severity,
                               first_detected_at AS detected_at,
                               assets_affected,
                               representative_headline AS summary
                        FROM event_clusters
                        WHERE severity >= ?
                    """
                    params: list[Any] = [min_severity]
                    if category:
                        query += " AND category = ?"
                        params.append(category)
                    query += " ORDER BY first_detected_at DESC LIMIT 10000"
                    rows = conn.execute(query, params).fetchall()
                    results = []
                    for r in rows:
                        d = dict(r)
                        d["assets_affected"] = json.loads(d.get("assets_affected", "[]"))
                        results.append(d)
                    logger.info("Backtesting on %d event clusters", len(results))
                    return results
        except Exception:
            pass

        return self.db.get_events(
            category=category,
            min_severity=min_severity,
            limit=10000,
        )

    def _compute_metrics(
        self,
        trades: list[Trade],
        asset: Optional[str] = None,
        exit_hours: int = 24,
    ) -> BacktestResult:
        """Compute aggregated backtest metrics from trade results.

        Args:
            trades: List of completed trades.
            asset: Asset used (for buy-hold comparison).
            exit_hours: Exit window used.

        Returns:
            BacktestResult with all metrics.
        """
        if not trades:
            return BacktestResult(
                total_trades=0, winning_trades=0, losing_trades=0,
                total_return_pct=0, avg_return_pct=0, median_return_pct=0,
                win_rate=0, profit_factor=0, max_drawdown_pct=0,
                sharpe_ratio=0, buy_hold_return_pct=0, excess_return_pct=0,
                trades=[], by_category={},
            )

        pnls = np.array([t.pnl_pct for t in trades])
        winners = pnls[pnls > 0]
        losers = pnls[pnls <= 0]

        total_return = float(np.sum(pnls * self.position_size))
        avg_return = float(np.mean(pnls))
        median_return = float(np.median(pnls))
        win_rate = float(len(winners) / len(pnls))

        gross_profit = float(np.sum(winners)) if len(winners) > 0 else 0
        gross_loss = float(abs(np.sum(losers))) if len(losers) > 0 else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown (cumulative P&L based)
        cum_pnl = np.cumsum(pnls * self.position_size)
        peak = np.maximum.accumulate(cum_pnl)
        drawdown = peak - cum_pnl
        max_drawdown = float(np.max(drawdown)) if len(drawdown) > 0 else 0

        # Sharpe ratio (annualized, assuming ~365 trading days)
        if len(pnls) > 1 and np.std(pnls) > 0:
            trades_per_year = 365 * 24 / exit_hours  # approximate
            sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(trades_per_year))
        else:
            sharpe = 0.0

        # Buy-and-hold comparison
        buy_hold = self._compute_buy_hold(trades, asset)

        # Per-category breakdown
        by_category = self._compute_by_category(trades)

        return BacktestResult(
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            total_return_pct=round(total_return, 4),
            avg_return_pct=round(avg_return, 4),
            median_return_pct=round(median_return, 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            max_drawdown_pct=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe, 4),
            buy_hold_return_pct=round(buy_hold, 4),
            excess_return_pct=round(total_return - buy_hold, 4),
            trades=trades,
            by_category=by_category,
        )

    def _compute_buy_hold(self, trades: list[Trade], asset: Optional[str]) -> float:
        """Compute buy-and-hold return over the same period.

        Args:
            trades: List of trades (used to determine time range).
            asset: Asset to compute buy-hold for. Uses BTC if None.

        Returns:
            Buy-and-hold return percentage.
        """
        if not trades:
            return 0.0

        target = asset or "BTC"
        sorted_trades = sorted(trades, key=lambda t: t.entry_time)
        first_time = sorted_trades[0].entry_time
        last_time = sorted_trades[-1].entry_time

        first_price = self.db.get_price_at(target, first_time)
        last_price = self.db.get_price_at(target, last_time)

        if not first_price or not last_price or first_price["close"] <= 0:
            return 0.0

        return ((last_price["close"] - first_price["close"]) / first_price["close"]) * 100

    def _compute_by_category(self, trades: list[Trade]) -> dict[str, dict[str, float]]:
        """Compute per-category performance metrics.

        Args:
            trades: List of trades.

        Returns:
            Dict mapping category to performance metrics.
        """
        from collections import defaultdict

        cat_trades: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            cat_trades[t.category].append(t.pnl_pct)

        result: dict[str, dict[str, float]] = {}
        for cat, pnls in sorted(cat_trades.items()):
            arr = np.array(pnls)
            result[cat] = {
                "trades": len(pnls),
                "avg_return": round(float(np.mean(arr)), 4),
                "win_rate": round(float(np.sum(arr > 0) / len(arr)), 4),
                "total_return": round(float(np.sum(arr) * self.position_size), 4),
            }

        return result

    def generate_report(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        exit_hours: Optional[int] = None,
    ) -> str:
        """Run backtest and generate a human-readable report.

        Args:
            category: Filter by event category.
            min_severity: Minimum event severity.
            exit_hours: Override default exit window.

        Returns:
            Formatted report string.
        """
        result = self.run(
            category=category,
            min_severity=min_severity,
            exit_hours=exit_hours,
        )

        if result.total_trades == 0:
            return "No trades generated. Ensure you have events with price data."

        lines = [
            "=" * 72,
            "BACKTEST REPORT",
            f"Exit window: {exit_hours or self.exit_hours}h | "
            f"Spread: {self.spread_pct}% | Slippage: {self.slippage_pct}% | "
            f"Position: {self.position_size:.0%}",
            "=" * 72,
            "",
            "PERFORMANCE SUMMARY",
            "-" * 40,
            f"  Total trades:        {result.total_trades}",
            f"  Winning trades:      {result.winning_trades}",
            f"  Losing trades:       {result.losing_trades}",
            f"  Win rate:            {result.win_rate:.1%}",
            f"  Avg return/trade:    {result.avg_return_pct:+.2f}%",
            f"  Median return/trade: {result.median_return_pct:+.2f}%",
            f"  Total return:        {result.total_return_pct:+.2f}%",
            f"  Profit factor:       {result.profit_factor:.2f}",
            f"  Max drawdown:        {result.max_drawdown_pct:.2f}%",
            f"  Sharpe ratio:        {result.sharpe_ratio:.2f}",
            "",
            "BENCHMARK COMPARISON",
            "-" * 40,
            f"  Buy & hold return:   {result.buy_hold_return_pct:+.2f}%",
            f"  Strategy return:     {result.total_return_pct:+.2f}%",
            f"  Excess return:       {result.excess_return_pct:+.2f}%",
        ]

        if result.by_category:
            lines.extend([
                "",
                "PERFORMANCE BY EVENT CATEGORY",
                "-" * 72,
                f"{'Category':<20} {'Trades':>7} {'AvgRet%':>9} {'WinRate':>8} {'TotalRet%':>10}",
                "-" * 72,
            ])
            for cat, metrics in sorted(result.by_category.items()):
                lines.append(
                    f"{cat:<20} {metrics['trades']:>7} "
                    f"{metrics['avg_return']:>+8.2f}% {metrics['win_rate']:>7.1%} "
                    f"{metrics['total_return']:>+9.2f}%"
                )

        lines.extend(["", "=" * 72])
        return "\n".join(lines)

    @staticmethod
    def _offset_timestamp(iso_ts: str, hours: int) -> str:
        """Add hours to an ISO timestamp string."""
        from datetime import datetime, timedelta, timezone

        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        new_dt = dt + timedelta(hours=hours)
        return new_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _direction_from_category(self, category: str, summary: str) -> str:
        """Determine trade direction from event category and summary."""
        from src.analysis.signal_generator import SIGNAL_RULES

        rule = SIGNAL_RULES.get(category, {})
        if not rule:
            return "neutral"

        text_lower = summary.lower()
        pos_kws = rule.get("positive_keywords", [])
        neg_kws = rule.get("negative_keywords", [])

        if any(kw in text_lower for kw in pos_kws):
            return rule.get("positive_direction", "long")
        if any(kw in text_lower for kw in neg_kws):
            return rule.get("negative_direction", "short")

        return rule.get("default_direction", "neutral")
