"""Enhanced backtester with walk-forward validation, latency buffer,
improved cost model, position sizing modes, and regime tagging.

Builds on the simple backtester from Phase 3 with rigorous methodology
needed to trust the results.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from src.analysis.backtester import Backtester, BacktestResult, Trade
from src.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    """A single train/test window in walk-forward validation."""

    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    in_sample: BacktestResult
    out_of_sample: BacktestResult
    regime: str  # 'bull', 'bear', 'choppy'


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward validation results."""

    windows: list[WalkForwardWindow]
    overall_in_sample: BacktestResult
    overall_out_of_sample: BacktestResult
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    by_regime: dict[str, BacktestResult]


class EnhancedBacktester(Backtester):
    """Enhanced backtester with walk-forward, latency, costs, and regimes.

    Upgrades over base Backtester:
    - Latency buffer: configurable delay between event and simulated entry
    - Improved cost model: fee + spread + slippage (separate)
    - Position sizing modes: fixed, confidence-scaled, Kelly
    - Regime tagging: bull/bear/choppy based on 30-day BTC trend
    - Walk-forward out-of-sample testing
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__(db, config)
        cfg = (config or {}).get("enhanced_backtester", {})

        # Latency buffer (minutes)
        self.latency_minutes: int = cfg.get("latency_minutes", 15)

        # Improved cost model
        self.fee_pct: float = cfg.get("fee_pct", 0.10)  # per side
        self.spread_pct: float = cfg.get("spread_pct", 0.05)
        self.slippage_pct: float = cfg.get("slippage_pct", 0.05)
        # Total round-trip = 2*fee + spread + slippage
        self.total_cost_pct: float = 2 * self.fee_pct + self.spread_pct + self.slippage_pct

        # Position sizing
        self.sizing_mode: str = cfg.get("sizing_mode", "fixed")  # fixed, confidence, kelly
        self.base_position: float = cfg.get("base_position", 0.10)

        # Walk-forward
        self.wf_min_train_days: int = cfg.get("wf_min_train_days", 14)
        self.wf_test_days: int = cfg.get("wf_test_days", 7)

    def run_enhanced(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        exit_hours: Optional[int] = None,
        asset: Optional[str] = None,
    ) -> BacktestResult:
        """Run enhanced backtest with latency buffer and improved costs.

        Same interface as base run() but with latency and better cost model.
        """
        exit_h = exit_hours or self.exit_hours
        events = self._get_events_for_backtest(category=category, min_severity=min_severity)

        trades: list[Trade] = []

        for event in events:
            assets_affected = event.get("assets_affected", [])
            if asset and asset not in assets_affected:
                continue

            target_assets = [asset] if asset else assets_affected
            detected_at = event["detected_at"]

            # Apply latency buffer
            entry_ts = self._offset_timestamp_minutes(detected_at, self.latency_minutes)

            for target in target_assets:
                entry_price_data = self.db.get_price_at(target, entry_ts)
                if not entry_price_data:
                    continue

                entry_price = entry_price_data["close"]
                if entry_price <= 0:
                    continue

                exit_ts = self._offset_timestamp(entry_ts, exit_h)
                exit_price_data = self.db.get_price_at(target, exit_ts)
                if not exit_price_data:
                    continue

                exit_price = exit_price_data["close"]
                if exit_price_data["timestamp"] == entry_price_data["timestamp"]:
                    continue

                direction = self._direction_from_category(
                    event["category"], event.get("summary", "")
                )
                if direction == "neutral":
                    continue

                # P&L with improved cost model
                if direction == "long":
                    raw_pnl = ((exit_price - entry_price) / entry_price) * 100
                else:
                    raw_pnl = ((entry_price - exit_price) / entry_price) * 100

                pnl = raw_pnl - self.total_cost_pct

                # Position sizing
                pos_size = self._compute_position_size(
                    event.get("severity", 3), trades
                )

                trade = Trade(
                    event_id=event["id"],
                    asset=target,
                    direction=direction,
                    entry_time=entry_ts,
                    entry_price=entry_price,
                    exit_time=exit_ts,
                    exit_price=exit_price,
                    pnl_pct=round(pnl, 4),
                    category=event["category"],
                    severity=event["severity"],
                )
                trades.append(trade)

        # Tag trades with regime
        for trade in trades:
            trade._regime = self._tag_regime(trade.entry_time)

        result = self._compute_metrics(trades, asset=asset, exit_hours=exit_h)
        return result

    def run_walk_forward(
        self,
        category: Optional[str] = None,
        min_severity: int = 1,
        exit_hours: Optional[int] = None,
        asset: Optional[str] = None,
    ) -> WalkForwardResult:
        """Run walk-forward out-of-sample validation.

        Splits data into expanding windows:
        - Train on window N, test on window N+1
        - Reports in-sample and out-of-sample metrics separately

        Returns:
            WalkForwardResult with per-window and aggregate metrics.
        """
        exit_h = exit_hours or self.exit_hours
        events = self._get_events_for_backtest(category=category, min_severity=min_severity)

        if not events:
            return self._empty_wf_result()

        # Sort events by time
        events.sort(key=lambda e: e["detected_at"])

        # Determine date range
        first_ts = events[0]["detected_at"]
        last_ts = events[-1]["detected_at"]
        first_dt = self._parse_ts(first_ts)
        last_dt = self._parse_ts(last_ts)

        total_days = (last_dt - first_dt).days
        if total_days < self.wf_min_train_days + self.wf_test_days:
            logger.warning(
                "Not enough data for walk-forward: %d days (need %d)",
                total_days, self.wf_min_train_days + self.wf_test_days,
            )
            # Fall back to single-window result
            result = self.run_enhanced(category, min_severity, exit_hours, asset)
            return WalkForwardResult(
                windows=[],
                overall_in_sample=result,
                overall_out_of_sample=result,
                in_sample_sharpe=result.sharpe_ratio,
                out_of_sample_sharpe=result.sharpe_ratio,
                by_regime={},
            )

        # Build expanding windows
        windows: list[WalkForwardWindow] = []
        train_start = first_dt
        window_id = 0

        while True:
            train_end = train_start + timedelta(days=self.wf_min_train_days + window_id * self.wf_test_days)
            test_start = train_end
            test_end = test_start + timedelta(days=self.wf_test_days)

            if test_end > last_dt:
                break

            # Filter events for train and test periods
            train_events = [
                e for e in events
                if train_start <= self._parse_ts(e["detected_at"]) < train_end
            ]
            test_events = [
                e for e in events
                if test_start <= self._parse_ts(e["detected_at"]) < test_end
            ]

            # Run backtest on each partition
            in_sample = self._backtest_events(train_events, exit_h, asset)
            out_of_sample = self._backtest_events(test_events, exit_h, asset)

            # Tag regime
            regime = self._tag_regime(test_start.strftime("%Y-%m-%dT%H:%M:%SZ"))

            wf_window = WalkForwardWindow(
                window_id=window_id,
                train_start=train_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                train_end=train_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                test_start=test_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                test_end=test_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                in_sample=in_sample,
                out_of_sample=out_of_sample,
                regime=regime,
            )
            windows.append(wf_window)
            window_id += 1

        # Aggregate results
        all_is_trades = []
        all_oos_trades = []
        regime_trades: dict[str, list[Trade]] = defaultdict(list)

        for w in windows:
            all_is_trades.extend(w.in_sample.trades)
            all_oos_trades.extend(w.out_of_sample.trades)
            regime_trades[w.regime].extend(w.out_of_sample.trades)

        overall_is = self._compute_metrics(all_is_trades, asset, exit_h)
        overall_oos = self._compute_metrics(all_oos_trades, asset, exit_h)

        by_regime = {}
        for regime, trades in sorted(regime_trades.items()):
            by_regime[regime] = self._compute_metrics(trades, asset, exit_h)

        return WalkForwardResult(
            windows=windows,
            overall_in_sample=overall_is,
            overall_out_of_sample=overall_oos,
            in_sample_sharpe=overall_is.sharpe_ratio,
            out_of_sample_sharpe=overall_oos.sharpe_ratio,
            by_regime=by_regime,
        )

    def _backtest_events(
        self, events: list[dict], exit_h: int, asset: Optional[str],
    ) -> BacktestResult:
        """Run backtest on a specific set of events."""
        trades: list[Trade] = []

        for event in events:
            assets_affected = event.get("assets_affected", [])
            if asset and asset not in assets_affected:
                continue

            target_assets = [asset] if asset else assets_affected
            detected_at = event["detected_at"]
            entry_ts = self._offset_timestamp_minutes(detected_at, self.latency_minutes)

            for target in target_assets:
                entry_price_data = self.db.get_price_at(target, entry_ts)
                if not entry_price_data:
                    continue

                entry_price = entry_price_data["close"]
                if entry_price <= 0:
                    continue

                exit_ts = self._offset_timestamp(entry_ts, exit_h)
                exit_price_data = self.db.get_price_at(target, exit_ts)
                if not exit_price_data:
                    continue

                exit_price = exit_price_data["close"]
                if exit_price_data["timestamp"] == entry_price_data["timestamp"]:
                    continue

                direction = self._direction_from_category(
                    event["category"], event.get("summary", "")
                )
                if direction == "neutral":
                    continue

                if direction == "long":
                    raw_pnl = ((exit_price - entry_price) / entry_price) * 100
                else:
                    raw_pnl = ((entry_price - exit_price) / entry_price) * 100

                pnl = raw_pnl - self.total_cost_pct

                trade = Trade(
                    event_id=event["id"],
                    asset=target,
                    direction=direction,
                    entry_time=entry_ts,
                    entry_price=entry_price,
                    exit_time=exit_ts,
                    exit_price=exit_price,
                    pnl_pct=round(pnl, 4),
                    category=event["category"],
                    severity=event["severity"],
                )
                trades.append(trade)

        return self._compute_metrics(trades, asset, exit_h)

    def _compute_position_size(
        self, severity: int, prior_trades: list[Trade],
    ) -> float:
        """Compute position size based on sizing mode.

        Args:
            severity: Event severity (proxy for confidence).
            prior_trades: Previous trades (for Kelly calculation).

        Returns:
            Position size as fraction of portfolio.
        """
        if self.sizing_mode == "confidence":
            # Scale by severity: sev 1=50%, sev 3=100%, sev 5=150% of base
            scale = 0.5 + (severity - 1) * 0.25
            return self.base_position * scale

        elif self.sizing_mode == "kelly":
            if len(prior_trades) < 10:
                return self.base_position

            pnls = [t.pnl_pct for t in prior_trades[-50:]]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            if not wins or not losses:
                return self.base_position

            win_rate = len(wins) / len(pnls)
            avg_win = sum(wins) / len(wins)
            avg_loss = abs(sum(losses) / len(losses))

            if avg_loss == 0:
                return self.base_position

            # Kelly fraction = W - (1-W)/R where R = avg_win/avg_loss
            r = avg_win / avg_loss
            kelly = win_rate - (1 - win_rate) / r if r > 0 else 0

            # Half-Kelly for safety, bounded
            return max(0.01, min(0.25, kelly * 0.5))

        # Default: fixed
        return self.base_position

    def _tag_regime(self, timestamp: str) -> str:
        """Tag a timestamp's market regime based on 30-day BTC trend.

        Args:
            timestamp: ISO timestamp to evaluate.

        Returns:
            'bull' (>+10%), 'bear' (<-10%), or 'choppy'.
        """
        start_ts = self._offset_timestamp(timestamp, -30 * 24)
        start_price = self.db.get_price_at("BTC", start_ts)
        end_price = self.db.get_price_at("BTC", timestamp)

        if not start_price or not end_price or start_price["close"] <= 0:
            return "choppy"

        change_pct = ((end_price["close"] - start_price["close"]) / start_price["close"]) * 100

        if change_pct > 10:
            return "bull"
        elif change_pct < -10:
            return "bear"
        return "choppy"

    def _empty_wf_result(self) -> WalkForwardResult:
        """Return empty walk-forward result."""
        empty = BacktestResult(
            total_trades=0, winning_trades=0, losing_trades=0,
            total_return_pct=0, avg_return_pct=0, median_return_pct=0,
            win_rate=0, profit_factor=0, max_drawdown_pct=0,
            sharpe_ratio=0, buy_hold_return_pct=0, excess_return_pct=0,
        )
        return WalkForwardResult(
            windows=[], overall_in_sample=empty, overall_out_of_sample=empty,
            in_sample_sharpe=0, out_of_sample_sharpe=0, by_regime={},
        )

    @staticmethod
    def _offset_timestamp_minutes(iso_ts: str, minutes: int) -> str:
        """Add minutes to an ISO timestamp string."""
        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_ts(iso_ts: str) -> datetime:
        """Parse ISO timestamp to datetime."""
        ts = iso_ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
