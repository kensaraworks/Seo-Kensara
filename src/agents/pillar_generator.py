"""Module 2.4 — Pillar Page Generation System

5-stage pipeline for generating comprehensive 3000-5000 word hub pages.

Model routing (spec 2.9.A):
    All LLM calls go through ModelRouter — never raw client calls in this file.
    Stages 1, 2 → Groq ("cluster_synthesis" / "outline" tasks, JSON mode)
    Stage 3 → per section_type routing:
        regulatory_explainer → NVIDIA primary, Groq fallback ("regulatory_section")
        all other sections   → Groq primary, NVIDIA fallback ("pillar" task)
    Stage 5 term extraction  → Groq ("term_extraction", JSON mode)

Token budget (spec 2.9.B):
    tier=0 sentinel maps to 40,000 token budget for pillar pages.
    All calls tracked per-job via TokenLedger → token_costs.db.

Anti-hallucination (spec 2.9.C):
    ANTI_HALLUCINATION_SYSTEM_PROMPT prefixed to every LLM call.
"""

import os
import json
import time
import uuid
import re
import datetime
from typing import List, Optional, Tuple

import structlog
from pydantic import BaseModel, Field

from src.config import settings
from src.agents.intent_classifier import IntentType
from src.context.builder import assemble_keyword_brief
from src.agents.serp_intelligence import get_full_serp_intelligence
from src.context.cta_library import get_cta, get_service_link
from src.context.india_style_guide import apply_india_style
from src.engines.geo_optimizer import run_geo_checklist
from src.engines.internal_linker import query_cluster_posts, register_post
from src.engines.model_router import (
    ModelRouter,
    BudgetExceededError,
    ANTI_HALLUCINATION_SYSTEM_PROMPT,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class PillarPage(BaseModel):
    """Data model representing a final, assembled Pillar Page."""
    title: str
    meta_description: str
    slug: str
    cluster_id: str
    content_markdown: str
    word_count: int

    is_pillar: bool = True
    tier: int = 1
    geo_score: int = 0
    qa_score: float = 0.0
    risk_level: str = "HIGH"
    approved: bool = False

    author: str = "Mr Rudraksh Tatwal"
    author_credentials: str = "Founder & CEO, KensaraAI"

    date_created: str = ""
    date_published: Optional[str] = None
    date_modified: Optional[str] = None

    schema_json: str = "{}"
    internal_links_injected: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline Entry Point
# ---------------------------------------------------------------------------

async def generate_pillar_page(
    cluster_id: str,
    cluster_keyword: str,
    paa_questions: List[str],
    gap_topics: List[str],
    existing_cluster_urls: Optional[List[dict]] = None,
) -> PillarPage:
    """Execute the 5-stage Pillar Page Generation pipeline (spec 2.4).

    tier=0 is the sentinel for pillar pages — maps to 40,000 token budget (spec 2.9.B).
    existing_cluster_urls: if None, queries SQLite internal_link_map directly.
    """
    job_id = str(uuid.uuid4())[:8]
    t_start = time.monotonic()
    log.info("pillar_generation_started", cluster=cluster_id, job_id=job_id)

    if existing_cluster_urls is None:
        db_posts = query_cluster_posts(cluster_id, exclude_keyword=cluster_keyword)
        existing_cluster_urls = [
            {"title": p["post_title"], "url": p["post_url"]}
            for p in db_posts
        ]
        log.info("stage4_cluster_posts_from_db", count=len(existing_cluster_urls), cluster=cluster_id)

    # tier=0 → pillar budget (40,000 tokens per spec 2.9.B TIER_TOKEN_BUDGET)
    router = ModelRouter(job_id=job_id, tier=0, cluster_id=cluster_id)

    serp_intel = await get_full_serp_intelligence(cluster_keyword)
    brief = assemble_keyword_brief(
        keyword=cluster_keyword,
        intent_type=IntentType.INFORMATIONAL.value,
        tier=1,
        cluster_id=cluster_id,
        news_angle=None,
        paa_questions=paa_questions,
        serp_intelligence=serp_intel,
    )
    brief.content_gap.gap_topics = gap_topics
    context_str = json.dumps(brief.model_dump(), indent=2)

    # Stage 1: Cluster Synthesis
    topic_map = await _stage1_cluster_synthesis(router, cluster_keyword, paa_questions, gap_topics)

    # Stage 2: Pillar Outline Generation
    outline = await _stage2_pillar_outline(router, cluster_keyword, topic_map, context_str)

    # Stage 3: Section-by-Section Generation
    sections = await _stage3_pillar_sections(router, outline, cluster_keyword, context_str)

    # Stage 4: Internal Link Matrix (deterministic, no LLM)
    sections = _stage4_link_matrix(sections, existing_cluster_urls, topic_map)

    # Stage 5: Multi-Schema Injection & Assembly
    final_md = _assemble_pillar_markdown(sections, outline)
    final_md = apply_india_style(final_md)

    meta_data, schema_blocks = await _stage5_schemas_and_meta(
        router, final_md, outline, cluster_keyword, sections
    )

    # GEO Optimization (pillar pages always flagged HIGH — spec 2.4)
    geo_score, geo_flags, failed_critical, risk_level, approved = run_geo_checklist(
        markdown=final_md,
        meta=meta_data,
        slug=meta_data["slug"],
        keyword=cluster_keyword,
    )
    risk_level = "HIGH"
    approved = False

    t_elapsed = time.monotonic() - t_start
    final_page = _create_pillar_object(
        final_md, meta_data, schema_blocks, cluster_id, geo_score, risk_level, approved
    )

    _write_to_drafts(final_page, geo_flags)

    register_post(
        post_url=f"https://kensara.in/{cluster_id}",
        post_title=final_page.title,
        primary_keyword=cluster_keyword,
        cluster_id=cluster_id,
        intent_type="informational",
        tier=1,
        is_pillar=True,
    )

    log.info(
        "pillar_generation_complete",
        cluster=cluster_id,
        word_count=final_page.word_count,
        elapsed_seconds=round(t_elapsed, 1),
        tokens_spent=router.tokens_spent,
        cost_usd=round(router.cost_usd, 4),
        fallback_used=router.fallback_used,
        job_id=job_id,
    )
    return final_page


# ---------------------------------------------------------------------------
# STAGE 1 — Cluster Synthesis
# ---------------------------------------------------------------------------

async def _stage1_cluster_synthesis(
    router: ModelRouter,
    cluster_keyword: str,
    paa_questions: List[str],
    gap_topics: List[str],
) -> dict:
    """Stage 1: Generate sub-topic map, categorised as definitive / supporting / gap.

    Uses "cluster_synthesis" task → Groq JSON mode, temp 0.2 (spec 2.9.A).
    """
    log.info("stage1_cluster_synthesis")
    prompt = f"""Generate a comprehensive topic map for a 3000-5000 word Pillar Page.
CLUSTER KEYWORD: {cluster_keyword}
PAA QUESTIONS: {json.dumps(paa_questions)}
GAP TOPICS: {json.dumps(gap_topics)}

Categorize sub-topics into exactly three tags:
- "definitive": Core definitions and high-level concepts that MUST be in the pillar page.
- "supporting": Specific deep-dives that belong in separate cluster posts.
- "gap": Important topics currently missing from the cluster.

OUTPUT STRICT JSON ONLY:
{{
  "sub_topics": [
    {{
      "topic": "string",
      "tag": "definitive|supporting|gap"
    }}
  ]
}}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    raw = await router.generate("cluster_synthesis", messages, json_mode=True)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# STAGE 2 — Pillar Outline Generation
# ---------------------------------------------------------------------------

async def _stage2_pillar_outline(
    router: ModelRouter,
    keyword: str,
    topic_map: dict,
    context_str: str,
) -> dict:
    """Stage 2: Generate full JSON outline (8-12 sections, strict structure).

    Uses "outline" task → Groq JSON mode, temp 0.3 (spec 2.9.A).
    """
    log.info("stage2_pillar_outline")
    definitive_topics = [
        t["topic"] for t in topic_map.get("sub_topics", []) if t["tag"] == "definitive"
    ]

    prompt = f"""Generate a strict JSON outline for a 3000-5000 word Pillar Page.
PRIMARY KEYWORD: {keyword}
DEFINITIVE SUB-TOPICS TO COVER: {json.dumps(definitive_topics)}
CONTEXT BRIEF: {context_str}

MANDATORY RULES:
1. MUST include exactly 8 to 12 sections total.
2. MUST include these specific section types in this order:
   - "introduction" (first section)
   - "definition_block" (second section)
   - multiple "regulatory_explainer", "how_to", "comparison_table", or "checklist" sections
   - "faq_block" (penultimate section)
   - "cta_section" (final section)
3. Each section must have target_words between 250 and 500.
4. H1 must contain the primary keyword.
5. url_slug: lowercase, hyphenated, keyword present, <60 chars.

OUTPUT STRICT JSON ONLY:
{{
  "h1_title": "string",
  "url_slug": "string",
  "meta_title": "string (<60 chars, keyword first)",
  "meta_description": "string (130-155 chars, action verb, data point)",
  "sections": [
    {{
      "h2_heading": "string",
      "h3_subheadings": ["string"],
      "target_words": 350,
      "section_type": "introduction|definition_block|regulatory_explainer|how_to|comparison_table|checklist|faq_block|cta_section",
      "key_points_to_cover": ["string"]
    }}
  ]
}}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    raw = await router.generate("outline", messages, json_mode=True)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# STAGE 3 — Section-by-Section Generation
# ---------------------------------------------------------------------------

_PILLAR_SECTION_RULES: dict = {
    "introduction": (
        "Set context for why this topic matters to Indian businesses. 200-300 words. "
        "Must mention DPDPA, name at least one affected business category, and state a compelling ₹ risk figure."
    ),
    "definition_block": (
        "Provide exact, citable definitions of key terms. "
        "Format clearly so AI systems can extract 'What is X?' standalone answers. "
        "Each term: bold heading, 2-4 sentence definition, real-world Indian context sentence."
    ),
    "regulatory_explainer": (
        "CITE AT LEAST 3 DPDPA Sections or Rules. "
        "Include ₹ penalty figures. Name Indian regulators (DPBI, MeitY, RBI, SEBI). "
        "End with 'What this means in practice' subsection (50-80 words, plain English)."
    ),
    "how_to": (
        "Numbered list. Maximum 10 steps. Start each step with an action verb. "
        "Maximum 40 words per step. Highly actionable, India-specific implementation guidance."
    ),
    "comparison_table": (
        "Markdown table. EXHAUSTIVE comparison: Minimum 6 rows, minimum 4 columns. "
        "KensaraAI must be one column. Include a caption above and alt-text summary below."
    ),
    "checklist": (
        "Checkbox list using '[ ] item'. Minimum 10 actionable compliance items. "
        "Each item: max 20 words. At least 3 items must cite a specific DPDPA provision."
    ),
    "faq_block": (
        "10 to 15 questions using PAA phrasing exactly. Each answer: 40-80 words. "
        "Format as ### Question\\n\\nAnswer. At least 3 answers must include a ₹ or % figure."
    ),
}


async def _stage3_pillar_sections(
    router: ModelRouter,
    outline: dict,
    keyword: str,
    context_str: str,
) -> List[dict]:
    """Stage 3: Generate each section individually.

    CTA sections are deterministic (cta_library).
    regulatory_explainer → NVIDIA primary ("regulatory_section" task).
    All other sections → Groq primary ("pillar" task) with NVIDIA fallback.
    Failing sections: best-effort content with inline [PILLAR_FLAG].
    """
    log.info("stage3_pillar_sections", num_sections=len(outline.get("sections", [])))
    sections_content: List[dict] = []

    for idx, sec in enumerate(outline.get("sections", [])):
        sec_type = sec.get("section_type", "introduction")

        # CTA section — never LLM-generated
        if sec_type == "cta_section":
            cta = get_cta(IntentType.INFORMATIONAL.value, keyword)
            service = get_service_link(keyword)
            content = (
                f"## {cta['heading']}\n\n"
                f"{cta['body']}\n\n"
                f"[{cta['cta_text']}]({cta['cta_url']})\n\n"
                f"Also see: [{service['anchor']}]({service['url']})"
            )
            sections_content.append({"type": sec_type, "content": content, "h2": sec.get("h2_heading")})
            continue

        rules = _PILLAR_SECTION_RULES.get(sec_type, "Write a highly detailed pillar section.")
        prompt = f"""Write a single pillar page section for a DPDPA compliance article.
KEYWORD: {keyword}
SECTION TYPE: {sec_type}
H2 HEADING: {sec.get('h2_heading', '')}
TARGET WORDS: {sec.get('target_words', 400)}
POINTS TO COVER: {json.dumps(sec.get('key_points_to_cover', []))}

MANDATORY SECTION RULES:
{rules}

OUTPUT: Raw markdown only. Include the H2 heading. No ``` code fences. No explanation text."""

        # regulatory_explainer → NVIDIA primary; all others → Groq primary (spec 2.9.A)
        task = "regulatory_section" if sec_type == "regulatory_explainer" else "pillar"

        messages = [
            {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            content, used_fallback = await router.generate_with_fallback(
                task=task,
                messages=messages,
                tier_override=None,  # tier=0 (pillar) — NVIDIA allowed for all sections
            )
        except BudgetExceededError:
            log.warning("stage3_budget_exceeded", section_idx=idx, sec_type=sec_type)
            content = (
                f"## {sec.get('h2_heading', 'Section')}\n\n"
                f"[PILLAR_FLAG: section skipped — token budget exhausted. Manual content required.]"
            )
        except RuntimeError as exc:
            log.error("stage3_both_providers_failed", idx=idx, error=str(exc))
            content = (
                f"## {sec.get('h2_heading', 'Section')}\n\n"
                f"[PILLAR_FLAG: generation failed for {sec_type}. Manual content required.]"
            )

        sections_content.append({"type": sec_type, "content": content, "h2": sec.get("h2_heading")})

    return sections_content


# ---------------------------------------------------------------------------
# STAGE 4 — Internal Link Matrix (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _stage4_link_matrix(
    sections: List[dict],
    existing_urls: List[dict],
    topic_map: dict,
) -> List[dict]:
    """Stage 4: Inject cluster links and gap-topic placeholders (deterministic).

    Inserts a 'Further Reading' block before the FAQ or CTA section.
    """
    log.info("stage4_link_matrix", cluster_posts=len(existing_urls))
    gap_topics = [t["topic"] for t in topic_map.get("sub_topics", []) if t["tag"] == "gap"]

    further_reading = "\n\n### Further Reading\n"
    for url_obj in existing_urls:
        further_reading += f"- [{url_obj['title']}]({url_obj['url']}) →\n"
    for gap in gap_topics:
        further_reading += f"- [Coming soon: complete guide to {gap}]\n"

    if len(sections) > 1:
        idx_to_inject = len(sections) - 2  # before FAQ/CTA
        sections[idx_to_inject]["content"] += further_reading

    return sections


# ---------------------------------------------------------------------------
# STAGE 5 — Multi-Schema Injection & Assembly
# ---------------------------------------------------------------------------

def _assemble_pillar_markdown(sections: List[dict], outline: dict) -> str:
    """Combine all sections into a single Markdown document with ToC and byline."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    md = f"# {outline.get('h1_title', 'Pillar Page')}\n\n"
    md += f"*Written by Mr Rudraksh Tatwal | Founder & CEO, KensaraAI | {today}*\n\n"
    md += f"*Last Updated: {today}*\n\n"

    # Table of Contents
    md += "## Table of Contents\n\n"
    for s in sections:
        h2 = s.get("h2")
        if h2:
            anchor = re.sub(r"[^a-z0-9]+", "-", h2.lower()).strip("-")
            md += f"- [{h2}](#{anchor})\n"
    md += "\n---\n\n"

    for s in sections:
        md += s["content"].strip() + "\n\n"

    md += (
        "---\n\n"
        "**About the Author**\n\n"
        "This article is published by KensaraAI leadership. Mr Rudraksh Tatwal (Founder & CEO) "
        "and Mr Prince (Co-founder & COO) lead KensaraAI's India-focused DPDPA compliance strategy.\n"
    )
    return md


async def _stage5_schemas_and_meta(
    router: ModelRouter,
    final_md: str,
    outline: dict,
    keyword: str,
    sections: List[dict],
) -> Tuple[dict, dict]:
    """Stage 5: Build metadata dict and all JSON-LD schema blocks.

    DefinedTermSet schema extraction uses "term_extraction" task → Groq JSON mode, temp 0.1.
    All other schema blocks are constructed deterministically.
    """
    log.info("stage5_schemas")

    meta_title = (outline.get("meta_title") or keyword)[:60]
    meta_desc = (outline.get("meta_description") or "")[:155]
    slug = (outline.get("url_slug") or keyword.replace(" ", "-").lower())[:60]

    meta = {
        "title": meta_title,
        "description": meta_desc,
        "slug": slug,
    }

    publish_date = datetime.datetime.now(datetime.timezone.utc).isoformat()
    schemas: dict = {
        "Article": {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": meta_title,
            "description": meta_desc,
            "datePublished": publish_date,
            "dateModified": publish_date,
            "author": {
                "@type": "Person",
                "name": "Mr Rudraksh Tatwal",
                "jobTitle": "Founder & CEO",
                "worksFor": {"@type": "Organization", "name": "KensaraAI"},
            },
            "publisher": {
                "@type": "Organization",
                "name": "KensaraAI",
                "logo": {"@type": "ImageObject", "url": "https://kensara.in/logo.png"},
            },
            "inLanguage": "en-IN",
            "keywords": keyword,
        },
        "BreadcrumbList": {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://www.kensara.in"},
                {"@type": "ListItem", "position": 2, "name": "Blogs", "item": "https://www.kensara.in/blogs"},
                {"@type": "ListItem", "position": 3, "name": meta_title, "item": f"https://www.kensara.in/{slug}"},
            ],
        },
        "SpeakableSpecification": {
            "@context": "https://schema.org",
            "@type": "SpeakableSpecification",
            "cssSelector": [".speakable-1", ".speakable-2"],
        },
    }

    # FAQPage schema
    faq_section = next((s for s in sections if s["type"] == "faq_block"), None)
    if faq_section:
        schemas["FAQPage"] = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [],
        }

    # DefinedTermSet — extract via LLM from definition_block section
    definition_section = next((s for s in sections if s["type"] == "definition_block"), None)
    if definition_section:
        extract_prompt = (
            f"Extract all defined terms and their definitions from the text below.\n\n"
            f"TEXT:\n{definition_section['content']}\n\n"
            f"OUTPUT STRICT JSON ONLY:\n"
            f'{{ "terms": [ {{"term": "string", "definition": "string"}} ] }}'
        )
        messages = [
            {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
            {"role": "user", "content": extract_prompt},
        ]
        try:
            raw = await router.generate("term_extraction", messages, json_mode=True)
            data = json.loads(raw)
            schemas["DefinedTermSet"] = {
                "@context": "https://schema.org",
                "@type": "DefinedTermSet",
                "name": f"DPDPA {keyword} Glossary",
                "hasDefinedTerm": [
                    {"@type": "DefinedTerm", "name": t["term"], "description": t["definition"]}
                    for t in data.get("terms", [])
                ],
            }
        except (json.JSONDecodeError, BudgetExceededError, RuntimeError) as exc:
            log.warning("defined_term_extraction_failed", error=str(exc))

    return meta, schemas


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _create_pillar_object(
    md: str,
    meta: dict,
    schemas: dict,
    cluster_id: str,
    geo_score: int,
    risk: str,
    approved: bool,
) -> PillarPage:
    return PillarPage(
        title=meta.get("title", ""),
        meta_description=meta.get("description", ""),
        slug=meta.get("slug", "pillar"),
        cluster_id=cluster_id,
        content_markdown=md,
        word_count=len(md.split()),
        geo_score=geo_score,
        risk_level=risk,
        approved=approved,
        date_created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        schema_json=json.dumps(schemas, ensure_ascii=False),
    )


def _write_to_drafts(post: PillarPage, geo_flags: List[str]) -> None:
    draft_dir = os.path.join("drafts", "pillars")
    os.makedirs(draft_dir, exist_ok=True)

    date_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    filename = f"{date_prefix}-{post.slug}.md"
    filepath = os.path.join(draft_dir, filename)

    flags_yaml = "\n".join(f"  # {f}" for f in geo_flags) if geo_flags else "  # none"
    frontmatter = f"""---
title: "{post.title.replace('"', "'")}"
slug: "{post.slug}"
meta_description: "{post.meta_description.replace('"', "'")}"
cluster: "{post.cluster_id}"
is_pillar: true
word_count: {post.word_count}
geo_score: {post.geo_score}
geo_flags:
{flags_yaml}
risk_level: "{post.risk_level}"
approved: {str(post.approved).lower()}
schema_json: '{post.schema_json.replace("'", "''")}'
---

"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(frontmatter + post.content_markdown)
    log.info("pillar_draft_saved", path=filepath)
