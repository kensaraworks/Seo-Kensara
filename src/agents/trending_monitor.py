"""Module 1.5: Trending Query & Real-Time Signal Monitor.

Monitors Google Trends, Autocomplete, Reddit/Quora, and LinkedIn for real-time
compliance signals and dynamically adds them to the keyword cluster queue.
"""
import asyncio
import json
import httpx
import structlog
from typing import Any

from src.config import settings
from src.queue.job_queue import job_queue
from src.agents.intent_classifier import classify_intent

log = structlog.get_logger()

# --- Helpers ---

def _get_tavily_client():
    if not settings.tavily_api_key:
        raise ValueError("Tavily API key not found in settings")
    try:
        from tavily import AsyncTavilyClient
        return AsyncTavilyClient(api_key=settings.tavily_api_key), True
    except ImportError:
        # tavily-python versions may expose only sync TavilyClient
        from tavily import TavilyClient
        return TavilyClient(api_key=settings.tavily_api_key), False


async def _tavily_search(client: Any, is_async_client: bool, **kwargs) -> dict[str, Any]:
    """Run Tavily search for either async or sync client implementations."""
    if is_async_client:
        return await client.search(**kwargs)
    return await asyncio.to_thread(client.search, **kwargs)

def _get_groq_client():
    from groq import AsyncGroq
    from dotenv import dotenv_values
    env = dotenv_values(".env")
    key = env.get("GROQ_API_KEY") or settings.groq_api_key
    return AsyncGroq(api_key=key)

async def _classify_and_upsert(cluster_id: str, cluster_name: str, keyword: str):
    """Classify intent and upsert a new keyword into the queue."""
    intent = await classify_intent(keyword)
    job_queue.upsert_keyword_cluster(
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        keyword=keyword,
        intent_type=intent.value
    )
    log.info("trending_upserted_keyword", cluster=cluster_id, keyword=keyword, intent=intent.value)


# --- 1.5.A Google Trends Monitor ---

async def monitor_google_trends() -> None:
    """Pull weekly rising queries using pytrends."""
    log.info("starting_google_trends_monitor")
    try:
        from pytrends.request import TrendReq
        import pandas as pd
    except ImportError:
        log.error("pytrends_not_installed")
        return

    # Backoff implementation would be wrapped around this blocking call
    # Running pytrends in a thread to avoid blocking the async event loop
    def _fetch_trends():
        # Avoid retries/backoff kwargs because pytrends 4.9.2 may pass
        # deprecated urllib3 Retry args (method_whitelist) under urllib3 v2.
        pytrends = TrendReq(hl='en-IN', tz=330)
        keywords = ["Digital Personal Data Protection Act", "data privacy India", "data breach India"]
        
        breakout_queries = []
        for kw in keywords:
            pytrends.build_payload([kw], geo='IN', timeframe='now 7-d')
            related = pytrends.related_queries()
            if kw in related and related[kw] is not None and 'rising' in related[kw] and related[kw]['rising'] is not None:
                rising_df = related[kw]['rising']
                # Pytrends returns string 'Breakout' or integer for value
                for index, row in rising_df.iterrows():
                    # Treat 'Breakout' or very high value as a trigger
                    if row['value'] == 'Breakout' or (isinstance(row['value'], (int, float)) and row['value'] > 5000):
                        breakout_queries.append(row['query'])
        return list(set(breakout_queries))

    try:
        breakout_queries = await asyncio.to_thread(_fetch_trends)
        for query in breakout_queries:
            await _classify_and_upsert("C_TRENDS", "Google Trends Breakout", query)
    except Exception as exc:
        log.error("google_trends_monitor_failed", error=str(exc))


# --- 1.5.B Google Autocomplete Mining ---

async def monitor_google_autocomplete() -> None:
    """Fetch undocumented Google Suggest API for seed keywords."""
    log.info("starting_google_autocomplete_monitor")
    seeds = [
        "DPDPA compliance",
        "data protection India",
        "DPDPA",
        "data fiduciary"
    ]
    
    unique_suggestions = set()
    async with httpx.AsyncClient() as client:
        for seed in seeds:
            try:
                # Undocumented suggest API
                url = f"http://suggestqueries.google.com/complete/search?client=chrome&gl=in&hl=en-IN&q={seed}"
                response = await client.get(url, timeout=10.0)
                if response.status_code == 200:
                    # JSON response format: [query, [suggestions...], ...]
                    data = response.json()
                    if len(data) > 1 and isinstance(data[1], list):
                        suggestions = data[1]
                        for s in suggestions:
                            if s.lower() != seed.lower():
                                unique_suggestions.add(s)
            except Exception as exc:
                log.error("google_autocomplete_failed", seed=seed, error=str(exc))
                
    for suggestion in unique_suggestions:
        await _classify_and_upsert("autocomplete_discovered", "Autocomplete Discovery", suggestion)


