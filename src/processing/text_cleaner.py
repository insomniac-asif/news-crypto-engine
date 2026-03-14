"""Text cleaning and normalization for ingested articles."""

import html
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def clean_html(text: Optional[str]) -> str:
    """Strip HTML tags and decode entities from raw article text.

    Args:
        text: Raw HTML or plain text content.

    Returns:
        Cleaned plain text string.
    """
    if not text:
        return ""

    # Decode HTML entities (e.g. &amp; → &)
    cleaned = html.unescape(text)

    # Remove script and style blocks entirely
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Replace <br>, <p>, <div> with newlines for readability
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", cleaned, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    # Normalize whitespace
    cleaned = normalize_whitespace(cleaned)

    return cleaned


def normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces/newlines into single spaces, strip edges.

    Args:
        text: Input text.

    Returns:
        Whitespace-normalized text.
    """
    # Replace multiple newlines with single newline
    text = re.sub(r"\n\s*\n+", "\n", text)
    # Replace multiple spaces/tabs with single space
    text = re.sub(r"[ \t]+", " ", text)
    # Strip leading/trailing whitespace per line
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def remove_urls(text: str) -> str:
    """Remove URLs from text.

    Args:
        text: Input text.

    Returns:
        Text with URLs removed.
    """
    return re.sub(r"https?://\S+", "", text)


def normalize_ticker_symbols(text: str) -> str:
    """Standardize common crypto ticker references.

    Maps common aliases to canonical symbols (e.g., 'Bitcoin' → 'BTC').

    Args:
        text: Input text.

    Returns:
        Text with standardized ticker references.
    """
    # Only normalize standalone words, not substrings
    replacements = {
        r"\bbitcoin\b": "BTC",
        r"\bethereum\b": "ETH",
        r"\bether\b": "ETH",
        r"\bsolana\b": "SOL",
        r"\bbinance coin\b": "BNB",
        r"\bripple\b": "XRP",
        r"\bcardano\b": "ADA",
        r"\bdogecoin\b": "DOGE",
        r"\bavalanche\b": "AVAX",
        r"\bpolkadot\b": "DOT",
        r"\bpolygon\b": "MATIC",
    }
    result = text
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def clean_article(title: str, content: Optional[str]) -> dict[str, str]:
    """Full cleaning pipeline for an article.

    Applies HTML cleaning, URL removal, and whitespace normalization.

    Args:
        title: Article title (may contain HTML).
        content: Article body (may contain HTML).

    Returns:
        Dict with 'title' and 'content' keys containing cleaned text.
    """
    cleaned_title = normalize_whitespace(clean_html(title))
    cleaned_content = clean_html(content)
    cleaned_content = remove_urls(cleaned_content)
    cleaned_content = normalize_whitespace(cleaned_content)

    return {"title": cleaned_title, "content": cleaned_content}
