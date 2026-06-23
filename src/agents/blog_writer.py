"""Blog writer agent — generates SEO blog posts targeting DPDPA/GDPR keywords.

Primary LLM: Groq (llama-3.3-70b-versatile) — fast, free, confirmed working.
Fallback LLM: NVIDIA NIM (mistralai/mistral-medium-3.5-128b) — slower free tier.

Context injected from src.context.builder — single source of truth for brand facts.
"""
import json

import structlog
from groq import AsyncGroq
from openai import AsyncOpenAI
from pydantic import BaseModel

from src.agents.news_scout import ScoredNewsItem
from src.config import settings
from src.context.builder import build_context

log = structlog.get_logger()

KEYWORD_ROTATION = [
    "DPDPA compliance software",
    "DSAR automation India",
    "consent management platform India",
    "data breach notification software India",
    "DPDPA compliance checklist",
    "GDPR compliance tool India",
    "DPIA assessment India",
    "DPDPA vs GDPR",
]


class BlogPost(BaseModel):
    title: str
    meta_description: str
    slug: str
    primary_keyword: str
    secondary_keywords: list[str]
    content_markdown: str
    word_count: int
    cta_url: str = "https://kensara.in/request-demo"


# --- Client factories ---

def _get_groq_client() -> AsyncGroq:
    """Groq client — PRIMARY. Reads .env directly to bypass OS env var override."""
    from dotenv import dotenv_values
    env = dotenv_values(".env")
    key = env.get("GROQ_API_KEY") or settings.groq_api_key
    return AsyncGroq(api_key=key)


def _get_nvidia_client() -> AsyncOpenAI:
    """NVIDIA NIM client — FALLBACK. OpenAI-compatible with custom base_url."""
    return AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=settings.nvidia_api_key,
    )


# --- Public API ---

async def generate_blog_post(news_item: ScoredNewsItem, keyword: str) -> BlogPost:
    """Generate a complete SEO blog post from a news item + target keyword.

    Tries NVIDIA NIM first. If that raises any exception, retries once with Groq.
    """
    log.info("blog_write_start", keyword=keyword, news_title=news_item.item.title[:60])

    # Build context once — inject into all prompt steps
    context_str = build_context(
        keyword=keyword,
        news_angle=news_item.suggested_angle,
    )

    try:
        post = await _generate_with_groq(news_item, keyword, context_str)
        log.info("blog_groq_success", keyword=keyword)
    except Exception as exc:
        log.warning(
            "blog_groq_failed_falling_back_to_nvidia",
            error=str(exc),
            keyword=keyword,
        )
        post = await _generate_with_nvidia(news_item, keyword, context_str)
        log.info("blog_nvidia_fallback_success", keyword=keyword)

    return post


# --- NVIDIA NIM generation path ---

async def _generate_with_nvidia(
    news_item: ScoredNewsItem, keyword: str, context_str: str
) -> BlogPost:
    """Full 3-step generation: outline → content → meta. Uses NVIDIA NIM."""
    client = _get_nvidia_client()

    outline = await _generate_outline_nvidia(client, news_item, keyword)
    content = await _generate_content_nvidia(client, outline, news_item, keyword, context_str)
    meta = await _generate_meta_nvidia(client, content, keyword)

    return _assemble_post(keyword, content, meta)


async def _generate_outline_nvidia(
    client: AsyncOpenAI, news: ScoredNewsItem, keyword: str
) -> str:
    prompt = _outline_prompt(keyword, news)
    response = await client.chat.completions.create(
        model=settings.nvidia_model_blog,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        timeout=120.0,
    )
    return response.choices[0].message.content or ""


async def _generate_content_nvidia(
    client: AsyncOpenAI,
    outline: str,
    news: ScoredNewsItem,
    keyword: str,
    context_str: str,
) -> str:
    prompt = _content_prompt(outline, news, keyword, context_str)
    response = await client.chat.completions.create(
        model=settings.nvidia_model_blog,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000,
        timeout=180.0,
    )
    return response.choices[0].message.content or ""


async def _generate_meta_nvidia(
    client: AsyncOpenAI, content: str, keyword: str
) -> dict:
    prompt = _meta_prompt(content, keyword)
    response = await client.chat.completions.create(
        model=settings.nvidia_model_blog,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        timeout=30.0,
    )
    raw = response.choices[0].message.content or "{}"
    return _parse_meta_json(raw, keyword)


# --- Groq fallback generation path ---

async def _generate_with_groq(
    news_item: ScoredNewsItem, keyword: str, context_str: str
) -> BlogPost:
    """Full 3-step generation using Groq (llama-3.3-70b-versatile) as fallback."""
    client = _get_groq_client()

    outline = await _generate_outline_groq(client, news_item, keyword)
    content = await _generate_content_groq(client, outline, news_item, keyword, context_str)
    meta = await _generate_meta_groq(client, content, keyword)

    return _assemble_post(keyword, content, meta)


