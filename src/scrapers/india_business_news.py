"""Indian Business News scraper — Economic Times, YourStory, Inc42, Entrackr.

Provides *dedicated*, DPDPA-targeted intelligence from the four Indian
business/startup publications that Indian DPOs and compliance teams read most.

Architecture:
  * Each fetcher first attempts a direct BeautifulSoup scrape of the
    publication's privacy/DPDPA tag page.
  * On any failure it falls back to a targeted Tavily ``site:`` query so the
    pipeline always produces results when Tavily is configured.
  * ``fetch_all_india_business_news()`` is the single entry-point wired into
    ``rss_scraper.fetch_rss_feeds()``.

Note: Economic Times, Inc42, Entrackr, and YourStory RSS feeds are already
consumed by the generic RSS layer (``RSS_FEEDS`` in rss_scraper.py).  This
module adds a *complementary* DPDPA-filtered scrape so niche privacy stories
that don't surface prominently in the main RSS stream are still captured.
"""
from __future__ import annotations

import asyncio
from datetime import date

import httpx
import structlog
from bs4 import BeautifulSoup

from src.scrapers.regulatory_scrapers import HEADERS, NewsItem, _tavily_fallback_search

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tag / topic page URLs — direct scrape targets
# ---------------------------------------------------------------------------

_ET_PRIVACY_TAG_URL = "https://economictimes.indiatimes.com/topic/data-privacy"
_INC42_PRIVACY_TAG_URL = "https://inc42.com/tag/data-privacy/"
_YOURSTORY_DPDPA_TAG_URL = "https://yourstory.com/tag/dpdpa"
_ENTRACKR_PRIVACY_TAG_URL = "https://entrackr.com/tag/data-privacy/"

# ---------------------------------------------------------------------------
# Tavily fallback queries — site-scoped, DPDPA-targeted
# ---------------------------------------------------------------------------

_ET_QUERY = (
    "site:economictimes.indiatimes.com "
    'DPDPA OR "data protection" OR "privacy law" OR "data breach" '
    "India 2025 2026"
)
_INC42_QUERY = (
    "site:inc42.com "
    'DPDPA OR "digital personal data" OR privacy compliance India 2025 2026'
)
_YOURSTORY_QUERY = (
    "site:yourstory.com "
    'DPDPA OR "data protection" OR privacy compliance startup India 2025 2026'
)
_ENTRACKR_QUERY = (
    "site:entrackr.com "
    "DPDPA OR data privacy OR privacy compliance India 2025 2026"
)
_LIVEMINT_QUERY = (
    "site:livemint.com "
    'DPDPA OR "data protection" OR privacy compliance India 2025 2026'
)
_BUSINESS_STANDARD_QUERY = (
    "site:business-standard.com "
    'DPDPA OR "personal data protection" OR privacy India 2025 2026'
)


# ---------------------------------------------------------------------------
# Generic direct-scrape helper
# ---------------------------------------------------------------------------


async def _scrape_tag_page(
    url: str,
    source_name: str,
    card_selectors: list[str],
    title_selectors: list[str],
    summary_selectors: list[str],
    base_url: str,
    fallback_query: str,
) -> list[NewsItem]:
    """Generic scraper for a publication's privacy/DPDPA tag page.

    Tries each CSS selector tuple in order; on any failure falls back to Tavily.
    """
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=12.0, follow_redirects=True
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            # Find article cards
            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    break

            items: list[NewsItem] = []
            for card in cards[:15]:
                # Find title + link
                title_elem = None
                for sel in title_selectors:
                    title_elem = card.select_one(sel)
                    if title_elem:
                        break
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                href = title_elem.get("href", "")
                if href and not href.startswith("http"):
                    href = base_url.rstrip("/") + href

                # Find summary
                summary_elem = None
                for sel in summary_selectors:
                    summary_elem = card.select_one(sel)
                    if summary_elem:
                        break
                summary = (
                    summary_elem.get_text(strip=True)[:400]
                    if summary_elem
                    else title
                )

                items.append(
                    NewsItem(
                        title=title,
                        url=href,
                        summary=summary,
                        published_date=str(date.today()),
                        source=source_name,
                    )
                )

            if items:
                log.info("direct_scrape_success", source=source_name, count=len(items))
                return items

    except Exception as exc:  # noqa: BLE001
        log.warning("direct_scrape_failed", source=source_name, error=str(exc))

    return await _tavily_fallback_search(fallback_query, source_name)


# ---------------------------------------------------------------------------
# Per-publication fetchers
# ---------------------------------------------------------------------------