# --- 1.5.C Reddit & Quora India Privacy Monitoring ---

async def monitor_reddit_quora() -> None:
    """Search Reddit/Quora via Tavily and use Groq to extract high-priority FAQ."""
    log.info("starting_reddit_quora_monitor")
    try:
        tavily, is_async_tavily = _get_tavily_client()
    except Exception as exc:
        log.error("reddit_quora_tavily_init_failed", error=str(exc))
        return

    try:
        groq_client = _get_groq_client()
    except Exception as exc:
        log.error("reddit_quora_groq_init_failed", error=str(exc))
        return
    
    queries = [
        "site:reddit.com DPDPA",
        "site:reddit.com data privacy India",
        "site:quora.com DPDPA",
        "site:quora.com data protection India"
    ]
    
    questions_found = set()
    
    for query in queries:
        try:
            # We request include_raw_content so the LLM can see exactly what the page is about
            # However, Tavily might not always return raw content for all sites, but we'll try
            search_result = await _tavily_search(
                tavily,
                is_async_tavily,
                query=query,
                search_depth="basic",
                include_raw_content=True,
                max_results=3,
            )
            
            # Combine snippets to send to LLM
            combined_context = ""
            for res in search_result.get("results", []):
                snippet = res.get("content", "")
                raw = res.get("raw_content", "") or ""
                # Trim raw content to avoid token limits
                combined_context += f"URL: {res.get('url')}\nSnippet: {snippet}\nRaw: {raw[:1000]}\n\n"
            
            prompt = f"""You are a content research agent. Review the following search results from Reddit/Quora.
            Identify specific questions users are asking that appear to have high engagement (e.g. lots of answers or upvotes mentioned in the text).
            
            Results:
            {combined_context}
            
            Return a JSON list of strings, where each string is a distinct, high-priority question found in the results.
            If no good questions are found, return an empty list []. Return ONLY the JSON array, no markdown fences."""
            
            completion = await groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            
            raw_response = completion.choices[0].message.content or "[]"
            # Parse JSON
            if raw_response.startswith("```"):
                lines = raw_response.splitlines()
                inner = [l for l in lines if not l.startswith("```")]
                raw_response = "\n".join(inner).strip()
                
            extracted_qs = json.loads(raw_response)
            for q in extracted_qs:
                if isinstance(q, str) and len(q) > 10:
                    questions_found.add(q)
                    
        except Exception as exc:
            log.error("reddit_quora_monitor_failed", query=query, error=str(exc))
            
    for q in questions_found:
        await _classify_and_upsert("C_FAQ", "Reddit/Quora FAQs", q)


# --- 1.5.D LinkedIn Topic Monitoring ---

async def monitor_linkedin() -> None:
    """Search LinkedIn for DPDPA pain points in the last 7 days using Tavily + Groq."""
    log.info("starting_linkedin_monitor")
    try:
        tavily, is_async_tavily = _get_tavily_client()
    except Exception as exc:
        log.error("linkedin_tavily_init_failed", error=str(exc))
        return

    try:
        groq_client = _get_groq_client()
    except Exception as exc:
        log.error("linkedin_groq_init_failed", error=str(exc))
        return
    
    queries = [
        "site:linkedin.com/posts DPDPA",
        "site:linkedin.com/posts data privacy India"
    ]
    
    topics_found = set()
    
    for query in queries:
        try:
            # We want recent results for LinkedIn, though Tavily's time filtering varies.
            search_result = await _tavily_search(
                tavily,
                is_async_tavily,
                query=query,
                search_depth="basic",
                max_results=5,
            )
            
            combined_context = ""
            for res in search_result.get("results", []):
                snippet = res.get("content", "")
                combined_context += f"Snippet: {snippet}\n\n"
                
            prompt = f"""You are a compliance market researcher. Review the following recent LinkedIn post snippets about data privacy in India.
            Identify specific pain points, topics being discussed, or questions asked by compliance professionals.
            
            Results:
            {combined_context}
            
            Return a JSON list of strings, where each string is a distinct, actionable topic or pain point suitable for an SEO keyword or blog title.
            Keep them concise (2-5 words). Return ONLY the JSON array, no markdown fences."""
            
            completion = await groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            
            raw_response = completion.choices[0].message.content or "[]"
            if raw_response.startswith("```"):
                lines = raw_response.splitlines()
                inner = [l for l in lines if not l.startswith("```")]
                raw_response = "\n".join(inner).strip()
                
            extracted_topics = json.loads(raw_response)
            for t in extracted_topics:
                if isinstance(t, str) and len(t) > 5:
                    topics_found.add(t)
                    
        except Exception as exc:
            log.error("linkedin_monitor_failed", query=query, error=str(exc))
            
    for topic in topics_found:
        await _classify_and_upsert("C_LINKEDIN", "LinkedIn Pain Points", topic)
