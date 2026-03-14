"""Entity extraction for crypto news articles.

Uses spaCy with custom EntityRuler patterns to detect crypto-specific
entities: coin names, exchanges, people, and organizations.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Crypto asset patterns — symbol and common name variants
CRYPTO_ASSETS: dict[str, list[str]] = {
    "BTC": ["bitcoin", "btc", "xbt", "satoshi"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "BNB": ["bnb", "binance coin"],
    "XRP": ["xrp", "ripple"],
    "ADA": ["cardano", "ada"],
    "DOGE": ["dogecoin", "doge"],
    "AVAX": ["avalanche", "avax"],
    "DOT": ["polkadot", "dot"],
    "MATIC": ["polygon", "matic"],
    "LINK": ["chainlink", "link"],
    "UNI": ["uniswap", "uni"],
    "AAVE": ["aave"],
    "CRV": ["curve", "crv"],
    "LTC": ["litecoin", "ltc"],
    "ATOM": ["cosmos", "atom"],
    "NEAR": ["near protocol", "near"],
    "ARB": ["arbitrum", "arb"],
    "OP": ["optimism"],
    "APT": ["aptos", "apt"],
    "SUI": ["sui"],
    "USDT": ["tether", "usdt"],
    "USDC": ["usdc", "usd coin"],
    "DAI": ["dai", "makerdao"],
}

# Major exchanges
EXCHANGES: list[str] = [
    "binance", "coinbase", "kraken", "okx", "bybit", "bitfinex",
    "gemini", "bitstamp", "kucoin", "huobi", "htx", "gate.io",
    "crypto.com", "robinhood", "upbit", "bithumb", "ftx",
]

# Regulatory bodies and orgs
REGULATORY_BODIES: list[str] = [
    "sec", "cftc", "fca", "finma", "esma", "fsb",
    "federal reserve", "fed", "ecb", "treasury",
    "doj", "fbi", "ofac", "fatf", "irs",
]

# Key people in crypto
CRYPTO_PEOPLE: list[str] = [
    "vitalik buterin", "satoshi nakamoto", "changpeng zhao", "cz",
    "gary gensler", "jerome powell", "brian armstrong",
    "sam bankman-fried", "sbf", "elon musk", "michael saylor",
    "cathie wood", "larry fink", "do kwon", "justin sun",
    "charles hoskinson", "gavin wood", "anatoly yakovenko",
]


class EntityExtractor:
    """Extract crypto-relevant entities from article text.

    Uses spaCy's EntityRuler for fast, pattern-based extraction
    plus spaCy's NER model for general entities (ORG, PERSON, GPE).
    """

    def __init__(self, spacy_model: str = "en_core_web_sm") -> None:
        """Initialize the extractor with a spaCy model.

        Args:
            spacy_model: Name of the spaCy model to load.
        """
        self._nlp = None
        self._model_name = spacy_model
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy-load spaCy model and add custom patterns."""
        if self._initialized:
            return

        try:
            import spacy
        except ImportError:
            logger.error("spaCy not installed. Run: pip install spacy && python -m spacy download en_core_web_sm")
            raise

        try:
            self._nlp = spacy.load(self._model_name)
        except OSError:
            logger.error("spaCy model '%s' not found. Run: python -m spacy download %s",
                         self._model_name, self._model_name)
            raise

        # Add EntityRuler with crypto-specific patterns
        ruler = self._nlp.add_pipe("entity_ruler", before="ner")
        patterns = self._build_patterns()
        ruler.add_patterns(patterns)

        self._initialized = True
        logger.info("EntityExtractor initialized with %d custom patterns", len(patterns))

    def _build_patterns(self) -> list[dict[str, Any]]:
        """Build spaCy EntityRuler patterns for crypto entities.

        Returns:
            List of pattern dicts for the EntityRuler.
        """
        patterns: list[dict[str, Any]] = []

        # Crypto asset patterns
        for symbol, names in CRYPTO_ASSETS.items():
            for name in names:
                tokens = name.split()
                if len(tokens) == 1:
                    patterns.append({
                        "label": "CRYPTO_ASSET",
                        "pattern": [{"LOWER": tokens[0]}],
                        "id": symbol,
                    })
                else:
                    patterns.append({
                        "label": "CRYPTO_ASSET",
                        "pattern": [{"LOWER": t} for t in tokens],
                        "id": symbol,
                    })

        # Exchange patterns
        for exchange in EXCHANGES:
            tokens = exchange.split()
            if len(tokens) == 1:
                patterns.append({
                    "label": "EXCHANGE",
                    "pattern": [{"LOWER": tokens[0]}],
                })
            else:
                patterns.append({
                    "label": "EXCHANGE",
                    "pattern": [{"LOWER": t} for t in tokens],
                })

        # Regulatory body patterns
        for body in REGULATORY_BODIES:
            tokens = body.split()
            if len(tokens) == 1:
                # For abbreviations like SEC, match uppercase too
                patterns.append({
                    "label": "REGULATORY_BODY",
                    "pattern": [{"LOWER": tokens[0]}],
                })
            else:
                patterns.append({
                    "label": "REGULATORY_BODY",
                    "pattern": [{"LOWER": t} for t in tokens],
                })

        # People patterns
        for person in CRYPTO_PEOPLE:
            tokens = person.split()
            if len(tokens) == 1:
                patterns.append({
                    "label": "CRYPTO_PERSON",
                    "pattern": [{"LOWER": tokens[0]}],
                })
            else:
                patterns.append({
                    "label": "CRYPTO_PERSON",
                    "pattern": [{"LOWER": t} for t in tokens],
                })

        return patterns

    def extract(self, text: str) -> dict[str, Any]:
        """Extract all crypto-relevant entities from text.

        Args:
            text: Cleaned article text (title + content).

        Returns:
            Dict with keys: assets, exchanges, regulatory_bodies, people,
            organizations, and all_entities (full entity list with labels).
        """
        self._ensure_initialized()

        # Process with length limit to keep it fast
        doc = self._nlp(text[:10000])

        assets: set[str] = set()
        exchanges: set[str] = set()
        regulatory_bodies: set[str] = set()
        people: set[str] = set()
        organizations: set[str] = set()
        all_entities: list[dict[str, str]] = []

        for ent in doc.ents:
            entity_info = {"text": ent.text, "label": ent.label_}

            if ent.label_ == "CRYPTO_ASSET":
                # Resolve to canonical symbol
                symbol = self._resolve_asset(ent.text)
                if symbol:
                    assets.add(symbol)
                    entity_info["symbol"] = symbol
            elif ent.label_ == "EXCHANGE":
                exchanges.add(ent.text.lower())
            elif ent.label_ == "REGULATORY_BODY":
                regulatory_bodies.add(ent.text.upper())
            elif ent.label_ == "CRYPTO_PERSON":
                people.add(ent.text)
            elif ent.label_ == "PERSON":
                people.add(ent.text)
            elif ent.label_ == "ORG":
                organizations.add(ent.text)

            all_entities.append(entity_info)

        result = {
            "assets": sorted(assets),
            "exchanges": sorted(exchanges),
            "regulatory_bodies": sorted(regulatory_bodies),
            "people": sorted(people),
            "organizations": sorted(organizations),
            "all_entities": all_entities,
        }

        logger.debug("Extracted entities: %d assets, %d exchanges, %d people",
                      len(assets), len(exchanges), len(people))
        return result

    def extract_assets(self, text: str) -> list[str]:
        """Extract only crypto asset symbols from text.

        Convenience method for when you only need affected assets.

        Args:
            text: Cleaned article text.

        Returns:
            Sorted list of canonical asset symbols (e.g. ['BTC', 'ETH']).
        """
        return self.extract(text)["assets"]

    def _resolve_asset(self, text: str) -> Optional[str]:
        """Resolve a text mention to a canonical asset symbol.

        Args:
            text: Entity text (e.g. 'bitcoin', 'ETH', 'Ethereum').

        Returns:
            Canonical symbol (e.g. 'BTC') or None if not found.
        """
        lower = text.lower().strip()
        for symbol, names in CRYPTO_ASSETS.items():
            if lower in names or lower == symbol.lower():
                return symbol
        return None
