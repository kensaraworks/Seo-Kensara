"""News scout agent — scores news items for DPDPA/GDPR relevance.

Scoring uses a pure Python keyword relevance function — no LLM call needed.
This is fast, free, and deterministic. LLMs are reserved for content generation.
"""
import asyncio
import re
import structlog
from pydantic import BaseModel

from src.scrapers.rss_scraper import NewsItem
from src.scrapers.date_utils import recency_score_delta

log = structlog.get_logger()

# High-relevance: directly about Indian data protection law or enforcement
HIGH_RELEVANCE_KEYWORDS = [
    "dpdpa",
    "data protection board",
    "meity",
    "personal data protection",
    "dsar",
    "consent management",
    "breach notification",
    "72 hour",
    "72-hour",
    "enforcement",
    "penalty",
    "fine",
    "india privacy",
    "dpo",
    "data principal",
    "data fiduciary",
    "significant data fiduciary",
    "privacy law india",
    "pdpb",
    "digital personal data",
    "data protection act india",
    "meitygov",
    "consent framework",
]

# Medium-relevance: global privacy topics that Indian DPOs care about
MEDIUM_RELEVANCE_KEYWORDS = [
    "gdpr",
    "ccpa",
    "ico",
    "edpb",
    "data breach",
    "privacy law",
    "compliance",
    "data protection",
    "personal data",
    "consent",
    "privacy regulation",
    "data localization",
    "cross-border transfer",
    "supervisory authority",
    "legitimate interest",
    "right to erasure",
    "data minimisation",
    "privacy by design",
]

# --- Suggested angle templates (keyword-based, no LLM) ---
_ANGLE_TEMPLATES: list[tuple[str, str]] = [
    ("dpdpa", "What this means for Indian companies under DPDPA — action steps for DPOs"),
    ("data protection board", "How the Data Protection Board ruling affects your DPDPA compliance posture"),
    ("meity", "MeitY update decoded — what Indian data fiduciaries must do now"),
    ("dsar", "DSAR processing in the spotlight — how to automate and stay compliant"),
    ("breach notification", "72-hour breach clock — what this enforcement action teaches Indian companies"),
    ("gdpr", "GDPR fine analysis — parallel obligations under India's DPDPA"),
    ("ico", "ICO ruling decoded — lessons for Indian data fiduciaries facing similar obligations"),
    ("edpb", "EDPB guidance decoded — how Indian companies with EU exposure must respond"),
    ("penalty", "Compliance fine analysis — how to avoid this penalty under DPDPA"),
    ("enforcement", "Enforcement action breakdown — 3 steps Indian DPOs must take this week"),
    ("consent", "Consent compliance gap — how Indian companies can close it before enforcement begins"),
    ("data breach", "Data breach case study — 72-hour DPDPA notification requirements explained"),
    # Court judgment angle templates
    ("judgment", "Court ruling decoded — DPDPA compliance implications for Indian enterprises"),
    ("high court", "High Court ruling on privacy — what every Indian data fiduciary must do next"),
    ("supreme court", "Supreme Court privacy judgment — updated DPDPA compliance checklist for Indian DPOs"),
    ("adjudication", "DPBI adjudication order — lessons and remediation steps for data fiduciaries"),
    ("right to privacy", "Right to privacy ruling — how it reshapes your DPDPA obligations"),
    # Indian business news angle templates
    ("startup", "DPDPA and India's startup ecosystem — compliance blueprint for growth-stage companies"),
    ("fintech", "Fintech DPDPA compliance — how India's fast-growing sector is adapting"),
    ("edtech", "EdTech data protection — DPDPA obligations for India's education platforms"),
    ("healthtech", "HealthTech under DPDPA — data fiduciary obligations for Indian health platforms"),
]

_DEFAULT_ANGLE = (
    "Privacy compliance update — what Indian enterprises need to know and do now"
)


class ScoredNewsItem(BaseModel):
    item: NewsItem
    relevance_score: int  # 0–10
    why_relevant: str
    suggested_angle: str  # hook for a blog post about this story


def qualifies_for_tier3(item: NewsItem) -> bool:
    """Gate Tier 3 newsjacking on an actual reported EVENT, not a generic status
    update (spec CHANGE-A5).

    Keyword-based relevance scoring alone can push a story like "MeitY has been
    actively working on implementation" above the Tier 3 threshold even though
    nothing new happened — that has been true since 2023. A story only
    qualifies for Tier 3 if it carries at least 2 of these specificity signals.
    """
    text = (item.title + " " + item.summary).lower()
    signals = [
        bool(re.search(r'₹\s*\d+', text)),
        bool(re.search(r'\brule\s+\d+\b|\bsection\s+\d+\(\d+\)', text)),
        bool(re.search(r'\border\b|\bpenalty\b|\bfine\b|\bnotice\b|\bdirective\b|\badjudicat', text)),
        bool(re.search(r'\bsection\s+\d+\b', text)),
    ]
    return sum(signals) >= 2


