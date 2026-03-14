"""Tests for Phase 9 data sources: CryptoPanic, CCXT, GDELT, asset discovery."""

import pytest

from src.ingestion.asset_discovery import AssetDiscovery, ASSET_METADATA
from src.ingestion.ccxt_collector import CCXTCollector, DEFAULT_SYMBOL_MAP
from src.ingestion.cryptopanic_collector import CryptoPanicCollector
from src.ingestion.gdelt_collector import GDELTCollector
from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Fresh database for each test."""
    return Database(str(tmp_path / "test_sources.db"))


@pytest.fixture
def config():
    return {
        "assets": {
            "symbols": ["BTC", "ETH", "SOL"],
        },
        "cryptopanic": {
            "api_token": "",  # intentionally empty for unit tests
            "rate_limit_calls": 5,
            "rate_limit_period": 60,
            "max_pages": 1,
        },
        "ccxt": {
            "exchange": "binance",
            "timeframe": "1h",
            "rate_limit_ms": 1200,
            "initial_history_days": 2,
        },
        "gdelt": {
            "search_terms": ["cryptocurrency"],
            "max_records": 10,
            "request_delay": 1.0,
        },
    }


class TestCryptoPanic:
    """Tests for CryptoPanic collector."""

    def test_not_configured_without_token(self, db, config):
        """Should report not configured when API token is empty."""
        collector = CryptoPanicCollector(config, db)
        assert collector.is_configured() is False

    def test_configured_with_token(self, db, config):
        """Should report configured when API token is set."""
        config["cryptopanic"]["api_token"] = "test_token_123"
        collector = CryptoPanicCollector(config, db)
        assert collector.is_configured() is True

    def test_collect_returns_zero_without_token(self, db, config):
        """collect_all should return 0 when not configured."""
        collector = CryptoPanicCollector(config, db)
        count = collector.collect_all()
        assert count == 0

    def test_store_post_deduplicates(self, db, config):
        """Duplicate URLs should not be stored twice."""
        config["cryptopanic"]["api_token"] = "test"
        collector = CryptoPanicCollector(config, db)

        post = {
            "url": "https://example.com/article-1",
            "title": "Test Article",
            "source": {"title": "TestSource"},
            "published_at": "2024-01-15T10:00:00Z",
            "votes": {"positive": 5, "negative": 1},
            "currencies": [{"code": "BTC"}, {"code": "ETH"}],
        }

        assert collector._store_post(post) is True
        assert collector._store_post(post) is False  # duplicate

    def test_store_post_captures_votes(self, db, config):
        """Stored article should contain vote data in content."""
        config["cryptopanic"]["api_token"] = "test"
        collector = CryptoPanicCollector(config, db)

        post = {
            "url": "https://example.com/votes-test",
            "title": "Vote Test",
            "source": {"title": "TestSource"},
            "published_at": "2024-01-15T10:00:00Z",
            "votes": {"positive": 10, "negative": 2, "important": 3, "liked": 5, "disliked": 1},
            "currencies": [{"code": "BTC"}],
        }
        collector._store_post(post)

        articles = db.get_articles(limit=1)
        assert len(articles) == 1
        assert "positive=10" in articles[0]["content"]
        assert "BTC" in articles[0]["content"]


class TestCCXT:
    """Tests for CCXT price collector."""

    def test_default_symbol_map(self):
        """Default symbol map should cover standard assets."""
        assert "BTC" in DEFAULT_SYMBOL_MAP
        assert "ETH" in DEFAULT_SYMBOL_MAP
        assert DEFAULT_SYMBOL_MAP["BTC"] == "BTC/USD"

    def test_add_asset(self, db, config):
        """add_asset should register new assets for tracking."""
        collector = CCXTCollector(config, db)
        collector.add_asset("LINK")
        assert "LINK" in collector.get_tracked_assets()
        assert "LINK/USD" == collector.symbol_map["LINK"]

    def test_add_asset_idempotent(self, db, config):
        """Adding same asset twice should not duplicate."""
        collector = CCXTCollector(config, db)
        collector.add_asset("LINK")
        collector.add_asset("LINK")
        assert collector.get_tracked_assets().count("LINK") == 1

    def test_tracked_assets_includes_config(self, db, config):
        """get_tracked_assets should include both config and discovered."""
        collector = CCXTCollector(config, db)
        tracked = collector.get_tracked_assets()
        assert "BTC" in tracked
        assert "ETH" in tracked
        assert "SOL" in tracked

    def test_exchange_lazy_init(self, db, config):
        """Exchange should not be initialized until first use."""
        collector = CCXTCollector(config, db)
        assert collector._exchange is None


class TestGDELT:
    """Tests for GDELT collector."""

    def test_date_parsing(self):
        """GDELT date formats should parse correctly."""
        collector = GDELTCollector.__new__(GDELTCollector)

        # Standard GDELT format
        result = GDELTCollector._parse_gdelt_date("20240115T120000")
        assert result == "2024-01-15T12:00:00Z"

        # ISO format
        result = GDELTCollector._parse_gdelt_date("2024-01-15T12:00:00Z")
        assert result == "2024-01-15T12:00:00Z"

        # Empty string
        result = GDELTCollector._parse_gdelt_date("")
        assert "T" in result  # should return current time

    def test_store_deduplicates(self, db, config):
        """GDELT articles should deduplicate by URL."""
        collector = GDELTCollector(config, db)

        article = {
            "source": "GDELT/example.com",
            "url": "https://example.com/gdelt-1",
            "title": "GDELT Test Article",
            "content": "GDELT tone=5.2",
            "published_at": "2024-01-15T10:00:00Z",
        }

        assert collector._store_article(article) is True
        assert collector._store_article(article) is False  # duplicate

    def test_default_search_terms(self, db, config):
        """Default search terms should include crypto keywords."""
        collector = GDELTCollector(config, db)
        assert "cryptocurrency" in collector.search_terms


class TestAssetDiscovery:
    """Tests for auto-discovery of assets from events."""

    def test_discover_new_asset(self, db, config):
        """Should discover assets mentioned in events but not in config."""
        # Insert an event mentioning LINK (not in config)
        art_id = db.insert_article("Test", "https://test.com/1", "Title", "", "2024-01-15T00:00:00Z")
        db.insert_event(art_id, "ADOPTION", 3, "Chainlink partnership", ["BTC", "LINK"])

        discovery = AssetDiscovery(db, config)
        new_assets = discovery.scan_events_for_new_assets()

        assert "LINK" in new_assets
        assert "BTC" not in new_assets  # already tracked

    def test_no_duplicate_discovery(self, db, config):
        """Same asset should not be discovered twice."""
        art_id = db.insert_article("Test", "https://test.com/1", "Title", "", "2024-01-15T00:00:00Z")
        db.insert_event(art_id, "ADOPTION", 3, "Test", ["LINK"])

        discovery = AssetDiscovery(db, config)
        first = discovery.scan_events_for_new_assets()
        second = discovery.scan_events_for_new_assets()

        assert "LINK" in first
        assert "LINK" not in second  # already discovered

    def test_asset_metadata(self, db, config):
        """Should return sector and cap tier for known assets."""
        discovery = AssetDiscovery(db, config)
        meta = discovery.get_asset_metadata("BTC")
        assert meta["sector"] == "L1"
        assert meta["cap_tier"] == "large"

    def test_unknown_asset_metadata(self, db, config):
        """Unknown assets should get 'unknown' metadata."""
        discovery = AssetDiscovery(db, config)
        meta = discovery.get_asset_metadata("FAKECOIN")
        assert meta["sector"] == "unknown"

    def test_sector_lookup(self, db, config):
        """Should return assets by sector."""
        discovery = AssetDiscovery(db, config)
        defi = discovery.get_sector_assets("DeFi")
        assert "LINK" in defi
        assert "UNI" in defi
        assert "BTC" not in defi

    def test_get_all_tracked(self, db, config):
        """Should return config + discovered assets."""
        art_id = db.insert_article("Test", "https://test.com/1", "Title", "", "2024-01-15T00:00:00Z")
        db.insert_event(art_id, "ADOPTION", 3, "Test", ["AVAX", "NEAR"])

        discovery = AssetDiscovery(db, config)
        discovery.scan_events_for_new_assets()
        tracked = discovery.get_all_tracked()

        assert "BTC" in tracked  # from config
        assert "ETH" in tracked  # from config
        assert "NEAR" in tracked  # auto-discovered
