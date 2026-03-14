"""Price data collector using CoinGecko free API."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from src.storage.database import Database

logger = logging.getLogger(__name__)


class PriceCollector:
    """Collects OHLCV price data from CoinGecko's free API.

    CoinGecko free tier limits:
    - ~10-30 calls/minute (no API key needed)
    - /coins/{id}/ohlc gives 1/4/12h candles depending on range
    - /coins/{id}/market_chart gives granular data (hourly for 1-90 days)
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        """Initialize the price collector.

        Args:
            config: Full config dict (expects 'price_collection' and 'assets' keys).
            db: Database instance for storing price data.
        """
        self.db = db
        self.assets = config.get("assets", {})
        pc = config.get("price_collection", {})
        cg = pc.get("coingecko", {})

        self.base_url = cg.get("base_url", "https://api.coingecko.com/api/v3")
        self.rate_limit_calls = cg.get("rate_limit_calls", 10)
        self.rate_limit_period = cg.get("rate_limit_period", 60)
        self.retry_attempts = cg.get("retry_attempts", 3)
        self.retry_delay = cg.get("retry_delay", 5)
        self.initial_history_days = pc.get("initial_history_days", 30)

        self.coingecko_ids: dict[str, str] = self.assets.get("coingecko_ids", {})
        self.symbols: list[str] = self.assets.get("symbols", [])

        self._call_timestamps: list[float] = []
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "news-crypto-engine/1.0",
        })

    def _rate_limit(self) -> None:
        """Enforce rate limiting by sleeping if necessary."""
        now = time.time()
        # Remove timestamps outside the rate limit window
        self._call_timestamps = [
            t for t in self._call_timestamps
            if now - t < self.rate_limit_period
        ]
        if len(self._call_timestamps) >= self.rate_limit_calls:
            sleep_time = self.rate_limit_period - (now - self._call_timestamps[0]) + 0.5
            if sleep_time > 0:
                logger.debug("Rate limit: sleeping %.1fs", sleep_time)
                time.sleep(sleep_time)
        self._call_timestamps.append(time.time())

    def _api_get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Optional[dict | list]:
        """Make a rate-limited GET request to CoinGecko with retry logic.

        Args:
            endpoint: API endpoint path (e.g. '/coins/bitcoin/market_chart').
            params: Query parameters.

        Returns:
            Parsed JSON response, or None on failure.
        """
        url = f"{self.base_url}{endpoint}"

        for attempt in range(1, self.retry_attempts + 1):
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    # Exponential backoff: 30s, 60s, 120s, 240s, 480s
                    wait = self.retry_delay * (2 ** attempt)
                    logger.warning("Rate limited (429), waiting %ds (attempt %d/%d)",
                                   wait, attempt, self.retry_attempts)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("Request timeout for %s (attempt %d/%d)",
                               endpoint, attempt, self.retry_attempts)
            except requests.exceptions.ConnectionError:
                logger.warning("Connection error for %s (attempt %d/%d)",
                               endpoint, attempt, self.retry_attempts)
            except requests.exceptions.HTTPError as e:
                logger.error("HTTP error %s for %s (attempt %d/%d)",
                             e.response.status_code if e.response else "?",
                             endpoint, attempt, self.retry_attempts)
            except requests.exceptions.RequestException as e:
                logger.error("Request failed for %s: %s", endpoint, e)
                return None

            if attempt < self.retry_attempts:
                time.sleep(self.retry_delay * attempt)

        logger.error("All %d attempts failed for %s", self.retry_attempts, endpoint)
        return None

    def fetch_market_chart(self, coin_id: str, days: int = 1) -> Optional[list[dict[str, Any]]]:
        """Fetch hourly OHLCV-equivalent data from CoinGecko market_chart endpoint.

        CoinGecko's market_chart returns price/volume at intervals:
        - 1 day: ~5-minute intervals
        - 2-90 days: hourly intervals
        - >90 days: daily intervals

        We convert price points into pseudo-OHLC candles by grouping into hourly buckets.

        Args:
            coin_id: CoinGecko coin ID (e.g. 'bitcoin').
            days: Number of days of history to fetch (2-90 for hourly).

        Returns:
            List of candle dicts with keys: timestamp, open, high, low, close, volume.
            Returns None on API failure.
        """
        # Clamp to 2-90 for hourly granularity
        days = max(2, min(days, 90))

        data = self._api_get(
            f"/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": str(days)},
        )
        if not data or "prices" not in data:
            return None

        prices = data["prices"]  # [[timestamp_ms, price], ...]
        volumes = data.get("total_volumes", [])  # [[timestamp_ms, volume], ...]

        # Build a volume lookup by hour
        volume_by_hour: dict[str, float] = {}
        for ts_ms, vol in volumes:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            hour_key = dt.strftime("%Y-%m-%dT%H:00:00Z")
            volume_by_hour[hour_key] = volume_by_hour.get(hour_key, 0) + (vol or 0)

        # Group prices into hourly candles
        hourly: dict[str, list[float]] = {}
        for ts_ms, price in prices:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            hour_key = dt.strftime("%Y-%m-%dT%H:00:00Z")
            hourly.setdefault(hour_key, []).append(price)

        candles = []
        for hour_key in sorted(hourly.keys()):
            price_points = hourly[hour_key]
            candles.append({
                "timestamp": hour_key,
                "open": price_points[0],
                "high": max(price_points),
                "low": min(price_points),
                "close": price_points[-1],
                "volume": volume_by_hour.get(hour_key, 0),
            })

        logger.debug("Fetched %d hourly candles for %s (%d days)", len(candles), coin_id, days)
        return candles

    def collect_asset(self, symbol: str, days: Optional[int] = None) -> int:
        """Collect price data for a single asset and store it in the database.

        Args:
            symbol: Asset symbol (e.g. 'BTC').
            days: Number of days of history. Defaults to initial_history_days config.

        Returns:
            Number of new price records inserted.
        """
        coin_id = self.coingecko_ids.get(symbol)
        if not coin_id:
            logger.warning("No CoinGecko ID configured for symbol %s", symbol)
            return 0

        if days is None:
            days = self.initial_history_days

        candles = self.fetch_market_chart(coin_id, days=days)
        if not candles:
            logger.warning("No price data returned for %s", symbol)
            return 0

        records = [{"asset": symbol, **c} for c in candles]
        inserted = self.db.insert_prices(records)
        return inserted

    def collect_all(self, days: Optional[int] = None) -> dict[str, int]:
        """Collect price data for all configured assets.

        Args:
            days: Number of days of history per asset.

        Returns:
            Dict mapping symbol to number of new records inserted.
        """
        results = {}
        for symbol in self.symbols:
            logger.info("Collecting prices for %s...", symbol)
            try:
                count = self.collect_asset(symbol, days=days)
                results[symbol] = count
            except Exception:
                logger.exception("Failed to collect prices for %s", symbol)
                results[symbol] = 0
        total = sum(results.values())
        logger.info("Price collection complete: %d total new records across %d assets",
                     total, len(results))
        return results

    def collect_recent(self) -> dict[str, int]:
        """Collect the most recent price data (last 2 days) for all assets.

        Suitable for scheduled/recurring collection runs.

        Returns:
            Dict mapping symbol to number of new records inserted.
        """
        return self.collect_all(days=2)
