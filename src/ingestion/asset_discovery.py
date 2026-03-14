"""Auto-discovery of new assets mentioned in events.

When the event classifier detects an asset not on the watchlist,
this module adds it for tracking with appropriate metadata.
"""

import json
import logging
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)

# Sector tags for known assets
ASSET_METADATA: dict[str, dict[str, Any]] = {
    "BTC": {"sector": "L1", "cap_tier": "large"},
    "ETH": {"sector": "L1", "cap_tier": "large"},
    "SOL": {"sector": "L1", "cap_tier": "large"},
    "BNB": {"sector": "L1", "cap_tier": "large"},
    "XRP": {"sector": "L1", "cap_tier": "large"},
    "ADA": {"sector": "L1", "cap_tier": "large"},
    "DOGE": {"sector": "meme", "cap_tier": "large"},
    "AVAX": {"sector": "L1", "cap_tier": "mid"},
    "DOT": {"sector": "L1", "cap_tier": "mid"},
    "MATIC": {"sector": "L2", "cap_tier": "mid"},
    "LINK": {"sector": "DeFi", "cap_tier": "mid"},
    "UNI": {"sector": "DeFi", "cap_tier": "mid"},
    "AAVE": {"sector": "DeFi", "cap_tier": "mid"},
    "ARB": {"sector": "L2", "cap_tier": "mid"},
    "OP": {"sector": "L2", "cap_tier": "mid"},
    "NEAR": {"sector": "L1", "cap_tier": "mid"},
    "APT": {"sector": "L1", "cap_tier": "mid"},
    "SUI": {"sector": "L1", "cap_tier": "mid"},
    "FET": {"sector": "AI", "cap_tier": "small"},
    "RNDR": {"sector": "AI", "cap_tier": "mid"},
    "INJ": {"sector": "DeFi", "cap_tier": "small"},
    "SHIB": {"sector": "meme", "cap_tier": "mid"},
    "PEPE": {"sector": "meme", "cap_tier": "small"},
    "WLD": {"sector": "AI", "cap_tier": "small"},
    "FIL": {"sector": "infra", "cap_tier": "mid"},
    "ATOM": {"sector": "L1", "cap_tier": "mid"},
    "LTC": {"sector": "L1", "cap_tier": "mid"},
    "BCH": {"sector": "L1", "cap_tier": "mid"},
    "TRX": {"sector": "L1", "cap_tier": "mid"},
    "TON": {"sector": "L1", "cap_tier": "mid"},
}


class AssetDiscovery:
    """Detect and register new assets from event data.

    Scans events for asset symbols not in the configured watchlist
    and adds them for price tracking.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        self.db = db
        assets_config = (config or {}).get("assets", {})
        self.tracked_symbols: set[str] = set(assets_config.get("symbols", []))
        self.auto_discovered: set[str] = set()

    def scan_events_for_new_assets(self, limit: int = 1000) -> list[str]:
        """Scan recent events for asset symbols not yet tracked.

        Args:
            limit: Number of recent events to scan.

        Returns:
            List of newly discovered asset symbols.
        """
        events = self.db.get_events(limit=limit)
        new_assets: list[str] = []

        for event in events:
            for asset in event.get("assets_affected", []):
                if asset and asset not in self.tracked_symbols and asset not in self.auto_discovered:
                    self.auto_discovered.add(asset)
                    new_assets.append(asset)
                    logger.info("Auto-discovered new asset: %s", asset)

        return new_assets

    def get_asset_metadata(self, symbol: str) -> dict[str, Any]:
        """Get metadata for an asset symbol.

        Args:
            symbol: Asset symbol (e.g. 'LINK').

        Returns:
            Dict with sector, cap_tier, auto_discovered flag.
        """
        meta = ASSET_METADATA.get(symbol, {
            "sector": "unknown",
            "cap_tier": "unknown",
        })
        return {
            **meta,
            "auto_discovered": symbol in self.auto_discovered,
        }

    def get_sector_assets(self, sector: str) -> list[str]:
        """Get all known assets in a sector.

        Args:
            sector: Sector tag (e.g. 'DeFi', 'L1', 'L2', 'meme', 'AI').

        Returns:
            List of asset symbols in that sector.
        """
        return [
            sym for sym, meta in ASSET_METADATA.items()
            if meta.get("sector") == sector
        ]

    def get_all_tracked(self) -> list[str]:
        """Get all tracked assets (configured + auto-discovered)."""
        return sorted(self.tracked_symbols | self.auto_discovered)
