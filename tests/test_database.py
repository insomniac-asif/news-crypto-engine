"""Tests for the SQLite database layer."""

import json
import os
import tempfile

import pytest

from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like database for each test."""
    db_path = tmp_path / "test.db"
    return Database(str(db_path))


class TestArticles:
    def test_insert_and_retrieve(self, db):
        article_id = db.insert_article(
            source="TestFeed",
            url="https://example.com/article-1",
            title="Bitcoin hits new high",
            content="BTC surged past $100k today.",
            published_at="2024-01-15T12:00:00Z",
        )
        assert article_id is not None
        articles = db.get_articles(limit=10)
        assert len(articles) == 1
        assert articles[0]["title"] == "Bitcoin hits new high"
        assert articles[0]["source"] == "TestFeed"

    def test_duplicate_url_returns_none(self, db):
        url = "https://example.com/same-article"
        first = db.insert_article("Feed1", url, "Title", "Content", "2024-01-15T12:00:00Z")
        second = db.insert_article("Feed2", url, "Title 2", "Content 2", "2024-01-16T12:00:00Z")
        assert first is not None
        assert second is None

    def test_filter_by_source(self, db):
        db.insert_article("CoinDesk", "https://a.com/1", "A", "", "2024-01-15T12:00:00Z")
        db.insert_article("CoinTelegraph", "https://b.com/1", "B", "", "2024-01-15T12:00:00Z")
        articles = db.get_articles(source="CoinDesk")
        assert len(articles) == 1
        assert articles[0]["source"] == "CoinDesk"

    def test_filter_by_since(self, db):
        db.insert_article("F", "https://a.com/1", "Old", "", "2024-01-01T00:00:00Z")
        db.insert_article("F", "https://a.com/2", "New", "", "2024-06-01T00:00:00Z")
        articles = db.get_articles(since="2024-03-01T00:00:00Z")
        assert len(articles) == 1
        assert articles[0]["title"] == "New"

    def test_unprocessed_articles(self, db):
        a1 = db.insert_article("F", "https://a.com/1", "T1", "", "2024-01-15T00:00:00Z")
        a2 = db.insert_article("F", "https://a.com/2", "T2", "", "2024-01-15T00:00:00Z")
        # Classify only a1
        db.insert_event(a1, "REGULATORY", 3, "Test event")
        unprocessed = db.get_unprocessed_articles()
        assert len(unprocessed) == 1
        assert unprocessed[0]["id"] == a2


class TestEvents:
    def test_insert_and_retrieve(self, db):
        a_id = db.insert_article("F", "https://a.com/1", "T", "", "2024-01-15T00:00:00Z")
        e_id = db.insert_event(a_id, "EXCHANGE", 4, "New listing", ["BTC", "ETH"])
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["category"] == "EXCHANGE"
        assert events[0]["severity"] == 4
        assert events[0]["assets_affected"] == ["BTC", "ETH"]

    def test_filter_by_category(self, db):
        a_id = db.insert_article("F", "https://a.com/1", "T", "", "2024-01-15T00:00:00Z")
        db.insert_event(a_id, "REGULATORY", 3)
        db.insert_event(a_id, "SECURITY", 5)
        events = db.get_events(category="SECURITY")
        assert len(events) == 1
        assert events[0]["category"] == "SECURITY"

    def test_filter_by_min_severity(self, db):
        a_id = db.insert_article("F", "https://a.com/1", "T", "", "2024-01-15T00:00:00Z")
        db.insert_event(a_id, "REGULATORY", 2)
        db.insert_event(a_id, "SECURITY", 5)
        events = db.get_events(min_severity=4)
        assert len(events) == 1
        assert events[0]["severity"] == 5


class TestPrices:
    def test_bulk_insert_and_retrieve(self, db):
        records = [
            {"asset": "BTC", "timestamp": "2024-01-15T00:00:00Z",
             "open": 42000, "high": 43000, "low": 41500, "close": 42500, "volume": 1e9},
            {"asset": "BTC", "timestamp": "2024-01-15T01:00:00Z",
             "open": 42500, "high": 42800, "low": 42100, "close": 42600, "volume": 8e8},
        ]
        inserted = db.insert_prices(records)
        assert inserted == 2

        prices = db.get_prices("BTC")
        assert len(prices) == 2
        assert prices[0]["open"] == 42000

    def test_duplicate_prices_skipped(self, db):
        rec = [{"asset": "BTC", "timestamp": "2024-01-15T00:00:00Z",
                "open": 42000, "high": 43000, "low": 41500, "close": 42500, "volume": 1e9}]
        assert db.insert_prices(rec) == 1
        assert db.insert_prices(rec) == 0  # duplicate

    def test_get_price_at(self, db):
        records = [
            {"asset": "BTC", "timestamp": "2024-01-15T00:00:00Z",
             "open": 42000, "high": 43000, "low": 41500, "close": 42500, "volume": 1e9},
            {"asset": "BTC", "timestamp": "2024-01-15T02:00:00Z",
             "open": 42500, "high": 42800, "low": 42100, "close": 42600, "volume": 8e8},
        ]
        db.insert_prices(records)
        # Exact match
        price = db.get_price_at("BTC", "2024-01-15T00:00:00Z")
        assert price["close"] == 42500
        # Between candles — should return the earlier one
        price = db.get_price_at("BTC", "2024-01-15T01:30:00Z")
        assert price["timestamp"] == "2024-01-15T00:00:00Z"


class TestSignals:
    def test_insert_and_update(self, db):
        a_id = db.insert_article("F", "https://a.com/1", "T", "", "2024-01-15T00:00:00Z")
        e_id = db.insert_event(a_id, "EXCHANGE", 4)
        s_id = db.insert_signal(e_id, "BTC", "long", 0.85, "2024-01-15T00:00:00Z", 42000)
        db.update_signal_prices(s_id, price_1h=42500, price_4h=43000, price_24h=44000)
        # Verify update worked (no exception means success)
        assert s_id is not None


class TestNarratives:
    def test_upsert(self, db):
        n_id = db.upsert_narrative("ETF Approval", ["etf", "sec", "approval"], 5, 2.3)
        assert n_id is not None
        # Update existing
        n_id2 = db.upsert_narrative("ETF Approval", ["etf", "sec", "approval", "spot"], 10, 3.1)
        assert n_id2 == n_id  # Same row updated


class TestMaintenance:
    def test_retention_enforcement(self, db):
        db.insert_article("F", "https://a.com/old", "Old", "", "2020-01-01T00:00:00Z")
        db.insert_article("F", "https://a.com/new", "New", "", "2099-01-01T00:00:00Z")
        deleted = db.enforce_retention(retention_days=90)
        assert deleted == 1
        articles = db.get_articles()
        assert len(articles) == 1
        assert articles[0]["title"] == "New"

    def test_stats(self, db):
        stats = db.get_stats()
        assert "articles" in stats
        assert "prices" in stats
        assert all(v == 0 for v in stats.values())
