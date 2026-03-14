"""Narrative tracking — detect and monitor evolving crypto narratives.

Identifies recurring themes/topics across articles over time and tracks
their momentum, frequency, and price impact.
"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)

# Pre-defined narrative keyword clusters
# Each narrative is identified by a set of related keywords
NARRATIVE_DEFINITIONS: dict[str, list[str]] = {
    "ETF": ["etf", "spot etf", "bitcoin etf", "ethereum etf", "etf approval",
             "etf filing", "etf inflows", "etf outflows"],
    "DeFi_Summer": ["defi", "yield farming", "liquidity mining", "tvl",
                     "decentralized finance", "dex", "amm"],
    "Layer2_Scaling": ["layer 2", "l2", "rollup", "optimistic rollup",
                       "zk rollup", "arbitrum", "optimism", "base"],
    "Regulatory_Crackdown": ["sec lawsuit", "regulatory crackdown", "crypto ban",
                              "enforcement action", "securities violation"],
    "AI_Crypto": ["ai token", "artificial intelligence", "machine learning",
                   "ai crypto", "gpu", "compute"],
    "RWA_Tokenization": ["real world asset", "rwa", "tokenization", "tokenized",
                          "treasury token", "on-chain treasury"],
    "Stablecoin_Regulation": ["stablecoin", "stablecoin bill", "usdt", "usdc",
                               "tether", "circle", "stablecoin regulation"],
    "Institutional_Adoption": ["institutional", "hedge fund", "pension fund",
                                "endowment", "family office", "wall street"],
    "Hack_Exploit_Wave": ["hack", "exploit", "bridge hack", "flash loan",
                           "rug pull", "drained", "vulnerability"],
    "Meme_Coin_Mania": ["meme coin", "memecoin", "doge", "shib", "pepe",
                         "bonk", "meme season"],
    "Bitcoin_Halving": ["halving", "halvening", "block reward", "mining reward",
                         "hash rate"],
    "CBDCs": ["cbdc", "central bank digital", "digital dollar", "digital euro",
              "digital yuan"],
}


@dataclass
class NarrativeSnapshot:
    """Point-in-time snapshot of a narrative's activity."""

    name: str
    keywords: list[str]
    event_count: int
    recent_count: int  # events in last 7 days
    first_seen: str
    last_seen: str
    momentum: float  # recent_count / total_count ratio
    avg_price_impact: float
    top_assets: list[str] = field(default_factory=list)


class NarrativeTracker:
    """Track evolving narratives across crypto news articles.

    Scans classified events and articles for narrative keyword clusters,
    tracks frequency over time, and measures associated price impact.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the tracker.

        Args:
            db: Database instance.
            config: Full config dict.
        """
        self.db = db
        self._narratives = NARRATIVE_DEFINITIONS.copy()

    def scan_articles(self, days: int = 90) -> dict[str, list[int]]:
        """Scan recent articles for narrative keyword matches.

        Args:
            days: How many days back to scan.

        Returns:
            Dict mapping narrative names to lists of matching article IDs.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        articles = self.db.get_articles(limit=10000, since=since)

        matches: dict[str, list[int]] = defaultdict(list)

        for article in articles:
            text = f"{article['title']} {article.get('content', '')}".lower()
            for narrative_name, keywords in self._narratives.items():
                if any(kw in text for kw in keywords):
                    matches[narrative_name].append(article["id"])

        logger.info("Scanned %d articles, found %d active narratives",
                     len(articles), len(matches))
        return matches

    def update_narratives(self, days: int = 90) -> list[NarrativeSnapshot]:
        """Scan articles and update narrative tracking in the database.

        Args:
            days: How many days back to scan.

        Returns:
            List of NarrativeSnapshot for all active narratives.
        """
        matches = self.scan_articles(days=days)
        events = self.db.get_events(limit=10000)

        # Build article_id → event mapping for price impact
        event_by_article: dict[int, dict] = {}
        for e in events:
            event_by_article[e["article_id"]] = e

        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

        snapshots: list[NarrativeSnapshot] = []
        articles_cache: dict[int, dict] = {}

        for name, article_ids in sorted(matches.items()):
            if not article_ids:
                continue

            keywords = self._narratives[name]
            event_count = len(article_ids)

            # Count recent articles (last 7 days)
            recent_count = 0
            assets_counter: Counter = Counter()
            first_seen = None
            last_seen = None

            for aid in article_ids:
                # Fetch article if not cached
                if aid not in articles_cache:
                    arts = self.db.get_articles(limit=1)
                    # Use the events to get timing info instead
                    pass

                # Check if there's an event for this article
                event = event_by_article.get(aid)
                if event:
                    detected = event["detected_at"]
                    if detected >= seven_days_ago:
                        recent_count += 1
                    if first_seen is None or detected < first_seen:
                        first_seen = detected
                    if last_seen is None or detected > last_seen:
                        last_seen = detected
                    for asset in event.get("assets_affected", []):
                        assets_counter[asset] += 1

            momentum = recent_count / event_count if event_count > 0 else 0

            # Get top affected assets
            top_assets = [a for a, _ in assets_counter.most_common(5)]

            snapshot = NarrativeSnapshot(
                name=name,
                keywords=keywords,
                event_count=event_count,
                recent_count=recent_count,
                first_seen=first_seen or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                last_seen=last_seen or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                momentum=round(momentum, 4),
                avg_price_impact=0.0,  # Filled by impact analyzer if available
                top_assets=top_assets,
            )
            snapshots.append(snapshot)

            # Persist to database
            self.db.upsert_narrative(
                name=name,
                keywords=keywords,
                event_count=event_count,
                avg_price_impact=0.0,
            )

        # Sort by momentum (most active first)
        snapshots.sort(key=lambda s: s.momentum, reverse=True)

        logger.info("Updated %d narratives", len(snapshots))
        return snapshots

    def get_active_narratives(self, min_events: int = 3) -> list[NarrativeSnapshot]:
        """Get currently active narratives sorted by momentum.

        Args:
            min_events: Minimum total events to be considered active.

        Returns:
            Filtered and sorted list of narrative snapshots.
        """
        all_narratives = self.update_narratives()
        return [n for n in all_narratives if n.event_count >= min_events]

    def generate_report(self, days: int = 90) -> str:
        """Generate a human-readable narrative tracking report.

        Args:
            days: How many days back to scan.

        Returns:
            Formatted report string.
        """
        snapshots = self.update_narratives(days=days)

        if not snapshots:
            return "No active narratives detected. Ingest and process more articles."

        lines = [
            "=" * 72,
            "NARRATIVE TRACKER REPORT",
            f"Lookback: {days} days",
            "=" * 72,
            "",
            f"{'Narrative':<25} {'Events':>7} {'Recent':>7} {'Momentum':>10} {'Assets':<20}",
            "-" * 72,
        ]

        for s in snapshots:
            assets_str = ", ".join(s.top_assets[:3]) if s.top_assets else "—"
            lines.append(
                f"{s.name:<25} {s.event_count:>7} {s.recent_count:>7} "
                f"{s.momentum:>9.1%} {assets_str:<20}"
            )

        lines.extend([
            "",
            "-" * 72,
            "Momentum = recent (7d) events / total events",
            "Higher momentum = narrative gaining traction",
            "=" * 72,
        ])

        return "\n".join(lines)
