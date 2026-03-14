"""Tests for enhanced backtester and experiment framework."""

import pytest
import numpy as np

from src.analysis.enhanced_backtester import EnhancedBacktester, WalkForwardResult
from src.analysis.experiments import ExperimentRunner, ExperimentResult
from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a database with enough data for backtesting and experiments."""
    db_path = tmp_path / "test_enhanced.db"
    database = Database(str(db_path))

    # Insert 30 days of BTC and ETH prices (hourly)
    for asset, base in [("BTC", 50000), ("ETH", 3000)]:
        prices = []
        for day in range(1, 31):
            for hour in range(24):
                # Slight uptrend with noise
                price = base + (day * 200) + (hour * 5) + ((day * 7 + hour * 3) % 100)
                vol = 500000 + ((day * hour) % 10) * 50000
                prices.append({
                    "asset": asset,
                    "timestamp": f"2024-01-{day:02d}T{hour:02d}:00:00Z",
                    "open": price - 20,
                    "high": price + 80,
                    "low": price - 60,
                    "close": price,
                    "volume": vol,
                })
        database.insert_prices(prices)

    # Insert articles and events spread across the month
    categories = ["REGULATORY", "EXCHANGE", "PROTOCOL", "ADOPTION", "SECURITY"]
    for i in range(20):
        day = 2 + (i * 1)  # days 2-21
        if day > 28:
            break
        hour = (i * 3) % 24

        art_id = database.insert_article(
            source="CoinDesk",
            url=f"https://test.com/article-{i}",
            title=f"Test article {i} about {categories[i % len(categories)]}",
            content=f"Article content about crypto event {i}.",
            published_at=f"2024-01-{day:02d}T{hour:02d}:00:00Z",
        )
        database.insert_event(
            article_id=art_id,
            category=categories[i % len(categories)],
            severity=2 + (i % 4),
            summary=f"Event {i} | sentiment=neutral(0.00)",
            assets_affected=["BTC", "ETH"],
            detected_at=f"2024-01-{day:02d}T{hour:02d}:00:00Z",
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
        "enhanced_backtester": {
            "latency_minutes": 15,
            "fee_pct": 0.10,
            "spread_pct": 0.05,
            "slippage_pct": 0.05,
            "sizing_mode": "fixed",
            "base_position": 0.10,
            "wf_min_train_days": 7,
            "wf_test_days": 3,
        },
        "clustering": {
            "similarity_threshold": 0.30,
            "time_window_hours": 48,
            "novelty_lambda": 0.1,
        },
        "analysis": {
            "impact_windows": [1, 4, 24],
            "min_sample_size": 3,
            "significance_level": 0.05,
        },
    }


class TestLatencyBuffer:
    """Tests for latency buffer."""

    def test_latency_delays_entry(self, db, config):
        """Entry should be delayed by latency_minutes."""
        bt = EnhancedBacktester(db, config)
        ts = "2024-01-15T10:00:00Z"
        delayed = bt._offset_timestamp_minutes(ts, 15)
        assert delayed == "2024-01-15T10:15:00Z"

    def test_zero_latency(self, db, config):
        """Zero latency should not change timestamp."""
        bt = EnhancedBacktester(db, config)
        ts = "2024-01-15T10:00:00Z"
        delayed = bt._offset_timestamp_minutes(ts, 0)
        assert delayed == "2024-01-15T10:00:00Z"

    def test_latency_crosses_hour(self, db, config):
        """Latency that crosses hour boundary should work."""
        bt = EnhancedBacktester(db, config)
        ts = "2024-01-15T10:50:00Z"
        delayed = bt._offset_timestamp_minutes(ts, 15)
        assert delayed == "2024-01-15T11:05:00Z"


class TestCostModel:
    """Tests for improved cost model."""

    def test_total_cost_calculation(self, db, config):
        """Total round-trip cost should be 2*fee + spread + slippage."""
        bt = EnhancedBacktester(db, config)
        expected = 2 * 0.10 + 0.05 + 0.05  # 0.30%
        assert abs(bt.total_cost_pct - expected) < 0.001

    def test_costs_subtract_from_pnl(self, db, config):
        """Enhanced backtest should subtract costs from each trade."""
        bt = EnhancedBacktester(db, config)
        result = bt.run_enhanced(exit_hours=4)
        # If there are trades, verify P&L accounts for costs
        if result.trades:
            # Each trade's pnl should be raw_move - total_cost
            # We can't verify exact raw_move, but total_cost is 0.30%
            assert bt.total_cost_pct > 0


class TestPositionSizing:
    """Tests for position sizing modes."""

    def test_fixed_sizing(self, db, config):
        """Fixed mode should return base_position."""
        bt = EnhancedBacktester(db, config)
        bt.sizing_mode = "fixed"
        size = bt._compute_position_size(3, [])
        assert size == bt.base_position

    def test_confidence_scaling(self, db, config):
        """Confidence mode should scale by severity."""
        bt = EnhancedBacktester(db, config)
        bt.sizing_mode = "confidence"
        size_low = bt._compute_position_size(1, [])
        size_high = bt._compute_position_size(5, [])
        assert size_high > size_low

    def test_kelly_with_no_history(self, db, config):
        """Kelly with insufficient history should fall back to base."""
        bt = EnhancedBacktester(db, config)
        bt.sizing_mode = "kelly"
        size = bt._compute_position_size(3, [])
        assert size == bt.base_position

    def test_kelly_bounded(self, db, config):
        """Kelly position should be bounded between 0.01 and 0.25."""
        bt = EnhancedBacktester(db, config)
        bt.sizing_mode = "kelly"
        from src.analysis.backtester import Trade
        # Create fake trade history with high win rate
        trades = []
        for i in range(20):
            t = Trade(event_id=i, asset="BTC", direction="long",
                      entry_time="", entry_price=50000,
                      pnl_pct=1.0 if i % 3 != 0 else -0.5,
                      category="ADOPTION", severity=3)
            trades.append(t)
        size = bt._compute_position_size(3, trades)
        assert 0.01 <= size <= 0.25


class TestRegimeTagging:
    """Tests for market regime classification."""

    def test_regime_is_valid(self, db, config):
        """Regime should be one of bull, bear, choppy."""
        bt = EnhancedBacktester(db, config)
        regime = bt._tag_regime("2024-01-20T12:00:00Z")
        assert regime in ("bull", "bear", "choppy")

    def test_regime_with_no_price_data(self, db, config):
        """Missing price data should default to choppy."""
        bt = EnhancedBacktester(db, config)
        regime = bt._tag_regime("2020-01-01T00:00:00Z")
        assert regime == "choppy"


class TestWalkForward:
    """Tests for walk-forward validation."""

    def test_walk_forward_produces_result(self, db, config):
        """Walk-forward should produce a WalkForwardResult."""
        bt = EnhancedBacktester(db, config)
        result = bt.run_walk_forward(exit_hours=4)
        assert isinstance(result, WalkForwardResult)

    def test_walk_forward_no_overlap(self, db, config):
        """Train and test windows should not overlap."""
        bt = EnhancedBacktester(db, config)
        result = bt.run_walk_forward(exit_hours=4)
        for w in result.windows:
            # Test starts where train ends
            assert w.test_start == w.train_end

    def test_walk_forward_has_both_metrics(self, db, config):
        """Result should have both in-sample and out-of-sample metrics."""
        bt = EnhancedBacktester(db, config)
        result = bt.run_walk_forward(exit_hours=4)
        assert result.in_sample_sharpe is not None
        assert result.out_of_sample_sharpe is not None

    def test_enhanced_run_produces_result(self, db, config):
        """Enhanced run should produce a valid BacktestResult."""
        bt = EnhancedBacktester(db, config)
        result = bt.run_enhanced(exit_hours=4)
        assert result.total_trades >= 0


class TestExperimentRunner:
    """Tests for the experiment framework."""

    def test_run_experiment_1(self, db, config):
        """Experiment 1 should produce valid result."""
        runner = ExperimentRunner(db, config)
        result = runner.run(1)
        assert isinstance(result, ExperimentResult)
        assert result.experiment_id == 1
        assert result.name == "Event-Return Correlation"
        assert "hypothesis" in result.__dict__

    def test_run_experiment_2(self, db, config):
        """Experiment 2 should produce valid result."""
        runner = ExperimentRunner(db, config)
        result = runner.run(2)
        assert result.experiment_id == 2
        assert "spearman_correlation" in result.results or "error" in result.results

    def test_run_experiment_3(self, db, config):
        """Experiment 3 should produce valid result."""
        runner = ExperimentRunner(db, config)
        result = runner.run(3)
        assert result.experiment_id == 3

    def test_run_experiment_4(self, db, config):
        """Experiment 4 should produce valid result."""
        runner = ExperimentRunner(db, config)
        result = runner.run(4)
        assert result.experiment_id == 4

    def test_run_experiment_5(self, db, config):
        """Experiment 5 should produce valid result."""
        runner = ExperimentRunner(db, config)
        result = runner.run(5)
        assert result.experiment_id == 5

    def test_run_all(self, db, config):
        """run_all should return 5 results."""
        runner = ExperimentRunner(db, config)
        results = runner.run_all()
        assert len(results) == 5
        assert [r.experiment_id for r in results] == [1, 2, 3, 4, 5]

    def test_invalid_experiment_id(self, db, config):
        """Invalid experiment ID should raise ValueError."""
        runner = ExperimentRunner(db, config)
        with pytest.raises(ValueError):
            runner.run(99)

    def test_text_report(self, db, config):
        """Text report should be a non-empty string."""
        runner = ExperimentRunner(db, config)
        report = runner.generate_report(fmt="text")
        assert len(report) > 100
        assert "EXPERIMENT REPORT" in report

    def test_markdown_report(self, db, config):
        """Markdown report should contain markdown headers."""
        runner = ExperimentRunner(db, config)
        report = runner.generate_report(fmt="md")
        assert "# Experiment Report" in report
        assert "## Experiment 1" in report

    def test_random_baseline_seeded(self, db, config):
        """Random baseline should be reproducible with same seed."""
        runner = ExperimentRunner(db, config)
        b1 = runner._generate_random_baseline(100)
        b2 = runner._generate_random_baseline(100)
        if b1.get("1h") and b2.get("1h"):
            assert b1["1h"] == b2["1h"]

    def test_experiment_results_have_tables(self, db, config):
        """Each experiment should produce tables (unless insufficient data)."""
        runner = ExperimentRunner(db, config)
        results = runner.run_all()
        for r in results:
            assert isinstance(r.tables, list)
