#!/usr/bin/env python3
"""Generate predictions and grade existing ones.

Usage:
    python scripts/predict.py                # Generate + grade
    python scripts/predict.py --generate     # Generate only
    python scripts/predict.py --grade        # Grade only
    python scripts/predict.py --trust        # Show trust leaderboard
    python scripts/predict.py --accuracy     # Show accuracy stats
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.analysis.predictions import (
    run_predictions,
    run_grading,
    get_trust_leaderboard,
    get_accuracy_stats,
)
from src.analysis.prediction_optimizer import (
    run_backtest_report,
    apply_best_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prediction engine")
    parser.add_argument("--generate", action="store_true", help="Generate predictions only")
    parser.add_argument("--grade", action="store_true", help="Grade predictions only")
    parser.add_argument("--trust", action="store_true", help="Show source trust leaderboard")
    parser.add_argument("--accuracy", action="store_true", help="Show accuracy stats")
    parser.add_argument("--optimize", action="store_true", help="Backtest all configs and apply best one")
    parser.add_argument("--backtest", action="store_true", help="Run backtest report without applying")
    parser.add_argument("--since", type=str, default=None, help="Only generate from clusters after this timestamp")
    args = parser.parse_args()

    if args.trust:
        leaderboard = get_trust_leaderboard()
        print(f"\n{'Source':<25} {'Trust':>6} {'Acc 1h':>7} {'Acc 4h':>7} {'Acc 24h':>7} {'Total':>6} {'Noise':>6}")
        print("-" * 75)
        for s in leaderboard:
            noise = "YES" if s["is_noise_source"] else ""
            print(f"{s['source_name']:<25} {s['trust_score']:>6.2f} {s['accuracy_1h']:>6.1%} {s['accuracy_4h']:>6.1%} {s['accuracy_24h']:>6.1%} {s['total_predictions']:>6} {noise:>6}")
        return

    if args.accuracy:
        stats = get_accuracy_stats()
        for tf in ["1h", "4h", "24h"]:
            s = stats[tf]
            print(f"\n{tf} Accuracy: {s['accuracy']:.1%} ({s['correct']}/{s['total']})")
            for grade in ["A", "B", "C", "D", "F"]:
                cnt = s["by_grade"][grade]
                bar = "█" * min(30, cnt)
                print(f"  {grade}: {cnt:>4} {bar}")
        return

    if args.backtest:
        report = run_backtest_report()
        print(report)
        return

    if args.optimize:
        print("Running parameter optimization backtest...")
        best = apply_best_config()
        print(f"\nBest config: {best['name']}")
        print(f"  1h: {best['accuracy_1h']:.1%}  4h: {best['accuracy_4h']:.1%}  24h: {best['accuracy_24h']:.1%}")
        print(f"  Saved to data/prediction_config.json")
        print(f"\nNow re-generating predictions with optimized parameters...")
        # Clear old predictions and regenerate
        import sqlite3
        conn = sqlite3.connect(str(project_root / "data" / "news_crypto.db"))
        conn.execute("DELETE FROM predictions")
        conn.commit()
        conn.close()
        count = run_predictions(since=args.since)
        print(f"Generated {count} predictions with optimized config")
        counts = run_grading()
        print(f"Graded {counts['graded']} predictions")
        return

    if args.generate or not (args.grade):
        count = run_predictions(since=args.since)
        print(f"Generated {count} predictions")

    if args.grade or not (args.generate):
        counts = run_grading()
        print(f"Graded {counts['graded']} predictions (skipped {counts['skipped']})")


if __name__ == "__main__":
    main()
