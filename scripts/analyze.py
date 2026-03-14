#!/usr/bin/env python3
"""CLI script for running analysis.

Usage:
    python scripts/analyze.py                    # Full impact analysis report
    python scripts/analyze.py --narratives       # Narrative tracking report
    python scripts/analyze.py --signals          # Generate and store signals
    python scripts/analyze.py --severity 3       # Only high-severity events
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.analysis.event_impact import EventImpactAnalyzer
from src.analysis.narrative_tracker import NarrativeTracker
from src.analysis.signal_generator import SignalGenerator
from src.config import load_config, setup_logging
from src.storage.database import Database


def main() -> None:
    """Main entry point for the analysis CLI."""
    parser = argparse.ArgumentParser(description="Crypto event impact analysis")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--severity", type=int, default=1, help="Minimum event severity (1-5)")
    parser.add_argument("--narratives", action="store_true", help="Run narrative tracking")
    parser.add_argument("--signals", action="store_true", help="Generate and store signals")
    parser.add_argument("--days", type=int, default=90, help="Lookback period in days")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    if args.narratives:
        tracker = NarrativeTracker(db, config)
        report = tracker.generate_report(days=args.days)
        print(report)
        return

    if args.signals:
        generator = SignalGenerator(db, config)
        count = generator.generate_and_store()
        print(f"\nGenerated and stored {count} signals.")
        return

    # Default: event impact analysis
    analyzer = EventImpactAnalyzer(db, config)
    report = analyzer.generate_report(min_severity=args.severity)
    print(report)


if __name__ == "__main__":
    main()
