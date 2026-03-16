#!/usr/bin/env python3
"""Send a daily snapshot of the crypto project to the dashboard API.

Reports: signals generated, articles processed, average sentiment,
and backtested P&L if available.

Usage:
    python scripts/daily_snapshot.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.config import load_config, setup_logging
from src.storage.database import Database

from dashboard_reporter import ProjectReporter


def main() -> None:
    config = load_config("config.yaml")
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    reporter = ProjectReporter(project="crypto")
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%dT00:00:00Z")

    # Count today's articles
    with db.connect() as conn:
        articles_today = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE published_at >= ?", (today_str,)
        ).fetchone()[0]

        events_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE detected_at >= ?", (today_str,)
        ).fetchone()[0]

        # Average sentiment for today's events
        avg_sent_row = conn.execute(
            "SELECT AVG(sentiment_score) FROM events WHERE detected_at >= ?",
            (today_str,),
        ).fetchone()
        avg_sentiment = round(avg_sent_row[0], 4) if avg_sent_row[0] is not None else 0.0

        # Multi-factor signals generated today
        signals_today = 0
        try:
            signals_today = conn.execute(
                "SELECT COUNT(*) FROM signals_v2 WHERE entry_time >= ?",
                (today_str,),
            ).fetchone()[0]
        except Exception:
            pass

        # Total signals in DB
        total_signals = 0
        try:
            total_signals = conn.execute("SELECT COUNT(*) FROM signals_v2").fetchone()[0]
        except Exception:
            pass

    reporter.log_snapshot({
        "daily_pnl": 0.0,  # No live trading — placeholder
        "cumulative_pnl": 0.0,
        "win_rate": 0.0,
        "open_positions": 0,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {
            "articles_today": articles_today,
            "events_today": events_today,
            "signals_today": signals_today,
            "total_signals": total_signals,
            "avg_sentiment": avg_sentiment,
            "snapshot_type": "crypto_daily",
        },
    })

    print(f"Crypto daily snapshot sent:")
    print(f"  Articles today: {articles_today}")
    print(f"  Events today:   {events_today}")
    print(f"  Signals today:  {signals_today}")
    print(f"  Avg sentiment:  {avg_sentiment}")


if __name__ == "__main__":
    main()
