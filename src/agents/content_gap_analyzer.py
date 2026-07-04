"""Competitor Intelligence System & Content Gap Analyzer.

Rebuilds the one-off content gap script into a recurring, database-driven 
pipeline that runs every Monday. Performs competitor crawling, gap analysis
(with Kensara cluster coexistence), ranking monitoring, LLM analysis, and 
generates the Monday Intelligence Brief.
"""
import asyncio
import importlib
import json
import re
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse, urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from pydantic import BaseModel

from src.queue.job_queue import job_queue
from src.config import settings
from src.agents.serp_intelligence import fetch_advanced_serp

log = structlog.get_logger()


def get_comparison_grounding(keyword: str, limit: int = 5) -> list[dict]:
    """Return real, previously-crawled competitor content relevant to `keyword`
    (spec Phase 0 Step 3) so comparison_table sections cite what competitors
    actually publish instead of an invented comparison. Pulls from the same
    competitor_intel rows the Monday intelligence brief already gathers via
    real Tavily crawls — no new crawling here, just relevance-scored reuse.
    """
    rows = job_queue.get_recent_competitor_intel(days=90)
    if not rows:
        return []

    kw_terms = {w for w in re.findall(r"[a-z0-9]+", keyword.lower()) if len(w) > 2}
    if not kw_terms:
        return rows[:limit]

    def _relevance(row: dict) -> int:
        text = f"{row.get('title', '')} {row.get('summary', '')} {row.get('primary_keyword', '')}".lower()
        return sum(1 for term in kw_terms if term in text)

    scored = [(row, _relevance(row)) for row in rows]
    scored = [(row, score) for row, score in scored if score > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [row for row, _ in scored[:limit]]

COMPETITOR_DOMAINS = [
    "securiti.ai",
    "cookieyes.com",
    "trustarc.com",
    "dpdpa.com",
    "onetrust.com",
    "deloitte.com/in",
    "pwc.com/in",
    "tsaaro.com",
    "tcs.com",
    "dutient.ai",
    # Added: publishes a substantial, actively-maintained DPDPA blog (Phase 1
    # guide, cookie consent, consent managers, etc.) but was missing from
    # tracking entirely — a real blind spot in the Monday competitor brief.
    "secureprivacy.ai",
]

CORE_KEYWORDS = [
    "DPDPA compliance software", "DSAR automation India", 
    "consent management platform India", "what is DPDPA"
]

_SERPER_SEARCH_URL = "https://google.serper.dev/search"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _domain_host(domain: str) -> str:
    """Normalize configured competitor domain to a host string."""
    return domain.split("/")[0].lower().replace("www.", "")


def _extract_host(url: str) -> str:
    """Extract normalized host from URL for domain comparisons."""
    return (urlparse(url).netloc or "").lower().replace("www.", "")

class ContentGap(BaseModel):
    keyword: str
    top_competitor_url: str
    missing_topics: list[str]
    missing_entities: list[str]
    recommended_additions: list[str]


# ---------------------------------------------------------------------------
# Legacy / Deep LLM Gap Analysis (Preserved)
# ---------------------------------------------------------------------------
async def _fetch_serper_results(keyword: str) -> list[dict]:
    serp_data = await fetch_advanced_serp(keyword)
    return serp_data.get("organic", [])[:5]

async def _analyze_gaps_with_llm(keyword: str, organic_results: list[dict]) -> tuple[list[str], list[str], list[str]]:
    if not settings.nvidia_api_key:
        return [], [], []
    client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=settings.nvidia_api_key)
    serp_text = "\n".join(f"{i+1}. {r.get('title', '')} — {r.get('snippet', '')}" for i, r in enumerate(organic_results))
    prompt = f"""You are an SEO content strategist for KensaraAI. Keyword: "{keyword}"\nTop 5 results:\n{serp_text}\nReturn JSON with missing_topics, missing_entities, recommended_additions."""
    try:
        response = await client.chat.completions.create(
            model="mistralai/mistral-medium-3.5-128b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=400,
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("missing_topics", []), data.get("missing_entities", []), data.get("recommended_additions", [])
    except Exception as exc:
        log.error("gap_llm_analysis_failed", error=str(exc))
        return [], [], []


# ---------------------------------------------------------------------------
# 1.3.A Weekly Competitor Content Crawl
# ---------------------------------------------------------------------------
async def _crawl_competitors() -> None:
    """Crawl competitor domains using Tavily site-scoped DPDPA queries."""
    log.info("competitor_crawl_start")
    if not settings.tavily_api_key:
        log.warning("competitor_crawl_skipped", reason="TAVILY_API_KEY not set")
        return

    async def _crawl_one(domain: str) -> int:
        query = f"DPDPA compliance site:{domain}"
        saved = 0
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    _TAVILY_SEARCH_URL,
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": query,
                        "max_results": 5,
                        "search_depth": "basic",
                        "include_answer": False,
                    },
                )
                response.raise_for_status()
                data = response.json()

            for result in data.get("results", []):
                title = (result.get("title") or "").strip()
                url = (result.get("url") or "").strip()
                snippet = (result.get("content") or "").strip()
                if not title or not url:
                    continue

                keyword = next(
                    (kw for kw in CORE_KEYWORDS if kw.lower() in (title + " " + snippet).lower()),
                    "DPDPA compliance",
                )
                word_count = len(snippet.split()) if snippet else 0
                job_queue.record_competitor_intel(
                    domain=domain,
                    url=url,
                    title=title,
                    pub_date=date.today().isoformat(),
                    keyword=keyword,
                    word_count=word_count,
                    summary=snippet,
                    gap_flag=False,
                )
                saved += 1
        except Exception as exc:
            log.warning("competitor_crawl_domain_failed", domain=domain, error=str(exc))
        return saved

    counts = await asyncio.gather(*[_crawl_one(domain) for domain in COMPETITOR_DOMAINS])
    log.info("competitor_crawl_done", domains=len(COMPETITOR_DOMAINS), records=sum(counts))


