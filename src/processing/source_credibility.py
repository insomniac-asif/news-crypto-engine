"""Source credibility scoring — tiered trust weighting for news sources.

Assigns credibility weights to news sources based on their reliability tier.
Higher-tier sources carry more weight in clustering, sentiment, and signal scoring.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default tiers when config is not provided
DEFAULT_TIERS: dict[str, dict[str, Any]] = {
    "tier_1": {
        "weight": 1.0,
        "sources": [
            "Reuters", "Bloomberg", "SEC", "CFTC",
            "Federal Reserve", "Treasury",
            "Binance Blog", "Coinbase Blog", "Kraken Blog",
        ],
    },
    "tier_2": {
        "weight": 0.7,
        "sources": [
            "CoinDesk", "The Block", "CoinTelegraph", "Decrypt",
            "Bitcoin Magazine", "Wall Street Journal", "Financial Times",
            "New York Times", "Washington Post",
        ],
    },
    "tier_3": {
        "weight": 0.4,
        "sources": [
            "reddit/cryptocurrency", "reddit/bitcoin", "reddit/ethereum",
            "Bitcoinist", "NewsBTC", "U.Today", "Crypto Briefing",
            "BeInCrypto", "AMBCrypto",
        ],
    },
    "tier_4": {
        "weight": 0.1,
        "sources": [],  # catch-all for unknown sources
    },
}


class SourceCredibility:
    """Look up credibility weight for a news source.

    Sources are assigned to tiers (1-4) with associated weights.
    Unknown sources default to the lowest tier.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize from config or defaults.

        Args:
            config: Full config dict. Looks for 'source_credibility' key
                    with tier definitions.
        """
        tier_config = (config or {}).get("source_credibility", {})
        self._source_map: dict[str, tuple[int, float]] = {}
        self._default_weight: float = 0.1

        tiers = tier_config if tier_config else DEFAULT_TIERS

        for tier_name, tier_data in tiers.items():
            # Extract tier number from name (e.g. "tier_1" → 1)
            try:
                tier_num = int(tier_name.split("_")[1])
            except (IndexError, ValueError):
                tier_num = 4

            weight = tier_data.get("weight", self._default_weight)
            sources = tier_data.get("sources", [])

            for source in sources:
                self._source_map[source.lower()] = (tier_num, weight)

            if tier_num == 4:
                self._default_weight = weight

    def get_tier(self, source_name: str) -> int:
        """Get the credibility tier for a source.

        Args:
            source_name: Name of the news source.

        Returns:
            Tier number (1=highest, 4=lowest/unknown).
        """
        entry = self._source_map.get(source_name.lower())
        if entry:
            return entry[0]
        return 4

    def get_weight(self, source_name: str) -> float:
        """Get the credibility weight for a source.

        Args:
            source_name: Name of the news source.

        Returns:
            Weight between 0.0 and 1.0.
        """
        entry = self._source_map.get(source_name.lower())
        if entry:
            return entry[1]
        return self._default_weight

    def get_tier_and_weight(self, source_name: str) -> tuple[int, float]:
        """Get both tier and weight for a source.

        Args:
            source_name: Name of the news source.

        Returns:
            Tuple of (tier_number, weight).
        """
        entry = self._source_map.get(source_name.lower())
        if entry:
            return entry
        return (4, self._default_weight)
