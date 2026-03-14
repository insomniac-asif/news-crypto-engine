"""Tests for event clustering, source credibility, and novelty decay."""

import math
import os
import tempfile

import pytest

from src.processing.event_clusterer import EventClusterer, compute_novelty
from src.processing.source_credibility import SourceCredibility
from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a fresh database for each test."""
    db_path = tmp_path / "test_cluster.db"
    return Database(str(db_path))


@pytest.fixture
def config():
    """Minimal config for clustering tests."""
    return {
        "clustering": {
            "similarity_threshold": 0.30,
            "time_window_hours": 48,
            "novelty_lambda": 0.1,
        },
        "source_credibility": {
            "tier_1": {"weight": 1.0, "sources": ["Reuters", "Bloomberg"]},
            "tier_2": {"weight": 0.7, "sources": ["CoinDesk", "The Block"]},
            "tier_3": {"weight": 0.4, "sources": ["reddit/cryptocurrency"]},
            "tier_4": {"weight": 0.1, "sources": []},
        },
    }


def _insert_article_and_event(db, source, url, title, content, pub_time, category, severity, assets=None):
    """Helper to insert an article and its classified event."""
    art_id = db.insert_article(source, url, title, content, pub_time)
    evt_id = db.insert_event(
        article_id=art_id,
        category=category,
        severity=severity,
        summary=f"{title} | sentiment=neutral(0.00)",
        assets_affected=assets or ["BTC"],
        detected_at=pub_time,
    )
    return art_id, evt_id


class TestEventClustering:
    """Tests for the core clustering logic."""

    # Shared realistic article bodies for SEC/Binance tests.
    # Content needs enough overlapping terms for the fallback similarity
    # to exceed the 0.30 threshold when sklearn is not available.
    _SEC_CONTENT_A = (
        "The Securities and Exchange Commission filed a major enforcement "
        "action and lawsuit against cryptocurrency exchange Binance and its "
        "CEO Changpeng Zhao. The SEC alleges Binance operated an unregistered "
        "securities exchange and offered unregistered securities to investors. "
        "The complaint details multiple violations of federal securities laws "
        "and seeks injunctive relief and monetary penalties."
    )
    _SEC_CONTENT_B = (
        "The SEC has filed a comprehensive enforcement action and lawsuit "
        "against crypto exchange Binance alleging the platform operated as "
        "an unregistered securities exchange. The complaint accuses Binance "
        "CEO Changpeng Zhao of multiple violations of federal securities "
        "laws including offering unregistered securities to US investors. "
        "Regulators are seeking injunctive relief and significant penalties."
    )

    def test_similar_articles_same_cluster(self, db, config):
        """Two very similar articles about the same event should cluster together."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            self._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 4, ["BTC", "BNB"],
        )
        _insert_article_and_event(
            db, "The Block", "https://b.com/1",
            "SEC files lawsuit against Binance for securities law violations",
            self._SEC_CONTENT_B,
            "2024-01-15T11:00:00Z", "REGULATORY", 4, ["BTC", "BNB"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 1
        assert clusters[0].article_count == 2
        assert clusters[0].category == "REGULATORY"

    def test_dissimilar_articles_separate_clusters(self, db, config):
        """Articles about different events should stay in separate clusters."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            self._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 4, ["BNB"],
        )
        _insert_article_and_event(
            db, "CoinDesk", "https://b.com/1",
            "Ethereum completes major network upgrade successfully",
            "The Ethereum mainnet upgrade was deployed successfully today. "
            "EIP-4844 proto-danksharding is now live on the network after "
            "months of extensive testing across all testnets. Developers "
            "celebrated the milestone as a major step toward scalability.",
            "2024-01-15T11:00:00Z", "PROTOCOL", 3, ["ETH"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 2
        assert all(c.article_count == 1 for c in clusters)

    def test_different_categories_not_clustered(self, db, config):
        """Even similar text should not cluster if categories differ."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "Bitcoin price surges on market momentum",
            "Bitcoin price has surged amid strong market momentum and "
            "buying pressure from retail and institutional investors.",
            "2024-01-15T10:00:00Z", "SENTIMENT", 2, ["BTC"],
        )
        _insert_article_and_event(
            db, "The Block", "https://b.com/1",
            "Bitcoin price surges on institutional adoption",
            "Bitcoin price has surged as institutional buyers adopt "
            "the asset amid growing market momentum and demand.",
            "2024-01-15T10:30:00Z", "ADOPTION", 3, ["BTC"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 2

    def test_time_window_enforcement(self, db, config):
        """Articles more than 48h apart should not cluster even if similar."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            self._SEC_CONTENT_A,
            "2024-01-10T10:00:00Z", "REGULATORY", 4, ["BNB"],
        )
        _insert_article_and_event(
            db, "The Block", "https://b.com/1",
            "SEC sues Binance for securities violations update",
            self._SEC_CONTENT_B,
            "2024-01-15T10:00:00Z", "REGULATORY", 4, ["BNB"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        # 5 days apart > 48h window → separate clusters
        assert len(clusters) == 2

    def test_cluster_severity_is_max(self, db, config):
        """Cluster severity should be the max across all articles."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            self._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 3, ["BNB"],
        )
        _insert_article_and_event(
            db, "Reuters", "https://b.com/1",
            "SEC sues Binance for major securities violations — breaking",
            self._SEC_CONTENT_B,
            "2024-01-15T10:30:00Z", "REGULATORY", 5, ["BNB"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 1
        assert clusters[0].severity == 5

    def test_cluster_representative_headline_from_best_source(self, db, config):
        """Representative headline should come from highest-credibility source."""
        _insert_article_and_event(
            db, "reddit/cryptocurrency", "https://reddit.com/1",
            "SEC files lawsuit against Binance for securities violations",
            self._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 3, ["BNB"],
        )
        _insert_article_and_event(
            db, "Reuters", "https://reuters.com/1",
            "SEC files enforcement action against Binance for violations",
            self._SEC_CONTENT_B,
            "2024-01-15T10:30:00Z", "REGULATORY", 4, ["BNB"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 1
        # Reuters (tier 1) should win over reddit (tier 3)
        assert "enforcement action" in clusters[0].representative_headline

    def test_cluster_assets_are_union(self, db, config):
        """Cluster assets should be the union of all article assets."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance — BNB and BTC affected",
            self._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 4, ["BTC", "BNB"],
        )
        _insert_article_and_event(
            db, "The Block", "https://b.com/1",
            "SEC sues Binance — ETH also at risk",
            self._SEC_CONTENT_B,
            "2024-01-15T10:30:00Z", "REGULATORY", 4, ["BNB", "ETH"],
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 1
        assert set(clusters[0].assets_affected) == {"BTC", "BNB", "ETH"}

    def test_already_clustered_events_not_reclustered(self, db, config):
        """Running clustering twice should not duplicate clusters."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            "The SEC filed a lawsuit against Binance.",
            "2024-01-15T10:00:00Z", "REGULATORY", 4,
        )

        clusterer = EventClusterer(db, config)
        clusters1 = clusterer.cluster_events()
        clusters2 = clusterer.cluster_events()

        assert len(clusters1) == 1
        assert len(clusters2) == 0  # already clustered

    def test_cluster_stored_in_db(self, db, config):
        """Clusters should be persisted to the database."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for securities violations",
            "The SEC filed a lawsuit against Binance.",
            "2024-01-15T10:00:00Z", "REGULATORY", 4,
        )

        clusterer = EventClusterer(db, config)
        clusterer.cluster_events()

        # Query DB directly
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM event_clusters").fetchone()[0]
            assert count == 1

            acm_count = conn.execute("SELECT COUNT(*) FROM article_cluster_map").fetchone()[0]
            assert acm_count == 1

    def test_get_clusters_with_filters(self, db, config):
        """get_clusters should support category and severity filters."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance", "SEC lawsuit.",
            "2024-01-15T10:00:00Z", "REGULATORY", 4,
        )
        _insert_article_and_event(
            db, "CoinDesk", "https://b.com/1",
            "Ethereum upgrade", "Ethereum upgraded.",
            "2024-01-15T11:00:00Z", "PROTOCOL", 2,
        )

        clusterer = EventClusterer(db, config)
        clusterer.cluster_events()

        all_clusters = clusterer.get_clusters()
        assert len(all_clusters) == 2

        reg_only = clusterer.get_clusters(category="REGULATORY")
        assert len(reg_only) == 1

        high_sev = clusterer.get_clusters(min_severity=4)
        assert len(high_sev) == 1

    def test_get_cluster_articles(self, db, config):
        """get_cluster_articles should return all articles in a cluster."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance for violations",
            TestEventClustering._SEC_CONTENT_A,
            "2024-01-15T10:00:00Z", "REGULATORY", 4,
        )
        _insert_article_and_event(
            db, "The Block", "https://b.com/1",
            "SEC sues Binance for securities violations",
            TestEventClustering._SEC_CONTENT_B,
            "2024-01-15T10:30:00Z", "REGULATORY", 4,
        )

        clusterer = EventClusterer(db, config)
        clusters = clusterer.cluster_events()

        assert len(clusters) == 1
        articles = clusterer.get_cluster_articles(clusters[0].cluster_id)
        assert len(articles) == 2


class TestSourceCredibility:
    """Tests for source tier lookup."""

    def test_tier_1_lookup(self, config):
        cred = SourceCredibility(config)
        assert cred.get_tier("Reuters") == 1
        assert cred.get_weight("Reuters") == 1.0

    def test_tier_2_lookup(self, config):
        cred = SourceCredibility(config)
        assert cred.get_tier("CoinDesk") == 2
        assert cred.get_weight("CoinDesk") == 0.7

    def test_tier_3_lookup(self, config):
        cred = SourceCredibility(config)
        assert cred.get_tier("reddit/cryptocurrency") == 3
        assert cred.get_weight("reddit/cryptocurrency") == 0.4

    def test_unknown_source_defaults_to_tier_4(self, config):
        cred = SourceCredibility(config)
        assert cred.get_tier("RandomCryptoBlog") == 4
        assert cred.get_weight("RandomCryptoBlog") == 0.1

    def test_case_insensitive_lookup(self, config):
        cred = SourceCredibility(config)
        assert cred.get_tier("reuters") == 1
        assert cred.get_tier("REUTERS") == 1
        assert cred.get_tier("coindesk") == 2

    def test_get_tier_and_weight(self, config):
        cred = SourceCredibility(config)
        tier, weight = cred.get_tier_and_weight("Bloomberg")
        assert tier == 1
        assert weight == 1.0

    def test_default_tiers_without_config(self):
        """Should work with default tiers when no config provided."""
        cred = SourceCredibility()
        # Defaults include CoinDesk as tier 2
        assert cred.get_tier("CoinDesk") == 2
        assert cred.get_tier("UnknownSource") == 4


class TestNoveltyDecay:
    """Tests for the novelty decay math."""

    def test_novelty_at_zero_hours(self):
        assert compute_novelty(0, 0.1) == 1.0

    def test_novelty_decays_over_time(self):
        n0 = compute_novelty(0, 0.1)
        n7 = compute_novelty(7, 0.1)
        n24 = compute_novelty(24, 0.1)
        n48 = compute_novelty(48, 0.1)

        assert n0 > n7 > n24 > n48
        assert n0 == 1.0

    def test_novelty_approximately_half_at_7_hours(self):
        """At lambda=0.1, novelty should be ~0.50 at 7 hours."""
        n = compute_novelty(7, 0.1)
        assert abs(n - math.exp(-0.7)) < 0.001
        assert 0.45 < n < 0.55

    def test_novelty_near_zero_at_48_hours(self):
        """At lambda=0.1, novelty should be very low at 48 hours."""
        n = compute_novelty(48, 0.1)
        assert n < 0.01

    def test_novelty_with_different_lambda(self):
        """Higher lambda = faster decay."""
        slow = compute_novelty(10, 0.05)
        fast = compute_novelty(10, 0.2)
        assert slow > fast

    def test_novelty_always_positive(self):
        """Novelty should always be positive (exponential never reaches 0)."""
        for hours in [0, 1, 10, 100, 1000]:
            assert compute_novelty(hours, 0.1) > 0

    def test_update_novelty_scores(self, db, config):
        """update_novelty_scores should recalculate based on current time."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance", "SEC lawsuit.",
            "2024-01-15T10:00:00Z", "REGULATORY", 4,
        )

        clusterer = EventClusterer(db, config)
        clusterer.cluster_events()

        # Novelty should be very low since event is from the past
        updated = clusterer.update_novelty_scores()
        assert updated == 1

        clusters = clusterer.get_clusters()
        assert len(clusters) == 1
        # Event from 2024 should have near-zero novelty now
        assert clusters[0]["novelty_score"] < 0.01


class TestDownstreamUsesCluster:
    """Verify that downstream analysis modules pick up clusters."""

    def test_impact_analyzer_detects_clusters(self, db, config):
        """Impact analyzer should detect and use cluster table when populated."""
        _insert_article_and_event(
            db, "CoinDesk", "https://a.com/1",
            "SEC sues Binance", "SEC lawsuit.",
            "2024-01-15T10:00:00Z", "REGULATORY", 4, ["BTC"],
        )

        # Add price data
        db.insert_prices([{
            "asset": "BTC", "timestamp": "2024-01-15T10:00:00Z",
            "open": 42000, "high": 43000, "low": 41500, "close": 42500, "volume": 1e9,
        }, {
            "asset": "BTC", "timestamp": "2024-01-15T14:00:00Z",
            "open": 42500, "high": 42800, "low": 42100, "close": 42600, "volume": 8e8,
        }])

        # Create clusters
        clusterer = EventClusterer(db, config)
        clusterer.cluster_events()

        # Impact analyzer should use clusters
        from src.analysis.event_impact import EventImpactAnalyzer
        analyzer = EventImpactAnalyzer(db)
        assert analyzer._has_clusters() is True

        moves = analyzer.compute_event_moves()
        assert len(moves) > 0
        # Should have cluster-specific fields
        assert "article_count" in moves[0]
        assert "novelty_score" in moves[0]
