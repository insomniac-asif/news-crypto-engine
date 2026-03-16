"""FastAPI dashboard for the Crypto News Sentiment Engine.

Serves the static frontend and provides REST endpoints for:
- Database stats and status
- Articles, events, clusters, signals, narratives
- Price data and sentiment analysis
- Pipeline status

Run with:
    uvicorn dashboard_fastapi.app:app --host 0.0.0.0 --port 8091
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Load env ──────────────────────────────────────────────────────────
load_dotenv(PROJECT_ROOT / ".env")

# ── Paths ─────────────────────────────────────────────────────────────
DB_PATH = PROJECT_ROOT / "data" / "news_crypto.db"
PIPELINE_LOG = PROJECT_ROOT / "data" / "daily_pipeline.log"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("dashboard")

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Crypto News Engine Dashboard",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (JS, CSS)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Startup timestamp ─────────────────────────────────────────────────
_STARTED_AT = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
#  Database helper
# ═══════════════════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    """Return a connection to the SQLite database with Row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
#  Static + Root
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Serve the frontend SPA."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_path))


# ═══════════════════════════════════════════════════════════════════════
#  API Endpoints
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats():
    """Overall database statistics."""
    try:
        conn = get_db()
        tables = {
            "articles": "articles",
            "events": "events",
            "clusters": "event_clusters",
            "signals": "signals_v2",
            "prices": "prices",
            "narratives": "narratives",
        }
        stats: dict[str, Any] = {}
        for key, table in tables.items():
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[key] = count

        # Last ingestion time
        row = conn.execute("SELECT MAX(ingested_at) AS latest FROM articles").fetchone()
        stats["last_ingestion"] = row["latest"] if row else None

        conn.close()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.exception("Error in /api/stats")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/articles")
