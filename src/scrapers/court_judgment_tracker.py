"""Indian Court Judgment Tracker — IndiaKanoon, Supreme Court, High Courts, DPBI.

Tracks DPDPA, privacy, and data-protection rulings across the Indian judicial
and quasi-judicial hierarchy.

Strategy:
  1. Direct BeautifulSoup scrape of IndiaKanoon search results.
  2. Tavily-backed targeted searches for Supreme Court & High Courts.
  3. Dedicated DPBI adjudication-order search (separate from the generic
     fetch_dpbi_orders stub in regulatory_scrapers.py which covers press
     releases only).

All functions return list[NewsItem] so they slot directly into the existing
news-pipeline without any model changes.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date

import httpx
import structlog
from bs4 import BeautifulSoup

from src.scrapers.regulatory_scrapers import HEADERS, NewsItem, _tavily_fallback_search

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Per-court Tavily search queries
# ---------------------------------------------------------------------------

_COURT_TAVILY_QUERIES: dict[str, str] = {
    "Supreme Court of India": (
        "Supreme Court India judgment DPDPA OR \"digital personal data\" "
        "OR \"right to privacy\" OR \"data protection\" 2024 2025 2026"
    ),
    "Delhi High Court": (
        "Delhi High Court judgment DPDPA OR \"personal data\" "
        "OR \"right to privacy\" OR \"data breach\" 2024 2025 2026"
    ),
    "Bombay High Court": (
        "Bombay High Court judgment DPDPA OR \"personal data\" "
        "OR privacy OR \"data breach\" 2024 2025 2026"
    ),
    "Madras High Court": (
        "Madras High Court judgment DPDPA OR \"personal data\" "
        "OR privacy OR \"data localisation\" 2024 2025 2026"
    ),
    "Karnataka High Court": (
        "Karnataka High Court judgment DPDPA data protection "
        "OR privacy OR \"personal data\" 2024 2025 2026"
    ),
}

# Tavily queries for DPBI adjudication orders (distinct from press-release stub)
_DPBI_ORDER_QUERIES: list[str] = [
    "site:dpboard.gov.in order adjudication penalty notice 2025 2026",
    "\"Data Protection Board of India\" adjudication order penalty fine 2025 2026",
    "DPBI order \"data fiduciary\" penalty fine adjudication India",
]

# Patterns used to heuristically identify the court from judgment text
_COURT_PATTERNS: list[tuple[str, str]] = [
    (r"supreme court of india|supreme court", "Supreme Court of India"),
    (r"delhi high court", "Delhi High Court"),
    (r"bombay high court", "Bombay High Court"),
    (r"madras high court", "Madras High Court"),
    (r"calcutta high court", "Calcutta High Court"),
    (r"karnataka high court", "Karnataka High Court"),
    (r"allahabad high court", "Allahabad High Court"),
    (r"gujarat high court", "Gujarat High Court"),
    (r"high court", "High Court"),
    (r"national company law tribunal|nclt", "NCLT"),
    (r"data protection board|dpbi|dpb of india", "Data Protection Board of India"),
    (r"telecom disputes|tdsat", "TDSAT"),
    (r"competition commission|cci", "Competition Commission of India"),
    (r"consumer disputes|ncdrc|consumer forum", "Consumer Forum"),
]

_DATE_PATTERNS: list[str] = [
    r"\b(\d{1,2}[\s/\-]\w+[\s/\-]\d{4})\b",   # 15 Jan 2025 / 15/01/2025
    r"\b(\w+ \d{1,2},?\s+\d{4})\b",             # January 15, 2025
    r"\b(\d{4}-\d{2}-\d{2})\b",                 # 2025-01-15
]


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------


async def fetch_indiankanoon_judgments() -> list[NewsItem]:
    """Scrape IndiaKanoon search for DPDPA and privacy judgments.

    Parses IndiaKanoon's HTML result cards (``div.result``).
    Falls back to a Tavily ``site:indiankanoon.org`` query on network/parse
    failure so the pipeline is never left empty.
    """
    log.info("fetch_indiankanoon_judgments_start")

    search_terms = (
        "dpdpa OR \"digital personal data\" "
        "OR \"personal data protection\" OR \"right to privacy\""
    )
    encoded = search_terms.replace(" ", "+").replace('"', "%22")
    url = f"https://indiankanoon.org/search/?formInput={encoded}&pagenum=0"

    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=15.0, follow_redirects=True
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            items: list[NewsItem] = []
            for div in soup.select("div.result")[:12]:
                anchor = div.select_one("h2 a") or div.select_one("a")
                if not anchor:
                    continue

                title = anchor.get_text(strip=True)
                href = anchor.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://indiankanoon.org" + href

                headnote = div.select_one("p.headnote") or div.select_one("p")
                summary = (
                    headnote.get_text(strip=True)[:500]
                    if headnote
                    else title
                )

                meta_text = div.get_text(" ", strip=True)
                court = _extract_court_name(meta_text)
                pub_date = _extract_date_from_text(meta_text)

                items.append(
                    NewsItem(
                        title=title,
                        url=href,
                        summary=summary,
                        published_date=pub_date,
                        source=(
                            f"IndiaKanoon — {court}" if court else "IndiaKanoon"
                        ),
                    )
                )

            if items:
                log.info("fetch_indiankanoon_success", count=len(items))
                return items

    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_indiankanoon_scrape_failed", error=str(exc))

    # Tavily fallback
    return await _tavily_fallback_search(
        'site:indiankanoon.org DPDPA OR "personal data protection" '
        'OR "right to privacy" judgment 2025 2026',
        "IndiaKanoon",
    )


async def fetch_supreme_court_privacy_orders() -> list[NewsItem]:
    """Search for Supreme Court of India data-protection orders via Tavily.

    The SCI portal (sci.gov.in) does not expose a machine-readable judgments
    feed, so Tavily is used as the primary extraction layer.  Two queries are
    attempted: a site-restricted one first, then a broader fallback.
    """
    log.info("fetch_supreme_court_privacy_orders_start")

    items = await _tavily_fallback_search(
        'site:sci.gov.in OR site:supremecourt.nic.in '
        'DPDPA OR "personal data" OR "right to privacy" '
        'OR "data protection" 2025 2026',
        "Supreme Court of India",
    )
    if not items:
        items = await _tavily_fallback_search(
            "Supreme Court India judgment privacy DPDPA "
            '"digital personal data protection" 2025 2026',
            "Supreme Court of India",
        )
    return items


async def fetch_high_court_privacy_judgments() -> list[NewsItem]:
    """Fetch DPDPA / privacy judgments from Delhi, Bombay, Madras, Karnataka High Courts.

    Each court is queried independently via Tavily so that failures in one
    query do not suppress results from others.
    """
    log.info("fetch_high_court_privacy_judgments_start")

    tasks = [
        _tavily_fallback_search(query, court)
        for court, query in _COURT_TAVILY_QUERIES.items()
        if court != "Supreme Court of India"
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    for result in results:
        if isinstance(result, BaseException):
            log.error("high_court_fetch_error", error=str(result))
        else:
            items.extend(result)
    return items


async def fetch_data_protection_board_orders() -> list[NewsItem]:
    """Search for Data Protection Board of India adjudication orders.

    Distinct from the ``fetch_dpbi_orders`` stub in regulatory_scrapers.py
    (which targets press-releases only).  Runs three targeted Tavily queries
    in parallel and deduplicates by URL.
    """
    log.info("fetch_dpbi_adjudication_orders_start")

    tasks = [
        _tavily_fallback_search(q, "Data Protection Board (Adjudication)")
        for q in _DPBI_ORDER_QUERIES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            log.error("dpbi_order_fetch_error", error=str(result))
            continue
        for item in result:
            if item.url not in seen:
                seen.add(item.url)
                items.append(item)
    return items


async def fetch_all_court_judgments() -> list[NewsItem]:
    """Aggregate all Indian court judgment sources into one deduplicated list.

    Called by ``rss_scraper.fetch_rss_feeds`` alongside other regulatory
    scrapers so judgment intelligence flows automatically into the daily
    news-scan → blog-generate pipeline.
    """
    log.info("fetch_all_court_judgments_start")

    tasks = [
        fetch_indiankanoon_judgments(),
        fetch_supreme_court_privacy_orders(),
        fetch_high_court_privacy_judgments(),
        fetch_data_protection_board_orders(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            log.error("court_judgment_source_failed", error=str(result))
            continue
        for item in result:
            if item.url not in seen:
                seen.add(item.url)
                items.append(item)

    log.info("fetch_all_court_judgments_done", total=len(items))
    return items


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_court_name(text: str) -> str:
    """Heuristically extract the court name from raw judgment text."""
    text_lower = text.lower()
    for pattern, court_name in _COURT_PATTERNS:
        if re.search(pattern, text_lower):
            return court_name
    return ""


def _extract_date_from_text(text: str) -> str:
    """Extract a date string from judgment text; falls back to today's date."""
    for pattern in _DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return str(date.today())
