# Contributing

Thanks for your interest in contributing to the Crypto News Research Engine.

## Development Setup

```bash
git clone https://github.com/YOUR_USERNAME/news-crypto-engine.git
cd news-crypto-engine
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
```

## Code Standards

- **Type hints** on all function parameters and return types
- **Docstrings** on all public functions (Google style)
- **Logging** via `logging` module — no `print()` statements in `src/`
- **Config-driven** — thresholds and settings go in `config.yaml`, not in code
- Format with `black` and lint with `ruff` before committing

```bash
black src/ tests/ scripts/
ruff check src/ tests/ scripts/
```

## Running Tests

```bash
python -m pytest tests/ -v
```

All tests must pass before submitting a PR. Tests use temporary SQLite databases — no external services needed.

## Adding a New Event Category

1. Add keyword patterns to `CATEGORY_KEYWORDS` in `src/processing/event_classifier.py`
2. Add a signal rule to `SIGNAL_RULES` in `src/analysis/signal_generator.py`
3. Add a test case in `tests/test_classifier.py`
4. Run `python scripts/process.py --reprocess` to reclassify existing articles

## Adding a New Data Source

1. Create a new collector in `src/ingestion/` following the pattern of `rss_collector.py`
2. Add configuration to `config.yaml`
3. Register it in `src/ingestion/scheduler.py`

## Project Structure

```
src/
├── ingestion/     # Data collection (RSS, Reddit, CoinGecko)
├── processing/    # NLP pipeline (clean, extract, classify, sentiment)
├── analysis/      # Research (impact measurement, signals, backtesting)
├── storage/       # SQLite database layer
└── dashboard/     # Streamlit UI
```

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your Python version and OS
