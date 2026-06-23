"""News scout agent — scores news items for DPDPA/GDPR relevance.

Scoring uses a pure Python keyword relevance function — no LLM call needed.
This is fast, free, and deterministic. LLMs are reserved for content generation.
"""
import asyncio
import structlog
from pydantic import BaseModel

from src.scrapers.rss_scraper import NewsItem

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
]

_DEFAULT_ANGLE = (
    "Privacy compliance update — what Indian enterprises need to know and do now"
)


class ScoredNewsItem(BaseModel):
    item: NewsItem
    relevance_score: int  # 0–10
    why_relevant: str
    suggested_angle: str  # hook for a blog post about this story


def score_relevance(item: NewsItem) -> int:
    """Score a news item's relevance to Indian DPDPA/GDPR compliance buyers.

    Returns an integer 0–10. Pure keyword matching — no LLM, no network call.
    High-relevance keywords score +2 each; medium-relevance +1 each. Capped at 10.
    """
    text = (item.title + " " + item.summary).lower()
    score = 0

    for kw in HIGH_RELEVANCE_KEYWORDS:
        if kw in text:
            score += 2

    for kw in MEDIUM_RELEVANCE_KEYWORDS:
        if kw in text:
            score += 1

    return min(10, score)


def _build_why_relevant(item: NewsItem, score: int) -> str:
    """Generate a short 'why relevant' explanation based on matched keywords."""
    text = (item.title + " " + item.summary).lower()

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

    # Run scoring concurrently via asyncio.to_thread (pure CPU/in-memory work,
    # but keeps async interface consistent for future network-based scoring)
    tasks = [asyncio.to_thread(_score_item_sync, item) for item in items]
    scored_all: list[ScoredNewsItem] = await asyncio.gather(*tasks)  # type: ignore[assignment]

    # Filter and log
    high_scoring = []
    for result in scored_all:
        log.debug(
            "news_scored",
            title=result.item.title[:60],
            score=result.relevance_score,
            source=result.item.source,
        )
        if result.relevance_score >= 5:
            high_scoring.append(result)

    high_scoring.sort(key=lambda x: x.relevance_score, reverse=True)
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
