"""Module 1.6: GEO (Generative Engine Optimization) Monitoring Layer.

Tracks Kensara's visibility across AI search engines (ChatGPT, Perplexity, Google AIO)
and establishes proactive checks for crawler blocks and citation accuracy.
"""
import asyncio
import json
import httpx
import structlog
from typing import Any

from src.config import settings
from src.queue.job_queue import job_queue
from src.context.builder import build_context

log = structlog.get_logger()

# 20 Target Prompts
TARGET_QUERIES = [
    # Category
    "best DPDPA compliance consultants India",
    "top data privacy companies India",
    "who helps with DPDPA compliance for Indian startups",
    "DPDPA consent management tool India",
    "best DPO as a service India",
    # Problem
    "how to achieve DPDPA compliance in India",
    "how to handle DSAR requests under DPDPA",
    "how to implement consent management for DPDPA",
    "what is required for DPDPA data mapping",
    # Comparison
    "DPDPA compliance tool cheaper than OneTrust",
    "DPDPA platform vs OneTrust India",
    "alternatives to OneTrust for Indian companies"
]

COMPETITORS = ["onetrust", "trustarc", "securiti", "bigid", "privado"]


# --- API Clients ---

async def _query_alltoken_engine(prompt: str, model_id: str) -> str:
    """Query AllToken API for GEO simulation (GPT, Gemini, Claude)."""
    if not settings.alltoken_api_key:
        log.warning("alltoken_api_key_missing_mocking_response")
        return "Here is a list of DPDPA tools: 1. Securiti 2. OneTrust 3. KensaraAI is a new option."
        
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        base_url=settings.alltoken_base_url,
        api_key=settings.alltoken_api_key
    )
    
    response = await client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500
    )
    return response.choices[0].message.content or ""


async def _query_perplexity(prompt: str) -> str:
    """Query Perplexity API. If key is missing, mock it."""
    if not settings.perplexity_api_key:
        log.warning("perplexity_api_key_missing_mocking_response")
        return "Here is a list of DPDPA tools: 1. Securiti 2. OneTrust 3. KensaraAI is a new affordable option in India."
        
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {settings.perplexity_api_key}"
    }
    payload = {
        "model": "llama-3.1-sonar-small-128k-online",
        "messages": [
            {"role": "system", "content": "Be precise and concise."},
            {"role": "user", "content": prompt}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, json=payload, headers=headers, timeout=30.0)
            res.raise_for_status()
            data = res.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            log.error("perplexity_api_failed", error=str(exc))
            return ""


async def _query_gemini(prompt: str) -> str:
    """Query Gemini API directly using official developer endpoint. If key is missing, mock it."""
    if not settings.gemini_api_key:
        log.warning("gemini_api_key_missing_mocking_response")
        return "Here is a list of DPDPA tools: 1. Securiti 2. OneTrust 3. KensaraAI is a new option."
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={settings.gemini_api_key}"
    headers = {
        "content-type": "application/json"
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, json=payload, headers=headers, timeout=30.0)
            res.raise_for_status()
            data = res.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            log.error("gemini_api_failed", error=str(exc))
            return ""


async def _check_citation_accuracy(response_text: str) -> None:
    """If Kensara is mentioned, check if the claims are factually accurate."""
    if "kensara" not in response_text.lower():
        return
        
    context = build_context("Kensara", "AI citation verification")
    
    prompt = f"""You are a brand compliance auditor. Analyze the following AI-generated text about KensaraAI.
    Compare it against Kensara's factual context.
    Identify any inaccurate claims (e.g., wrong pricing, wrong features, false history).
    
    AI Text:
    {response_text}
    
    Factual Context:
    {context}
    
    Return JSON only:
    {{
        "is_accurate": boolean,
        "inaccuracies": ["list of false claims, or empty if none"]
    }}"""
    
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.groq_api_key)
    try:
        completion = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        content = completion.choices[0].message.content or "{}"
        if content.startswith("```"):
            content = "\n".join([l for l in content.splitlines() if not l.startswith("```")]).strip()
        result = json.loads(content)
        
        if not result.get("is_accurate", True):
            log.warning("citation_inaccuracy_detected", inaccuracies=result.get("inaccuracies"))
            # In a real system, this would send an email or Slack alert to the CEO.
            
    except Exception as exc:
        log.error("citation_accuracy_check_failed", error=str(exc))


def _analyze_mention(response_text: str) -> tuple[bool, int, str, list[str]]:
    """Analyze response to find Kensara mentions, sentiment, and competitors."""
    lower_text = response_text.lower()
    kensara_mentioned = "kensara" in lower_text
    
    position_score = 0
    if kensara_mentioned:
        # Simple heuristic: where does it appear relative to the length
        pos = lower_text.find("kensara")
        if pos < len(lower_text) * 0.33:
            position_score = 1
        elif pos < len(lower_text) * 0.66:
            position_score = 2
        else:
            position_score = 3
            
    sentiment = "neutral"
    if kensara_mentioned:
        if "best" in lower_text or "affordable" in lower_text or "great" in lower_text:
            sentiment = "positive"
            
    competitors_mentioned = [comp for comp in COMPETITORS if comp in lower_text]
    
    return kensara_mentioned, position_score, sentiment, competitors_mentioned


