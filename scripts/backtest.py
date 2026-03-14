#!/usr/bin/env python3
"""CLI script for running backtests.

Usage:
    python scripts/backtest.py                          # Full backtest
    python scripts/backtest.py --category SECURITY      # Filter by category
    python scripts/backtest.py --severity 3             # High-severity only
    python scripts/backtest.py --exit-hours 4           # 4-hour exit window
    python scripts/backtest.py --asset BTC              # BTC only
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.analysis.backtester import Backtester
from src.config import load_config, setup_logging
from src.storage.database import Database


def main() -> None:
    """Main entry point for the backtest CLI."""
    parser = argparse.ArgumentParser(description="Event-driven crypto backtester")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--category", type=str, default=None,
                        help="Filter by event category (e.g. SECURITY, REGULATORY)")
    parser.add_argument("--severity", type=int, default=1,
                        help="Minimum event severity (1-5)")
    parser.add_argument("--exit-hours", type=int, default=None,
                        help="Exit window in hours (default: from config)")
    parser.add_argument("--asset", type=str, default=None,
                        help="Filter by specific asset (e.g. BTC)")
    parser.add_argument("--confidence", type=float, default=0.0,
                        help="Minimum signal confidence (0-1)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    backtester = Backtester(db, config)
    report = backtester.generate_report(
        category=args.category,
        min_severity=args.severity,
        exit_hours=args.exit_hours,
    )
    print(report)


if __name__ == "__main__":
    main()
