"""Search Intent Classification Engine.

Classifies a keyword into one of four intent types based on signal words:
- INFORMATIONAL
- COMMERCIAL
- TRANSACTIONAL
- NAVIGATIONAL

Falls back to SERP analysis if keyword signals are ambiguous.
"""

from enum import Enum
import structlog
import httpx
from src.config import settings

log = structlog.get_logger()


class IntentType(str, Enum):
    INFORMATIONAL = "informational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"
    NAVIGATIONAL = "navigational"


# Signal word lists
SIGNALS_INFORMATIONAL = [
    "what is", "how does", "explained", "guide to", "meaning of",
    "definition", "overview", "understand"
]

SIGNALS_COMMERCIAL = [
    "best", "comparison", "vs", "review", "alternative to",
    "top", "choose", "compare", "alternative"
]

SIGNALS_TRANSACTIONAL = [
    "cost", "pricing", "hire", "consultant", "service",
    "buy", "get", "book", "assessment"
]

SIGNALS_NAVIGATIONAL = [
    "kensara", "kensaraai", "login", "contact"
]


async def _fetch_serper_results(keyword: str) -> list[dict]:
    """Call Serper.dev Google Search API for Indian results."""
    if not settings.serper_api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": keyword, "gl": "in", "hl": "en", "num": 5},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("organic", [])[:5]
    except Exception as exc:
        log.error("intent_serper_error", keyword=keyword, error=str(exc))
        return []


async def classify_intent_with_serp(keyword: str) -> IntentType:
    """Classify intent based on the domains of top 5 SERP results."""
    results = await _fetch_serper_results(keyword)
    if not results:
        # Fallback if API fails or no key
        return IntentType.INFORMATIONAL

    commercial_count = 0
    transactional_count = 0
    navigational_count = 0

    for result in results:
        url = result.get("link", "").lower()
        title = result.get("title", "").lower()

        if "kensara" in url or "kensaraai" in url:
            navigational_count += 1
        elif "pricing" in url or "services" in url or "consult" in url:
            transactional_count += 1
        elif "best" in title or "review" in url or "vs" in title or "alternative" in title:
            commercial_count += 1

    # Heuristic based on SERP profile
    if navigational_count >= 2:
        return IntentType.NAVIGATIONAL
    elif commercial_count >= 2:
        return IntentType.COMMERCIAL
    elif transactional_count >= 2:
        return IntentType.TRANSACTIONAL
    
    return IntentType.INFORMATIONAL


async def classify_intent(keyword: str) -> IntentType:
    """Classify a keyword using signal words, falling back to SERP analysis."""
    kw_lower = keyword.lower()

    # Check Navigational first (brand names)
    for signal in SIGNALS_NAVIGATIONAL:
        if signal in kw_lower:
            return IntentType.NAVIGATIONAL

    # Check Commercial
    for signal in SIGNALS_COMMERCIAL:
        if signal in kw_lower:
            return IntentType.COMMERCIAL

    # Check Transactional
    for signal in SIGNALS_TRANSACTIONAL:
        if signal in kw_lower:
            return IntentType.TRANSACTIONAL

    # Check Informational
    for signal in SIGNALS_INFORMATIONAL:
        if signal in kw_lower:
            return IntentType.INFORMATIONAL

    # If no strong keyword signal, fallback to SERP-based classification
    log.info("intent_signal_ambiguous_using_serp", keyword=keyword)
    return await classify_intent_with_serp(keyword)