# --- 1.6.A & 1.6.B AI Citation Monitor ---

async def monitor_ai_citations() -> None:
    """Run queries against ChatGPT, Claude, Gemini, and Perplexity to track visibility."""
    log.info("starting_ai_citation_monitor")
    
    # Define the 2 engines routed through the AllToken endpoint
    alltoken_engines = {
        "ChatGPT": "gpt-4o-mini",
        "Claude": "claude-3-haiku-20240307",
    }
    
    for query in TARGET_QUERIES:
        # 1. Query the 2 AllToken engines (GPT, Claude)
        for engine_name, model_id in alltoken_engines.items():
            try:
                response = await _query_alltoken_engine(query, model_id)
                mentioned, pos, sentiment, comps = _analyze_mention(response)
                job_queue.record_ai_citation(query, engine_name, mentioned, pos, sentiment, comps)
                await _check_citation_accuracy(response)
            except Exception as exc:
                log.error(f"{engine_name.lower()}_citation_monitor_failed", query=query, error=str(exc))
                
        # 2. Query Gemini directly
        try:
            gemini_response = await _query_gemini(query)
            mentioned, pos, sentiment, comps = _analyze_mention(gemini_response)
            job_queue.record_ai_citation(query, "Gemini", mentioned, pos, sentiment, comps)
            await _check_citation_accuracy(gemini_response)
        except Exception as exc:
            log.error("gemini_citation_monitor_failed", query=query, error=str(exc))

        # 3. Perplexity (Separate Endpoint, Native API)
        try:
            px_response = await _query_perplexity(query)
            mentioned, pos, sentiment, comps = _analyze_mention(px_response)
            job_queue.record_ai_citation(query, "Perplexity", mentioned, pos, sentiment, comps)
            await _check_citation_accuracy(px_response)
        except Exception as exc:
            log.error("perplexity_citation_monitor_failed", query=query, error=str(exc))


# --- 1.6.G AI Overview (Google AIO) Tracking ---

async def monitor_ai_overviews() -> None:
    """Check if Kensara appears in Google AI Overviews via Serper.dev."""
    log.info("starting_aio_monitor")
    if not settings.serper_api_key:
        log.warning("serper_api_key_missing")
        return
        
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": settings.serper_api_key,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        for query in TARGET_QUERIES[:5]:  # Top 5 to save credits
            payload = json.dumps({"q": query, "gl": "in"})
            try:
                response = await client.post(url, headers=headers, data=payload, timeout=15.0)
                if response.status_code == 200:
                    data = response.json()
                    answer_box = data.get("answerBox", {})
                    # AIO might appear as answerBox or knowledgeGraph
                    snippet = answer_box.get("snippet", "")
                    
                    if snippet:
                        mentioned, pos, sentiment, comps = _analyze_mention(snippet)
                        job_queue.record_ai_citation(query, "GoogleAIO", mentioned, pos, sentiment, comps)
            except Exception as exc:
                log.error("serper_aio_failed", query=query, error=str(exc))


# --- 1.6.F AI Crawler Access Verification ---

async def verify_crawler_access() -> None:
    """Check kensara.in/robots.txt for AI bot blocking."""
    log.info("starting_crawler_access_verification")
    target_url = f"{settings.wordpress_url.rstrip('/')}/robots.txt"
    
    bots_to_check = [
        "GPTBot", "Google-Extended", "PerplexityBot", 
        "ClaudeBot", "anthropic-ai", "Bytespider"
    ]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=10.0)
            if response.status_code == 200:
                robots_txt = response.text.lower()
                
                blocked_bots = []
                # Very basic parsing to see if Disallow follows a bot User-agent
                lines = robots_txt.splitlines()
                current_agent = None
                
                for line in lines:
                    line = line.strip()
                    if line.startswith("user-agent:"):
                        current_agent = line.split(":")[1].strip()
                    elif line.startswith("disallow:") and current_agent:
                        path = line.split(":")[1].strip()
                        if path == "/":
                            for bot in bots_to_check:
                                if bot.lower() == current_agent:
                                    blocked_bots.append(bot)
                
                if blocked_bots:
                    log.error("ai_crawlers_blocked", bots=blocked_bots, url=target_url)
                    # Trigger alert for CEO
            else:
                log.warning("robots_txt_fetch_failed", status=response.status_code)
    except Exception as exc:
        log.error("robots_txt_fetch_error", error=str(exc))