async def fetch_et_business_news() -> list[NewsItem]:
    """Fetch Economic Times articles on DPDPA and data privacy."""
    log.info("fetch_et_business_news_start")
    return await _scrape_tag_page(
        url=_ET_PRIVACY_TAG_URL,
        source_name="ET Business News",
        card_selectors=["div.eachStory", "article", "div.story-box", "div.clDetail"],
        title_selectors=["h3 a", "h2 a", ".story-title a", "a"],
        summary_selectors=["p.synopsis", "p", ".abstract"],
        base_url="https://economictimes.indiatimes.com",
        fallback_query=_ET_QUERY,
    )


async def fetch_inc42_news() -> list[NewsItem]:
    """Fetch Inc42 articles on DPDPA and data privacy."""
    log.info("fetch_inc42_news_start")
    return await _scrape_tag_page(
        url=_INC42_PRIVACY_TAG_URL,
        source_name="Inc42",
        card_selectors=["article", "div.post-item", "div.story-card", "div.td-block-span6"],
        title_selectors=["h2 a", "h3 a", ".entry-title a", "a"],
        summary_selectors=[".excerpt", "p", ".entry-summary", ".td-excerpt"],
        base_url="https://inc42.com",
        fallback_query=_INC42_QUERY,
    )


async def fetch_yourstory_news() -> list[NewsItem]:
    """Fetch YourStory DPDPA articles.

    YourStory tag pages are behind Cloudflare bot protection which blocks plain
    httpx requests (no real browser TLS fingerprint).  We use curl_cffi with
    Chrome impersonation to bypass the challenge.  Falls back to Tavily on any
    failure.
    """
    log.info("fetch_yourstory_news_start")
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as session:
            r = await session.get(
                _YOURSTORY_DPDPA_TAG_URL,
                impersonate="chrome",
                timeout=15,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            cards: list = []
            for selector in ["article", "div.story-card", "div.post-item", "div.ys-card"]:
                cards = soup.select(selector)
                if cards:
                    break

            items: list[NewsItem] = []
            for card in cards[:15]:
                title_elem = None
                for sel in ["h2 a", "h3 a", ".story-title a", "a"]:
                    title_elem = card.select_one(sel)
                    if title_elem:
                        break
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                href = title_elem.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://yourstory.com" + href

                summary_elem = None
                for sel in ["p", ".description", ".excerpt", ".ys-excerpt"]:
                    summary_elem = card.select_one(sel)
                    if summary_elem:
                        break
                summary = summary_elem.get_text(strip=True)[:400] if summary_elem else title

                items.append(NewsItem(
                    title=title,
                    url=href,
                    summary=summary,
                    published_date=str(date.today()),
                    source="YourStory",
                ))

            if items:
                log.info("yourstory_cffi_success", count=len(items))
                return items

            log.warning("yourstory_cffi_no_cards", html_len=len(r.text))

    except ImportError:
        log.warning("curl_cffi_not_installed", hint="pip install curl-cffi")
    except Exception as exc:
        log.warning("yourstory_cffi_failed", error=str(exc))

    return await _tavily_fallback_search(_YOURSTORY_QUERY, "YourStory")


async def fetch_entrackr_news() -> list[NewsItem]:
    """Fetch Entrackr articles on DPDPA and data privacy."""
    log.info("fetch_entrackr_news_start")
    return await _scrape_tag_page(
        url=_ENTRACKR_PRIVACY_TAG_URL,
        source_name="Entrackr",
        card_selectors=["article", "div.post", "div.post-item"],
        title_selectors=["h2 a", "h3 a", "a"],
        summary_selectors=["p", ".excerpt"],
        base_url="https://entrackr.com",
        fallback_query=_ENTRACKR_QUERY,
    )


async def fetch_livemint_news() -> list[NewsItem]:
    """Fetch LiveMint articles on DPDPA and data privacy via Tavily."""
    log.info("fetch_livemint_news_start")
    return await _tavily_fallback_search(_LIVEMINT_QUERY, "LiveMint")


async def fetch_business_standard_news() -> list[NewsItem]:
    """Fetch Business Standard articles on DPDPA and data privacy via Tavily."""
    log.info("fetch_business_standard_news_start")
    return await _tavily_fallback_search(_BUSINESS_STANDARD_QUERY, "Business Standard")


async def fetch_all_india_business_news() -> list[NewsItem]:
    """Aggregate all Indian business news sources into one deduplicated list.

    Called by ``rss_scraper.fetch_rss_feeds`` alongside other regulatory
    scrapers so Indian business intelligence flows into the daily pipeline.
    """
    log.info("fetch_all_india_business_news_start")

    tasks = [
        fetch_et_business_news(),
        fetch_inc42_news(),
        fetch_yourstory_news(),
        fetch_entrackr_news(),
        fetch_livemint_news(),
        fetch_business_standard_news(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            log.error("india_business_news_source_failed", error=str(result))
            continue
        for item in result:
            if item.url not in seen:
                seen.add(item.url)
                items.append(item)

    log.info("fetch_all_india_business_news_done", total=len(items))
    return items
