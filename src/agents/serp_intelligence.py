"""SERP Intelligence Module.

Provides advanced functionality to fetch rich SERP data (Serper.dev)
and concurrently scrape competitor pages for word counts and H2 structures.
"""
import asyncio
import structlog
import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, List

from src.config import settings
from src.context.builder import SerpIntelligence

log = structlog.get_logger()

async def fetch_advanced_serp(keyword: str) -> Dict[str, Any]:
    """Fetch advanced SERP data from Serper.dev."""
    if not settings.serper_api_key:
        log.warning("serper_skipped", reason="SERPER_API_KEY not set")
        return {}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": keyword, "gl": "in", "hl": "en", "num": 5},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        log.error("serper_error", keyword=keyword, error=str(exc))
        return {}

async def scrape_competitor_page(url: str) -> Dict[str, Any]:
    """Scrape a single competitor page for word count and H2 structures."""
    result = {"url": url, "h2_structures": [], "word_count": 0}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Mask as a standard browser
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            
            # Extract H2s
            h2s = soup.find_all('h2')
            result["h2_structures"] = [h2.get_text(strip=True) for h2 in h2s if h2.get_text(strip=True)]
            
            # Rough word count: find main article or body
            main_content = soup.find('article') or soup.find('main') or soup.find('body')
            if main_content:
                text = main_content.get_text(separator=' ', strip=True)
                words = text.split()
                result["word_count"] = len(words)
                
    except Exception as exc:
        log.warning("scrape_competitor_failed", url=url, error=str(exc))
    
    return result

async def get_full_serp_intelligence(keyword: str) -> SerpIntelligence:
    """Orchestrate the advanced SERP intelligence gathering."""
    log.info("fetching_serp_intelligence", keyword=keyword)
    
    # 1. Fetch raw SERP data
    serp_data = await fetch_advanced_serp(keyword)
    if not serp_data:
        # Return empty intelligence if Serper fails or has no key
        return SerpIntelligence(
            top_5_competitor_urls=[],
            top_5_competitor_h2_structures=[],
            top_5_avg_word_count=0,
            featured_snippet_exists=False,
            featured_snippet_format=None,
            ai_overview_exists=False,
            ai_overview_competitor=None,
            paa_questions=[]
        )
    
    organic = serp_data.get("organic", [])[:5]
    top_urls = [res.get("link") for res in organic if res.get("link")]
    
    # Extract rich snippets
    answer_box = serp_data.get("answerBox")
    feat_snippet_exists = bool(answer_box)
    feat_snippet_format = None
    if feat_snippet_exists:
        if "list" in answer_box:
            feat_snippet_format = "list"
        elif "table" in answer_box: # Serper doesn't strictly have a table key always, but heuristic
            feat_snippet_format = "table"
        else:
            feat_snippet_format = "paragraph"
            
    # Extract PAA
    paa = serp_data.get("peopleAlsoAsk", [])
    paa_questions = [item.get("question") for item in paa if item.get("question")]
    
    # AI Overview (Serper sometimes maps this to knowledgeGraph or similar)
    # This is a simplification as actual AIO is hard to scrape reliably via Serper currently
    aio_exists = "knowledgeGraph" in serp_data
    aio_competitor = None
    
    # 2. Concurrently scrape competitor pages
    scrape_tasks = [scrape_competitor_page(url) for url in top_urls]
    scraped_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
    
    h2_structures = []
    total_words = 0
    valid_word_counts = 0
    
    for res in scraped_results:
        if isinstance(res, dict):
            h2_structures.append({"url": res["url"], "h2s": res["h2_structures"]})
            if res["word_count"] > 0:
                total_words += res["word_count"]
                valid_word_counts += 1
                
    avg_words = total_words // valid_word_counts if valid_word_counts > 0 else 0
    
    intel = SerpIntelligence(
        top_5_competitor_urls=top_urls,
        top_5_competitor_h2_structures=h2_structures,
        top_5_avg_word_count=avg_words,
        featured_snippet_exists=feat_snippet_exists,
        featured_snippet_format=feat_snippet_format,
        ai_overview_exists=aio_exists,
        ai_overview_competitor=aio_competitor,
        paa_questions=paa_questions
    )
    
    log.info("serp_intelligence_gathered", keyword=keyword, avg_words=avg_words, has_feat_snippet=feat_snippet_exists)
    return intel
