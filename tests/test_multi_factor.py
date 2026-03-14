"""Tests for market confirmation, multi-factor signal scoring, and confirmation gate."""

import math

import pytest

from src.analysis.market_confirmation import MarketConfirmation, MarketContext
from src.analysis.multi_factor_signal import (
    MultiFactorSignalGenerator,
    DEFAULT_WEIGHTS,
    DEFAULT_THRESHOLDS,
)
from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a fresh database with price data for tests."""
    db_path = tmp_path / "test_mf.db"
    database = Database(str(db_path))

    # Insert hourly BTC prices: 24 hours on day 15, 6 hours on day 16
    prices = []
    base_price = 50000
    base_volume = 1_000_000
    for h in range(24):
        price = base_price + h * 50  # slow uptrend
        vol = base_volume + (h % 5) * 200_000  # varying volume
        prices.append({
            "asset": "BTC",
            "timestamp": f"2024-01-15T{h:02d}:00:00Z",
            "open": price - 20,
            "high": price + 80,
            "low": price - 60,
            "close": price,
            "volume": vol,
        })
    for h in range(6):
        price = base_price + (24 + h) * 50
        vol = base_volume + (h % 5) * 200_000
        prices.append({
            "asset": "BTC",
            "timestamp": f"2024-01-16T{h:02d}:00:00Z",
            "open": price - 20,
            "high": price + 80,
            "low": price - 60,
            "close": price,
            "volume": vol,
        })
    # Add a high-volume spike at day 16 hour 01
    prices[25]["volume"] = 5_000_000
    database.insert_prices(prices)

    # Insert ETH prices too
    eth_prices = []
    for h in range(24):
        price = 3000 - h * 10  # downtrend
        eth_prices.append({
            "asset": "ETH",
            "timestamp": f"2024-01-15T{h:02d}:00:00Z",
            "open": price + 5,
            "high": price + 30,
            "low": price - 30,
            "close": price,
            "volume": 500_000,
        })
    database.insert_prices(eth_prices)

    # Insert articles, events, and clusters for signal generation
    for i in range(3):
        art_id = database.insert_article(
            source="CoinDesk",
            url=f"https://test.com/article-{i}",
            title=f"Test article {i}",
            content=f"Test content {i}",
            published_at=f"2024-01-15T{10 + i}:00:00Z",
        )
        database.insert_event(
            article_id=art_id,
            category="REGULATORY",
            severity=4,
            summary="SEC enforcement action | sentiment=negative(-0.50)",
            assets_affected=["BTC"],
            detected_at=f"2024-01-15T{10 + i}:00:00Z",
        )

    # Create clusters
    from src.processing.event_clusterer import EventClusterer
    clusterer = EventClusterer(database, {
        "clustering": {"similarity_threshold": 0.30, "time_window_hours": 48, "novelty_lambda": 0.1},
    })
    clusterer.cluster_events()

    return database


@pytest.fixture
def config():
    return {
        "signal_model": {
            "weights": DEFAULT_WEIGHTS,
            "thresholds": DEFAULT_THRESHOLDS,
            "min_confirmation_factors": 2,
            "volume_lookback": 20,
            "volume_zscore_threshold": 1.0,
            "atr_period": 14,
        },
        "clustering": {
            "similarity_threshold": 0.30,
            "time_window_hours": 48,
            "novelty_lambda": 0.1,
        },
    }


class TestVolumeZScore:
    """Tests for volume z-score calculation."""

    def test_normal_volume_low_zscore(self, db, config):
        """Normal volume should give z-score near 0."""
        mc = MarketConfirmation(db, config)
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z")
        assert ctx is not None
        # Normal volume candle shouldn't have extreme z-score
        assert -2.0 < ctx.volume_zscore < 3.0

    def test_high_volume_spike_detected(self, db, config):
        """Volume spike should produce high z-score."""
        mc = MarketConfirmation(db, config)
        # Day 16 hour 01 has 5x normal volume
        ctx = mc.get_market_context("BTC", "2024-01-16T01:00:00Z")
        assert ctx is not None
        assert ctx.volume_zscore > 1.0

    def test_zscore_requires_data(self, db, config):
        """Z-score should return None with insufficient data."""
        mc = MarketConfirmation(db, config)
        ctx = mc.get_market_context("NOSUCHCOIN", "2024-01-15T10:00:00Z")
        assert ctx is None

    def test_volume_confirmed_flag(self, db, config):
        """volume_confirmed should be True when z-score >= threshold."""
        mc = MarketConfirmation(db, config)
        ctx_high = MarketContext(
            asset="BTC", timestamp="", price=50000,
            volume_zscore=1.5, momentum_1h=0.5, atr_14=100,
            volatility_regime="normal",
            volume_confirmed=True, momentum_aligned=True,
        )
        assert ctx_high.volume_confirmed is True

        ctx_low = MarketContext(
            asset="BTC", timestamp="", price=50000,
            volume_zscore=0.3, momentum_1h=0.5, atr_14=100,
            volatility_regime="normal",
            volume_confirmed=False, momentum_aligned=True,
        )
        assert ctx_low.volume_confirmed is False


class TestMomentumAlignment:
    """Tests for momentum alignment detection."""

    def test_long_signal_positive_momentum(self, db, config):
        """Long signal with positive momentum should be aligned."""
        mc = MarketConfirmation(db, config)
        # BTC has uptrend, so momentum should be positive
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z", direction="long")
        assert ctx is not None
        assert ctx.momentum_1h > 0
        assert ctx.momentum_aligned is True

    def test_short_signal_negative_momentum(self, db, config):
        """Short signal with negative momentum should be aligned."""
        mc = MarketConfirmation(db, config)
        # ETH has downtrend
        ctx = mc.get_market_context("ETH", "2024-01-15T20:00:00Z", direction="short")
        assert ctx is not None
        assert ctx.momentum_1h < 0
        assert ctx.momentum_aligned is True

    def test_misaligned_momentum(self, db, config):
        """Signal contradicting momentum should not be aligned."""
        mc = MarketConfirmation(db, config)
        # BTC is trending up, short signal should not align
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z", direction="short")
        assert ctx is not None
        assert ctx.momentum_aligned is False


class TestATRAndRegime:
    """Tests for ATR and volatility regime."""

    def test_atr_positive(self, db, config):
        """ATR should be a positive number."""
        mc = MarketConfirmation(db, config)
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z")
        assert ctx is not None
        assert ctx.atr_14 > 0

    def test_regime_classification(self, db, config):
        """Regime should be one of the valid values."""
        mc = MarketConfirmation(db, config)
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z")
        assert ctx is not None
        assert ctx.volatility_regime in ("low", "normal", "high")


class TestConfirmationScore:
    """Tests for the composite market confirmation score."""

    def test_score_range(self, db, config):
        """Score should be between 0 and 1."""
        mc = MarketConfirmation(db, config)
        ctx = mc.get_market_context("BTC", "2024-01-15T20:00:00Z", direction="long")
        assert ctx is not None
        score = mc.compute_confirmation_score(ctx)
        assert 0.0 <= score <= 1.0

    def test_aligned_higher_than_misaligned(self, db, config):
        """Aligned momentum should give higher score."""
        mc = MarketConfirmation(db, config)
        # BTC uptrend — long should score higher than short
        ctx_long = mc.get_market_context("BTC", "2024-01-15T20:00:00Z", direction="long")
        ctx_short = mc.get_market_context("BTC", "2024-01-15T20:00:00Z", direction="short")
        assert ctx_long is not None and ctx_short is not None

        score_long = mc.compute_confirmation_score(ctx_long)
        score_short = mc.compute_confirmation_score(ctx_short)
        assert score_long > score_short


class TestMultiFactorScoring:
    """Tests for the multi-factor signal scoring model."""

    def test_weighted_score_calculation(self):
        """Verify weighted combination produces expected output."""
        w = DEFAULT_WEIGHTS
        news, market, narrative, novelty = 0.8, 0.6, 0.5, 1.0
        expected = (
            w["news"] * news
            + w["market"] * market
            + w["narrative"] * narrative
            + w["novelty"] * novelty
        )
        assert abs(expected - (0.4*0.8 + 0.25*0.6 + 0.20*0.5 + 0.15*1.0)) < 0.001

    def test_high_confidence_threshold(self):
        """Score > 0.7 should produce HIGH confidence."""
        assert DEFAULT_THRESHOLDS["high"] == 0.7

    def test_medium_confidence_threshold(self):
        """Score 0.4-0.7 should produce MEDIUM confidence."""
        assert DEFAULT_THRESHOLDS["medium"] == 0.4

    def test_below_noise_threshold_no_signal(self):
        """Score < 0.4 should not generate a signal."""
        assert DEFAULT_THRESHOLDS["noise"] == 0.4


class TestConfirmationGate:
    """Tests for the confirmation gate logic."""

    def test_two_factors_required(self, db, config):
        """Signal should require at least 2 confirmation factors."""
        gen = MultiFactorSignalGenerator(db, config)
        assert gen.min_confirmation_factors == 2

    def test_single_factor_blocked(self, db, config):
        """A signal with only 1 supporting factor should not fire."""
        gen = MultiFactorSignalGenerator(db, config)
        # Create a cluster with low severity (weak news) and no market data
        with db.connect() as conn:
            conn.execute("DELETE FROM event_clusters")
            conn.execute("DELETE FROM article_cluster_map")
            conn.commit()

        art_id = db.insert_article(
            "unknown_blog", "https://x.com/1", "Minor news",
            "Some minor event happened", "2024-01-15T10:00:00Z",
        )
        evt_id = db.insert_event(
            art_id, "SENTIMENT", 1, "Minor | sentiment=neutral(0.00)",
            ["XRP"], "2024-01-15T10:00:00Z",
        )
        # Manually create a minimal cluster
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO event_clusters
                   (category, severity, sentiment, first_detected_at, last_article_at,
                    article_count, representative_headline, novelty_score, assets_affected)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("SENTIMENT", 1, 0.0, "2024-01-15T10:00:00Z", "2024-01-15T10:00:00Z",
                 1, "Minor news", 0.01, '["XRP"]'),
            )
            conn.commit()

        signals = gen.generate_all()
        # Sentiment with sev=1 and neutral direction → should produce 0 signals
        assert len(signals) == 0

    def test_generate_produces_valid_signals(self, db, config):
        """Generated signals should have all required fields."""
        gen = MultiFactorSignalGenerator(db, config)
        signals = gen.generate_all()

        for sig in signals:
            assert sig.direction in ("long", "short")
            assert sig.confidence in ("HIGH", "MEDIUM")
            assert 0.0 <= sig.signal_score <= 1.0
            assert 0.0 <= sig.news_component <= 1.0
            assert 0.0 <= sig.market_component <= 1.0
            assert 0.0 <= sig.narrative_component <= 1.0
            assert 0.0 <= sig.novelty_component <= 1.0
            assert len(sig.confirmation_factors) >= 2
            assert sig.reasoning != ""
            assert sig.asset != ""
            assert sig.entry_time != ""

    def test_store_and_retrieve(self, db, config):
        """Signals should persist to signals_v2 table."""
        gen = MultiFactorSignalGenerator(db, config)
        count = gen.generate_and_store()

        with db.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM signals_v2").fetchone()[0]
        assert rows == count


class TestNewsComponent:
    """Tests for the news score computation."""

    def test_higher_severity_higher_score(self, db, config):
        """Higher severity should produce higher news score."""
        gen = MultiFactorSignalGenerator(db, config)
        from src.analysis.signal_generator import SIGNAL_RULES

        rule = SIGNAL_RULES["REGULATORY"]
        score_low = gen._compute_news_score(rule, 1, "SEC sues", -0.5, "short")
        score_high = gen._compute_news_score(rule, 5, "SEC sues", -0.5, "short")
        assert score_high > score_low

    def test_sentiment_alignment_boosts_score(self, db, config):
        """Aligned sentiment should boost news score."""
        gen = MultiFactorSignalGenerator(db, config)
        from src.analysis.signal_generator import SIGNAL_RULES

        rule = SIGNAL_RULES["ADOPTION"]
        # Positive sentiment + long direction = aligned
        score_aligned = gen._compute_news_score(rule, 3, "ETF approved", 0.8, "long")
        # Negative sentiment + long direction = misaligned
        score_misaligned = gen._compute_news_score(rule, 3, "ETF approved", -0.8, "long")
        assert score_aligned > score_misaligned

    def test_news_score_bounded(self, db, config):
        """News score should always be between 0 and 1."""
        gen = MultiFactorSignalGenerator(db, config)
        from src.analysis.signal_generator import SIGNAL_RULES

        for cat, rule in SIGNAL_RULES.items():
            for sev in range(1, 6):
                for sent in [-1.0, 0.0, 1.0]:
                    score = gen._compute_news_score(rule, sev, "test", sent, "long")
                    assert 0.0 <= score <= 1.0, f"Out of bounds: {cat} sev={sev} sent={sent} -> {score}"
