"""RSS feed scraper — fetches privacy/DPDPA news from multiple sources.

Bug fix: feedparser.parse() is synchronous. Wrapped in asyncio.to_thread() to
avoid blocking the asyncio event loop.

Optional: Tavily API supplements RSS when TAVILY_API_KEY is configured.
"""
import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import feedparser
import httpx
import structlog
from pydantic import BaseModel

from src.config import settings
from src.scrapers.date_utils import is_recent_enough
from src.scrapers.regulatory_scrapers import (
    fetch_meity_gazette,
    fetch_meity_press_releases,
    fetch_dpbi_orders,
    fetch_cert_in_advisories,
    fetch_sebi_circulars,
    fetch_irdai_circulars,
    fetch_india_kanoon_judgments,
    fetch_ico_enforcement,
    fetch_iapp_resources,
    fetch_privacy_enforcement_press,
    fetch_appa_forum,
    fetch_data_guidance,
    fetch_dsci_news,
    NewsItem,
)
from src.scrapers.court_judgment_tracker import fetch_all_court_judgments
from src.scrapers.india_business_news import fetch_all_india_business_news

log = structlog.get_logger()

RSS_FEEDS = {
    # ICO (ico.org.uk) removed RSS after 2024 site redesign — scraped via fetch_ico_enforcement()
    # IAPP (iapp.org) has no public RSS — scraped via fetch_iapp_resources()
    "EDPB": "https://edpb.europa.eu/feed/news_en",
    "RBI": "https://rbi.org.in/Scripts/RSSCirculars.aspx",
    "ET Tech": "https://economictimes.indiatimes.com/tech/rssfeeds/13357220.cms",
    "LiveMint Tech": "https://www.livemint.com/rss/technology",
    "YourStory": "https://yourstory.com/feed",
    "Inc42": "https://inc42.com/feed/",
    "Entrackr": "https://entrackr.com/rss",
}

SEARCH_TERMS = [
    "dpdpa", "data protection", "privacy", "gdpr", "dpo", "dsar",
    "breach", "consent", "personal data", "india privacy", "meity",
    "data fiduciary", "data principal", "compliance",
]

TAVILY_SEARCH_QUERY = (
    "DPDPA GDPR privacy compliance India enforcement penalty breach 2025 2026"
)




async def fetch_rss_feeds() -> list[NewsItem]:
    """Fetch all RSS feeds + optional Tavily results + regulatory scrapers. Return deduplicated news items."""
    # Use hour-based cache suffix to support 4-hour MeitY polling intervals
    cache_suffix = f"{date.today()}_{datetime.now().hour // 4}"
    cache_file = Path(settings.content_output_dir) / ".cache" / f"news_{cache_suffix}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        log.info("rss_cache_hit", date=str(date.today()), suffix=cache_suffix)
        try:
            return [NewsItem(**item) for item in json.loads(cache_file.read_text(encoding="utf-8"))]
        except Exception:
            pass

    # Gather RSS feeds, optional Tavily, and custom regulatory scrapers in parallel
    tasks = [
        _fetch_all_rss(),
        fetch_meity_gazette(),
        fetch_meity_press_releases(),
        fetch_dpbi_orders(),
        fetch_cert_in_advisories(),
        fetch_sebi_circulars(),
        fetch_irdai_circulars(),
        fetch_india_kanoon_judgments(),
        fetch_ico_enforcement(),       # ICO: HTML scraper (RSS discontinued)
        fetch_iapp_resources(),        # IAPP: HTML scraper (no public RSS)
        fetch_privacy_enforcement_press(),
        fetch_appa_forum(),
        fetch_data_guidance(),
        fetch_dsci_news(),
        fetch_all_court_judgments(),
        fetch_all_india_business_news(),
    ]
    
    if settings.tavily_api_key:
        tasks.append(_fetch_tavily())
    else:
        log.debug("tavily_skipped", reason="TAVILY_API_KEY not configured")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            log.error("news_source_failed", task_index=i, error=str(result))
        else:
            items.extend(result)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique_items: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique_items.append(item)

    relevant = _filter_relevant(unique_items)
    
    # Deduplicate against db historical items using Cosine Similarity & URL/ID check
    from src.queue.job_queue import job_queue
    from src.scrapers.deduplicator import is_duplicate_story, get_word_frequencies
    import hashlib
    
    recent_fps = job_queue.get_recent_processed_fingerprints()
    recent_fps_json = [fp for _, fp in recent_fps]
    
    final_relevant = []
    for item in relevant:
        story_id = hashlib.md5(item.url.encode("utf-8")).hexdigest()
        if job_queue.is_url_processed(item.url) or job_queue.is_story_processed(story_id):
            log.debug("story_already_processed", url=item.url)
            continue
        
        text_to_compare = item.title + " " + item.summary
        is_dup, score = is_duplicate_story(text_to_compare, recent_fps_json)
        if is_dup:
            log.info("duplicate_story_suppressed", title=item.title[:60], score=score)
            # Record as suppressed duplicate
            job_queue.record_processed_story(
                story_id=story_id,
                source=item.source,
                headline=item.title,
                url=item.url,
                score=0,
                intent_tag="duplicate",
                fingerprint_vector=json.dumps(get_word_frequencies(text_to_compare)),
                action_taken="suppressed"
            )
            continue
            
        final_relevant.append(item)

    cache_file.write_text(json.dumps([i.model_dump() for i in final_relevant]), encoding="utf-8")
    log.info("rss_done", total=len(unique_items), relevant=len(final_relevant))
    return final_relevant


