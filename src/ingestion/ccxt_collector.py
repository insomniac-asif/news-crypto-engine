"""CCXT-based price collector — exchange-native OHLCV data.

Uses CCXT library to fetch candles directly from exchanges (default: Binance).
Advantages over CoinGecko:
- Higher granularity (1m to 1d candles)
- More reliable for backtesting (exchange-native data)
- No API key needed for public market data on most exchanges
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)

# Map asset symbols to exchange trading pairs
# Uses USD pairs for Kraken compatibility (Kraken doesn't have USDT pairs for all)
DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "BNB": "BNB/USD",
    "XRP": "XRP/USD",
    "ADA": "ADA/USD",
    "DOGE": "DOGE/USD",
    "AVAX": "AVAX/USD",
    "DOT": "DOT/USD",
    "MATIC": "POL/USD",
}


class CCXTCollector:
    """Collect OHLCV price data using CCXT from exchange APIs.

    Default exchange: Binance (no API key required for public data).
    Supports configurable timeframe (15m, 1h, 4h, 1d).
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        self.db = db

        ccxt_config = config.get("ccxt", {})
        self.exchange_id: str = ccxt_config.get("exchange", "binance")
        self.timeframe: str = ccxt_config.get("timeframe", "1h")
        self.rate_limit_ms: int = ccxt_config.get("rate_limit_ms", 1200)
        self.initial_history_days: int = ccxt_config.get("initial_history_days", 30)

        # Symbol map: asset_symbol -> exchange trading pair
        self.symbol_map: dict[str, str] = ccxt_config.get("symbol_map", DEFAULT_SYMBOL_MAP)

        # Also pull from config assets
        assets_config = config.get("assets", {})
        self.symbols: list[str] = assets_config.get("symbols", list(DEFAULT_SYMBOL_MAP.keys()))

        # Auto-discovered assets
        self._auto_discovered: set[str] = set()

        self._exchange = None

    def _get_exchange(self):
        """Lazy-initialize the CCXT exchange instance."""
        if self._exchange is None:
            try:
                import ccxt
                exchange_class = getattr(ccxt, self.exchange_id, None)
                if exchange_class is None:
                    logger.error("Unknown exchange: %s", self.exchange_id)
                    return None
                self._exchange = exchange_class({
                    "enableRateLimit": True,
                    "rateLimit": self.rate_limit_ms,
                })
            except ImportError:
                logger.error("ccxt not installed. Run: pip install ccxt")
                return None
        return self._exchange

    def collect_asset(
        self,
        symbol: str,
        days: Optional[int] = None,
        timeframe: Optional[str] = None,
    ) -> int:
        """Collect OHLCV candles for a single asset.

        Args:
            symbol: Asset symbol (e.g. 'BTC').
            days: Number of days of history to fetch.
            timeframe: Override default timeframe (e.g. '15m', '1h').

        Returns:
            Number of new price records inserted.
        """
        exchange = self._get_exchange()
        if exchange is None:
            return 0

        pair = self.symbol_map.get(symbol, f"{symbol}/USDT")
        tf = timeframe or self.timeframe
        fetch_days = days or self.initial_history_days

        since_dt = datetime.now(timezone.utc) - timedelta(days=fetch_days)
        since_ms = int(since_dt.timestamp() * 1000)

        all_candles = []
        try:
            while True:
                candles = exchange.fetch_ohlcv(pair, tf, since=since_ms, limit=1000)
                if not candles:
                    break
                all_candles.extend(candles)
                # Move to after last candle
                since_ms = candles[-1][0] + 1
                if len(candles) < 1000:
                    break
                time.sleep(self.rate_limit_ms / 1000)
        except Exception as e:
            logger.error("CCXT fetch failed for %s: %s", pair, e)
            return 0

        if not all_candles:
            logger.warning("No candles returned for %s", pair)
            return 0

        # Convert to DB records
        records = []
        for candle in all_candles:
            ts_ms, o, h, l, c, v = candle
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            records.append({
                "asset": symbol,
                "timestamp": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v or 0,
            })

        inserted = self.db.insert_prices(records)
        logger.info("CCXT: %d new candles for %s (%s, %d total fetched)",
                     inserted, symbol, tf, len(all_candles))
        return inserted

    def collect_all(self, days: Optional[int] = None) -> dict[str, int]:
        """Collect candles for all configured + auto-discovered assets.

        Returns:
            Dict mapping symbol to number of new records.
        """
        all_symbols = list(set(self.symbols) | self._auto_discovered)
        results = {}
        for symbol in all_symbols:
            try:
                count = self.collect_asset(symbol, days=days)
                results[symbol] = count
            except Exception:
                logger.exception("Failed to collect %s via CCXT", symbol)
                results[symbol] = 0

        total = sum(results.values())
        logger.info("CCXT collection complete: %d total new records", total)
        return results

    def collect_recent(self) -> dict[str, int]:
        """Collect last 2 days for all assets (for scheduled runs)."""
        return self.collect_all(days=2)

    def add_asset(self, symbol: str) -> None:
        """Auto-discover and start tracking a new asset.

        Args:
            symbol: Asset symbol to add (e.g. 'LINK').
        """
        if symbol not in self.symbols and symbol not in self._auto_discovered:
            self._auto_discovered.add(symbol)
            # Add default USDT pair if not in symbol map
            if symbol not in self.symbol_map:
                self.symbol_map[symbol] = f"{symbol}/USD"
            logger.info("Auto-discovered asset: %s (pair: %s)", symbol, self.symbol_map[symbol])

    def get_tracked_assets(self) -> list[str]:
        """Get all tracked assets (configured + auto-discovered)."""
        return sorted(set(self.symbols) | self._auto_discovered)

    def fetch_recent_candles(
        self,
        symbol: str,
        count: int = 24,
        timeframe: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent candles on demand (for real-time market confirmation).

        Args:
            symbol: Asset symbol.
            count: Number of candles to fetch.
            timeframe: Override timeframe.

        Returns:
            List of candle dicts, or empty list on failure.
        """
        exchange = self._get_exchange()
        if exchange is None:
            return []

        pair = self.symbol_map.get(symbol, f"{symbol}/USDT")
        tf = timeframe or self.timeframe

        try:
            candles = exchange.fetch_ohlcv(pair, tf, limit=count)
            results = []
            for ts_ms, o, h, l, c, v in candles:
                dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                results.append({
                    "asset": symbol,
                    "timestamp": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": o, "high": h, "low": l, "close": c,
                    "volume": v or 0,
                })
            return results
        except Exception as e:
            logger.error("CCXT real-time fetch failed for %s: %s", pair, e)
            return []
