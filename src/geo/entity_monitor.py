"""Module 1.7: Entity Authority Building Monitor.

Tracks Kensara's presence as a verified entity in Google's Knowledge Graph,
unlinked brand mentions, third-party directory listings, and the founder's
personal brand.
"""
import asyncio
import json
import httpx
from urllib.parse import urlparse
import structlog

from src.config import settings
from src.queue.job_queue import job_queue
from src.scrapers.linkedin_monitor import monitor_linkedin_metrics
from src.agents.intent_classifier import IntentType

log = structlog.get_logger()

# Target third-party directories for AI citations
TARGET_DIRECTORIES = {
    "g2": "g2.com",
    "capterra": "capterra.com",
    "product_hunt": "producthunt.com",
    "clutch": "clutch.co",
    "yourstory": "yourstory.com",
    "tracxn": "tracxn.com",
    "startup_india": "startupindia.gov.in"
}


async def _serper_search(query: str, search_type: str = "search") -> dict:
    """Helper to query Serper.dev."""
    if not settings.serper_api_key:
        log.warning("serper_api_key_missing")
        return {}
        
    url = f"https://google.serper.dev/{search_type}"
    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json"
    }
    payload = json.dumps({"q": query, "gl": "in"})
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=payload, timeout=15.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            log.error("serper_api_failed", query=query, error=str(exc))
            return {}


async def check_knowledge_panel() -> None:
    """Check if Google Knowledge Panel exists for Kensara or KensaraAI Private Limited."""
    log.info("starting_knowledge_panel_check")
    entities = ["Kensara AI", "KensaraAI Private Limited"]
    
    for entity in entities:
        data = await _serper_search(entity)
        if not data:
            continue
            
        kg = data.get("knowledgeGraph")
        if kg and kg.get("title", "").lower() == entity.lower():
            # Panel exists!
            job_queue.record_entity_status("Google Knowledge Graph", "Verified", profile_url=kg.get("website", ""), completeness_score=100)
            log.info("knowledge_panel_found", entity=entity)
        else:
            job_queue.record_entity_status("Google Knowledge Graph", "Not Found")
            log.info("knowledge_panel_not_found", entity=entity)


async def monitor_brand_mentions() -> None:
    """Find mentions of Kensara across the web that are NOT linked to us."""
    log.info("starting_brand_mention_monitor")
    # Search for mentions of Kensara but exclude our own site
    queries = ['"KensaraAI" -site:kensara.in', '"KensaraAI Private Limited" -site:kensara.in']
    
    for query in queries:
        data = await _serper_search(query)
        for result in data.get("organic", []):
            url = result.get("link", "")
            domain = urlparse(url).netloc
            snippet = result.get("snippet", "")
            
            if url and "kensara.in" not in domain:
                # Log it as an unlinked mention opportunity
                brand_term = "KensaraAI" if "KensaraAI" in query else "KensaraAI Private Limited"
                job_queue.record_unlinked_mention(domain, url, brand_term)
                log.info("unlinked_mention_found", domain=domain, url=url)

    # After processing topics, gather raw metrics and store them
    await monitor_linkedin_metrics()


async def audit_third_party_listings() -> None:
    """Audit Kensara's presence on high-AI-citation sources."""
    log.info("starting_third_party_listing_audit")
    
    for platform_name, domain in TARGET_DIRECTORIES.items():
        query = f'"KensaraAI" site:{domain}'
        data = await _serper_search(query)
        
        results = data.get("organic", [])
        if results:
            top_url = results[0].get("link", "")
            job_queue.record_entity_status(platform_name, "Listed", profile_url=top_url, completeness_score=100)
            log.info("third_party_listing_found", platform=platform_name, url=top_url)
        else:
            job_queue.record_entity_status(platform_name, "Missing")
            log.info("third_party_listing_missing", platform=platform_name)


async def monitor_founder_brand() -> None:
    """Track media/web mentions of Rudraksh Tatwal and Prince Raj in privacy context."""
    log.info("starting_founder_brand_monitor")
    if not settings.tavily_api_key:
        log.warning("tavily_api_key_missing_for_founder_monitor")
        return
        
    from tavily import AsyncTavilyClient
    client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    
    query = '("Rudraksh Tatwal" OR "Prince Raj") (DPDPA OR "data privacy" OR Kensara)'
    
    try:
        # Search recent news/posts
        search_result = await client.search(query=query, search_depth="basic", max_results=5)
        
        for res in search_result.get("results", []):
            url = res.get("url", "")
            content = res.get("content", "")
            
            # Record the mention
            job_queue.record_founder_mention(url, content)
            log.info("founder_mention_found", url=url)
            
            # Use Groq to see if this is a high-engagement topic we should write about
            from groq import AsyncGroq
            groq_client = AsyncGroq(api_key=settings.groq_api_key)
            
            prompt = f"""Analyze this recent media mention of Kensara founders (Rudraksh Tatwal or Prince Raj).
            Does it mention a specific, actionable data privacy topic or trend that we should write a blog post about to capitalize on the momentum?
            
            Mention:
            {content}
            
            If yes, return a single JSON string with the proposed keyword/topic (e.g., "how to manage vendor risk under DPDPA").
            If no, return an empty JSON string "".
            Return ONLY a valid JSON string."""
            
            try:
                completion = await groq_client.chat.completions.create(
                    model=settings.groq_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                topic = json.loads(completion.choices[0].message.content)
                
                if topic and len(topic) > 5:
                    job_queue.upsert_keyword_cluster(
                        cluster_id="founder_momentum",
                        cluster_name="Founder Brand Momentum",
                        keyword=topic,
                        intent_type=IntentType.INFORMATIONAL.value
                    )
                    log.info("founder_momentum_keyword_queued", keyword=topic)
            except Exception as e:
                log.debug("groq_founder_topic_extraction_failed", error=str(e))
                
    except Exception as exc:
        log.error("founder_brand_monitor_failed", error=str(exc))