async def get_articles(
    limit: int = Query(50, ge=1, le=500),
    source: Optional[str] = Query(None),
):
    """Recent articles with optional source filter."""
    try:
        conn = get_db()
        query = "SELECT id, source, title, published_at, url FROM articles WHERE 1=1"
        params: list[Any] = []

        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY published_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return JSONResponse(content=rows_to_dicts(rows))
    except Exception as e:
        logger.exception("Error in /api/articles")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    category: Optional[str] = Query(None),
):
    """Recent events with optional category filter."""
    try:
        conn = get_db()
        query = "SELECT id, category, severity, summary, assets_affected, detected_at FROM events WHERE 1=1"
        params: list[Any] = []

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["assets_affected"] = json.loads(d["assets_affected"])
            except (json.JSONDecodeError, TypeError):
                d["assets_affected"] = []
            results.append(d)
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/events")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/clusters")
async def get_clusters(limit: int = Query(50, ge=1, le=500)):
    """Event clusters ordered by recency."""
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT id, category, severity, sentiment, first_detected_at,
                      last_article_at, article_count, representative_headline,
                      novelty_score, assets_affected
               FROM event_clusters
               ORDER BY last_article_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["assets_affected"] = json.loads(d["assets_affected"])
            except (json.JSONDecodeError, TypeError):
                d["assets_affected"] = []
            results.append(d)
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/clusters")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals")
async def get_signals():
    """All signals_v2 records."""
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT id, asset, direction, confidence, signal_score,
                      news_component, market_component, narrative_component,
                      novelty_component, entry_time, price_at_signal,
                      volume_zscore, momentum_1h, reasoning, confirmation_factors
               FROM signals_v2
               ORDER BY entry_time DESC"""
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["confirmation_factors"] = json.loads(d["confirmation_factors"])
            except (json.JSONDecodeError, TypeError):
                d["confirmation_factors"] = []
            results.append(d)
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/signals")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/narratives")
async def get_narratives():
    """All narratives."""
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT name, keywords, first_seen, last_seen,
                      event_count, avg_price_impact
               FROM narratives
               ORDER BY event_count DESC"""
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["keywords"] = json.loads(d["keywords"])
            except (json.JSONDecodeError, TypeError):
                d["keywords"] = []
            results.append(d)
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/narratives")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prices")
async def get_prices(
    asset: str = Query("BTC"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Price OHLCV data for a given asset."""
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM prices
               WHERE asset = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (asset, limit),
        ).fetchall()
        conn.close()

        # Return in ascending order for charts
        results = rows_to_dicts(rows)
        results.reverse()
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/prices")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sentiment-trend")
async def get_sentiment_trend():
    """Daily average sentiment from events grouped by date.

    Maps categories to sentiment values:
    ADOPTION/PROTOCOL = positive (+1), SECURITY = negative (-1),
    REGULATORY/MACRO = mixed (0), EXCHANGE/SENTIMENT/MARKET_STRUCTURE = neutral (0).
    Also uses event_clusters.sentiment where available.
    """
    try:
        conn = get_db()

        # Category-based sentiment mapping
        sentiment_map = {
            "ADOPTION": 1.0,
            "PROTOCOL": 0.5,
            "EXCHANGE": 0.0,
            "SENTIMENT": 0.0,
            "MARKET_STRUCTURE": 0.0,
            "MACRO": -0.2,
            "REGULATORY": -0.3,
            "SECURITY": -1.0,
        }

        rows = conn.execute(
            """SELECT DATE(detected_at) AS date, category, COUNT(*) AS cnt
               FROM events
               GROUP BY DATE(detected_at), category
               ORDER BY date ASC"""
        ).fetchall()
        conn.close()

        # Aggregate by date
        daily: dict[str, dict] = {}
        for r in rows:
            date = r["date"]
            cat = r["category"]
            cnt = r["cnt"]
            if date not in daily:
                daily[date] = {
                    "date": date,
                    "total_events": 0,
                    "sentiment_sum": 0.0,
                    "category_counts": {},
                }
            daily[date]["total_events"] += cnt
            daily[date]["sentiment_sum"] += sentiment_map.get(cat, 0.0) * cnt
            daily[date]["category_counts"][cat] = cnt

        results = []
        for date in sorted(daily.keys()):
            d = daily[date]
            avg_sent = d["sentiment_sum"] / d["total_events"] if d["total_events"] > 0 else 0
            results.append({
                "date": d["date"],
                "avg_sentiment": round(avg_sent, 3),
                "event_count": d["total_events"],
                "category_counts": d["category_counts"],
            })

        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/sentiment-trend")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/category-breakdown")
async def get_category_breakdown():
    """Event counts per category with average severity."""
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT category, COUNT(*) AS count, AVG(severity) AS avg_severity
               FROM events
               GROUP BY category
               ORDER BY count DESC"""
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            results.append({
                "category": r["category"],
                "count": r["count"],
                "avg_severity": round(r["avg_severity"], 2),
            })
        return JSONResponse(content=results)
    except Exception as e:
        logger.exception("Error in /api/category-breakdown")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pipeline-status")
async def get_pipeline_status():
    """Parse the last pipeline run from daily_pipeline.log."""
    try:
        if not PIPELINE_LOG.exists():
            return JSONResponse(content={
                "status": "unknown",
                "message": "Pipeline log not found",
                "last_run": None,
            })

        # Read last 40 lines
        text = PIPELINE_LOG.read_text()
        lines = text.strip().split("\n")
        tail = lines[-40:] if len(lines) > 40 else lines

        last_run = None
        stats = {}
        status = "unknown"

        for line in reversed(tail):
            if "Pipeline complete:" in line:
                parts = line.split("Pipeline complete:")
                if len(parts) > 1:
                    last_run = parts[1].strip()
                    status = "ok"
                break
            if "Articles processed:" in line:
                try:
                    val = line.split(":")[1].strip().replace(",", "")
                    stats["articles_processed"] = int(val)
                except (ValueError, IndexError):
                    pass
            if "Events created:" in line:
                try:
                    val = line.split(":")[1].strip().replace(",", "")
                    stats["events_created"] = int(val)
                except (ValueError, IndexError):
                    pass
            if "Clusters created:" in line:
                try:
                    val = line.split(":")[1].strip().replace(",", "")
                    stats["clusters_created"] = int(val)
                except (ValueError, IndexError):
                    pass

        return JSONResponse(content={
            "status": status,
            "last_run": last_run,
            "stats": stats,
            "log_tail": tail[-10:],
        })
    except Exception as e:
        logger.exception("Error in /api/pipeline-status")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def get_status():
    """System status: online state, DB size, last ingestion, total articles."""
    try:
        db_size = 0
        if DB_PATH.exists():
            db_size = round(DB_PATH.stat().st_size / (1024 * 1024), 2)

        conn = get_db()
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        row = conn.execute("SELECT MAX(ingested_at) AS latest FROM articles").fetchone()
        last_ingestion = row["latest"] if row else None
        conn.close()

        return JSONResponse(content={
            "online": True,
            "db_size_mb": db_size,
            "last_ingestion": last_ingestion,
            "total_articles": article_count,
            "started_at": _STARTED_AT,
        })
    except Exception as e:
        logger.exception("Error in /api/status")
        return JSONResponse(content={
            "online": False,
            "error": str(e),
        })


@app.get("/api/movers")
async def get_movers():
    """Top movers: assets ranked by recent price change with news context.

    For each asset with price data, computes:
    - Latest price, 1h change %, 24h change %
    - Recent news count and dominant category
    - Significance score (combines price move + news volume + severity)
    """
    try:
        conn = get_db()
        assets = [r["asset"] for r in conn.execute(
            "SELECT DISTINCT asset FROM prices ORDER BY asset"
        ).fetchall()]

        movers = []
        for asset in assets:
            # Latest price and 1h/24h changes
            prices = conn.execute(
                """SELECT timestamp, close FROM prices
                   WHERE asset = ? ORDER BY timestamp DESC LIMIT 25""",
                (asset,),
            ).fetchall()
            if not prices:
                continue

            latest = prices[0]["close"]
            price_1h = prices[0]["close"]
            price_24h = prices[0]["close"]
            if len(prices) >= 2:
                price_1h = prices[1]["close"]  # ~1h ago (hourly candles)
            if len(prices) >= 24:
                price_24h = prices[23]["close"]

            chg_1h = ((latest - price_1h) / price_1h * 100) if price_1h > 0 else 0
            chg_24h = ((latest - price_24h) / price_24h * 100) if price_24h > 0 else 0

            # Recent news for this asset (last 48h events mentioning it)
            news_rows = conn.execute(
                """SELECT e.category, e.severity, e.summary, e.detected_at
                   FROM events e
                   WHERE e.assets_affected LIKE ?
                   AND e.detected_at >= datetime('now', '-2 days')
                   ORDER BY e.detected_at DESC LIMIT 10""",
                (f'%"{asset}"%',),
            ).fetchall()
            news_count = len(news_rows)

            # Dominant category + top headline
            top_category = ""
            top_headline = ""
            max_severity = 0
            if news_rows:
                cats = {}
                for nr in news_rows:
                    c = nr["category"]
                    cats[c] = cats.get(c, 0) + 1
                    if nr["severity"] > max_severity:
                        max_severity = nr["severity"]
                        top_headline = nr["summary"] or ""
                        top_category = c
                if not top_category:
                    top_category = max(cats, key=cats.get)

            # Significance: abs(price move) * news_volume * max_severity
            abs_move = abs(chg_1h) + abs(chg_24h) * 0.3
            significance = round(abs_move * max(1, news_count) * max(1, max_severity) / 5, 1)

            # Direction sentiment
            if chg_1h > 0.5 and news_count > 0:
                sentiment = "bullish"
            elif chg_1h < -0.5 and news_count > 0:
                sentiment = "bearish"
            elif abs(chg_1h) < 0.2:
                sentiment = "quiet"
            else:
                sentiment = "mixed"

            movers.append({
                "asset": asset,
                "price": round(latest, 2),
                "chg_1h_pct": round(chg_1h, 2),
                "chg_24h_pct": round(chg_24h, 2),
                "news_count": news_count,
                "max_severity": max_severity,
                "top_category": top_category,
                "top_headline": top_headline[:120],
                "significance": significance,
                "sentiment": sentiment,
            })

        conn.close()
        movers.sort(key=lambda m: m["significance"], reverse=True)
        return JSONResponse(content=movers)
    except Exception as e:
        logger.exception("Error in /api/movers")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/impact-feed")
async def get_impact_feed(limit: int = Query(20, ge=1, le=100)):
    """Recent high-impact events: news + price combined, sorted by significance.

    Each item is a news event enriched with price context:
    - What happened (headline)
    - Which assets moved and by how much
    - Significance level (CRITICAL / NOTABLE / MINOR)
    """
    try:
        conn = get_db()

        clusters = conn.execute(
            """SELECT ec.id, ec.category, ec.severity, ec.sentiment,
                      ec.representative_headline, ec.article_count,
                      ec.novelty_score, ec.assets_affected,
                      ec.first_detected_at, ec.last_article_at
               FROM event_clusters ec
               ORDER BY ec.first_detected_at DESC
               LIMIT ?""",
            (limit * 2,),
        ).fetchall()

        feed = []
        for c in clusters:
            assets_raw = c["assets_affected"]
            try:
                assets = json.loads(assets_raw) if assets_raw else []
            except (json.JSONDecodeError, TypeError):
                assets = []

            # Get price impact for each asset
            asset_impacts = []
            for asset in assets[:3]:
                detected = c["first_detected_at"]
                price_at = conn.execute(
                    "SELECT close FROM prices WHERE asset = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                    (asset, detected),
                ).fetchone()
                price_after = conn.execute(
                    "SELECT close FROM prices WHERE asset = ? AND timestamp > ? ORDER BY timestamp ASC LIMIT 1",
                    (asset, detected),
                ).fetchone()
                if price_at and price_after:
                    chg = ((price_after["close"] - price_at["close"]) / price_at["close"]) * 100
                    asset_impacts.append({
                        "asset": asset,
                        "price_before": round(price_at["close"], 2),
                        "price_after": round(price_after["close"], 2),
                        "change_pct": round(chg, 2),
                    })
                elif price_at:
                    asset_impacts.append({
                        "asset": asset,
                        "price_before": round(price_at["close"], 2),
                        "price_after": None,
                        "change_pct": None,
                    })

            # Score significance
            severity = c["severity"] or 1
            article_count = c["article_count"] or 1
            novelty = c["novelty_score"] or 0.5
            max_price_move = max((abs(ai["change_pct"]) for ai in asset_impacts if ai["change_pct"]), default=0)

            sig_score = (severity / 5) * 0.3 + min(article_count / 5, 1) * 0.2 + novelty * 0.2 + min(max_price_move / 3, 1) * 0.3
            sig_score = round(sig_score, 2)

            if sig_score >= 0.6:
                level = "CRITICAL"
            elif sig_score >= 0.35:
                level = "NOTABLE"
            else:
                level = "MINOR"

            # Direction summary
            if asset_impacts and any(ai["change_pct"] for ai in asset_impacts if ai["change_pct"]):
                avg_chg = sum(ai["change_pct"] for ai in asset_impacts if ai["change_pct"]) / len([ai for ai in asset_impacts if ai["change_pct"]])
                if avg_chg > 0.3:
                    direction = "BULLISH"
                elif avg_chg < -0.3:
                    direction = "BEARISH"
                else:
                    direction = "NEUTRAL"
            else:
                direction = "PENDING"

            feed.append({
                "headline": c["representative_headline"] or "Unknown event",
                "category": c["category"],
                "severity": severity,
                "article_count": article_count,
                "detected_at": c["first_detected_at"],
                "assets": [ai["asset"] for ai in asset_impacts],
                "asset_impacts": asset_impacts,
                "significance": sig_score,
                "level": level,
                "direction": direction,
                "novelty": round(novelty, 2),
            })

        conn.close()
        feed.sort(key=lambda f: f["significance"], reverse=True)
        return JSONResponse(content=feed[:limit])
    except Exception as e:
        logger.exception("Error in /api/impact-feed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hourly-pulse")
async def get_hourly_pulse(asset: str = Query("BTC"), hours: int = Query(24, ge=1, le=168)):
    """Hourly breakdown: price change, volume, news count, significance.

    Returns one entry per hour for the specified asset.
    """
    try:
        conn = get_db()

        prices = conn.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM prices WHERE asset = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (asset, hours + 1),
        ).fetchall()

        if len(prices) < 2:
            conn.close()
            return JSONResponse(content=[])

        # Build hourly entries (newest first, we'll reverse at end)
        hourly = []
        for i in range(len(prices) - 1):
            current = prices[i]
            previous = prices[i + 1]
            ts = current["timestamp"]
            chg_pct = ((current["close"] - previous["close"]) / previous["close"] * 100) if previous["close"] > 0 else 0

            # Count news events in this hour for this asset
            news_count = conn.execute(
                """SELECT COUNT(*) as cnt FROM events
                   WHERE assets_affected LIKE ?
                   AND detected_at BETWEEN ? AND ?""",
                (f'%"{asset}"%', previous["timestamp"], ts),
            ).fetchone()["cnt"]

            # Max severity in this hour
            max_sev_row = conn.execute(
                """SELECT MAX(severity) as ms FROM events
                   WHERE assets_affected LIKE ?
                   AND detected_at BETWEEN ? AND ?""",
                (f'%"{asset}"%', previous["timestamp"], ts),
            ).fetchone()
            max_sev = max_sev_row["ms"] or 0

            # Significance for this hour
            significance = round(abs(chg_pct) * max(1, news_count) * max(1, max_sev) / 5, 2)

            hourly.append({
                "timestamp": ts,
                "close": round(current["close"], 2),
                "change_pct": round(chg_pct, 3),
                "volume": current["volume"],
                "news_count": news_count,
                "max_severity": max_sev,
                "significance": significance,
            })

        conn.close()
        hourly.reverse()
        return JSONResponse(content=hourly)
    except Exception as e:
        logger.exception("Error in /api/hourly-pulse")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/predictions")
async def api_predictions(limit: int = Query(50, ge=1, le=500)):
    """Recent predictions with grades."""
    try:
        from src.analysis.predictions import get_predictions
        preds = get_predictions(limit=limit)
        return JSONResponse(content=preds)
    except Exception as e:
        logger.exception("Error in /api/predictions")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/predictions/accuracy")
async def api_prediction_accuracy():
    """Prediction accuracy by timeframe."""
    try:
        from src.analysis.predictions import get_accuracy_stats
        stats = get_accuracy_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.exception("Error in /api/predictions/accuracy")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/source-trust")
async def api_source_trust():
    """Source trust leaderboard."""
    try:
        from src.analysis.predictions import get_trust_leaderboard
        leaderboard = get_trust_leaderboard()
        return JSONResponse(content=leaderboard)
    except Exception as e:
        logger.exception("Error in /api/source-trust")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/predictions/run")
async def api_run_predictions():
    """Trigger prediction generation + grading."""
    try:
        from src.analysis.predictions import run_predictions, run_grading
        pred_count = run_predictions()
        grade_counts = run_grading()
        return JSONResponse(content={
            "predictions_generated": pred_count,
            "graded": grade_counts["graded"],
            "skipped": grade_counts["skipped"],
        })
    except Exception as e:
        logger.exception("Error in /api/predictions/run")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sources")
async def get_sources():
    """Distinct article sources for filter dropdowns."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT source FROM articles ORDER BY source"
        ).fetchall()
        conn.close()
        return JSONResponse(content=[r["source"] for r in rows])
    except Exception as e:
        logger.exception("Error in /api/sources")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/assets")
async def get_assets():
    """Distinct asset symbols from prices table."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT asset FROM prices ORDER BY asset"
        ).fetchall()
        conn.close()
        return JSONResponse(content=[r["asset"] for r in rows])
    except Exception as e:
        logger.exception("Error in /api/assets")
        raise HTTPException(status_code=500, detail=str(e))
