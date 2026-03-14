"""Reddit collector for crypto subreddit posts and sentiment."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)


class RedditCollector:
    """Collects posts from crypto-related subreddits using PRAW.

    Requires Reddit API credentials. Set via environment variables:
    - REDDIT_CLIENT_ID
    - REDDIT_CLIENT_SECRET
    - REDDIT_USER_AGENT (optional, has a default)

    Create an app at: https://www.reddit.com/prefs/apps/
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        """Initialize the Reddit collector.

        Args:
            config: Full config dict (expects 'reddit' key).
            db: Database instance for storing articles.
        """
        self.db = db
        reddit_cfg = config.get("reddit", {})

        self.client_id = os.environ.get("REDDIT_CLIENT_ID", reddit_cfg.get("client_id", ""))
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET", reddit_cfg.get("client_secret", ""))
        self.user_agent = os.environ.get(
            "REDDIT_USER_AGENT",
            reddit_cfg.get("user_agent", "news-crypto-engine/1.0"),
        )
        self.subreddits: list[str] = reddit_cfg.get("subreddits", ["cryptocurrency"])
        self.posts_per_sub: int = reddit_cfg.get("posts_per_sub", 25)
        self.min_score: int = reddit_cfg.get("min_score", 10)

        self._reddit = None

    def _get_reddit(self) -> Any:
        """Lazily initialize PRAW Reddit instance.

        Returns:
            praw.Reddit instance.

        Raises:
            RuntimeError: If credentials are not configured.
        """
        if self._reddit is not None:
            return self._reddit

        if not self.client_id or self.client_id.startswith("${"):
            raise RuntimeError(
                "Reddit API credentials not configured. "
                "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables. "
                "Create an app at https://www.reddit.com/prefs/apps/"
            )

        try:
            import praw
        except ImportError:
            raise RuntimeError("praw package not installed. Run: pip install praw")

        self._reddit = praw.Reddit(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
        )
        logger.info("Reddit API initialized (read-only mode)")
        return self._reddit

    def collect_subreddit(self, subreddit_name: str) -> int:
        """Collect hot posts from a single subreddit.

        Args:
            subreddit_name: Name of the subreddit (without r/ prefix).

        Returns:
            Number of new articles inserted.
        """
        reddit = self._get_reddit()
        subreddit = reddit.subreddit(subreddit_name)
        inserted = 0

        try:
            for post in subreddit.hot(limit=self.posts_per_sub):
                if post.score < self.min_score:
                    continue
                if post.stickied:
                    continue

                # Build article from Reddit post
                created_utc = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                published_at = created_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Combine title + selftext for content
                content = post.selftext or ""
                if post.url and not post.is_self:
                    content = f"[Link: {post.url}] {content}"
                content = f"[Score: {post.score}, Comments: {post.num_comments}] {content}"

                url = f"https://reddit.com{post.permalink}"
                source = f"reddit/{subreddit_name}"

                article_id = self.db.insert_article(
                    source=source,
                    url=url,
                    title=post.title,
                    content=content,
                    published_at=published_at,
                )
                if article_id is not None:
                    inserted += 1

        except Exception:
            logger.exception("Error collecting from r/%s", subreddit_name)

        if inserted:
            logger.info("r/%s: %d new posts ingested", subreddit_name, inserted)
        return inserted

    def collect_all(self) -> dict[str, int]:
        """Collect posts from all configured subreddits.

        Returns:
            Dict mapping subreddit name to number of new articles inserted.
        """
        results = {}
        for sub in self.subreddits:
            try:
                count = self.collect_subreddit(sub)
                results[sub] = count
            except RuntimeError as e:
                logger.error("Reddit collection skipped: %s", e)
                results[sub] = 0
                break  # No point trying other subs if creds are missing
            except Exception:
                logger.exception("Failed to collect r/%s", sub)
                results[sub] = 0

        total = sum(results.values())
        logger.info("Reddit collection complete: %d new posts from %d subreddits",
                     total, len(results))
        return results

    def is_configured(self) -> bool:
        """Check if Reddit API credentials are available.

        Returns:
            True if client_id and client_secret are set.
        """
        return bool(
            self.client_id
            and not self.client_id.startswith("${")
            and self.client_secret
            and not self.client_secret.startswith("${")
        )