# ---------------------------------------------------------------------------
# 1.3.B Gap Analysis Report Generation (Coexists with 1.2 Clusters)
# ---------------------------------------------------------------------------
def _analyze_gaps_db() -> list[dict]:
    """Compare competitor content against Kensara's existing coverage."""
    log.info("gap_analysis_start")
    recent_intel = job_queue.get_recent_competitor_intel(days=14)
    
    topic_data = {}
    for intel in recent_intel:
        kw = intel["primary_keyword"]
        wc = intel["word_count"] or 0
        if kw:
            if kw not in topic_data:
                topic_data[kw] = {"count": 0, "min_wc": wc}
            topic_data[kw]["count"] += 1
            if wc < topic_data[kw]["min_wc"]:
                topic_data[kw]["min_wc"] = wc
            
    gaps_found = []
    for topic, data in topic_data.items():
        count = data["count"]
        min_wc = data["min_wc"]
        kensara_coverage = job_queue.get_keyword_coverage(topic)
        
        priority = 0.0
        status = "none"
        
        # Rule 1: Covered by 2+ competitors but not by Kensara
        if count >= 2 and kensara_coverage in ("none", "uncovered"):
            priority = count * 10.0
            status = "uncovered_gap"
            
        # Rule 2: Covered by competitors only shallowly (< 600 words) where Kensara has no content
        elif min_wc > 0 and min_wc < 600 and kensara_coverage in ("none", "uncovered"):
            priority = 15.0
            status = "shallow_competitor_gap"
            
        # Rule 3: Kensara has published content, but competitors are now publishing -> needs update
        elif count >= 1 and kensara_coverage == "published":
            priority = count * 5.0
            status = "needs_update"
            
        if priority > 0:
            job_queue.upsert_competitor_gap(topic, count, kensara_coverage, priority)
            gaps_found.append({"topic": topic, "count": count, "priority": priority, "status": status})
            
            # Enqueue high priority gaps into the content queue
            if status in ("uncovered_gap", "shallow_competitor_gap"):
                # Insert into clusters if not present so it coexists
                job_queue.upsert_keyword_cluster("cluster_competitor_gap", "Competitor Gaps", topic, "informational")
                job_queue.enqueue_content(topic, "informational", "cluster_competitor_gap", priority, [])
            
    log.info("gap_analysis_done", gaps=len(gaps_found))
    return gaps_found


# ---------------------------------------------------------------------------
# 1.3.C Content Freshness Tracker
# ---------------------------------------------------------------------------
_COMPETITOR_BLOG_URLS: dict[str, str] = {
    "securiti.ai": "https://securiti.ai/blog/",
    "cookieyes.com": "https://www.cookieyes.com/blog/",
    "onetrust.com": "https://www.onetrust.com/blog/",
}


