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

log = structlog.get_logger()

RSS_FEEDS = {
    "ICO": "https://ico.org.uk/about-the-ico/news-and-events/feed/",
    "EDPB": "https://www.edpb.europa.eu/edpb/rss_en",
    "IAPP": "https://iapp.org/feed/",
}

SEARCH_TERMS = [
    "dpdpa", "data protection", "privacy", "gdpr", "dpo", "dsar",
    "breach", "consent", "personal data", "india privacy", "meity",
    "data fiduciary", "data principal", "compliance",
]

TAVILY_SEARCH_QUERY = (
    "DPDPA GDPR privacy compliance India enforcement penalty breach 2025 2026"
)


class NewsItem(BaseModel):
    title: str
    url: str
    summary: str
    published_date: str
    source: str


async def fetch_rss_feeds() -> list[NewsItem]:
    """Fetch all RSS feeds + optional Tavily results. Return deduplicated news items."""
    cache_file = Path(settings.content_output_dir) / ".cache" / f"news_{date.today()}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        log.info("rss_cache_hit", date=str(date.today()))
        return [NewsItem(**item) for item in json.loads(cache_file.read_text())]

    # Gather RSS feeds and optional Tavily in parallel
    tasks = [_fetch_all_rss()]
    if settings.tavily_api_key:
        tasks.append(_fetch_tavily())
    else:
        log.debug("tavily_skipped", reason="TAVILY_API_KEY not configured")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    for result in results:
        if isinstance(result, BaseException):
            log.error("news_source_failed", error=str(result))
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
    cache_file.write_text(json.dumps([i.model_dump() for i in relevant]))
    log.info("rss_done", total=len(unique_items), relevant=len(relevant))
    return relevant


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
