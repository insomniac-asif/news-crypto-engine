#!/usr/bin/env python3
"""CLI script for processing ingested articles.

Runs the NLP pipeline: clean text, extract entities, classify events,
and score sentiment for all unprocessed articles.

Usage:
    python scripts/process.py                  # Process all unprocessed articles
    python scripts/process.py --limit 50       # Process up to 50 articles
    python scripts/process.py --reprocess      # Re-process all articles
    python scripts/process.py --stats          # Show processing statistics
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.config import load_config, setup_logging
from src.processing.entity_extractor import EntityExtractor
from src.processing.event_classifier import EventClassifier
from src.processing.sentiment import SentimentAnalyzer
from src.processing.text_cleaner import clean_article
from src.storage.database import Database

logger = logging.getLogger(__name__)


def process_articles(
    db: Database,
    config: dict,
    limit: int = 500,
    reprocess: bool = False,
) -> dict[str, int]:
    """Run the full processing pipeline on articles.

    Args:
        db: Database instance.
        config: Full config dict.
        limit: Max articles to process.
        reprocess: If True, re-process all articles (not just unprocessed).

    Returns:
        Dict with processing statistics.
    """
    extractor = EntityExtractor(config.get("nlp", {}).get("spacy_model", "en_core_web_sm"))
    classifier = EventClassifier(config)
    sentiment = SentimentAnalyzer(config)

    if reprocess:
        # Delete existing events before re-processing
        with db.connect() as conn:
            conn.execute("DELETE FROM events")
            conn.commit()
        logger.info("Cleared existing events for re-processing")
        articles = db.get_articles(limit=limit)
        logger.info("Re-processing %d articles", len(articles))
    else:
        articles = db.get_unprocessed_articles(limit=limit)
        logger.info("Processing %d unprocessed articles", len(articles))

    if not articles:
        logger.info("No articles to process")
        return {"processed": 0, "events_created": 0}

    processed = 0
    events_created = 0
    errors = 0

    for article in articles:
        try:
            # Step 1: Clean text
            cleaned = clean_article(article["title"], article["content"])

            # Step 2: Extract entities
            combined_text = f"{cleaned['title']} {cleaned['content']}"
            entities = extractor.extract(combined_text)

            # Step 3: Classify event
            event = classifier.classify(cleaned["title"], cleaned["content"])

            # Step 4: Sentiment analysis
            sent = sentiment.get_headline_sentiment(cleaned["title"], cleaned["content"])

            # Merge entity-detected assets with classifier results
            assets = entities["assets"]

            # Step 5: Store event in database
            event_id = db.insert_event(
                article_id=article["id"],
                category=event.category,
                severity=event.severity,
                summary=f"{event.summary} | sentiment={sent['label']}({sent['compound']:.2f})",
                assets_affected=assets,
            )

            events_created += 1
            processed += 1

            logger.debug(
                "Processed article %d → event %d: %s (severity=%d, assets=%s, sentiment=%s)",
                article["id"], event_id, event.category, event.severity,
                assets, sent["label"],
            )

        except Exception:
            errors += 1
            logger.exception("Error processing article %d: %s",
                             article["id"], article["title"][:80])

    stats = {
        "processed": processed,
        "events_created": events_created,
        "errors": errors,
    }
    logger.info("Processing complete: %s", stats)
    return stats


def show_stats(db: Database) -> None:
    """Display processing statistics."""
    stats = db.get_stats()
    print("\nProcessing Statistics:")
    print("-" * 40)
    for table, count in stats.items():
        print(f"  {table:>12}: {count:,} rows")

    # Show unprocessed count
    unprocessed = db.get_unprocessed_articles(limit=10000)
    print(f"\n  Unprocessed articles: {len(unprocessed)}")

    # Show event category breakdown
    from collections import Counter

    events = db.get_events(limit=10000)
    if events:
        cats = Counter(e["category"] for e in events)
        print("\n  Events by category:")
        for cat, count in cats.most_common():
            print(f"    {cat:>20}: {count}")

    print()


def main() -> None:
    """Main entry point for the processing CLI."""
    parser = argparse.ArgumentParser(description="Process ingested articles with NLP pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--limit", type=int, default=500, help="Max articles to process")
    parser.add_argument("--reprocess", action="store_true", help="Re-process all articles")
    parser.add_argument("--stats", action="store_true", help="Show processing statistics")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    if args.stats:
        show_stats(db)
        return

    results = process_articles(db, config, limit=args.limit, reprocess=args.reprocess)

    print(f"\nProcessing complete!")
    print(f"  Articles processed: {results['processed']}")
    print(f"  Events created:     {results['events_created']}")
    if results.get("errors"):
        print(f"  Errors:             {results['errors']}")


if __name__ == "__main__":
    main()
