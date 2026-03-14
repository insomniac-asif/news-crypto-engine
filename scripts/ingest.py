#!/usr/bin/env python3
"""CLI script for running data ingestion.

Usage:
    python scripts/ingest.py                 # Run all collectors once
    python scripts/ingest.py --schedule      # Run on a schedule (blocks)
    python scripts/ingest.py --prices-only   # Only collect prices
    python scripts/ingest.py --rss-only      # Only collect RSS feeds
    python scripts/ingest.py --stats         # Show database stats
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.config import load_config, setup_logging
from src.ingestion.price_collector import PriceCollector
from src.ingestion.rss_collector import RSSCollector
from src.ingestion.scheduler import CollectionScheduler
from src.storage.database import Database


def main() -> None:
    """Main entry point for the ingestion CLI."""
    parser = argparse.ArgumentParser(description="Crypto news data ingestion")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--schedule", action="store_true", help="Run on a schedule (blocks)")
    parser.add_argument("--prices-only", action="store_true", help="Only collect price data")
    parser.add_argument("--rss-only", action="store_true", help="Only collect RSS feeds")
    parser.add_argument("--reddit-only", action="store_true", help="Only collect Reddit posts")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--days", type=int, default=None, help="Days of price history to fetch")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    if args.stats:
        stats = db.get_stats()
        print("\nDatabase Statistics:")
        print("-" * 30)
        for table, count in stats.items():
            print(f"  {table:>12}: {count:,} rows")
        print()
        return

    if args.schedule:
        scheduler = CollectionScheduler(config, db)
        scheduler.run()
        return

    if args.prices_only:
        collector = PriceCollector(config, db)
        results = collector.collect_all(days=args.days)
        total = sum(results.values())
        print(f"\nPrice collection complete: {total} new records")
        for symbol, count in results.items():
            print(f"  {symbol}: {count}")
        return

    if args.rss_only:
        collector = RSSCollector(config, db)
        results = collector.collect_all()
        total = sum(results.values())
        print(f"\nRSS collection complete: {total} new articles")
        for feed, count in results.items():
            print(f"  {feed}: {count}")
        return

    if args.reddit_only:
        from src.ingestion.reddit_collector import RedditCollector

        collector = RedditCollector(config, db)
        if not collector.is_configured():
            print("Reddit API credentials not configured.")
            print("Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables.")
            sys.exit(1)
        results = collector.collect_all()
        total = sum(results.values())
        print(f"\nReddit collection complete: {total} new posts")
        for sub, count in results.items():
            print(f"  r/{sub}: {count}")
        return

    # Default: run all once
    scheduler = CollectionScheduler(config, db)
    results = scheduler.run_once()

    print("\nIngestion complete!")
    print("-" * 40)
    stats = db.get_stats()
    for table, count in stats.items():
        print(f"  {table:>12}: {count:,} rows")


if __name__ == "__main__":
    main()
