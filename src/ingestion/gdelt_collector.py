"""GDELT integration for historical news backtesting depth.

Uses the GDELT DOC API (free, no key needed) to query crypto-relevant
articles from its massive global news database. This backfills historical
events for deeper backtesting.

NOT a full GDELT ingestion — queries only for crypto-relevant articles
using keyword filters to keep volume manageable.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import requests

from src.storage.database import Database

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Crypto-specific search terms for GDELT queries
DEFAULT_SEARCH_TERMS = [
    "cryptocurrency",
    "bitcoin",
    "ethereum",
    "crypto regulation",
    "SEC crypto",
    "crypto exchange",
    "DeFi",
    "stablecoin",
]


class GDELTCollector:
    """Collect historical crypto news from GDELT's DOC API.

    The GDELT DOC API allows free-text search across GDELT's monitored
    news articles. We use it to backfill historical crypto events.

    Rate limits: be polite — 1 request per 5 seconds.
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        self.db = db
        gdelt_config = config.get("gdelt", {})
        self.search_terms: list[str] = gdelt_config.get("search_terms", DEFAULT_SEARCH_TERMS)
        self.max_records: int = gdelt_config.get("max_records", 250)
        self.request_delay: float = gdelt_config.get("request_delay", 5.0)

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "news-crypto-engine/1.0",
        })

    def collect_historical(
        self,
        query: Optional[str] = None,
        days_back: int = 30,
        max_records: Optional[int] = None,
    ) -> int:
        """Fetch historical articles from GDELT and store new ones.

        Args:
            query: Custom search query. If None, uses default crypto terms.
            days_back: How many days back to search.
            max_records: Max articles to fetch per query.

        Returns:
            Total number of new articles inserted.
        """
        if query:
            queries = [query]
        else:
            queries = self.search_terms

        total_inserted = 0
        for q in queries:
            articles = self._search(q, days_back, max_records or self.max_records)
            for article in articles:
                inserted = self._store_article(article)
                if inserted:
                    total_inserted += 1
            time.sleep(self.request_delay)

        logger.info("GDELT: %d new articles from %d queries", total_inserted, len(queries))
        return total_inserted

    def _search(
        self, query: str, days_back: int, max_records: int,
    ) -> list[dict[str, Any]]:
        """Search GDELT DOC API for articles.

        Args:
            query: Search terms.
            days_back: How far back to search.
            max_records: Max results.

        Returns:
            List of article dicts.
        """
        start_date = (datetime.now(timezone.utc) - timedelta(days=days_back))
        start_str = start_date.strftime("%Y%m%d%H%M%S")
        end_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(max_records),
            "format": "json",
            "startdatetime": start_str,
            "enddatetime": end_str,
            "sort": "datedesc",
        }

        try:
            resp = self._session.get(GDELT_DOC_API, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("GDELT API error for query '%s': %s", query, e)
            return []
        except (ValueError, KeyError):
            logger.error("GDELT returned invalid JSON for query '%s'", query)
            return []

        articles_raw = data.get("articles", [])
        if not articles_raw:
            logger.debug("GDELT: no articles for query '%s'", query)
            return []

        articles = []
        for art in articles_raw:
            url = art.get("url", "")
            title = art.get("title", "")
            if not url or not title:
                continue

            # Parse GDELT datetime format (YYYYMMDDTHHMMSS or ISO)
            seendate = art.get("seendate", "")
            published_at = self._parse_gdelt_date(seendate)

            source_name = art.get("domain", art.get("sourcecountry", "GDELT"))

            # GDELT provides tone scores
            tone = art.get("tone", 0)

            articles.append({
                "source": f"GDELT/{source_name}",
                "url": url,
                "title": title,
                "content": f"GDELT tone={tone}",
                "published_at": published_at,
            })

        logger.debug("GDELT: %d articles for query '%s'", len(articles), query)
        return articles

    def _store_article(self, article: dict[str, Any]) -> bool:
        """Store a GDELT article, deduplicating by URL.

        Returns:
            True if new article was inserted.
        """
        article_id = self.db.insert_article(
            source=article["source"],
            url=article["url"],
            title=article["title"],
            content=article.get("content", ""),
            published_at=article["published_at"],
        )
        return article_id is not None

    @staticmethod
    def _parse_gdelt_date(date_str: str) -> str:
        """Parse GDELT's date format to ISO 8601."""
        if not date_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # GDELT uses YYYYMMDDTHHMMSS format
        for fmt in ("%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M%S"):
            try:
                dt = datetime.strptime(date_str.rstrip("Z"), fmt.rstrip("Z"))
                dt = dt.replace(tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
