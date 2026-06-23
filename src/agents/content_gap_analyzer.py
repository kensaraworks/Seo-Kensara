"""Content Gap Analyzer — identifies what top-ranking pages have that KensaraAI content lacks.

For each target keyword, searches Serper.dev for top 5 results, then uses NVIDIA NIM
to identify missing topics, entities, and content recommendations.
"""
import json
from datetime import date
from pathlib import Path

import httpx
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()

TARGET_KEYWORDS = [
    "DPDPA compliance software",
    "DPDPA compliance tool India",
    "DSAR automation India",
    "consent management platform India",
    "data breach notification software India",
    "DPDPA compliance checklist",
    "what is DPDPA India",
    "DPDPA vs GDPR",
    "DPDPA penalty India",
    "GDPR compliance tool India",
]


class ContentGap(BaseModel):
    keyword: str
    top_competitor_url: str
    missing_topics: list[str]
    missing_entities: list[str]
    recommended_additions: list[str]


async def _fetch_serper_results(keyword: str) -> list[dict]:
    """
    Call Serper.dev Google Search API for Indian results.
    Returns top 5 organic result dicts with keys: title, link, snippet.
    Returns empty list if Serper key not configured or request fails.
    """
    if not settings.serper_api_key:
        log.warning(
            "serper_skipped",
            reason="SERPER_API_KEY not set",
            action="set SERPER_API_KEY to enable content gap analysis",
        )
        return []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": keyword, "gl": "in", "hl": "en", "num": 5},
            )
            response.raise_for_status()
            data = response.json()
            organic = data.get("organic", [])
            log.debug(
                "serper_results_fetched",
                keyword=keyword[:50],
                result_count=len(organic),
            )
            return organic[:5]
    except httpx.HTTPStatusError as exc:
        log.error(
            "serper_http_error",
            keyword=keyword[:50],
            status_code=exc.response.status_code,
            error=str(exc),
        )
        return []
    except httpx.RequestError as exc:
        log.error(
            "serper_request_error",
            keyword=keyword[:50],
            error=str(exc),
        )
        return []


async def _analyze_gaps_with_llm(
    keyword: str,
    organic_results: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """
    Use NVIDIA NIM to identify content gaps from SERP titles + snippets.
    Returns (missing_topics, missing_entities, recommended_additions).
    """
    if not settings.nvidia_api_key:
        log.warning(
            "nvidia_skipped_for_gap_analysis",
            reason="NVIDIA_API_KEY not set",
        )
        return [], [], []

    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=settings.nvidia_api_key,
    )

    # Build a compact representation of the SERP for the prompt
    serp_text = "\n".join(
        f"{i+1}. {r.get('title', '')} — {r.get('snippet', '')}"
        for i, r in enumerate(organic_results)
    )

    prompt = f"""You are an SEO content strategist for KensaraAI — India's AI-native DPDPA + GDPR compliance platform.

Keyword: "{keyword}"

Top 5 Google results (India):
{serp_text}

KensaraAI context:
- Covers: DPDPA, GDPR, CCPA compliance
- Modules: DSAR automation, consent management, GRC/DPIA
- Audience: Indian DPOs, CISOs, compliance managers

Analyze what the top-ranking content covers that KensaraAI content likely misses.
Focus on: subtopics, specific regulations, named entities, questions users ask, data points.

Return JSON:
{{
  "missing_topics": ["<topic 1>", "<topic 2>", "<topic 3>"],
  "missing_entities": ["<named regulation/body/term 1>", "<term 2>"],
  "recommended_additions": ["<specific content addition 1>", "<specific addition 2>", "<addition 3>"]
}}

Be specific and actionable. No generic advice."""

    try:
        response = await client.chat.completions.create(
            model="mistralai/mistral-medium-3.5-128b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=400,
            timeout=30.0,
        )
        data = json.loads(response.choices[0].message.content)
        return (
            data.get("missing_topics", []),
            data.get("missing_entities", []),
            data.get("recommended_additions", []),
        )
    except Exception as exc:
        log.error(
            "gap_llm_analysis_failed",
            keyword=keyword[:50],
            error=str(exc),
        )
        return [], [], []


async def analyze_content_gap(keyword: str) -> ContentGap | None:
    """
    For a given keyword:
    1. Search Serper.dev for top 5 results (India, English)
    2. Use NVIDIA NIM to analyze what topics/entities are in SERP but missing from KensaraAI
    3. Save results to drafts/reports/content-gaps/YYYY-MM-DD-{keyword-slug}.json
    4. Return ContentGap or None if dependencies not configured

    Gracefully skips analysis if API keys are not set — logs warning, never crashes.
    """
    log.info("content_gap_analysis_start", keyword=keyword[:60])

    organic_results = await _fetch_serper_results(keyword)

    if not organic_results:
        log.warning(
            "content_gap_no_serp_data",
            keyword=keyword[:60],
            action="skipping gap analysis — no SERP results available",
        )
        return None

    top_competitor_url = organic_results[0].get("link", "") if organic_results else ""

    missing_topics, missing_entities, recommended_additions = await _analyze_gaps_with_llm(
        keyword, organic_results
    )

    gap = ContentGap(
        keyword=keyword,
        top_competitor_url=top_competitor_url,
        missing_topics=missing_topics,
        missing_entities=missing_entities,
        recommended_additions=recommended_additions,
    )

    # Persist result
    today = date.today().isoformat()
    safe_slug = keyword.lower().replace(" ", "-").replace("/", "-")[:60]
    output_dir = Path(settings.content_output_dir) / "reports" / "content-gaps"
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{today}-{safe_slug}.json"

    try:
        filepath.write_text(gap.model_dump_json(indent=2), encoding="utf-8")
        log.info(
            "content_gap_saved",
            keyword=keyword[:60],
            path=str(filepath),
            missing_topics=len(missing_topics),
            recommendations=len(recommended_additions),
        )
    except OSError as exc:
        log.error("content_gap_save_failed", keyword=keyword[:60], error=str(exc))

    return gap


async def run_weekly_gap_analysis(keywords: list[str] | None = None) -> list[ContentGap]:
    """
    Run content gap analysis for all target keywords.
    Called weekly by the APScheduler job in main.py.

    Uses TARGET_KEYWORDS by default. Pass a custom list to override.
    Skips keywords where analysis fails — never aborts the full batch.
    """
    kw_list = keywords if keywords is not None else TARGET_KEYWORDS
    log.info("weekly_gap_analysis_start", keyword_count=len(kw_list))

    gaps: list[ContentGap] = []
    for keyword in kw_list:
        result = await analyze_content_gap(keyword)
        if result is not None:
            gaps.append(result)

    log.info(
        "weekly_gap_analysis_done",
        requested=len(kw_list),
        completed=len(gaps),
        skipped=len(kw_list) - len(gaps),
    )
    return gaps
