"""Lightweight scheduler for periodic data collection."""

import logging
import signal
import sys
import time
from typing import Any, Optional

import schedule

from src.ingestion.price_collector import PriceCollector
from src.ingestion.reddit_collector import RedditCollector
from src.ingestion.rss_collector import RSSCollector
from src.storage.database import Database

logger = logging.getLogger(__name__)


class CollectionScheduler:
    """Manages periodic execution of data collection tasks.

    Uses the `schedule` library for lightweight cron-style scheduling.
    Runs in a single thread — suitable for laptop/single-machine deployment.
    """

    def __init__(self, config: dict[str, Any], db: Database) -> None:
        """Initialize the scheduler with collectors.

        Args:
            config: Full config dict.
            db: Database instance shared across collectors.
        """
        self.config = config
        self.db = db
        self._running = False

        sched_cfg = config.get("scheduler", {})
        self.price_interval = sched_cfg.get("price_interval", 15)
        self.rss_interval = sched_cfg.get("rss_interval", 30)
        self.reddit_interval = sched_cfg.get("reddit_interval", 60)
        retention_days = config.get("database", {}).get("retention_days", 90)
        self.retention_days = retention_days

        # Initialize collectors
        self.price_collector = PriceCollector(config, db)
        self.rss_collector = RSSCollector(config, db)
        self.reddit_collector = RedditCollector(config, db)

    def _collect_prices(self) -> None:
        """Run price collection job."""
        logger.info("Scheduled job: collecting prices...")
        try:
            results = self.price_collector.collect_recent()
            total = sum(results.values())
            logger.info("Price job done: %d new records", total)
        except Exception:
            logger.exception("Price collection job failed")

    def _collect_rss(self) -> None:
        """Run RSS collection job."""
        logger.info("Scheduled job: collecting RSS feeds...")
        try:
            results = self.rss_collector.collect_all()
            total = sum(results.values())
            logger.info("RSS job done: %d new articles", total)
        except Exception:
            logger.exception("RSS collection job failed")

    def _collect_reddit(self) -> None:
        """Run Reddit collection job."""
        if not self.reddit_collector.is_configured():
            logger.debug("Reddit collection skipped (not configured)")
            return
        logger.info("Scheduled job: collecting Reddit posts...")
        try:
            results = self.reddit_collector.collect_all()
            total = sum(results.values())
            logger.info("Reddit job done: %d new posts", total)
        except Exception:
            logger.exception("Reddit collection job failed")

    def _enforce_retention(self) -> None:
        """Run data retention enforcement."""
        try:
            deleted = self.db.enforce_retention(self.retention_days)
            if deleted:
                logger.info("Retention job: removed %d old articles", deleted)
        except Exception:
            logger.exception("Retention enforcement failed")

    def setup_schedule(self) -> None:
        """Configure all scheduled jobs based on config intervals."""
        schedule.every(self.price_interval).minutes.do(self._collect_prices)
        schedule.every(self.rss_interval).minutes.do(self._collect_rss)
        schedule.every(self.reddit_interval).minutes.do(self._collect_reddit)
        # Run retention check once per day
        schedule.every().day.at("03:00").do(self._enforce_retention)

        logger.info(
            "Schedule configured: prices every %dm, RSS every %dm, Reddit every %dm",
            self.price_interval, self.rss_interval, self.reddit_interval,
        )

    def run_once(self, include_reddit: bool = True) -> dict[str, Any]:
        """Run all collection tasks once (not on a schedule).

        Useful for initial data load or manual runs.

        Args:
            include_reddit: Whether to include Reddit collection.

        Returns:
            Dict with results from each collector.
        """
        results: dict[str, Any] = {}

        logger.info("Running one-time collection...")
        results["rss"] = self.rss_collector.collect_all()
        results["prices"] = self.price_collector.collect_all()

        if include_reddit and self.reddit_collector.is_configured():
            results["reddit"] = self.reddit_collector.collect_all()
        else:
            results["reddit"] = {}
            if include_reddit:
                logger.info("Reddit skipped (credentials not configured)")

        # Show stats
        stats = self.db.get_stats()
        logger.info("Database stats after collection: %s", stats)

        return results

    def run(self) -> None:
        """Start the scheduler loop. Blocks until interrupted.

        Handles SIGINT/SIGTERM gracefully for clean shutdown.
        """
        self._running = True

        def _handle_signal(signum: int, frame: Any) -> None:
            logger.info("Received signal %d, shutting down scheduler...", signum)
            self._running = False

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        # Run all jobs immediately on start
        logger.info("Running initial collection before starting schedule...")
        self.run_once()

        self.setup_schedule()
        logger.info("Scheduler running. Press Ctrl+C to stop.")

        while self._running:
            schedule.run_pending()
            time.sleep(1)

        logger.info("Scheduler stopped.")
