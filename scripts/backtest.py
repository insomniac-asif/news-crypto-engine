#!/usr/bin/env python3
"""CLI script for running backtests (Phase 3 placeholder)."""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def main() -> None:
    """Placeholder for backtest CLI — implemented in Phase 3."""
    print("Backtester will be implemented in Phase 3.")
    print("Run 'python scripts/ingest.py' first to collect data.")


if __name__ == "__main__":
    main()
