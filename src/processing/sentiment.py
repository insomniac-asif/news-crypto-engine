"""Sentiment analysis for crypto news articles.

Uses VADER (from NLTK) as baseline with crypto-specific lexicon adjustments.
VADER is fast, free, and doesn't require a GPU — perfect for laptop use.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """VADER-based sentiment analyzer with crypto-specific lexicon.

    Adjusts VADER's built-in lexicon with crypto-relevant terms
    (e.g., 'moon' = positive, 'rug' = negative) loaded from config.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the sentiment analyzer.

        Args:
            config: Full config dict (expects 'nlp.sentiment' key).
        """
        self._analyzer = None
        nlp_cfg = (config or {}).get("nlp", {})
        self._crypto_lexicon = nlp_cfg.get("sentiment", {}).get("crypto_lexicon", {})
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy-load VADER and apply crypto lexicon adjustments."""
        if self._initialized:
            return

        try:
            import nltk
            from nltk.sentiment.vader import SentimentIntensityAnalyzer
        except ImportError:
            logger.error("NLTK not installed. Run: pip install nltk")
            raise

        # Download vader_lexicon if not present
        try:
            nltk.data.find("sentiment/vader_lexicon.zip")
        except LookupError:
            logger.info("Downloading VADER lexicon...")
            nltk.download("vader_lexicon", quiet=True)

        self._analyzer = SentimentIntensityAnalyzer()

        # Apply crypto-specific lexicon adjustments
        if self._crypto_lexicon:
            self._analyzer.lexicon.update(self._crypto_lexicon)
            logger.info("Applied %d crypto-specific lexicon adjustments",
                         len(self._crypto_lexicon))

        self._initialized = True
        logger.info("SentimentAnalyzer initialized")

    def analyze(self, text: str) -> dict[str, float]:
        """Compute sentiment scores for text.

        Args:
            text: Cleaned article text (title + content).

        Returns:
            Dict with keys: compound (-1 to 1), pos, neg, neu (0 to 1),
            and label ('positive', 'negative', or 'neutral').
        """
        self._ensure_initialized()

        # VADER works best on shorter text; for long articles,
        # analyze title + first ~500 chars for speed
        truncated = text[:2000]
        scores = self._analyzer.polarity_scores(truncated)

        compound = scores["compound"]
        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"

        result = {
            "compound": compound,
            "positive": scores["pos"],
            "negative": scores["neg"],
            "neutral": scores["neu"],
            "label": label,
        }

        logger.debug("Sentiment: %s (compound=%.3f) for: %s",
                      label, compound, text[:80])
        return result

    def analyze_batch(self, texts: list[str]) -> list[dict[str, float]]:
        """Analyze sentiment for multiple texts.

        Args:
            texts: List of cleaned text strings.

        Returns:
            List of sentiment result dicts.
        """
        return [self.analyze(text) for text in texts]

    def get_headline_sentiment(self, title: str, content: str) -> dict[str, float]:
        """Analyze sentiment with title weighted more heavily.

        Title is repeated to give it more influence on the score,
        since headlines often carry the strongest sentiment signal.

        Args:
            title: Article headline.
            content: Article body text.

        Returns:
            Sentiment result dict.
        """
        # Title repeated 3x to weight it more heavily
        weighted_text = f"{title}. {title}. {title}. {content}"
        return self.analyze(weighted_text)
