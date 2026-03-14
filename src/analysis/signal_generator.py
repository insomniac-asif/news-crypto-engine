"""Signal generation — convert event analysis into trading signals.

Uses event classification, severity, and historical impact data to
generate directional trading signals with confidence scores.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.storage.database import Database

logger = logging.getLogger(__name__)

# Signal rules: maps event categories to expected price direction
# direction: 'long' (price up), 'short' (price down), 'neutral'
# These are initial hypotheses — backtesting will validate/refine them
SIGNAL_RULES: dict[str, dict[str, Any]] = {
    "REGULATORY": {
        "default_direction": "short",
        "positive_keywords": ["approved", "approval", "license", "clarity"],
        "positive_direction": "long",
        "min_severity": 2,
        "base_confidence": 0.5,
    },
    "EXCHANGE": {
        "default_direction": "long",  # listings tend to be positive
        "negative_keywords": ["delisting", "delisted", "hack", "outage", "suspend"],
        "negative_direction": "short",
        "min_severity": 2,
        "base_confidence": 0.5,
    },
    "PROTOCOL": {
        "default_direction": "long",  # upgrades generally positive
        "negative_keywords": ["vulnerability", "bug", "delay", "postpone"],
        "negative_direction": "short",
        "min_severity": 2,
        "base_confidence": 0.4,
    },
    "MACRO": {
        "default_direction": "short",  # rate hikes, inflation = risk-off
        "positive_keywords": ["rate cut", "dovish", "stimulus", "easing"],
        "positive_direction": "long",
        "min_severity": 3,
        "base_confidence": 0.4,
    },
    "ADOPTION": {
        "default_direction": "long",
        "min_severity": 2,
        "base_confidence": 0.6,
    },
    "SENTIMENT": {
        "default_direction": "neutral",
        "min_severity": 3,
        "base_confidence": 0.3,
    },
    "SECURITY": {
        "default_direction": "short",
        "min_severity": 2,
        "base_confidence": 0.6,
    },
    "MARKET_STRUCTURE": {
        "default_direction": "neutral",
        "positive_keywords": ["short squeeze", "inflow"],
        "positive_direction": "long",
        "negative_keywords": ["liquidation", "outflow", "whale sell"],
        "negative_direction": "short",
        "min_severity": 3,
        "base_confidence": 0.4,
    },
}


@dataclass
class Signal:
    """A generated trading signal."""

    event_id: int
    asset: str
    direction: str  # 'long', 'short', 'neutral'
    confidence: float  # 0-1
    category: str
    severity: int
    summary: str


class SignalGenerator:
    """Generate trading signals from classified events.

    Applies rule-based logic to map events to directional signals.
    Confidence is adjusted by severity, keyword matches, and
    historical impact data when available.
    """

    def __init__(self, db: Database, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the signal generator.

        Args:
            db: Database instance.
            config: Full config dict.
        """
        self.db = db
        self._rules = SIGNAL_RULES.copy()

    def generate_for_event(self, event: dict[str, Any]) -> list[Signal]:
        """Generate signals for a single classified event.

        Args:
            event: Event dict from the database.

        Returns:
            List of Signal objects (one per affected asset).
        """
        category = event["category"]
        severity = event["severity"]
        summary = event.get("summary", "")
        assets = event.get("assets_affected", [])

        rule = self._rules.get(category)
        if not rule:
            logger.debug("No rule for category %s", category)
            return []

        if severity < rule.get("min_severity", 1):
            logger.debug("Event severity %d below threshold for %s", severity, category)
            return []

        # Determine direction
        direction = self._determine_direction(rule, summary)

        # Calculate confidence
        confidence = self._calculate_confidence(rule, severity, summary)

        if direction == "neutral" and confidence < 0.5:
            return []

        signals: list[Signal] = []
        for asset in assets:
            signal = Signal(
                event_id=event["id"],
                asset=asset,
                direction=direction,
                confidence=round(confidence, 3),
                category=category,
                severity=severity,
                summary=summary,
            )
            signals.append(signal)

        return signals

    def generate_all(self, since: Optional[str] = None) -> list[Signal]:
        """Generate signals for all recent events.

        Args:
            since: Only process events detected after this timestamp.

        Returns:
            List of all generated signals.
        """
        events = self.db.get_events(since=since, limit=10000)
        all_signals: list[Signal] = []

        for event in events:
            signals = self.generate_for_event(event)
            all_signals.extend(signals)

        logger.info("Generated %d signals from %d events", len(all_signals), len(events))
        return all_signals

    def generate_and_store(self, since: Optional[str] = None) -> int:
        """Generate signals and persist them to the database.

        Args:
            since: Only process events detected after this timestamp.

        Returns:
            Number of signals stored.
        """
        signals = self.generate_all(since=since)
        stored = 0

        for signal in signals:
            # Look up current price
            events = self.db.get_events(limit=10000)
            event = next((e for e in events if e["id"] == signal.event_id), None)
            if not event:
                continue

            price = self.db.get_price_at(signal.asset, event["detected_at"])
            price_at_signal = price["close"] if price else None

            self.db.insert_signal(
                event_id=signal.event_id,
                asset=signal.asset,
                direction=signal.direction,
                confidence=signal.confidence,
                entry_time=event["detected_at"],
                price_at_signal=price_at_signal,
            )
            stored += 1

        logger.info("Stored %d signals", stored)
        return stored

    def _determine_direction(self, rule: dict[str, Any], text: str) -> str:
        """Determine signal direction based on rule keywords.

        Args:
            rule: Signal rule dict.
            text: Event summary text to check for keywords.

        Returns:
            'long', 'short', or 'neutral'.
        """
        text_lower = text.lower()

        # Check for positive override keywords
        positive_kws = rule.get("positive_keywords", [])
        if any(kw in text_lower for kw in positive_kws):
            return rule.get("positive_direction", "long")

        # Check for negative override keywords
        negative_kws = rule.get("negative_keywords", [])
        if any(kw in text_lower for kw in negative_kws):
            return rule.get("negative_direction", "short")

        return rule.get("default_direction", "neutral")

    def _calculate_confidence(
        self, rule: dict[str, Any], severity: int, text: str
    ) -> float:
        """Calculate signal confidence based on severity and keyword matches.

        Args:
            rule: Signal rule dict.
            severity: Event severity (1-5).
            text: Event summary text.

        Returns:
            Confidence score 0-1.
        """
        base = rule.get("base_confidence", 0.4)

        # Severity boost: each point above 2 adds 0.1
        severity_boost = max(0, (severity - 2) * 0.1)

        # Keyword match boost
        text_lower = text.lower()
        all_keywords = (
            rule.get("positive_keywords", []) + rule.get("negative_keywords", [])
        )
        keyword_matches = sum(1 for kw in all_keywords if kw in text_lower)
        keyword_boost = min(0.2, keyword_matches * 0.05)

        confidence = base + severity_boost + keyword_boost
        return min(1.0, max(0.0, confidence))
