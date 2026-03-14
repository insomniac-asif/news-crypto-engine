"""CryptoPanic news collector — free-tier API integration.

CryptoPanic aggregates crypto news and provides community sentiment
votes (bullish/bearish) as a free additional signal.

Free tier: 5 requests per minute, returns title/url/source/currencies.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from src.storage.database import Database

logger = logging.getLogger(__name__)

CRYPTOPANIC_BASE_URL = "https://cryptopanic.com/api/v1/posts/"


class CryptoPanicCollector:
    """Collect crypto news from CryptoPanic API.

    Features:
    - Fetches recent news with title, URL, source, mentioned currencies
    - Captures community votes (bullish/bearish/important) as free sentiment
    - Deduplicates against existing articles by URL
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        self.db = db
        cp_config = config.get("cryptopanic", {})
        self.api_token: str = cp_config.get("api_token", "")
        self.rate_limit_calls: int = cp_config.get("rate_limit_calls", 5)
        self.rate_limit_period: int = cp_config.get("rate_limit_period", 60)
        self.max_pages: int = cp_config.get("max_pages", 3)

        self._call_timestamps: list[float] = []
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "news-crypto-engine/1.0",
        })

    def is_configured(self) -> bool:
        """Check if API token is set."""
        return bool(self.api_token)

    def collect_all(self, filter_type: str = "news") -> int:
        """Fetch recent posts from CryptoPanic and store new articles.

        Args:
            filter_type: Post filter — 'news', 'media', or 'all'.

        Returns:
            Number of new articles inserted.
        """
        if not self.is_configured():
            logger.warning("CryptoPanic API token not configured")
            return 0

        total_inserted = 0
        next_url = None

        for page in range(self.max_pages):
            if page == 0:
                data = self._fetch_page(filter_type=filter_type)
            else:
                if not next_url:
                    break
                data = self._fetch_url(next_url)

            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            for post in results:
                inserted = self._store_post(post)
                if inserted:
                    total_inserted += 1

            next_url = data.get("next")

        logger.info("CryptoPanic: %d new articles from %d pages", total_inserted, page + 1)
        return total_inserted

    def _fetch_page(
        self,
        filter_type: str = "news",
        currencies: Optional[str] = None,
    ) -> Optional[dict]:
        """Fetch a page of posts from CryptoPanic API.

        Args:
            filter_type: 'news', 'media', or 'all'.
            currencies: Comma-separated currency codes (e.g. 'BTC,ETH').

        Returns:
            API response dict or None.
        """
        self._rate_limit()
        params: dict[str, str] = {
            "auth_token": self.api_token,
            "filter": filter_type,
            "public": "true",
        }
        if currencies:
            params["currencies"] = currencies

        try:
            resp = self._session.get(CRYPTOPANIC_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("CryptoPanic API error: %s", e)
            return None

    def _fetch_url(self, url: str) -> Optional[dict]:
        """Fetch a specific URL (for pagination)."""
        self._rate_limit()
        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("CryptoPanic pagination error: %s", e)
            return None

    def _store_post(self, post: dict) -> bool:
        """Store a CryptoPanic post as an article.

        Args:
            post: CryptoPanic API post object.

        Returns:
            True if article was inserted (not a duplicate).
        """
        url = post.get("url", "")
        title = post.get("title", "")
        if not url or not title:
            return False

        # Source info
        source_info = post.get("source", {})
        source_name = source_info.get("title", "CryptoPanic") if isinstance(source_info, dict) else "CryptoPanic"

        # Published timestamp
        published_raw = post.get("published_at", "")
        if published_raw:
            try:
                dt = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                published_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                published_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            published_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build content from votes + currencies
        votes = post.get("votes", {})
        currencies = post.get("currencies", [])
        currency_codes = [c.get("code", "") for c in currencies if isinstance(c, dict)]

        content_parts = [title]
        if votes:
            content_parts.append(
                f"Community votes: "
                f"positive={votes.get('positive', 0)}, "
                f"negative={votes.get('negative', 0)}, "
                f"important={votes.get('important', 0)}, "
                f"liked={votes.get('liked', 0)}, "
                f"disliked={votes.get('disliked', 0)}"
            )
        if currency_codes:
            content_parts.append(f"Currencies: {', '.join(currency_codes)}")

        content = " | ".join(content_parts)

        article_id = self.db.insert_article(
            source=source_name,
            url=url,
            title=title,
            content=content,
            published_at=published_at,
        )
        return article_id is not None

    def _rate_limit(self) -> None:
        """Enforce rate limiting."""
        now = time.time()
        self._call_timestamps = [
            t for t in self._call_timestamps
            if now - t < self.rate_limit_period
        ]
        if len(self._call_timestamps) >= self.rate_limit_calls:
            sleep_time = self.rate_limit_period - (now - self._call_timestamps[0]) + 0.5
            if sleep_time > 0:
                logger.debug("CryptoPanic rate limit: sleeping %.1fs", sleep_time)
                time.sleep(sleep_time)
        self._call_timestamps.append(time.time())