async def _fetch_all_rss() -> list[NewsItem]:
    """Fetch all configured RSS feeds concurrently using asyncio.to_thread."""
    feed_tasks = [
        _fetch_single_rss(source, url)
        for source, url in RSS_FEEDS.items()
    ]
    results = await asyncio.gather(*feed_tasks, return_exceptions=True)

    items: list[NewsItem] = []
    for result in results:
        if isinstance(result, BaseException):
            log.error("rss_feed_gather_error", error=str(result))
        else:
            items.extend(result)
    return items


async def _fetch_single_rss(source: str, url: str) -> list[NewsItem]:
    """Fetch a single RSS feed. Uses asyncio.to_thread to avoid blocking event loop.

    feedparser.parse() is a synchronous, blocking network call.
    Wrapping in asyncio.to_thread() runs it in a thread pool executor.
    """
    try:
        # FIX: was `feedparser.parse(url)` — synchronous, blocks event loop
        feed = await asyncio.to_thread(feedparser.parse, url)
        items = []
        # UI override for news_max_age_days takes priority over .env/settings
        try:
            from src.context.platform_stats import get_platform_stats as _gps
            max_age = int(_gps().get("news_max_age_days", settings.news_max_age_days))
        except Exception:
            max_age = settings.news_max_age_days

        skipped_stale = 0
        for entry in feed.entries[:20]:
            pub_date_str = entry.get("published", str(date.today()))
            if not is_recent_enough(pub_date_str, max_age):
                skipped_stale += 1
                continue
            items.append(NewsItem(
                title=entry.get("title", "").strip(),
                url=entry.get("link", ""),
                summary=entry.get("summary", "")[:500].strip(),
                published_date=pub_date_str,
                source=source,
            ))
        if skipped_stale:
            log.info(
                "rss_stale_entries_skipped",
                source=source,
                skipped=skipped_stale,
                max_age_days=settings.news_max_age_days,
            )
        log.info("rss_fetched", source=source, total=len(feed.entries), kept=len(items))
        return items
    except Exception as exc:
        log.error("rss_fetch_failed", source=source, error=str(exc))
        return []


async def _fetch_tavily() -> list[NewsItem]:
    """Fetch recent privacy/DPDPA news via Tavily Search API.

    Only called when settings.tavily_api_key is non-empty.
    Returns up to 10 NewsItem objects from Tavily results.
    """
    payload = {
        "api_key": settings.tavily_api_key,
        "query": TAVILY_SEARCH_QUERY,
        "max_results": 10,
        "search_depth": "basic",
        "include_answer": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        items = []
        for result in data.get("results", []):
            items.append(NewsItem(
                title=result.get("title", "").strip(),
                url=result.get("url", ""),
                summary=result.get("content", "")[:500].strip(),
                published_date=str(date.today()),
                source="Tavily",
            ))

        log.info("tavily_fetched", count=len(items))
        return items

    except httpx.HTTPStatusError as exc:
        log.error(
            "tavily_http_error",
            status_code=exc.response.status_code,
            error=str(exc),
        )
        return []
    except httpx.RequestError as exc:
        log.error("tavily_request_error", error=str(exc))
        return []


def _filter_relevant(items: list[NewsItem]) -> list[NewsItem]:
    """Keep items that mention at least one search term."""
    result = []
    for item in items:
        text = (item.title + " " + item.summary).lower()
        if any(term in text for term in SEARCH_TERMS):
            result.append(item)
    return result
