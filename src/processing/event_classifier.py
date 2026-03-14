"""Rule-based event classification for crypto news articles.

Classifies articles into the event taxonomy using keyword pattern matching.
Designed to be easily extensible — add new categories by adding keyword sets.
Can be swapped for an ML classifier later without changing the interface.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Event Taxonomy ────────────────────────────────────────────────────

VALID_CATEGORIES = [
    "REGULATORY",
    "EXCHANGE",
    "PROTOCOL",
    "MACRO",
    "ADOPTION",
    "SENTIMENT",
    "SECURITY",
    "MARKET_STRUCTURE",
]

# ── Keyword Rules ─────────────────────────────────────────────────────
# Each category maps to a list of keyword/phrase patterns.
# Patterns are matched case-insensitively against the article text.
# More specific patterns are listed first for priority matching.

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "REGULATORY": [
        r"\bsec\b", r"\bcftc\b", r"\bregulat\w*\b", r"\blegislat\w*\b",
        r"\bcompliance\b", r"\blawsuit\b", r"\bsued?\b", r"\bsettlement\b",
        r"\bban(?:ned|s)?\b", r"\bapproved?\b", r"\bapproval\b",
        r"\blicens\w*\b", r"\benforcement\b", r"\bsanction\w*\b",
        r"\bgovernment\b", r"\bcongresss?\b", r"\bsenate\b",
        r"\bexecutive order\b", r"\btax(?:ation|es|ed)?\b",
        r"\banti-money laundering\b", r"\baml\b", r"\bkyc\b",
        r"\bstablecoin bill\b", r"\bcrypto bill\b",
        r"\bmica\b", r"\beuropol\b", r"\bdoj\b", r"\bfbi\b",
        r"\bauthorit\w*\b", r"\bfreeze\b.*\bcrypto\b", r"\bseize[ds]?\b",
        r"\billicit\b", r"\blegal\b", r"\blaw\b",
        r"\bprosecutor\b", r"\bcharg(?:ed|es|ing)\b",
        r"\bcourt\b", r"\bjudge\b", r"\bruling\b",
        r"\bpolicy\b", r"\bcrackdown\b",
    ],
    "EXCHANGE": [
        r"\blisting\b", r"\bdelisting\b", r"\blisted\b", r"\bdelisted\b",
        r"\bexchange\b.*\b(?:launch|announce|add)\b",
        r"\btrading pair\b", r"\bnew market\b",
        r"\bexchange outage\b", r"\bexchange hack\b",
        r"\bwithdrawal\b.*\b(?:halt|suspend|resume)\b",
        r"\bdeposit\b.*\b(?:halt|suspend|resume)\b",
        r"\bcoinbase\b", r"\bbinance\b", r"\bkraken\b",
        r"\bokx\b", r"\bbybit\b", r"\bgemini\b",
        r"\brobinhood\b", r"\bupbit\b",
    ],
    "PROTOCOL": [
        r"\bupgrade\b", r"\bhard fork\b", r"\bsoft fork\b", r"\bfork\b",
        r"\bgovernance\b.*\bvote\b", r"\bproposal\b",
        r"\bprotocol\b.*\b(?:update|upgrade|change)\b",
        r"\bmainnet\b", r"\btestnet\b", r"\bmerge\b",
        r"\blayer 2\b", r"\bl2\b", r"\brollup\b",
        r"\bsharding\b", r"\bstaking\b",
        r"\beip-?\d+\b", r"\bbip-?\d+\b",
        r"\bsmart contract\b", r"\btoken burn\b",
        r"\bscaling\b", r"\bmigration\b",
        r"\boptimism\b", r"\barbitrum\b", r"\bbase\b.*\bchain\b",
        r"\blay\w* off\b.*\bteam\b",
        r"\bon-?chain\b", r"\bdapp\b", r"\bdefi\b",
        r"\btvl\b", r"\byield\b.*\bfarm\w*\b",
        r"\bliquidity\b.*\bpool\b", r"\bamm\b",
        r"\bdex\b", r"\bdecentralized\b.*\bexchange\b",
        r"\bnft\b", r"\btoken\b.*\blaunch\b",
    ],
    "MACRO": [
        r"\bfederal reserve\b", r"\bfed\b.*\brate\b", r"\binterest rate\b",
        r"\binflation\b", r"\bcpi\b", r"\bppi\b",
        r"\bdollar\b.*\b(?:strength|weak|index)\b", r"\bdxy\b",
        r"\brisk[- ]on\b", r"\brisk[- ]off\b",
        r"\brecession\b", r"\bgdp\b", r"\bunemployment\b",
        r"\bquantitative\b", r"\byield\b.*\bcurve\b",
        r"\btreasury\b.*\b(?:yield|bond|bill|secretary)\b",
        r"\bbank\b.*\b(?:crisis|fail|collapse)\b",
        r"\bbonds?\b", r"\bstocks?\b.*\b(?:struggle|fall|drop|rise)\b",
        r"\boil\b.*\b(?:price|surge|drop)\b", r"\btariff\b",
        r"\bgeopoliti\w*\b", r"\btension\b", r"\bmiddle east\b",
        r"\bwar\b", r"\biran\b", r"\bchina\b.*\b(?:trade|tariff|ban)\b",
        r"\btrade war\b", r"\bglobal\b.*\b(?:economy|market|risk)\b",
        r"\bequit\w*\b", r"\bs&p\b", r"\bnasdaq\b",
        r"\btreasury secretary\b", r"\bbessent\b",
        r"\bmacro\b", r"\brisk\b.*\basset\b",
    ],
    "ADOPTION": [
        r"\binstitutional\b", r"\betf\b", r"\bspot etf\b",
        r"\badoption\b", r"\baccepti?ng?\b.*\b(?:crypto|bitcoin|payment)\b",
        r"\bpayment\b.*\b(?:integrat|accept|support)\b",
        r"\bmicrostrategy\b", r"\btesla\b.*\bbitcoin\b",
        r"\bgrayscale\b", r"\bblackrock\b", r"\bfidelity\b",
        r"\bcustody\b", r"\bcustodial\b",
        r"\bmerchant\b", r"\bretail\b.*\badoption\b",
        r"\bcentral bank digital\b", r"\bcbdc\b",
        r"\bjpmorgan\b", r"\bgoldman\b", r"\bvaneck\b",
        r"\bark invest\b", r"\bcathie wood\b",
        r"\btether\b", r"\bcircle\b", r"\bstablecoin\b",
        r"\bpartnership\b", r"\bintegrat\w*\b",
        r"\bfundrais\w*\b", r"\braise[ds]?\b.*\b(?:million|billion)\b",
        r"\bventure\b", r"\bvc\b", r"\bfunding\b",
        r"\bminer\w*\b", r"\bmining\b",
        r"\bai\b.*\b(?:demand|crypto|token)\b",
        r"\bmoonpay\b", r"\bstripe\b", r"\bpaypal\b",
        r"\binvestor\b", r"\bboard\b.*\bjoin\b",
    ],
    "SENTIMENT": [
        r"\bbullish\b", r"\bbearish\b", r"\bfud\b",
        r"\bmoon(?:ing)?\b", r"\bpump\b",
        r"\brally\b", r"\bcrash\b", r"\bplunge\b",
        r"\bfear\b.*\bgreed\b", r"\bsentiment\b",
        r"\belon\b.*\b(?:tweet|post|said)\b",
        r"\binfluencer\b", r"\bhype\b",
        r"\ball[- ]time (?:high|low)\b", r"\bath\b",
        r"\bcapitulat\w*\b", r"\beuphori\w*\b",
        r"\bmemecoin\b", r"\bmeme\s*coin\b",
        r"\bgala\b", r"\bpromotion\b",
        r"\btrump\b.*\b(?:coin|token|crypto|nft)\b",
    ],
    "SECURITY": [
        r"\bhack(?:ed|s|er)?\b", r"\bexploit(?:ed|s)?\b",
        r"\brug\s*pull\b", r"\brugged\b",
        r"\bvulnerabilit\w*\b", r"\bbug\b.*\b(?:bounty|critical|found)\b",
        r"\bdrain(?:ed|s)?\b", r"\bstolen\b",
        r"\bphishing\b", r"\bscam\b",
        r"\baudit\b", r"\bsecurity\b.*\b(?:breach|incident|flaw)\b",
        r"\bflash loan\b.*\battack\b",
        r"\bbridge\b.*\b(?:hack|exploit|attack)\b",
        r"\b(?:stolen|lost|drained|hack)\b",
        r"\bbotched\b", r"\bloss\b", r"\blosses\b",
        r"\bfraud\b", r"\bponzi\b", r"\bwash trad\w*\b",
        r"\bquantum\b.*\bthreat\b",
        r"\bcyber\w*\b", r"\battack\b",
        r"\bproxy\b.*\bnetwork\b",
    ],
    "MARKET_STRUCTURE": [
        r"\bliquidat\w*\b", r"\bshort squeeze\b",
        r"\bwhale\b", r"\blarge transfer\b",
        r"\bfunding rate\b", r"\bopen interest\b",
        r"\blong[/ ]short ratio\b",
        r"\border book\b", r"\bmarket maker\b",
        r"\bspot\b.*\bvolume\b", r"\bfutures\b.*\bvolume\b",
        r"\bmarket cap\b", r"\bdominance\b",
        r"\binflow\b", r"\boutflow\b",
        r"\bflow divergence\b", r"\bflows?\b",
        r"\bprice\b.*\b(?:surge|drop|climb|recover|fall|hit|rise)\b",
        r"\btrader\b", r"\btrading\b",
        r"\brefund\b", r"\bfee\b",
        r"\baave\b", r"\buniswap\b",
    ],
}

# Severity boosters — patterns that increase severity when matched
SEVERITY_BOOSTERS: list[tuple[str, int]] = [
    (r"\$\d+\s*(?:billion|trillion)", 2),
    (r"\$\d+\s*(?:million)", 1),
    (r"\bbreaking\b", 1),
    (r"\burgent\b", 1),
    (r"\bunprecedented\b", 1),
    (r"\bfirst[- ]ever\b", 1),
    (r"\bhistoric\b", 1),
    (r"\bcrisis\b", 1),
    (r"\bemergency\b", 1),
    (r"\bcollapse[ds]?\b", 1),
    (r"\binsolvency\b", 1),
    (r"\bbankrupt\w*\b", 1),
]


@dataclass
class ClassifiedEvent:
    """Result of article classification."""

    category: str
    severity: int
    summary: str
    confidence: float
    matched_keywords: list[str] = field(default_factory=list)


class EventClassifier:
    """Rule-based classifier mapping articles to event taxonomy categories.

    Uses keyword pattern matching with configurable rules. Designed to be
    swapped for an ML classifier later — same interface, better accuracy.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the classifier.

        Args:
            config: Optional config dict with 'event_classification' key.
        """
        cfg = (config or {}).get("event_classification", {})
        self._default_severity = cfg.get("default_severity", 2)
        self._compiled_patterns = self._compile_patterns()
        self._compiled_boosters = [
            (re.compile(pattern, re.IGNORECASE), boost)
            for pattern, boost in SEVERITY_BOOSTERS
        ]
        logger.info("EventClassifier initialized with %d categories",
                     len(CATEGORY_KEYWORDS))

    def _compile_patterns(self) -> dict[str, list[re.Pattern]]:
        """Pre-compile all regex patterns for performance.

        Returns:
            Dict mapping categories to compiled pattern lists.
        """
        compiled: dict[str, list[re.Pattern]] = {}
        for category, patterns in CATEGORY_KEYWORDS.items():
            compiled[category] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]
        return compiled

    def classify(self, title: str, content: str) -> ClassifiedEvent:
        """Classify an article into an event category.

        Scores each category by counting keyword matches, picks the
        highest-scoring category. Severity is based on match density
        plus severity boosters.

        Args:
            title: Cleaned article title.
            content: Cleaned article body.

        Returns:
            ClassifiedEvent with category, severity, summary, and confidence.
        """
        # Title matches count double
        text = f"{title} {title} {content}"

        # Score each category
        scores: dict[str, float] = {}
        matches_by_cat: dict[str, list[str]] = {}

        for category, patterns in self._compiled_patterns.items():
            cat_matches: list[str] = []
            score = 0.0

            for pattern in patterns:
                found = pattern.findall(text)
                if found:
                    cat_matches.extend(found)
                    # Each unique pattern match adds 1.0, repeats add 0.25
                    score += 1.0 + (len(found) - 1) * 0.25

            scores[category] = score
            matches_by_cat[category] = cat_matches

        # Pick the best category
        best_category = max(scores, key=scores.get)
        best_score = scores[best_category]

        if best_score == 0:
            # No matches — default to SENTIMENT as catch-all
            best_category = "SENTIMENT"
            confidence = 0.1
        else:
            # Confidence based on score margin over second-best
            sorted_scores = sorted(scores.values(), reverse=True)
            second_best = sorted_scores[1] if len(sorted_scores) > 1 else 0
            margin = best_score - second_best
            confidence = min(1.0, 0.3 + margin * 0.15)

        # Calculate severity
        severity = self._calculate_severity(text, best_score)

        # Generate summary
        summary = self._generate_summary(title, best_category)

        matched = matches_by_cat.get(best_category, [])

        event = ClassifiedEvent(
            category=best_category,
            severity=severity,
            summary=summary,
            confidence=confidence,
            matched_keywords=list(set(matched))[:10],
        )

        logger.debug("Classified as %s (severity=%d, confidence=%.2f): %s",
                      event.category, event.severity, event.confidence, title[:80])
        return event

    def _calculate_severity(self, text: str, base_score: float) -> int:
        """Calculate event severity (1-5) from match score and boosters.

        Args:
            text: Full text for booster matching.
            base_score: Raw keyword match score.

        Returns:
            Severity rating 1-5.
        """
        # Base severity from keyword density
        if base_score >= 8:
            severity = 4
        elif base_score >= 5:
            severity = 3
        elif base_score >= 2:
            severity = self._default_severity
        else:
            severity = 1

        # Apply boosters
        for pattern, boost in self._compiled_boosters:
            if pattern.search(text):
                severity += boost

        return max(1, min(5, severity))

    def _generate_summary(self, title: str, category: str) -> str:
        """Generate a brief event summary from the title and category.

        Args:
            title: Article title.
            category: Classified event category.

        Returns:
            Summary string.
        """
        # Truncate title if too long
        max_len = 200
        if len(title) <= max_len:
            return f"[{category}] {title}"
        return f"[{category}] {title[:max_len]}..."

    def classify_batch(
        self,
        articles: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], ClassifiedEvent]]:
        """Classify a batch of articles.

        Args:
            articles: List of article dicts with 'title' and 'content' keys.

        Returns:
            List of (article, event) tuples.
        """
        results = []
        for article in articles:
            title = article.get("title", "")
            content = article.get("content", "")
            event = self.classify(title, content)
            results.append((article, event))

        logger.info("Classified %d articles", len(results))
        return results
