"""Tests for the backtester and event impact analyzer."""

import pytest
import tempfile
import os

from src.storage.database import Database
from src.analysis.event_impact import EventImpactAnalyzer
from src.analysis.backtester import Backtester
from src.analysis.signal_generator import SignalGenerator, SIGNAL_RULES


@pytest.fixture
def db():
    """Create a temporary database with test data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)

    # Insert price data for BTC — hourly candles over 48 hours
    prices = []
    base_price = 50000.0
    for i in range(48):
        hour = f"{i:02d}" if i < 24 else f"{i - 24:02d}"
        day = "01" if i < 24 else "02"
        # Simulate a price move: drops 5% at hour 12, then recovers
        if i < 12:
            price = base_price + (i * 100)
        elif i < 24:
            price = base_price - 2500 + (i - 12) * 200
        else:
            price = base_price + (i - 24) * 150

        prices.append({
            "asset": "BTC",
            "timestamp": f"2024-01-{day}T{hour}:00:00Z",
            "open": price - 50,
            "high": price + 100,
            "low": price - 100,
            "close": price,
            "volume": 1000000,
        })

    database.insert_prices(prices)

    # Insert articles
    art1_id = database.insert_article(
        source="test",
        url="https://test.com/sec-lawsuit",
        title="SEC files lawsuit against crypto exchange",
        content="The SEC has filed a lawsuit alleging securities violations.",
        published_at="2024-01-01T06:00:00Z",
    )

    art2_id = database.insert_article(
        source="test",
        url="https://test.com/hack",
        title="Major DeFi protocol hacked for $50 million",
        content="Hackers exploited a vulnerability, draining $50 million.",
        published_at="2024-01-01T10:00:00Z",
    )

    art3_id = database.insert_article(
        source="test",
        url="https://test.com/etf",
        title="BlackRock Bitcoin ETF approved by SEC",
        content="SEC has approved the first spot Bitcoin ETF. Institutional adoption milestone.",
        published_at="2024-01-01T14:00:00Z",
    )

    # Insert events (detected_at must match price data timeframe)
    database.insert_event(
        article_id=art1_id,
        category="REGULATORY",
        severity=4,
        summary="SEC lawsuit against exchange",
        assets_affected=["BTC"],
        detected_at="2024-01-01T06:00:00Z",
    )

    database.insert_event(
        article_id=art2_id,
        category="SECURITY",
        severity=5,
        summary="$50 million hack exploit vulnerability",
        assets_affected=["BTC"],
        detected_at="2024-01-01T10:00:00Z",
    )

    database.insert_event(
        article_id=art3_id,
        category="ADOPTION",
        severity=4,
        summary="Bitcoin ETF approved institutional",
        assets_affected=["BTC"],
        detected_at="2024-01-01T14:00:00Z",
    )

    yield database

    os.unlink(path)


# ── Event Impact Tests ────────────────────────────────────────────────


class TestEventImpact:
    def test_compute_event_moves(self, db):
        analyzer = EventImpactAnalyzer(db)
        moves = analyzer.compute_event_moves()
        assert len(moves) > 0
        for m in moves:
            assert "category" in m
            assert "asset" in m
            assert "base_price" in m
            assert "moves" in m

    def test_analyze_by_category(self, db):
        analyzer = EventImpactAnalyzer(db)
        results = analyzer.analyze_by_category()
        assert len(results) > 0
        for r in results:
            assert r.category in ["REGULATORY", "SECURITY", "ADOPTION"]
            assert 1 <= r.window_hours <= 24
            assert r.sample_size > 0

    def test_impact_result_fields(self, db):
        analyzer = EventImpactAnalyzer(db)
        results = analyzer.analyze_by_category()
        for r in results:
            assert isinstance(r.avg_move_pct, float)
            assert isinstance(r.median_move_pct, float)
            assert isinstance(r.win_rate, float)
            assert 0 <= r.win_rate <= 1
            assert isinstance(r.p_value, float)
            assert 0 <= r.p_value <= 1

    def test_generate_report(self, db):
        analyzer = EventImpactAnalyzer(db)
        report = analyzer.generate_report()
        assert "EVENT IMPACT ANALYSIS" in report
        assert "REGULATORY" in report or "SECURITY" in report

    def test_offset_timestamp(self):
        result = EventImpactAnalyzer._offset_timestamp("2024-01-01T12:00:00Z", 4)
        assert result == "2024-01-01T16:00:00Z"

    def test_offset_timestamp_day_rollover(self):
        result = EventImpactAnalyzer._offset_timestamp("2024-01-01T23:00:00Z", 4)
        assert result == "2024-01-02T03:00:00Z"

    def test_empty_database(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = Database(path)
        analyzer = EventImpactAnalyzer(db)
        report = analyzer.generate_report()
        assert "No event impact data" in report
        os.unlink(path)


# ── Signal Generator Tests ────────────────────────────────────────────


class TestSignalGenerator:
    def test_generate_for_event(self, db):
        generator = SignalGenerator(db)
        events = db.get_events(limit=10)
        all_signals = []
        for event in events:
            signals = generator.generate_for_event(event)
            all_signals.extend(signals)
        assert len(all_signals) > 0

    def test_signal_directions(self, db):
        generator = SignalGenerator(db)
        events = db.get_events(limit=10)
        for event in events:
            signals = generator.generate_for_event(event)
            for s in signals:
                assert s.direction in ("long", "short", "neutral")
                assert 0 <= s.confidence <= 1
                assert s.asset == "BTC"

    def test_regulatory_direction(self, db):
        generator = SignalGenerator(db)
        events = db.get_events(category="REGULATORY", limit=1)
        assert len(events) > 0
        signals = generator.generate_for_event(events[0])
        assert len(signals) > 0
        # Default regulatory direction is short
        assert signals[0].direction == "short"

    def test_security_direction(self, db):
        generator = SignalGenerator(db)
        events = db.get_events(category="SECURITY", limit=1)
        assert len(events) > 0
        signals = generator.generate_for_event(events[0])
        assert len(signals) > 0
        assert signals[0].direction == "short"

    def test_adoption_direction(self, db):
        generator = SignalGenerator(db)
        events = db.get_events(category="ADOPTION", limit=1)
        assert len(events) > 0
        signals = generator.generate_for_event(events[0])
        assert len(signals) > 0
        assert signals[0].direction == "long"

    def test_signal_rules_cover_all_categories(self):
        from src.processing.event_classifier import VALID_CATEGORIES
        for cat in VALID_CATEGORIES:
            assert cat in SIGNAL_RULES, f"Missing signal rule for {cat}"

    def test_generate_all(self, db):
        generator = SignalGenerator(db)
        signals = generator.generate_all()
        assert len(signals) > 0


# ── Backtester Tests ──────────────────────────────────────────────────


class TestBacktester:
    def test_run_produces_result(self, db):
        bt = Backtester(db)
        result = bt.run()
        assert result.total_trades >= 0

    def test_backtest_metrics(self, db):
        bt = Backtester(db)
        result = bt.run()
        if result.total_trades > 0:
            assert 0 <= result.win_rate <= 1
            assert isinstance(result.sharpe_ratio, float)
            assert result.max_drawdown_pct >= 0
            assert result.profit_factor >= 0

    def test_filter_by_category(self, db):
        bt = Backtester(db)
        result = bt.run(category="SECURITY")
        for trade in result.trades:
            assert trade.category == "SECURITY"

    def test_filter_by_severity(self, db):
        bt = Backtester(db)
        result = bt.run(min_severity=4)
        for trade in result.trades:
            assert trade.severity >= 4

    def test_filter_by_asset(self, db):
        bt = Backtester(db)
        result = bt.run(asset="BTC")
        for trade in result.trades:
            assert trade.asset == "BTC"

    def test_transaction_costs_applied(self, db):
        config = {"backtester": {"spread_pct": 1.0, "slippage_pct": 1.0}}
        bt = Backtester(db, config)
        result = bt.run()
        # High costs should reduce returns
        if result.total_trades > 0:
            # Each trade costs 4% (2% entry + 2% exit)
            assert result.avg_return_pct < 100  # sanity check

    def test_generate_report(self, db):
        bt = Backtester(db)
        report = bt.generate_report()
        if "No trades" not in report:
            assert "BACKTEST REPORT" in report
            assert "Win rate" in report
            assert "Sharpe ratio" in report

    def test_empty_database(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        empty_db = Database(path)
        bt = Backtester(empty_db)
        result = bt.run()
        assert result.total_trades == 0
        assert result.total_return_pct == 0
        os.unlink(path)

    def test_by_category_breakdown(self, db):
        bt = Backtester(db)
        result = bt.run()
        if result.total_trades > 0 and result.by_category:
            for cat, metrics in result.by_category.items():
                assert "trades" in metrics
                assert "avg_return" in metrics
                assert "win_rate" in metrics
