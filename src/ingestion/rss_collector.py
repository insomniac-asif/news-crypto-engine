"""RSS feed collector for crypto news sources."""

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import feedparser
import requests

from src.storage.database import Database

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags from text.

    Args:
        text: Raw HTML text.

    Returns:
        Plain text with HTML tags removed.
    """
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_date(entry: dict[str, Any]) -> str:
    """Extract and normalize the published date from a feed entry.

    Tries multiple date fields and formats. Falls back to current time.

    Args:
        entry: feedparser entry dict.

    Returns:
        ISO 8601 UTC timestamp string.
    """
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            pass

    # feedparser's parsed struct
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                pass

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RSSCollector:
    """Collects and ingests articles from configured RSS feeds."""

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        """Initialize the RSS collector.

        Args:
            config: Full config dict (expects 'rss_feeds' key).
            db: Database instance for storing articles.
        """
        self.db = db
        self.feeds: list[dict[str, str]] = config.get("rss_feeds", [])
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "news-crypto-engine/1.0",
        })

    def fetch_feed(self, feed_config: dict[str, str]) -> list[dict[str, Any]]:
        """Fetch and parse a single RSS feed.

        Args:
            feed_config: Dict with 'name', 'url', and optionally 'category'.

        Returns:
            List of article dicts ready for database insertion.
        """
        name = feed_config["name"]
        url = feed_config["url"]
        articles = []

        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("Failed to fetch feed %s (%s): %s", name, url, e)
            return []

        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            logger.warning("Feed %s returned malformed data: %s", name, feed.bozo_exception)
            return []

        for entry in feed.entries:
            link = entry.get("link", "")
            title = entry.get("title", "")
            if not link or not title:
                continue

            # Extract content — try multiple fields
            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].get("value", "")
            elif hasattr(entry, "summary"):
                content = entry.get("summary", "")
            elif hasattr(entry, "description"):
                content = entry.get("description", "")

            content = _strip_html(content)
            title = _strip_html(title)
            published_at = _parse_date(entry)

            articles.append({
                "source": name,
                "url": link,
                "title": title,
                "content": content,
                "published_at": published_at,
            })

        logger.debug("Parsed %d entries from %s", len(articles), name)
        return articles

    def collect_feed(self, feed_config: dict[str, str]) -> int:
        """Fetch a single feed and store new articles in the database.

        Args:
            feed_config: Dict with 'name' and 'url'.

        Returns:
            Number of new articles inserted.
        """
        articles = self.fetch_feed(feed_config)
        inserted = 0
        for article in articles:
            article_id = self.db.insert_article(**article)
            if article_id is not None:
                inserted += 1
        if inserted:
            logger.info("Feed %s: %d new articles (of %d fetched)",
                        feed_config["name"], inserted, len(articles))
        return inserted

    def collect_all(self) -> dict[str, int]:
        """Fetch all configured RSS feeds and store new articles.

        Returns:
            Dict mapping feed name to number of new articles inserted.
        """
        results = {}
        for feed_config in self.feeds:
            name = feed_config.get("name", feed_config.get("url", "unknown"))
            try:
                count = self.collect_feed(feed_config)
                results[name] = count
            except Exception:
                logger.exception("Failed to collect feed %s", name)
                results[name] = 0

        total = sum(results.values())
        logger.info("RSS collection complete: %d new articles from %d feeds",
                     total, len(results))
        return results
