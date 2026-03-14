"""Tests for the event classifier and text processing pipeline."""

import pytest

from src.processing.event_classifier import EventClassifier, VALID_CATEGORIES
from src.processing.text_cleaner import clean_article, clean_html, normalize_whitespace, remove_urls
from src.processing.sentiment import SentimentAnalyzer


# ── Text Cleaner Tests ────────────────────────────────────────────────


class TestTextCleaner:
    def test_clean_html_strips_tags(self):
        html = "<p>Hello <b>world</b></p>"
        assert clean_html(html) == "Hello world"

    def test_clean_html_decodes_entities(self):
        html = "AT&amp;T &lt;3 crypto"
        assert "AT&T" in clean_html(html)
        assert "<3" in clean_html(html)

    def test_clean_html_removes_script_tags(self):
        html = "Before<script>alert('xss')</script>After"
        result = clean_html(html)
        assert "alert" not in result
        assert "Before" in result
        assert "After" in result

    def test_clean_html_handles_none(self):
        assert clean_html(None) == ""

    def test_clean_html_handles_empty(self):
        assert clean_html("") == ""

    def test_normalize_whitespace(self):
        text = "  hello   world  \n\n\n  test  "
        result = normalize_whitespace(text)
        assert "  " not in result
        assert result == "hello world\ntest"

    def test_remove_urls(self):
        text = "Check https://example.com/page for details and http://test.org too"
        result = remove_urls(text)
        assert "https://" not in result
        assert "http://" not in result
        assert "details" in result

    def test_clean_article_pipeline(self):
        result = clean_article(
            title="<b>Breaking</b> News",
            content="<p>Bitcoin surges to $50k. Visit https://example.com</p>",
        )
        assert result["title"] == "Breaking News"
        assert "https://" not in result["content"]
        assert "Bitcoin surges" in result["content"]


# ── Event Classifier Tests ────────────────────────────────────────────


class TestEventClassifier:
    @pytest.fixture
    def classifier(self):
        return EventClassifier()

    def test_regulatory_classification(self, classifier):
        event = classifier.classify(
            "SEC files lawsuit against major crypto exchange",
            "The Securities and Exchange Commission has filed a lawsuit "
            "alleging violations of securities regulations.",
        )
        assert event.category == "REGULATORY"
        assert event.severity >= 2

    def test_exchange_classification(self, classifier):
        event = classifier.classify(
            "Coinbase announces new token listing",
            "Coinbase will list three new tokens starting next week. "
            "Trading pairs will be available on the exchange.",
        )
        assert event.category == "EXCHANGE"

    def test_security_classification(self, classifier):
        event = classifier.classify(
            "DeFi protocol hacked for $100 million",
            "Hackers exploited a vulnerability in the smart contract, "
            "draining $100 million from the protocol.",
        )
        assert event.category == "SECURITY"
        assert event.severity >= 3

    def test_macro_classification(self, classifier):
        event = classifier.classify(
            "Federal Reserve raises interest rates by 25 basis points",
            "The Fed announced a rate hike amid persistent inflation. "
            "CPI data came in above expectations.",
        )
        assert event.category == "MACRO"

    def test_adoption_classification(self, classifier):
        event = classifier.classify(
            "BlackRock Bitcoin ETF sees record inflows",
            "Institutional investors poured billions into the spot Bitcoin ETF. "
            "BlackRock and Fidelity custody services reported massive adoption.",
        )
        assert event.category == "ADOPTION"

    def test_protocol_classification(self, classifier):
        event = classifier.classify(
            "Ethereum mainnet upgrade scheduled for next month",
            "The protocol upgrade includes EIP-4844 for sharding. "
            "Governance vote passed with 95% approval.",
        )
        assert event.category == "PROTOCOL"

    def test_sentiment_classification(self, classifier):
        event = classifier.classify(
            "Bitcoin reaches all-time high amid bullish sentiment",
            "The market is euphoric as BTC moons past $100k. "
            "Influencers are calling for even higher targets.",
        )
        assert event.category == "SENTIMENT"

    def test_market_structure_classification(self, classifier):
        event = classifier.classify(
            "Massive liquidations hit crypto futures market",
            "Over $500 million in liquidations as whale movements "
            "triggered a short squeeze. Open interest dropped sharply.",
        )
        assert event.category == "MARKET_STRUCTURE"

    def test_severity_boosters(self, classifier):
        # Billion-dollar events should get higher severity
        event = classifier.classify(
            "Breaking: $2 billion hack rocks crypto",
            "Unprecedented security breach drains $2 billion from protocol. "
            "Emergency measures enacted.",
        )
        assert event.severity >= 4

    def test_low_confidence_fallback(self, classifier):
        # Unrelated text defaults to SENTIMENT with low confidence
        event = classifier.classify(
            "Weekend weather forecast",
            "Sunny skies expected across the country.",
        )
        assert event.confidence < 0.3

    def test_valid_category_always_returned(self, classifier):
        event = classifier.classify("random headline", "random content")
        assert event.category in VALID_CATEGORIES

    def test_severity_bounds(self, classifier):
        event = classifier.classify("test", "test content")
        assert 1 <= event.severity <= 5

    def test_classify_batch(self, classifier):
        articles = [
            {"title": "SEC sues exchange", "content": "Regulatory action taken"},
            {"title": "New token listed", "content": "Exchange listing announced"},
        ]
        results = classifier.classify_batch(articles)
        assert len(results) == 2
        assert results[0][1].category == "REGULATORY"
        assert results[1][1].category == "EXCHANGE"


# ── Sentiment Analyzer Tests ─────────────────────────────────────────


class TestSentimentAnalyzer:
    @pytest.fixture
    def analyzer(self):
        config = {
            "nlp": {
                "sentiment": {
                    "crypto_lexicon": {
                        "moon": 2.5,
                        "rug": -3.5,
                        "bullish": 2.5,
                        "bearish": -2.5,
                    }
                }
            }
        }
        return SentimentAnalyzer(config)

    def test_positive_sentiment(self, analyzer):
        result = analyzer.analyze("Great news! Bitcoin adoption is growing rapidly.")
        assert result["compound"] > 0
        assert result["label"] == "positive"

    def test_negative_sentiment(self, analyzer):
        result = analyzer.analyze("Terrible hack drains millions. Investors devastated.")
        assert result["compound"] < 0
        assert result["label"] == "negative"

    def test_neutral_sentiment(self, analyzer):
        result = analyzer.analyze("The price of bitcoin is currently at $50,000.")
        assert result["label"] in ("neutral", "positive")  # VADER may lean slightly positive

    def test_crypto_lexicon_positive(self, analyzer):
        result = analyzer.analyze("BTC is mooning! Very bullish outlook.")
        assert result["compound"] > 0

    def test_crypto_lexicon_negative(self, analyzer):
        result = analyzer.analyze("This looks like a rug pull. Very bearish.")
        assert result["compound"] < 0

    def test_result_keys(self, analyzer):
        result = analyzer.analyze("test text")
        assert "compound" in result
        assert "positive" in result
        assert "negative" in result
        assert "neutral" in result
        assert "label" in result

    def test_headline_sentiment(self, analyzer):
        result = analyzer.get_headline_sentiment(
            "Catastrophic crash wipes out billions",
            "Markets see a small correction.",
        )
        # Title is weighted more heavily, should be negative
        assert result["compound"] < 0

    def test_analyze_batch(self, analyzer):
        texts = ["Great news!", "Terrible disaster!", "Normal update."]
        results = analyzer.analyze_batch(texts)
        assert len(results) == 3
        assert results[0]["compound"] > results[1]["compound"]