async def _track_content_freshness() -> list[str]:
    """Fetch competitor blog index pages and return the most recent article URL
    found on each.  Uses curl_cffi with Chrome TLS impersonation to bypass
    Cloudflare / bot-protection middleware (securiti.ai, cookieyes.com, etc.).
    """
    try:
        async_session_cls = importlib.import_module("curl_cffi.requests").AsyncSession
    except Exception:
        log.warning("curl_cffi_not_installed", hint="pip install curl-cffi")
        return []

    async def _check_one(domain: str, url: str) -> str | None:
        try:
            async with async_session_cls() as session:
                r = await session.get(url, impersonate="chrome", timeout=15)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                for sel in [
                    "article a", "h2 a", "h3 a",
                    ".post-title a", ".entry-title a",
                    f"a[href*='/{domain.split('.')[0]}']",
                ]:
                    link = soup.select_one(sel)
                    if link:
                        href = link.get("href", "")
                        if href:
                            if not href.startswith("http"):
                                href = urljoin(url, href)
                            log.info("competitor_fresh_found", domain=domain, url=href)
                            return href
        except Exception as exc:
            log.warning("competitor_freshness_check_failed", domain=domain, error=str(exc))
        return None

    results = await asyncio.gather(
        *[_check_one(domain, url) for domain, url in _COMPETITOR_BLOG_URLS.items()]
    )
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# 1.3.D Competitor Keyword Ranking Monitor
# ---------------------------------------------------------------------------
async def _monitor_rankings() -> list[dict]:
    """Track competitor rankings in top 10 Serper results for core keywords."""
    if not settings.serper_api_key:
        log.warning("competitor_rankings_skipped", reason="SERPER_API_KEY not set")
        return []

    threats = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for kw in CORE_KEYWORDS[:10]:
            try:
                response = await client.post(
                    _SERPER_SEARCH_URL,
                    headers={
                        "X-API-KEY": settings.serper_api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": kw, "gl": "in", "hl": "en", "num": 10},
                )
                response.raise_for_status()
                organic = response.json().get("organic", [])[:10]
            except Exception as exc:
                log.warning("competitor_ranking_query_failed", keyword=kw, error=str(exc))
                continue

            found_positions: dict[str, int] = {}
            for idx, result in enumerate(organic, start=1):
                host = _extract_host(result.get("link", ""))
                for domain in COMPETITOR_DOMAINS:
                    competitor = _domain_host(domain)
                    if competitor in host and competitor not in found_positions:
                        found_positions[competitor] = idx

            for competitor, position in found_positions.items():
                change = job_queue.record_competitor_ranking(kw, competitor, position)
                # Positive change means competitor moved up (threatening us).
                if change > 0:
                    threats.append(
                        {
                            "keyword": kw,
                            "domain": competitor,
                            "position": position,
                            "change": change,
                        }
                    )

    log.info("competitor_rankings_done", threats=len(threats))
    return threats


# ---------------------------------------------------------------------------
# 1.3.E Competitor Backlink Surge Detector
# ---------------------------------------------------------------------------
async def _detect_backlink_surges() -> list[dict]:
    """Approximate backlink surges using weekly Tavily mention count deltas."""
    if not settings.tavily_api_key:
        log.warning("backlink_surge_skipped", reason="TAVILY_API_KEY not set")
        return []

    surges = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for domain in COMPETITOR_DOMAINS:
            try:
                response = await client.post(
                    _TAVILY_SEARCH_URL,
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": f"link:{domain} DPDPA privacy 2026",
                        "max_results": 10,
                        "search_depth": "basic",
                        "include_answer": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                current_count = len(data.get("results", []))

                previous_count = job_queue.get_previous_backlink_count(domain, days_ago=7)
                job_queue.record_competitor_backlink_count(domain, current_count)

                if current_count > previous_count + 3:
                    surges.append(
                        {
                            "domain": domain,
                            "new_referring_domains": current_count - previous_count,
                            "previous_mentions": previous_count,
                            "current_mentions": current_count,
                        }
                    )
            except Exception as exc:
                log.warning("backlink_surge_query_failed", domain=domain, error=str(exc))

    log.info("backlink_surge_detection_done", surges=len(surges))
    return surges


# ---------------------------------------------------------------------------
# 1.3.F Monday Intelligence Brief Generation
# ---------------------------------------------------------------------------
def _generate_monday_brief(gaps: list[dict], fresh: list[str], threats: list[dict], surges: list[dict]) -> None:
    """Generate structured JSON + HTML summary for the dashboard."""
    top_gaps = job_queue.get_top_competitor_gaps(limit=3)
    
    brief = {
        "generated_at": datetime.now().isoformat(),
        "top_content_gaps": top_gaps,
        "competitor_updates": fresh,
        "ranking_threats": threats,
        "backlink_surges": surges
    }
    
    output_dir = Path(settings.content_output_dir) / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    json_path = output_dir / "monday-brief.json"
    html_path = output_dir / "monday-brief.html"
    
    try:
        json_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        
        html = f"<html><head><title>Monday Intelligence Brief</title></head><body>"
        html += "<h1>Monday Intelligence Brief</h1>"
        html += f"<p>Generated: {brief['generated_at']}</p>"
        html += "<h2>Top Content Gaps</h2><ul>"
        for g in top_gaps:
            html += f"<li>{g['topic']} (Competitors: {g['competitor_count']})</li>"
        html += "</ul></body></html>"
        
        html_path.write_text(html, encoding="utf-8")
        log.info("monday_brief_generated", json=str(json_path), html=str(html_path))
    except OSError as exc:
        log.error("monday_brief_generation_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Main Pipeline Entry Point
# ---------------------------------------------------------------------------
async def run_competitor_intelligence() -> None:
    """Main job that runs every Monday at 06:00 IST."""
    log.info("run_competitor_intelligence_start")
    try:
        await _crawl_competitors()
        gaps = _analyze_gaps_db()
        fresh = await _track_content_freshness()
        threats = await _monitor_rankings()
        surges = await _detect_backlink_surges()
        
        _generate_monday_brief(gaps, fresh, threats, surges)
        log.info("run_competitor_intelligence_done")
    except Exception as exc:
        log.error("run_competitor_intelligence_failed", error=str(exc))