INDIA_SOURCES = {
    "meity", "meity gazette", "meity press releases", "dpbi", "cert-in", "rbi",
    "sebi", "irdai", "indian court judgments", "et tech", "livemint tech",
    "yourstory", "inc42", "entrackr", "data security council of india",
    # Court judgment tracker sources
    "indiankanoon", "supreme court of india", "delhi high court",
    "bombay high court", "madras high court", "karnataka high court",
    "allahabad high court", "gujarat high court", "data protection board (adjudication)",
    # Indian business news sources
    "et business news", "livemint", "business standard",
}

INDIAN_COMPANIES = {
    "tcs", "infosys", "wipro", "reliance", "jio", "airtel", "hdfc", "icici", "sbi",
    "paytm", "phonepe", "zerodha", "ola", "uber india", "zomato", "swiggy", "tata",
    "adani", "lic", "byjus", "flipkart", "meesho", "nykaa", "cred"
}

ENFORCEMENT_WORDS = {
    "penalty", "penalize", "penalized", "fine", "fined", "adjudicate", "prosecute",
    "enforcement", "order", "investigate", "investigation"
}

URGENCY_WORDS = {
    "effective immediately", "deadline", "urgency", "emergency", "timeline",
    "urgent", "immediately", "immediate"
}

def score_relevance(item: NewsItem) -> int:
    """Score a news item's relevance to Indian DPDPA/GDPR compliance buyers.

    Base keyword score is capped at 20. A recency delta (signal 12) is applied
    on top and may push the final value negative for stale articles.
    No LLM or network call — pure in-memory computation.
    """
    text = (item.title + " " + item.summary).lower()
    score = 0

    # 1. Base keyword checks
    for kw in HIGH_RELEVANCE_KEYWORDS:
        if kw in text:
            score += 2

    for kw in MEDIUM_RELEVANCE_KEYWORDS:
        if kw in text:
            score += 1

    # 2. India-origin source (+2)
    source_lower = item.source.lower()
    if source_lower in INDIA_SOURCES or any(src in source_lower for src in ["meity", "dpbi", "cert-in", "rbi", "sebi", "irdai"]):
        score += 2

    # 3. Penalty amount mentioned (+3)
    if re.search(r'(?:₹|rs\.?|rupees?|\b\d+\s*(?:lakh|crore|million|billion)\b)', text):
        score += 3

    # 4. Named Indian company involved (+2)
    has_company = any(c in text for c in INDIAN_COMPANIES) or bool(re.search(r'\b[a-z0-9]+\s+(?:pvt\.?\s+ltd\.?|ltd\.?|llp)\b', text))
    if has_company:
        score += 2

    # 5. Specific DPDPA Rule or Section number cited (+2)
    if re.search(r'\b(?:section|sec\.?|rule|clause|article|art\.?)\s+\d+\b', text) or "schedule i" in text or "schedule ii" in text:
        score += 2

    # 6. Enforcement action (+2)
    if any(w in text for w in ENFORCEMENT_WORDS):
        score += 2

    # 7. Temporal urgency signal (+2)
    if any(w in text for w in URGENCY_WORDS):
        score += 2

    # 8. RBI + DPDPA intersection bonus (+3)
    is_rbi = "rbi" in source_lower or "reserve bank" in source_lower
    is_dpdpa = any(kw in text for kw in ["dpdpa", "digital personal data", "data protection board", "dpdp rules"])
    if is_rbi and is_dpdpa:
        score += 3

    # 9. Custom source scores (+1 or +2)
    if "iapp resources" in source_lower or "iapp.org/resources" in item.url:
        score += 1
    elif "privacy enforcement" in source_lower or "privacyenforcement.net" in item.url:
        score += 1
    elif "appa forum" in source_lower or "appaforum.org" in item.url:
        score += 1
    elif "dataguidance" in source_lower or "dataguidance.com" in item.url:
        score += 2
    elif "data security council" in source_lower or "dsci" in source_lower or "dsci.in" in item.url:
        score += 2

    # 10. Court judgment sources — highest intelligence value (+4)
    # A DPDPA ruling from any court creates authoritative content opportunities
    is_court_source = any(
        kw in source_lower for kw in [
            "indiankanoon", "supreme court", "high court",
            "data protection board (adjudication)", "nclt", "tdsat", "cci",
        ]
    ) or "indiankanoon.org" in item.url
    if is_court_source:
        score += 4
        # Additional bonus when section/rule numbers cited (+2 already handled above,
        # but boost again for judgments as they cite precise legal provisions)
        if re.search(r'\b(?:section|sec\.?|rule|art\.?)\s+\d+\b', text):
            score += 1

    # 11. Indian business news with DPDPA angle (+2)
    is_india_biz_source = any(
        kw in source_lower for kw in [
            "et business news", "inc42", "yourstory", "entrackr",
            "livemint", "business standard",
        ]
    )
    if is_india_biz_source:
        score += 2

    # 12. Recency signal — rewards fresh articles, penalises stale ones.
    # Applied after the keyword cap so a day-old story can exceed 20 and an
    # old one can go negative, making the >= 6 threshold in the caller
    # the effective recency gate.
    base_score = min(20, score)
    return base_score + recency_score_delta(item.published_date, court_source=is_court_source)


