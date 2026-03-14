#!/usr/bin/env bash
# Daily ingestion + processing pipeline for the Crypto News Research Engine.
# Intended to be run via crontab.
#
# Usage (manual):   ./scripts/daily_pipeline.sh
# Crontab entry:    17 6 * * * /home/USERNAME/news-crypto-engine/scripts/daily_pipeline.sh

set -euo pipefail

PROJECT_DIR="/home/USERNAME/news-crypto-engine"
VENV="$PROJECT_DIR/venv/bin/activate"
LOG="$PROJECT_DIR/data/daily_pipeline.log"

cd "$PROJECT_DIR"
source "$VENV"

echo "========================================" >> "$LOG"
echo "Pipeline run: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"

echo ">> CryptoPanic..." >> "$LOG"
python scripts/ingest.py --cryptopanic >> "$LOG" 2>&1 || echo "  CryptoPanic skipped" >> "$LOG"

echo ">> CCXT prices..." >> "$LOG"
python scripts/ingest.py --ccxt --days 2 >> "$LOG" 2>&1 || echo "  CCXT skipped" >> "$LOG"

echo ">> RSS feeds..." >> "$LOG"
python scripts/ingest.py --rss-only >> "$LOG" 2>&1 || echo "  RSS skipped" >> "$LOG"

echo ">> Processing..." >> "$LOG"
python scripts/process.py >> "$LOG" 2>&1 || echo "  Processing failed" >> "$LOG"

echo ">> Clustering..." >> "$LOG"
python scripts/process.py --cluster-only >> "$LOG" 2>&1 || echo "  Clustering failed" >> "$LOG"

echo ">> Stats:" >> "$LOG"
python scripts/ingest.py --stats >> "$LOG" 2>&1

echo "Pipeline complete: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
echo "========================================" >> "$LOG"
