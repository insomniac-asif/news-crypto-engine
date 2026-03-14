"""Market confirmation layer — validate news signals against market data.

Checks volume, momentum, and volatility before confirming a signal.
Reduces false signals from news that the market has already priced in
or that contradicts current market structure.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """Market conditions at signal time for a specific asset."""

    asset: str
    timestamp: str
    price: float
    volume_zscore: float  # current volume vs 20-period avg
    momentum_1h: float  # 1h price change %
    atr_14: float  # 14-period Average True Range
    volatility_regime: str  # 'low', 'normal', 'high'
    volume_confirmed: bool  # z-score >= 1.0
    momentum_aligned: bool  # momentum direction matches signal


class MarketConfirmation:
    """Check market data to confirm or weaken news-based signals.

    Three checks:
    1. Volume confirmation: is volume elevated? (z-score vs 20-period avg)
    2. Momentum alignment: is price moving in the signal direction?
    3. Volatility regime: ATR-based regime detection affects thresholds.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        self.db = db
        cfg = (config or {}).get("signal_model", {})
        self.volume_lookback: int = cfg.get("volume_lookback", 20)
        self.volume_threshold: float = cfg.get("volume_zscore_threshold", 1.0)
        self.atr_period: int = cfg.get("atr_period", 14)
        # ATR percentile thresholds for regime classification
        self.high_vol_multiplier: float = cfg.get("high_vol_atr_multiplier", 1.5)

    def get_market_context(
        self, asset: str, timestamp: str, direction: str = "long"
    ) -> Optional[MarketContext]:
        """Build market context for an asset at a specific time.

        Args:
            asset: Asset symbol (e.g. 'BTC').
            timestamp: ISO timestamp to evaluate.
            direction: Signal direction ('long' or 'short').

        Returns:
            MarketContext or None if insufficient price data.
        """
        # Get recent candles ending at timestamp
        lookback_hours = max(self.volume_lookback, self.atr_period) + 5
        start_ts = self._offset_timestamp(timestamp, -lookback_hours)
        candles = self.db.get_prices(asset, start=start_ts, end=timestamp)

        if len(candles) < 3:
            return None

        current = candles[-1]
        price = current["close"]

        # Volume z-score
        volume_zscore = self._compute_volume_zscore(candles)

        # 1h momentum
        momentum_1h = self._compute_momentum(candles, hours=1)

        # ATR
        atr = self._compute_atr(candles)

        # Volatility regime
        regime = self._classify_regime(candles, atr)

        # Alignment checks
        volume_confirmed = volume_zscore >= self.volume_threshold
        if direction == "long":
            momentum_aligned = momentum_1h > 0
        elif direction == "short":
            momentum_aligned = momentum_1h < 0
        else:
            momentum_aligned = False

        return MarketContext(
            asset=asset,
            timestamp=timestamp,
            price=price,
            volume_zscore=round(volume_zscore, 4),
            momentum_1h=round(momentum_1h, 4),
            atr_14=round(atr, 4),
            volatility_regime=regime,
            volume_confirmed=volume_confirmed,
            momentum_aligned=momentum_aligned,
        )

    def compute_confirmation_score(self, ctx: MarketContext) -> float:
        """Compute a 0-1 market confirmation score.

        Combines volume z-score and momentum alignment into a single
        score that feeds into the multi-factor signal model.

        Args:
            ctx: MarketContext for the asset at signal time.

        Returns:
            Score between 0.0 and 1.0.
        """
        # Volume component: sigmoid of z-score centered at threshold
        vol_score = 1.0 / (1.0 + math.exp(-(ctx.volume_zscore - self.volume_threshold)))

        # Momentum component: 1.0 if aligned, 0.0 if contradicts
        mom_score = 1.0 if ctx.momentum_aligned else 0.0

        # Weighted combination
        score = 0.6 * vol_score + 0.4 * mom_score
        return round(min(1.0, max(0.0, score)), 4)

    def _compute_volume_zscore(self, candles: list[dict]) -> float:
        """Compute volume z-score: (current - mean) / std.

        Args:
            candles: Price candles ordered by time ascending.

        Returns:
            Z-score of the most recent candle's volume.
        """
        if len(candles) < 2:
            return 0.0

        volumes = [c["volume"] for c in candles]
        current_vol = volumes[-1]

        # Use lookback window for baseline
        lookback = volumes[-min(self.volume_lookback + 1, len(volumes)):-1]
        if not lookback:
            return 0.0

        mean_vol = sum(lookback) / len(lookback)
        if mean_vol <= 0:
            return 0.0

        variance = sum((v - mean_vol) ** 2 for v in lookback) / len(lookback)
        std_vol = math.sqrt(variance) if variance > 0 else 1.0

        return (current_vol - mean_vol) / std_vol if std_vol > 0 else 0.0

    def _compute_momentum(self, candles: list[dict], hours: int = 1) -> float:
        """Compute price momentum over N hours.

        Args:
            candles: Price candles ordered by time ascending.
            hours: Number of hours to look back.

        Returns:
            Percentage price change over the period.
        """
        if len(candles) < 2:
            return 0.0

        current_price = candles[-1]["close"]

        # Find the candle closest to N hours ago
        target_idx = max(0, len(candles) - 1 - hours)
        past_price = candles[target_idx]["close"]

        if past_price <= 0:
            return 0.0

        return ((current_price - past_price) / past_price) * 100

    def _compute_atr(self, candles: list[dict]) -> float:
        """Compute Average True Range over the ATR period.

        True Range = max(high-low, |high-prev_close|, |low-prev_close|)

        Args:
            candles: Price candles ordered by time ascending.

        Returns:
            ATR value.
        """
        if len(candles) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        # Use the most recent atr_period TRs
        recent = true_ranges[-min(self.atr_period, len(true_ranges)):]
        return sum(recent) / len(recent) if recent else 0.0

    def _classify_regime(self, candles: list[dict], current_atr: float) -> str:
        """Classify volatility regime based on ATR relative to recent history.

        Args:
            candles: Price candles.
            current_atr: Current ATR value.

        Returns:
            'low', 'normal', or 'high'.
        """
        if len(candles) < self.atr_period + 5 or current_atr <= 0:
            return "normal"

        # Compute historical ATR for comparison
        price = candles[-1]["close"]
        if price <= 0:
            return "normal"

        # Normalize ATR as % of price
        atr_pct = (current_atr / price) * 100

        # Simple thresholds: < 1% = low, > 2.5% = high (for hourly candles)
        if atr_pct > 2.5 * self.high_vol_multiplier:
            return "high"
        elif atr_pct < 0.5:
            return "low"
        return "normal"

    @staticmethod
    def _offset_timestamp(iso_ts: str, hours: int) -> str:
        """Add/subtract hours from an ISO timestamp."""
        ts = iso_ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