def _build_why_relevant(item: NewsItem, score: int) -> str:
    """Generate a short 'why relevant' explanation based on matched keywords."""
    text = (item.title + " " + item.summary).lower()
    source_lower = item.source.lower()

    # Court judgment signals — highest editorial priority
    is_court_source = any(
        kw in source_lower for kw in [
            "indiankanoon", "supreme court", "high court",
            "data protection board (adjudication)",
        ]
    ) or "indiankanoon.org" in item.url
    if is_court_source:
        return "Indian court ruling on privacy or DPDPA — authoritative legal signal for compliance content."

    if "dpdpa" in text or "data protection board" in text or "meity" in text:
        return "Directly covers Indian data protection law — critical for DPDPA compliance teams."
    if "dsar" in text:
        return "DSAR obligations affect every company holding Indian personal data."
    if "breach" in text or "72 hour" in text or "72-hour" in text:
        return "Breach notification timelines are a top compliance risk for Indian enterprises."
    if "penalty" in text or "fine" in text or "enforcement" in text:
        return "Enforcement action demonstrates real regulatory risk — motivates compliance spend."
    if "gdpr" in text or "ico" in text or "edpb" in text:
        return "GDPR developments set precedent for DPDPA enforcement expected in India."
    if "consent" in text:
        return "Consent management is a core DPDPA obligation for all data fiduciaries."
    if any(kw in source_lower for kw in ["inc42", "yourstory", "et business", "entrackr", "livemint"]):
        return "Indian business press coverage of privacy — signals boardroom-level compliance awareness."
    if score >= 5:
        return "Privacy compliance topic relevant to Indian DPOs and data fiduciaries."
    return "General privacy news with indirect relevance to Indian compliance landscape."


def _suggest_angle(item: NewsItem) -> str:
    """Pick a suggested blog angle based on keyword match — template-driven, no LLM."""
    text = (item.title + " " + item.summary).lower()

    for trigger, angle in _ANGLE_TEMPLATES:
        if trigger in text:
            return angle

    return _DEFAULT_ANGLE


def _score_item_sync(item: NewsItem) -> ScoredNewsItem:
    """Pure synchronous scoring — safe to call from asyncio.to_thread or directly."""
    score = score_relevance(item)
    return ScoredNewsItem(
        item=item,
        relevance_score=score,
        why_relevant=_build_why_relevant(item, score),
        suggested_angle=_suggest_angle(item),
    )


async def score_news_items(items: list[NewsItem]) -> list[ScoredNewsItem]:
    """Score each news item for DPDPA/GDPR relevance. Returns top items (score >= 5).

    Scoring is synchronous keyword matching, but the function is async to preserve
    the interface for callers and allow future LLM augmentation without breaking changes.
    Uses asyncio.gather() for parallel processing to maintain async contract.
    """
    if not items:
        log.warning("news_scout_no_items")
        return []

    high_scoring = await score_all_relevant_news_items(items)

    top = high_scoring[:3]

    log.info(
        "news_scout_done",
        total_items=len(items),
        scored_above_threshold=len(high_scoring),
        top_selected=len(top),
    )

    if top:
        for i, t in enumerate(top, 1):
            log.info(
                "news_top_story",
                rank=i,
                score=t.relevance_score,
                title=t.item.title[:70],
                angle=t.suggested_angle[:80],
            )

    return top


async def score_all_relevant_news_items(items: list[NewsItem]) -> list[ScoredNewsItem]:
    """Score and return all relevant items (score >= 6), sorted descending."""
    if not items:
        return []

    # Run scoring concurrently via asyncio.to_thread (pure CPU/in-memory work,
    # but keeps async interface consistent for future network-based scoring)
    tasks = [asyncio.to_thread(_score_item_sync, item) for item in items]
    scored_all: list[ScoredNewsItem] = await asyncio.gather(*tasks)  # type: ignore[assignment]

    high_scoring = []
    for result in scored_all:
        log.debug(
            "news_scored",
            title=result.item.title[:60],
            score=result.relevance_score,
            source=result.item.source,
        )
        if result.relevance_score >= 6:
            high_scoring.append(result)

    high_scoring.sort(key=lambda x: x.relevance_score, reverse=True)
    return high_scoring
