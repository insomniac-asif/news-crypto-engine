#!/usr/bin/env python3
"""CLI script for running research experiments.

Usage:
    python scripts/experiment.py --run all          # Run all 5 experiments
    python scripts/experiment.py --run 1            # Run specific experiment
    python scripts/experiment.py --report           # Generate full report
    python scripts/experiment.py --report --format md  # Export as markdown
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.analysis.experiments import ExperimentRunner
from src.config import load_config, setup_logging
from src.storage.database import Database


def main() -> None:
    """Main entry point for the experiment CLI."""
    parser = argparse.ArgumentParser(description="Run research experiments")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--run", type=str, help="Run experiment(s): 'all' or experiment ID (1-5)")
    parser.add_argument("--report", action="store_true", help="Generate full experiment report")
    parser.add_argument("--format", choices=["text", "md"], default="text",
                        help="Report format (text or md)")
    parser.add_argument("--output", type=str, help="Write report to file instead of stdout")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    db = Database(db_path)

    runner = ExperimentRunner(db, config)

    if args.run:
        if args.run.lower() == "all":
            results = runner.run_all()
        else:
            try:
                exp_id = int(args.run)
                results = [runner.run(exp_id)]
            except ValueError:
                print(f"Error: --run must be 'all' or a number 1-5, got '{args.run}'")
                sys.exit(1)

        report = runner.generate_report(results, fmt=args.format)

        if args.output:
            Path(args.output).write_text(report)
            print(f"Report written to {args.output}")
        else:
            print(report)
        return

    if args.report:
        report = runner.generate_report(fmt=args.format)
        if args.output:
            Path(args.output).write_text(report)
            print(f"Report written to {args.output}")
        else:
            print(report)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
