"""Tests for the full pipeline: ingest → process → analyze → signal."""

import os
import tempfile

import pytest

from src.analysis.event_impact import EventImpactAnalyzer
from src.analysis.signal_generator import Signal, SignalGenerator
from src.processing.event_classifier import EventClassifier
from src.processing.sentiment import SentimentAnalyzer
from src.processing.text_cleaner import clean_article
from src.storage.database import Database


@pytest.fixture
def pipeline_db():
    """Create a database with a full pipeline run on synthetic data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)

    # Insert synthetic articles
    articles = [
        ("SEC sues Binance for securities violations",
         "The SEC filed a comprehensive lawsuit against Binance exchange "
         "alleging multiple securities violations and unregistered offerings.",
         "REGULATORY"),
        ("Ethereum completes major network upgrade",
         "The Ethereum mainnet upgrade was successfully deployed. "
         "The protocol now supports EIP-4844 proto-danksharding.",
         "PROTOCOL"),
        ("Hackers drain $200 million from DeFi bridge",
         "A critical vulnerability in the bridge smart contract was exploited. "
         "$200 million in crypto assets were drained by hackers.",
         "SECURITY"),
        ("Federal Reserve signals rate cuts ahead",
         "Fed chair announced potential rate cuts as inflation cools. "
         "CPI data shows progress toward 2% target.",
         "MACRO"),
        ("Coinbase lists new Solana-based tokens",
         "Coinbase exchange announced listing of three new Solana ecosystem tokens. "
         "Trading pairs available starting next week.",
         "EXCHANGE"),
    ]

    # Insert prices for BTC and ETH
    for asset, base in [("BTC", 50000), ("ETH", 3000)]:
        prices = []
        for day in range(1, 4):
            for hour in range(24):
                price = base + (day * 500) + (hour * 10)
                prices.append({
                    "asset": asset,
                    "timestamp": f"2024-01-{day:02d}T{hour:02d}:00:00Z",
                    "open": price - 20, "high": price + 50,
                    "low": price - 50, "close": price, "volume": 500000,
                })
        db.insert_prices(prices)

    # Process each article through the pipeline
    classifier = EventClassifier()
    sentiment_analyzer = SentimentAnalyzer()

    for i, (title, content, expected_cat) in enumerate(articles):
        hour = i * 4 + 2  # Space them out
        pub_time = f"2024-01-01T{hour:02d}:00:00Z"

        art_id = db.insert_article(
            source="test",
            url=f"https://test.com/article-{i}",
            title=title,
            content=content,
            published_at=pub_time,
        )

        cleaned = clean_article(title, content)
        event = classifier.classify(cleaned["title"], cleaned["content"])
        sent = sentiment_analyzer.get_headline_sentiment(cleaned["title"], cleaned["content"])

        db.insert_event(
            article_id=art_id,
            category=event.category,
            severity=event.severity,
            summary=f"{event.summary} | sentiment={sent['label']}",
            assets_affected=["BTC", "ETH"],
            detected_at=pub_time,
        )

    yield db
    os.unlink(path)


class TestFullPipeline:
    def test_events_created_for_all_articles(self, pipeline_db):
        unprocessed = pipeline_db.get_unprocessed_articles()
        assert len(unprocessed) == 0

    def test_events_have_correct_fields(self, pipeline_db):
        events = pipeline_db.get_events(limit=100)
        assert len(events) == 5
        for e in events:
            assert e["category"] in [
                "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
                "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
            ]
            assert 1 <= e["severity"] <= 5
            assert len(e["assets_affected"]) > 0

    def test_signals_generated_for_events(self, pipeline_db):
        generator = SignalGenerator(pipeline_db)
        signals = generator.generate_all()
        # Should generate signals for non-neutral categories
        assert len(signals) > 0
        for s in signals:
            assert isinstance(s, Signal)
            assert s.asset in ("BTC", "ETH")

    def test_impact_analysis_with_pipeline_data(self, pipeline_db):
        analyzer = EventImpactAnalyzer(pipeline_db)
        results = analyzer.analyze_by_category()
        assert len(results) > 0
        # Should have data for multiple windows
        windows = {r.window_hours for r in results}
        assert len(windows) > 0

    def test_report_generation(self, pipeline_db):
        analyzer = EventImpactAnalyzer(pipeline_db)
        report = analyzer.generate_report()
        assert "EVENT IMPACT ANALYSIS" in report
        # Should contain at least one category
        assert any(cat in report for cat in [
            "REGULATORY", "SECURITY", "PROTOCOL", "MACRO", "EXCHANGE",
        ])

    def test_signal_confidence_varies_by_severity(self, pipeline_db):
        generator = SignalGenerator(pipeline_db)
        events = pipeline_db.get_events(limit=100)

        confidences_by_severity: dict[int, list[float]] = {}
        for event in events:
            signals = generator.generate_for_event(event)
            for s in signals:
                sev = event["severity"]
                confidences_by_severity.setdefault(sev, []).append(s.confidence)

        # Higher severity should generally have higher confidence
        if len(confidences_by_severity) > 1:
            severities = sorted(confidences_by_severity.keys())
            low_avg = sum(confidences_by_severity[severities[0]]) / len(confidences_by_severity[severities[0]])
            high_avg = sum(confidences_by_severity[severities[-1]]) / len(confidences_by_severity[severities[-1]])
            assert high_avg >= low_avg
