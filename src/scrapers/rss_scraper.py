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
from src.scrapers.regulatory_scrapers import (
    fetch_meity_gazette,
    fetch_meity_press_releases,
    fetch_dpbi_orders,
    fetch_cert_in_advisories,
    fetch_sebi_circulars,
    fetch_irdai_circulars,
    fetch_india_kanoon_judgments,
    fetch_iapp_resources,
    fetch_privacy_enforcement_press,
    fetch_appa_forum,
    fetch_data_guidance,
    fetch_dsci_news,
    NewsItem,
)

log = structlog.get_logger()

RSS_FEEDS = {
    "ICO": "https://ico.org.uk/about-the-ico/news-and-events/feed/",
    "EDPB": "https://www.edpb.europa.eu/edpb/rss_en",
    "IAPP": "https://iapp.org/feed/",
    "RBI": "https://rbi.org.in/Scripts/RSSCirculars.aspx",
    "ET Tech": "https://economictimes.indiatimes.com/tech/rssfeeds/13357555.xml",
    "LiveMint Tech": "https://www.livemint.com/rss/technology",
    "YourStory": "https://yourstory.com/feed",
    "Inc42": "https://inc42.com/feed/",
    "Entrackr": "https://entrackr.com/feed/",
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
        fetch_iapp_resources(),
        fetch_privacy_enforcement_press(),
        fetch_appa_forum(),
        fetch_data_guidance(),
        fetch_dsci_news(),
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
        for entry in feed.entries[:20]:
            items.append(NewsItem(
                title=entry.get("title", "").strip(),
                url=entry.get("link", ""),
                summary=entry.get("summary", "")[:500].strip(),
                published_date=entry.get("published", str(date.today())),
                source=source,
            ))
        log.info("rss_fetched", source=source, count=len(feed.entries))
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