async def _generate_outline_groq(
    client: AsyncGroq, news: ScoredNewsItem, keyword: str
) -> str:
    prompt = _outline_prompt(keyword, news)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        timeout=30.0,
    )
    return response.choices[0].message.content or ""


async def _generate_content_groq(
    client: AsyncGroq,
    outline: str,
    news: ScoredNewsItem,
    keyword: str,
    context_str: str,
) -> str:
    prompt = _content_prompt(outline, news, keyword, context_str)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000,
        timeout=60.0,
    )
    return response.choices[0].message.content or ""


async def _generate_meta_groq(
    client: AsyncGroq, content: str, keyword: str
) -> dict:
    prompt = _meta_prompt(content, keyword)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        timeout=30.0,
    )
    raw = response.choices[0].message.content or "{}"
    return _parse_meta_json(raw, keyword)


# --- Shared prompt builders ---

def _outline_prompt(keyword: str, news: ScoredNewsItem) -> str:
    return f"""Create a blog post outline for an SEO article targeting Indian compliance buyers.

Primary keyword: {keyword}
News angle: {news.suggested_angle}
News source: {news.item.title} ({news.item.source})

Outline format:
- H1 title (60-70 chars, includes keyword)
- Intro hook (fear/urgency — reference the news)
- H2 section 1: What is [topic] / Why it matters now in India
- H2 section 2: Step-by-step / Checklist for Indian companies
- H2 section 3: Common mistakes Indian companies make
- H2 section 4: How KensaraAI solves this (soft sell)
- Conclusion + CTA to kensara.in/request-demo

Return the outline only. No fluff."""


def _content_prompt(
    outline: str, news: ScoredNewsItem, keyword: str, context_str: str
) -> str:
    return f"""Write a complete SEO blog post for KensaraAI following this outline:

{outline}

{context_str}

News reference to weave in:
Title: {news.item.title}
Summary: {news.item.summary}
URL: {news.item.url}

Rules:
- 800-1200 words total
- Primary keyword "{keyword}" appears in H1, first 100 words, and 2-3 times naturally
- India-focused, practical, DPO-friendly tone
- Reference the news item naturally (not forced)
- End with strong CTA to https://kensara.in/request-demo
- Format in clean Markdown (## for H2, ### for H3)
- No generic filler. Every paragraph must deliver value.
- Never claim anything not listed in the KENSARAI BRAND CONTEXT above"""


def _meta_prompt(content: str, keyword: str) -> str:
    return f"""Generate SEO metadata for this blog post. Primary keyword: {keyword}

Content preview: {content[:500]}...

Return JSON only (no markdown fences):
{{
  "title": "<60-70 chars, includes keyword, compelling>",
  "meta_description": "<150-160 chars, includes keyword, ends with action>",
  "secondary_keywords": ["<keyword 1>", "<keyword 2>", "<keyword 3>"]
}}"""


# --- Helpers ---

def _parse_meta_json(raw: str, keyword: str) -> dict:
    """Parse meta JSON from LLM response. Strip markdown fences if present."""
    cleaned = raw.strip()
    # Strip ```json ... ``` fences if model wraps output
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Remove first and last fence lines
        inner = [l for l in lines if not l.startswith("```")]
        cleaned = "\n".join(inner).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.warning("meta_json_parse_failed", error=str(exc), raw_preview=raw[:100])
        # Graceful fallback — generate minimal meta from keyword
        return {
            "title": f"{keyword} — Complete Guide for Indian Companies",
            "meta_description": (
                f"Complete guide to {keyword} for Indian enterprises. "
                f"Stay DPDPA compliant. Book demo at kensara.in."
            )[:160],
            "secondary_keywords": ["DPDPA compliance", "data privacy India", "GDPR India"],
        }


def _assemble_post(keyword: str, content: str, meta: dict) -> BlogPost:
    """Assemble final BlogPost. Logs warning if word count is outside 800-1200 range."""
    word_count = len(content.split())
    if word_count < 800 or word_count > 1200:
        log.warning("blog_word_count_off", word_count=word_count, keyword=keyword)

    slug = keyword.lower().replace(" ", "-").replace("/", "-")

    post = BlogPost(
        title=meta.get("title", f"{keyword} — Guide for Indian Companies"),
        meta_description=meta.get("meta_description", "")[:160],
        slug=slug,
        primary_keyword=keyword,
        secondary_keywords=meta.get("secondary_keywords", []),
        content_markdown=content,
        word_count=word_count,
    )

    log.info("blog_write_done", title=post.title[:70], word_count=word_count, slug=slug)
    return post
