"""Blog writer agent — Module 2.2 GEO 7-Step Generation Pipeline.

Step 1  — SERP-Informed Outline Generation (strict JSON, 1-retry validation loop)
Step 2  — Section-by-Section Body Generation (per section_type rules + validation)
Step 3  — Assembly & Continuity Pass (LLM editing pass, NOT a rewrite)
Step 4  — On-Page SEO Injection (deterministic NLP, no LLM call)
Step 5  — Metadata & Structured Data Generation (Article, FAQ, HowTo, Breadcrumb, Speakable)
Step 6  — GEO Optimization Pass (20-item deterministic rubric)
Step 7  — Final Document Assembly & Frontmatter Generation

Model routing (spec 2.9.A):
    All LLM calls go through ModelRouter — never raw client calls in this file.
    Steps 1, 3, 5 → Groq ("outline" / "assembly" / "metadata" tasks)
    Step 2 generic sections → Groq ("section" task) with NVIDIA fallback
    Step 2 regulatory_explainer → NVIDIA primary ("regulatory_section" task)
    Tier 3 posts → Groq ONLY for all steps (latency requirement)

Anti-hallucination (spec 2.9.C):
    ANTI_HALLUCINATION_SYSTEM_PROMPT imported from model_router and used as the
    system message for EVERY LLM call — outline, section, assembly, metadata.

CTA text: NEVER LLM-generated — always pulled from cta_library.py.
India English: applied as a deterministic post-processing pass (spec 2.8.B).
"""

import os
import re
import json
import time
import uuid
import asyncio
import datetime
import sqlite3
import structlog

from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.config import settings
from src.agents.news_scout import ScoredNewsItem
from src.context.builder import assemble_keyword_brief, SerpIntelligence
from src.agents.intent_classifier import IntentType
from src.agents.serp_intelligence import get_full_serp_intelligence
from src.context.cta_library import get_cta, get_service_link
from src.context.india_style_guide import apply_india_style
from src.engines.geo_optimizer import run_geo_checklist
from src.engines.tier_templates import get_tier_config, generate_tier3_title
from src.engines.internal_linker import inject_mandatory_links, register_post, validate_and_inject_optional_links
from src.engines.model_router import (
    ModelRouter,
    BudgetExceededError,
    ANTI_HALLUCINATION_SYSTEM_PROMPT,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Spacy — Step 4 semantic injection (no LLM, faster and cheaper per spec)
# ---------------------------------------------------------------------------
try:
    import spacy
    _nlp = spacy.load("en_core_web_sm")
    SPACY_AVAILABLE = True
except (ImportError, OSError):
    log.warning("spacy_not_available", reason="en_core_web_sm not loaded; falling back to regex for Step 4.")
    SPACY_AVAILABLE = False

# ---------------------------------------------------------------------------
# Tier word count RANGES (spec 2.3, widened per Phase 1 CHANGE — depth should
# follow how much genuinely specific content a topic has, not a fixed target.
# These are outer bounds, not a number to hit — see _step1_generate_outline.
# ---------------------------------------------------------------------------
TIER_WORD_COUNT: dict = {
    1: (1400, 3000),
    2: (900, 2200),
    3: (500, 1000),
}

# Weekly keyword rotation — cycles deterministically by ISO week number.
# Editable from the Context & Setup page; these are the hardcoded fallback defaults.
KEYWORD_ROTATION: list[str] = [
    "DPDPA compliance checklist India",
    "data protection officer India DPDPA",
    "consent management platform India DPDPA",
    "personal data breach notification India 72 hours",
    "data fiduciary obligations DPDPA India",
    "DPDPA vs GDPR key differences India",
    "digital personal data protection act India guide",
    "data principal rights DPDPA India",
    "DPDPA implementation roadmap enterprises India",
    "DPDPA penalty enforcement India",
    "significant data fiduciary India DPDPA",
    "cross-border data transfer DPDPA India",
]


def _wrap_internal_context(context_str: str) -> str:
    """Wrap injected keyword-brief/brand JSON so the LLM treats it strictly as
    background knowledge, never as literal text to reproduce (spec CHANGE-B4).
    Prevents brand facts leaking into the article as bare standalone lines.
    """
    return (
        "─── INTERNAL CONTEXT — DO NOT REPRODUCE IN OUTPUT ───\n"
        "The following facts are background knowledge only, to inform what you write. "
        "Reference them naturally where relevant to the content. "
        "Do NOT output them as standalone sentences, headings, or bullet lists. "
        "Do NOT reproduce this label, the JSON structure, or field names in the article.\n"
        f"{context_str}\n"
        "─── END OF INTERNAL CONTEXT ───"
    )


_PREVIOUSLY_WRITTEN_WORD_LIMIT = 900


def _build_previously_written_block(previously_written: str) -> str:
    """Build the anti-redundancy context block injected into every section
    prompt after the first (spec CHANGE-A1) — the single root cause of
    cross-section redundancy is that each section was generated cold, with no
    knowledge of what earlier sections already said.

    Truncates to the last 2 sections once the cumulative draft grows past a
    budget-safe word limit, so long Tier 1 posts don't blow the per-call
    token budget while still telling the model what NOT to repeat.
    """
    text = previously_written.strip()
    if not text:
        return ""

    if len(text.split()) > _PREVIOUSLY_WRITTEN_WORD_LIMIT:
        parts = text.split("\n\n## ")
        if len(parts) > 2:
            kept = "## " + "\n\n## ".join(parts[-2:])
            text = (
                "[Earlier sections omitted for brevity — do not repeat facts, "
                "figures, penalty amounts, or rules already covered in the omitted "
                "sections.]\n\n" + kept
            )

    return (
        "\n\n=== PREVIOUSLY WRITTEN SECTIONS (DO NOT REPEAT ANYTHING FROM THESE) ===\n"
        f"{text}\n"
        "=== END OF PREVIOUS SECTIONS ===\n\n"
        "STRICT RULE: Your section must contain ZERO sentences that repeat a fact, rule, "
        "obligation, penalty amount, or regulatory requirement already stated in the "
        "sections above. Do not rephrase, restate, or summarise what is above. If you "
        "cannot add genuinely new information without repeating what is above, write "
        "fewer words — a short section with new information is better than a long "
        "one that repeats old information."
    )


def _get_keyword_rotation() -> list[str]:
    """Return keyword rotation from platform_stats.json if set, else use hardcoded list."""
    try:
        from src.context.platform_stats import get_platform_stats
        rotation = get_platform_stats().get("keyword_rotation", [])
        clean = [k.strip() for k in rotation if k.strip()]
        if clean:
            return clean
    except Exception:
        pass
    return KEYWORD_ROTATION

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class BlogPost(BaseModel):
    """Complete blog post output with all frontmatter fields (spec 2.2 Step 7)."""

    title: str
    meta_description: str
    slug: str
    primary_keyword: str
    secondary_keywords: List[str] = Field(default_factory=list)
    content_markdown: str
    word_count: int

    cta_url: str = "https://www.kensara.in/book-demo"

    cluster: str = "general"
    intent: str = "informational"
    tier: int = 1

    geo_score: int = 0
    qa_score: float = 0.0

    risk_level: str = "HIGH"
    approved: bool = False

    # Set when check_penalty_consistency() finds a banned/contradictory ₹
    # figure — the post is written to drafts/flagged/ instead of drafts/blogs/
    # and must never be treated as a normal pending-review item.
    flagged: bool = False
    flag_reason: str = ""

    author: str = "Mr Rudraksh Tatwal"
    author_credentials: str = "Founder & CEO, KensaraAI"

    date_created: str = ""
    date_published: Optional[str] = None
    date_modified: Optional[str] = None

    schema_json: str = "{}"

    internal_links_injected: List[str] = Field(default_factory=list)

    source_story_url: Optional[str] = None
    featured_image_alt: str = ""
    wp_post_id: Optional[str] = None
    wp_post_url: Optional[str] = None

    # ── Supabase public.blogs fields (seo_agent_post_guide.md) ──────────────
    # Cover banner image URL. Rendered by Next.js above the article body.
    image_url: Optional[str] = None
    # Exact pillar slug from blog_slug_reference.md (resolved at publish time).
    # Stored here so the object is self-contained after generation.
    pillar: str = ""
    # Badge displayed on the blog card (e.g. "Fintech", "Guide", "Deep dive").
    category: str = ""


# ---------------------------------------------------------------------------
def _assemble_post(keyword: str, content: str, meta: dict) -> BlogPost:
    """Build a BlogPost from assembled content + meta dict."""
    from src.data.shell_slugs import CLUSTER_TO_PILLAR, CLUSTER_TO_CATEGORY
    slug = meta.get("slug", _slugify(keyword))
    cluster = meta.get("cluster", "general")
    pillar = CLUSTER_TO_PILLAR.get(cluster, "fundamentals")
    category = meta.get("category", CLUSTER_TO_CATEGORY.get(cluster, "Guide"))
    return BlogPost(
        title=meta.get("title", keyword),
        meta_description=meta.get("description", ""),
        slug=slug,
        primary_keyword=keyword,
        secondary_keywords=meta.get("secondary_keywords", []),
        content_markdown=content,
        word_count=len(content.split()),
        cta_url=meta.get("cta_url", "https://www.kensara.in/book-demo"),
        cluster=cluster,
        intent=meta.get("intent", "informational"),
        tier=meta.get("tier", 1),
        geo_score=meta.get("geo_score", 0),
        qa_score=meta.get("qa_score", 0.0),
        risk_level=meta.get("risk_level", "HIGH"),
        approved=meta.get("approved", False),
        flagged=meta.get("flagged", False),
        flag_reason=meta.get("flag_reason", ""),
        date_created=meta.get("date_created", datetime.datetime.now(datetime.timezone.utc).isoformat()),
        schema_json=meta.get("schema_json", "{}"),
        internal_links_injected=meta.get("internal_links_injected", []),
        source_story_url=meta.get("source_story_url"),
        featured_image_alt=meta.get("featured_image_alt", f"{keyword} — kensara.in"),
        image_url=meta.get("image_url"),
        pillar=pillar,
        category=category,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_blog_post(
    news_item: ScoredNewsItem,
    keyword: str,
    intent_type: str = IntentType.INFORMATIONAL.value,
    paa_questions: Optional[List[str]] = None,
    tier: int = 1,
    cluster_id: str = "general",
    industry: Optional[str] = None,
) -> BlogPost:
    """Execute the full 7-step GEO pipeline (Module 2.2).

    Tier 3 posts skip the GEO optimization pass (spec 2.3, TIER 3 note).
    All LLM calls are routed through ModelRouter for per-job token tracking.
    """
    job_id = str(uuid.uuid4())[:8]
    t_start = time.monotonic()
    log.info("blog_generation_started", keyword=keyword, tier=tier, job_id=job_id)

    # One ModelRouter per job — tracks budget and logs all token costs (spec 2.9.B)
    router = ModelRouter(job_id=job_id, tier=tier, cluster_id=cluster_id)

    # -------------------------------------------------------------------
    # Pre-generation: Module 2.1 keyword brief assembly
    # -------------------------------------------------------------------
    serp_intel = await get_full_serp_intelligence(keyword)
    brief = assemble_keyword_brief(
        keyword=keyword,
        intent_type=intent_type,
        tier=tier,
        cluster_id=cluster_id,
        news_angle=news_item.suggested_angle,
        paa_questions=paa_questions,
        serp_intelligence=serp_intel if isinstance(serp_intel, SerpIntelligence) else None,
        industry=industry,
    )
    brief_dict = brief.model_dump()

    target_words_range = TIER_WORD_COUNT.get(tier, (1200, 1600))
    target_words = int((target_words_range[0] + target_words_range[1]) / 2)
    brief_dict["target_word_count"] = target_words
    context_str = json.dumps(brief_dict, indent=2)

    tier_config = get_tier_config(tier, industry)
    tier3_title = None
    if tier == 3:
        entity = news_item.item.url or "Regulator"
        if "gov.in" in entity:
            entity = "Gov"
        tier3_title = generate_tier3_title(entity, news_item.suggested_angle or "Update")

    # -------------------------------------------------------------------
    # Step 1: SERP-Informed Outline Generation
    # -------------------------------------------------------------------
    outline = await _step1_generate_outline(
        router, keyword, context_str, intent_type,
        paa_questions, tier, target_words, tier_config, tier3_title,
        cluster_id=cluster_id,
    )

    # -------------------------------------------------------------------
    # Step 2: Section-by-Section Body Generation
    # -------------------------------------------------------------------
    sections = await _step2_generate_sections(
        router, outline, keyword, context_str, intent_type, tier, industry=industry
    )

    # -------------------------------------------------------------------
    # Step 3: Assembly & Continuity Pass
    # -------------------------------------------------------------------
    assembled_md = await _step3_assembly_pass(router, sections, outline, keyword, tier=tier)

    # -------------------------------------------------------------------
    # Step 4: On-Page SEO Injection (deterministic, no LLM)
    # -------------------------------------------------------------------
    optimised_md = _step4_seo_injection(
        assembled_md, keyword,
        brief_dict.get("content_gap", {}).get("gap_topics", [])
    )

    # Apply Indian English enforcement (spec 2.8.B)
    optimised_md = apply_india_style(optimised_md)

    # -------------------------------------------------------------------
    # Step 4c: QA Gate & Targeted Fix (spec Phase 2 — before link injection so
    # the fix pass can never disturb an already-injected hyperlink)
    # -------------------------------------------------------------------
    optimised_md = await _step4c_qa_gate_and_fix(router, optimised_md, outline, keyword, intent_type, tier)

    # -------------------------------------------------------------------
    # Step 4b: Mandatory Internal Link Injection (spec 2.5.B — deterministic)
    # -------------------------------------------------------------------
    optional_suggestions = [
        {
            "url": s.get("internal_link_opportunity", ""),
            "anchor": s.get("h2_heading", ""),
            "context_phrase": "",
        }
        for s in outline.get("sections", [])
        if s.get("internal_link_opportunity")
    ]
    optimised_md, injected_links = inject_mandatory_links(
        markdown=optimised_md,
        keyword=keyword,
        cluster_id=cluster_id,
        intent_type=intent_type,
        tier=tier,
    )
    if optional_suggestions:
        optimised_md, injected_links = validate_and_inject_optional_links(
            markdown=optimised_md,
            suggested_links=optional_suggestions,
            current_keyword=keyword,
            injected_urls=injected_links,
        )
    log.info("step4b_links_injected", count=len(injected_links))

    # -------------------------------------------------------------------
    # Step 5: Metadata & Structured Data Generation
    # -------------------------------------------------------------------
    meta_data, schema_blocks = await _step5_metadata_and_schema(
        router, optimised_md, outline, keyword, intent_type
    )

    # -------------------------------------------------------------------
    # Step 6: GEO Optimization Pass (skip for Tier 3 — spec 2.3)
    # -------------------------------------------------------------------
    if tier == 3:
        geo_score, geo_flags, failed_critical, risk_level, approved = (
            14, [], [], "MEDIUM", False
        )
        log.info("geo_pass_skipped_tier3", keyword=keyword)
    else:
        geo_score, geo_flags, failed_critical, risk_level, approved = run_geo_checklist(
            markdown=optimised_md,
            meta=meta_data,
            slug=meta_data["slug"],
            keyword=keyword,
        )
        if tier == 1:
            # Spec 2.3: Tier 1 always requires CEO review, never auto-approve
            risk_level = "HIGH"
            approved = False

    # Hard blocker: banned/contradictory ₹ penalty figures force HIGH risk +
    # manual review regardless of GEO score or tier (spec CHANGE-B2). This is
    # the live-pipeline home for the penalty-consistency check — the
    # dedicated QA module (src/quality/checker.py) is not wired into the
    # generation path, so it runs inline here where it can actually affect
    # risk_level/approved on every post before it reaches the queue.
    from src.quality.checker import check_penalty_consistency
    penalty_check = check_penalty_consistency(optimised_md)
    flagged = False
    flag_reason = ""
    if not penalty_check["passed"]:
        geo_flags.append(
            f"[PENALTY_CONSISTENCY] banned_amounts={penalty_check['banned_amounts_found']} "
            f"conflicting_sentences={len(penalty_check['conflicting_sentences'])}"
        )
        risk_level = "HIGH"
        approved = False
        log.warning(
            "penalty_consistency_check_failed",
            keyword=keyword,
            banned=penalty_check["banned_amounts_found"],
            conflicts=len(penalty_check["conflicting_sentences"]),
        )
        # A banned (draft-bill-era) figure is a hard legal-accuracy blocker —
        # per spec CHANGE-B2 this post never reaches the normal review queue;
        # it's written to drafts/flagged/ instead of drafts/blogs/ and
        # surfaced separately in the content UI. Internal contradictions
        # (conflicting_sentences with no banned figure) stay in the normal
        # queue with HIGH risk — a softer signal, still worth a human look
        # but not necessarily a legal-accuracy violation.
        if penalty_check["banned_amounts_found"]:
            flagged = True
            flag_reason = (
                "Banned penalty figure(s) detected — not in the enacted DPDPA: "
                f"{', '.join('₹' + a for a in penalty_check['banned_amounts_found'])}"
            )

    if geo_flags:
        log.warning("geo_flags_detected", flags=geo_flags, failed_critical=failed_critical)

    # -------------------------------------------------------------------
    # Step 7: Final Document Assembly & Frontmatter
    # -------------------------------------------------------------------
    t_elapsed = time.monotonic() - t_start
    final_post = _step7_final_assembly(
        markdown=optimised_md,
        meta=meta_data,
        schema=schema_blocks,
        outline=outline,
        keyword=keyword,
        intent=intent_type,
        tier=tier,
        cluster=cluster_id,
        geo_score=geo_score,
        geo_flags=geo_flags,
        risk_level=risk_level,
        approved=approved,
        flagged=flagged,
        flag_reason=flag_reason,
    )

    _write_to_drafts(final_post)
    _log_to_sqlite(
        post=final_post,
        geo_score=geo_score,
        job_id=job_id,
        elapsed_seconds=t_elapsed,
        tier=tier,
        cluster=cluster_id,
        fallback_used=router.fallback_used,
        tokens_spent=router.tokens_spent,
        cost_usd=router.cost_usd,
    )

    # Register in the link map (spec 2.5.A) so future posts can link to it
    register_post(
        post_url=f"https://kensara.in/blogs/{final_post.slug}",
        post_title=final_post.title,
        primary_keyword=keyword,
        cluster_id=cluster_id,
        intent_type=intent_type,
        tier=tier,
        is_pillar=False,
    )

    log.info(
        "blog_generation_complete",
        keyword=keyword,
        word_count=final_post.word_count,
        geo_score=geo_score,
        risk_level=risk_level,
        approved=approved,
        elapsed_seconds=round(t_elapsed, 1),
        tokens_spent=router.tokens_spent,
        cost_usd=round(router.cost_usd, 4),
        fallback_used=router.fallback_used,
        job_id=job_id,
    )
    return final_post


# ---------------------------------------------------------------------------
# STEP 1 — SERP-Informed Outline Generation
# ---------------------------------------------------------------------------

_OUTLINE_SCHEMA = """{
  "h1_title": "string — primary keyword in first 60 chars",
  "url_slug": "string — hyphenated, <60 chars, no stop words, no special chars",
  "meta_title": "string — <60 chars, keyword first, | KensaraAI last",
  "meta_description": "string — 130-155 chars, includes CTA verb + one data point",
  "featured_snippet_block": "string — 40-60 words, standalone answer, no fluff",
  "sections": [
    {
      "h2_heading": "string — question format preferred",
      "h3_subheadings": ["string"],
      "target_words": 250,
      "section_type": "answer_block|regulatory_explainer|how_to|comparison_table|case_study|faq_block|cta_section",
      "key_points_to_cover": ["string — specific facts, not generic topics"],
      "internal_link_opportunity": "string|null",
      "india_specificity_requirement": "string — exact India signal required in this section"
    }
  ],
  "faq_section": {
    "include": "boolean — set to false if no topic-relevant questions are available. Do NOT include generic FAQs.",
    "questions": ["string — exact PAA phrasing where possible. MUST be highly topic-specific. Do NOT include generic definitions like 'What is DPDPA?', 'What are the penalties?', 'Who is a Data Fiduciary?', 'Is DPDPA active?'"]
  },
  "cta_section": {
    "heading": "string",
    "body_instruction": "string",
    "cta_url": "string",
    "cta_text": "string"
  }
}"""


def _get_winning_template_hint(cluster_id: str) -> str:
    """Return a soft structural hint from GSC-confirmed winning posts in this
    cluster (spec Phase 2) — closed-loop learning from what our own traffic
    has actually rewarded, which no competitor can replicate for our site.

    Explicitly advisory, never mandatory: depth still follows topic value
    (Phase 1), this only informs H2 shape/length when real winners exist.
    Returns "" when there's no confirmed winner yet for this cluster.
    """
    try:
        from src.analytics.feedback_loop import extract_winning_templates
        templates = extract_winning_templates(cluster_id=cluster_id)
    except Exception as exc:
        log.warning("winning_template_lookup_failed", error=str(exc))
        return ""

    if not templates:
        return ""

    lines = []
    for t in templates[:2]:
        h2s = ", ".join(t.get("h2_structure") or []) or "structure not recorded"
        lines.append(
            f"- \"{t.get('keyword', '')}\" ({t.get('word_count', 0)} words, "
            f"{t.get('impressions_30d', 0)} impressions/30d): H2s were [{h2s}]"
        )

    return (
        "\n\nSTRUCTURAL REFERENCE FROM HIGH-PERFORMING POSTS IN THIS CLUSTER "
        "(advisory only — do not copy verbatim, do not let this override rule 8's "
        "depth-follows-content principle; use only as a signal for what has "
        "actually worked for this audience):\n" + "\n".join(lines)
    )


async def _step1_generate_outline(
    router: ModelRouter,
    keyword: str,
    context_str: str,
    intent_type: str,
    paa_questions: Optional[List[str]],
    tier: int,
    target_words: int,
    tier_config: dict,
    tier3_title: Optional[str] = None,
    cluster_id: str = "general",
) -> dict:
    """Step 1: Strict JSON outline with a 1-retry validation loop.

    Validation rules per spec 2.2 STEP 1:
      - H1 contains primary keyword
      - featured_snippet_block present and 40-60 words
      - At least 2 question-format H2s
      - CTA section present
      - Word count targets sum within the tier's word band
    """
    log.info("step1_outline_started", keyword=keyword, tier=tier, target_words=target_words)
    tier_low, tier_high = TIER_WORD_COUNT.get(tier, (1200, 1600))
    winning_template_hint = _get_winning_template_hint(cluster_id)

    base_prompt = f"""Generate a strict JSON outline for a Tier {tier} SEO-and-GEO-optimised article.

PRIMARY KEYWORD: {keyword}
INTENT TYPE: {intent_type}
TIER: {tier} (Total length must fall between {tier_low} and {tier_high} words — see rule 8 below for how to choose where in that range)
PAA QUESTIONS TO INCORPORATE: {json.dumps(paa_questions or [], ensure_ascii=False)}{winning_template_hint}

FULL KEYWORD BRIEF (use all signals below):
{_wrap_internal_context(context_str)}

MANDATORY TIER {tier} STRUCTURE (generate exactly these H2s in this order):
{json.dumps(tier_config["structure"], indent=2)}

LOCALIZATION RULES:
{tier_config["localization_rules"]}

MANDATORY OUTLINE RULES — ALL MUST BE FOLLOWED:
1. h1_title MUST contain the exact primary keyword.
2. featured_snippet_block MUST be 40-60 words, self-contained, answers the keyword directly.
3. FIRST section after H1 MUST be section_type="answer_block" using the featured_snippet_block.
4. At least 2 H2 headings MUST be phrased as questions ending with "?".
5. At least 1 H2 MUST cover a topic zero competitors cover (use gap_topics from context).
6. section_type values MUST be one of: answer_block, regulatory_explainer, how_to, comparison_table, case_study, faq_block, cta_section.
7. The final content section MUST be section_type="cta_section".
8. Depth follows how much you actually have to say, not a fixed target. Set each section's
   target_words based on how many genuinely specific key_points_to_cover it has (rule 12) —
   a section with 2 specific facts should be short; a section with 5+ should run long. The
   SUM of all target_words must land between {tier_low} and {tier_high} words total. Do NOT
   pad a section to reach a round number, and do NOT truncate a section that has real
   specific content just to save words. A well-supported {tier_low}-word article beats a
   padded {tier_high}-word one.
9. faq_section.questions MUST use exact PAA phrasing from context where available. MUST be highly specific to the primary keyword. BANNED generic questions: "What is DPDPA?", "What are the penalties under DPDPA?", "Who is a Data Fiduciary?", "Does DPDPA apply to my business?", "Is DPDPA active?".
10. url_slug MUST be all-lowercase, hyphenated, contain the primary keyword, no stop words (the, and, a, for, of, in).
11. BANNED: If no highly topic-specific or context-specific PAA questions are available, set faq_section.include to false. Do NOT generate generic regulatory placeholder questions.
12. key_points_to_cover MUST be SPECIFIC FACTUAL CLAIMS, never generic topic labels. Each item MUST contain at least one of: a specific ₹ figure or percentage, a named Indian regulatory body, a specific DPDPA Section or Rule number, a specific timeframe (72 hours, 30 days, etc.), or a named sector/entity type.
    WRONG (topic labels — do not do this): ["compliance challenges", "SMEs", "sector specific"]
    RIGHT (specific factual claims): ["MSMEs with turnover below ₹250 crore face resource constraints meeting the 72-hour window", "RBI-regulated NBFCs must notify both DPBI and RBI simultaneously"]
    A vague label without one of these signals is not acceptable and will cause the section writer to pad with generic filler.

OUTPUT: Valid JSON only. No markdown code fences. No explanation text.
JSON SCHEMA:
{_OUTLINE_SCHEMA}"""

    validation_errors: List[str] = []

    for attempt in range(2):
        retry_instruction = ""
        if validation_errors:
            retry_instruction = (
                "\n\nPREVIOUS ATTEMPT FAILED VALIDATION. MUST FIX:\n"
                + "\n".join(f"  - {e}" for e in validation_errors)
            )

        messages = [
            {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
            {"role": "user", "content": base_prompt + retry_instruction},
        ]

        raw, _ = await router.generate_with_fallback("outline", messages, json_mode=True)

        try:
            outline = json.loads(raw)
        except json.JSONDecodeError as e:
            log.error("step1_json_parse_failed", attempt=attempt, error=str(e))
            if attempt == 1:
                raise RuntimeError(f"Step 1 outline JSON parse failed after 2 attempts: {e}") from e
            validation_errors = ["JSON was malformed — output ONLY valid JSON, no markdown fences."]
            continue

        validation_errors = _validate_outline(outline, keyword, tier_low, tier_high)
        if not validation_errors:
            log.info("step1_outline_valid", sections=len(outline.get("sections", [])))
            return outline

        if attempt == 0:
            log.warning("step1_outline_validation_failed", errors=validation_errors)

    # After 2 attempts, log and return best-effort
    log.error("step1_outline_failed_validation_twice", keyword=keyword, errors=validation_errors)
    return outline  # type: ignore[possibly-undefined]


def _validate_outline(outline: dict, keyword: str, tier_low: int, tier_high: int) -> List[str]:
    """Return list of validation error strings. Empty list = valid."""
    errors = []
    kw_lower = keyword.lower()
    sections = outline.get("sections", [])

    h1 = outline.get("h1_title", "")
    if kw_lower not in h1.lower():
        errors.append(f"H1 '{h1}' does not contain keyword '{keyword}'.")

    snippet = outline.get("featured_snippet_block", "").strip()
    if not snippet:
        errors.append("featured_snippet_block is missing or empty.")
    else:
        snippet_words = len(snippet.split())
        if not (30 <= snippet_words <= 80):
            errors.append(f"featured_snippet_block is {snippet_words} words — must be 40-60 words.")

    question_h2s = [s for s in sections if "?" in s.get("h2_heading", "")]
    if len(question_h2s) < 2:
        errors.append(f"Only {len(question_h2s)} question-format H2s — need at least 2.")

    if not outline.get("cta_section"):
        errors.append("cta_section is missing.")

    section_types = [s.get("section_type", "") for s in sections]
    if "cta_section" not in section_types:
        errors.append("No section has section_type='cta_section'.")

    # Depth follows content, not a fixed midpoint (spec Phase 1) — validate
    # against the tier's outer band with modest slack, not closeness to one
    # number, so a genuinely thin or genuinely rich topic isn't forced to pad
    # or cut just to hit a target.
    total_target = sum(s.get("target_words", 0) for s in sections)
    band_low = int(tier_low * 0.85)
    band_high = int(tier_high * 1.15)
    if total_target > 0 and not (band_low <= total_target <= band_high):
        errors.append(
            f"Section target_words sum ({total_target}) falls outside the Tier "
            f"{band_low}-{band_high} word band. Adjust section depth to fit — pad "
            f"nothing, cut nothing that has genuine specific content."
        )

    return errors


# ---------------------------------------------------------------------------
# STEP 2 — Section-by-Section Body Generation
# ---------------------------------------------------------------------------

_SECTION_TYPE_RULES: dict = {
    "answer_block": (
        "Open with a SINGLE sentence directly answering the H2. Maximum 60 words total. "
        "No fluff. If extracted with no surrounding context, it must still make complete sense."
    ),
    "regulatory_explainer": (
        "MUST cite a specific DPDPA Section or Rule number in the VERY FIRST sentence. "
        "MUST include a plain-English translation of the regulatory text. "
        "MUST include at least one ₹ penalty figure (from context). "
        "MUST name at least one Indian regulator (DPBI, MeitY, RBI, SEBI, IRDAI, CERT-In). "
        "MUST end with a subsection: 'What this means in practice'."
    ),
    "how_to": (
        "Use a numbered list. Maximum 8 steps. Maximum 25 words per step. "
        "EVERY step MUST start with an action verb (Submit, Configure, Document, Appoint, Register). "
        "Total word count for the entire how_to block: 150-250 words."
    ),
    "comparison_table": (
        "Output a Markdown table. Minimum 4 rows, maximum 8 rows. Minimum 3 columns, maximum 5 columns. "
        "Include a table caption (keyword-containing phrase) ABOVE the table. "
        "Include a one-sentence alt-text summary BELOW the table for screen readers and AI extractors. "
        "Tables MUST focus on objective compliance data, industry breakdowns, or regulatory requirements. "
        "Do NOT include promotional or marketing columns like 'KensaraAI Support' in educational/informational tables."
    ),
    "case_study": (
        "ALWAYS begin with 'Illustrative Example:' label. "
        "Use a fictional but plausible Indian company — never name a real company as the "
        "violator in the illustrative example itself. "
        "Structure: Company profile (2 sentences) → Challenge (2 sentences) → "
        "How DPDPA requirement applies (3 sentences) → Resolution approach (3 sentences) → "
        "Lesson for reader (1 sentence). "
        "If REGULATORY PRECEDENT EXAMPLES are present in the context below, ground the "
        "stakes and consequences in one of those REAL historical cases — cite the real "
        "company, authority, and outcome explicitly as precedent (e.g. 'This mirrors the "
        "real 2022 RBI action against Mastercard...'), separately from the fictional "
        "illustrative company. Do NOT invent a precedent that isn't in the context. "
        "Do NOT claim the illustrative company is a real Kensara client."
    ),
    "faq_block": (
        "3-5 questions pulled directly from PAA questions in the context. "
        "Each answer: 40-80 words. "
        "Question format: exact PAA phrasing where possible. "
        "BANNED generic questions: 'What is DPDPA?', 'What are the penalties?', 'Who is a Data Fiduciary?', 'Is DPDPA active?'. "
        "Questions MUST be highly specific to the context/topic. "
        "Format as: ### [Question]\n\n[Answer]"
    ),
}


async def _get_comparison_grounding_block(keyword: str) -> str:
    """Retrieve real, previously-crawled competitor content for comparison_table
    sections (spec Phase 0 Step 3) so tables compare against actual competitor
    facts instead of an invented comparison. Degrades to "" on any failure —
    the section still generates, just without competitor-grounded facts.
    """
    try:
        from src.agents.content_gap_analyzer import get_comparison_grounding
        rows = await asyncio.to_thread(get_comparison_grounding, keyword)
    except Exception as exc:
        log.warning("comparison_grounding_retrieval_failed", error=str(exc))
        return ""

    if not rows:
        return ""

    lines = "\n".join(
        f"- {r.get('competitor_domain', '')}: \"{r.get('title', '')}\" — "
        f"{(r.get('summary') or '')[:200]}"
        for r in rows
    )
    return (
        "\n\n=== REAL COMPETITOR CONTENT (crawled — for factual comparison only) ===\n"
        f"{lines}\n"
        "Use this ONLY to ground what competitors actually publish or offer. Do NOT "
        "invent a competitor claim not shown here. Do NOT frame any competitor "
        "negatively — neutral, factual comparison only.\n"
        "=== END OF COMPETITOR CONTENT ==="
    )


async def _get_regulatory_grounding_block(section_heading: str, keyword: str, industry: Optional[str]) -> str:
    """Retrieve verified DPDP Rules 2025 text for a regulatory_explainer section
    (spec CHANGE Phase 0: ground the writer in real statutory text instead of a
    synthetic keyword-brief label). Degrades to "" on any retrieval failure —
    the section still generates, just without grounded citations that call.

    RAG imports are lazy and local to this function, not module-level — the
    retrieval chain pulls in chromadb and sentence-transformers (torch under
    the hood), which added several minutes to app startup and broke the
    Azure App Service startup probe when imported eagerly at module load.
    Every other RAG usage in this codebase (context/builder.py,
    content_gap_analyzer.py) already follows this lazy pattern.
    """
    try:
        from src.rag.query_library import get_dpdpa_grounding
        from src.rag.context_builder import build_context_block

        chunks = await asyncio.to_thread(get_dpdpa_grounding, section_heading, keyword, industry)
    except Exception as exc:
        log.warning("dpdpa_grounding_retrieval_failed", section=section_heading[:80], error=str(exc))
        return ""

    if not chunks:
        return ""

    return "\n\n" + build_context_block(
        "statutory_text", "dpdpa_source", chunks, token_budget=1200
    )


async def _step2_generate_sections(
    router: ModelRouter,
    outline: dict,
    keyword: str,
    context_str: str,
    intent_type: str,
    tier: int,
    industry: Optional[str] = None,
) -> List[dict]:
    """Step 2: Generate each section individually, respecting section_type rules.

    CTA sections are NEVER sent to the LLM — pulled from cta_library.
    Regulatory sections use NVIDIA as primary model (better legal reasoning) and
    are grounded in real retrieved DPDP Rules 2025 text when a matching rule
    exists (spec Phase 0 — the model cites verified text, not a guessed number).
    Sections failing validation are retried once with corrective instruction.
    Tier 3: single pass, Groq only, no retries (speed over depth).
    """
    log.info("step2_sections_started", num_sections=len(outline.get("sections", [])))
    sections_content: List[dict] = []

    # Insert featured snippet block first
    snippet = outline.get("featured_snippet_block", "")
    sections_content.append({
        "type": "answer_block",
        "content": f"**Quick Answer:** {snippet}",
    })

    # Cumulative anti-redundancy context (spec CHANGE-A1). Every section after
    # the first is told what earlier sections already covered so it stops
    # restating the same facts, penalty figures, and rules in every section —
    # the single root cause of cross-section redundancy in generated posts.
    previously_written = f"## Quick Answer\n\n{snippet}" if snippet else ""

    for idx, sec in enumerate(outline.get("sections", [])):
        sec_type = sec.get("section_type", "standard")

        # CTA section — deterministic, never LLM-generated (spec 2.2, CTA_SECTION)
        if sec_type == "cta_section":
            cta = get_cta(intent_type, keyword)
            service = get_service_link(keyword)
            cta_content = (
                f"## {cta['heading']}\n\n"
                f"{cta['body']}\n\n"
                f"[{cta['cta_text']}]({cta['cta_url']})\n\n"
                f"Also see: [{service['anchor']}]({service['url']})"
            )
            sections_content.append({"type": "cta_section", "content": cta_content})
            continue

        rules = _SECTION_TYPE_RULES.get(
            sec_type, "Write a well-structured section for this topic."
        )
        # Section types with their own built-in structure — H3s either redundant or not standard
        _NO_H3_TYPES = {"answer_block", "how_to", "comparison_table", "faq_block", "cta_section"}
        h3s = sec.get("h3_subheadings", [])
        h3_block = ""
        if sec_type not in _NO_H3_TYPES:
            if h3s:
                h3_list = "\n".join(f"  - ### {h}" for h in h3s)
                h3_block = (
                    f"\nH3 SUBHEADINGS (mandatory — include each as a ### heading within the section):\n"
                    f"{h3_list}"
                )
            else:
                h3_block = (
                    "\nMANDATORY H3 STRUCTURE: Include at least 2 ### subheadings within this section. "
                    "Use them to break the content into named sub-topics (e.g. '### What This Means in Practice', "
                    "'### Key Obligations', '### Common Mistakes'). Every H2 section MUST contain at least one H3."
                )

        grounding_block = ""
        grounding_instruction = ""
        if sec_type == "regulatory_explainer":
            grounding_block = await _get_regulatory_grounding_block(
                sec.get("h2_heading", ""), keyword, industry
            )
            if grounding_block:
                grounding_instruction = (
                    "\nA VERIFIED DPDP Rules 2025 excerpt is provided below under "
                    "'VERIFIED DPDPA STATUTORY TEXT'. You MUST base your rule/section "
                    "citation and obligations on that excerpt, not on general knowledge. "
                    "Cite the exact Rule number it names."
                )
        elif sec_type == "comparison_table":
            grounding_block = await _get_comparison_grounding_block(keyword)
            if grounding_block:
                grounding_instruction = (
                    "\nReal, crawled competitor content is provided below under "
                    "'REAL COMPETITOR CONTENT'. Ground your table in these actual "
                    "facts wherever relevant instead of inventing a comparison."
                )

        prompt = f"""Write a single section for a Tier {tier} DPDPA compliance article targeting: '{keyword}'

SECTION TYPE: {sec_type}
H2 HEADING: {sec.get('h2_heading', '')}
TARGET WORD COUNT: {sec.get('target_words', 250)} words
KEY POINTS TO COVER: {json.dumps(sec.get('key_points_to_cover', []))}
INDIA SPECIFICITY REQUIRED: {sec.get('india_specificity_requirement', 'Include at least one India-specific signal (₹, regulator name, DPDPA section, or Indian company example).')}
INTERNAL LINK OPPORTUNITY: {sec.get('internal_link_opportunity', 'None')}{h3_block}{grounding_instruction}

SECTION TYPE RULES (MANDATORY):
{rules}

KEYWORD BRIEF CONTEXT:
{_wrap_internal_context(context_str)}
{_build_previously_written_block(previously_written)}{grounding_block}

OUTPUT: Raw markdown only. Include the H2 heading. No ``` fences. No explanation."""

        content = await _generate_section_with_fallback(
            router=router,
            prompt=prompt,
            sec_type=sec_type,
            keyword=keyword,
            tier=tier,
            section_idx=idx,
        )
        sections_content.append({"type": sec_type, "content": content})
        previously_written += f"\n\n## {sec.get('h2_heading', sec_type)}\n\n{content}"

    return sections_content


async def _generate_section_with_fallback(
    router: ModelRouter,
    prompt: str,
    sec_type: str,
    keyword: str,
    tier: int,
    section_idx: int = 0,
) -> str:
    """Generate one section with model routing, fallback, and 1-retry validation.

    Routing per spec 2.9.A:
      - regulatory_explainer: NVIDIA primary → Groq fallback (better legal reasoning)
      - all other sections: Groq primary → NVIDIA fallback
      - Tier 3 posts: Groq ONLY for all sections
    Tier 3 posts get one attempt only (speed requirement, spec 2.3).
    Two validation failures: return best-effort content with an inline flag.
    """
    task = "regulatory_section" if sec_type == "regulatory_explainer" else "section"
    max_attempts = 1 if tier == 3 else 2
    validation_error = ""
    content = ""

    for attempt in range(max_attempts):
        retry_msg = (
            f"\n\nRETRY — FIX THIS SPECIFIC ISSUE: {validation_error}"
            if validation_error else ""
        )
        messages = [
            {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt + retry_msg},
        ]

        try:
            content, _ = await router.generate_with_fallback(
                task=task,
                messages=messages,
                tier_override=tier,
            )
        except BudgetExceededError:
            log.warning(
                "step2_budget_exceeded",
                section_idx=section_idx,
                sec_type=sec_type,
            )
            return (
                f"## {sec_type.replace('_', ' ').title()}\n\n"
                f"[Section skipped — token budget exhausted. Manual review required.]"
            )
        except RuntimeError as exc:
            log.error("step2_both_providers_failed", idx=section_idx, error=str(exc))
            content = (
                f"## Section unavailable\n\n"
                f"[Generation failed for {sec_type} section. Manual review required.]"
            )
            break

        error = _validate_section(content, sec_type, keyword)
        if not error:
            return content

        validation_error = error
        log.warning(
            "step2_section_validation_failed",
            sec_type=sec_type,
            attempt=attempt,
            error=error,
        )

    # Best-effort: flag for human reviewer
    if validation_error:
        log.error("step2_section_failed_twice", sec_type=sec_type, idx=section_idx)
        content += f"\n\n[SECTION_FLAG: validation failed — {validation_error}]"

    return content


def _validate_section(content: str, sec_type: str, keyword: str) -> str:
    """Return an error string if the section fails validation, else empty string."""
    kw_words = keyword.lower().split()[:2]
    if not any(w in content.lower() for w in kw_words):
        return f"Primary keyword '{keyword}' (or variant) not present in section."

    india_signals = ["₹", "dpbi", "meity", "rbi", "sebi", "india", "section ", "rule ", "dpdpa"]
    if not any(sig in content.lower() for sig in india_signals):
        return "No India-specific signal (₹, regulator name, DPDPA section, India) in section."

    if sec_type == "regulatory_explainer":
        if "₹" not in content:
            return "regulatory_explainer must contain ₹ penalty figure."
        if not re.search(r"\bsection\s+\d+\b|\brule\s+\d+\b", content, re.IGNORECASE):
            return "regulatory_explainer must cite specific DPDPA Section or Rule number."

    if sec_type == "how_to":
        if not re.search(r"^\d+\.", content, re.MULTILINE):
            return "how_to section must use numbered steps."

    if sec_type == "comparison_table":
        if "|" not in content:
            return "comparison_table section must contain a Markdown table."

    return ""


# ---------------------------------------------------------------------------
# STEP 3 — Assembly & Continuity Pass
# ---------------------------------------------------------------------------

async def _generate_faq_answers(
    router: ModelRouter,
    questions: List[str],
    keyword: str,
    article_body: str = "",
) -> str:
    """Generate FAQ answers grounded in the already-assembled article body.

    Passing the assembled body as context (spec CHANGE-A3) stops this — a
    separate LLM call from the body sections — from inventing its own ₹
    figures, DPDPA section numbers, or deadlines that contradict the article.
    Routed to the "faq" task (NVIDIA Mistral, spec CHANGE-C1): measurably
    better than Groq at honouring "don't introduce facts not given" rules.
    """
    q_list = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    article_block = (
        "The following compliance article has already been written and approved. "
        "Your answers MUST be 100% consistent with it:\n"
        f"---\n{article_body.strip()}\n---\n\n"
        if article_body.strip() else ""
    )
    prompt = (
        f"Write concise FAQ answers for a DPDPA compliance article about: '{keyword}'\n\n"
        f"{article_block}"
        f"QUESTIONS:\n{q_list}\n\n"
        "RULES:\n"
        "- Each answer MUST be a complete 40-80 word detailed paragraph.\n"
        "- Do NOT introduce any ₹ figure, DPDPA Section/Rule number, or deadline that is not "
        "already present in the article above. If a question needs a fact not covered by the "
        "article, answer in general terms (e.g. 'as per DPDPA requirements') instead of inventing one.\n"
        "- Include India-specific context (₹ figures, DPDPA section numbers, or regulator names) "
        "only when they already appear in the article above.\n"
        "- Format each Q&A pair as:\n\n"
        "### [Question]\n\n"
        "[Full answer paragraph]\n\n"
        "- MANDATORY: You MUST write the complete answer text under each heading. Do NOT output a bare bullet list of questions without answers.\n"
        "- No preamble, no explanation — output only the ### Q&A pairs."
    )
    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    result, _ = await router.generate_with_fallback("faq", messages)
    return f"## Frequently Asked Questions\n\n{result.strip()}\n\n"


async def _step3b_critique_and_revise(router: ModelRouter, assembled: str, keyword: str) -> str:
    """A second, skeptical pass over the assembled draft (spec Phase 1).

    The assembly pass only removes redundancy/filler — it never asks whether
    the surviving content is actually useful. This runs a separate call that
    role-plays a compliance officer who has read this topic before and lists
    ONLY concrete objections (generic claims, unsupported assertions, filler
    that survived assembly). A single targeted revision then addresses just
    those objections — it is not a rewrite pass.

    Skipped for Tier 3 (speed requirement, spec 2.3) and degrades safely to
    the unrevised draft if the job's token budget is exhausted or either call
    fails, so this never blocks publication.
    """
    critique_prompt = f"""You are a skeptical, experienced Indian data-protection compliance officer reviewing a draft article about '{keyword}' before publication. You have read dozens of DPDPA explainer posts already and are tired of generic ones.

List ONLY concrete problems below — no praise, no summary of what's good, no restating the article.
For each problem: quote the exact offending sentence or phrase, then explain in one line what's wrong. Look specifically for:
- Generic advice that could apply to any compliance topic, not specific to this one
- A claim presented as fact with no support anywhere in the article
- A sentence that just restates something already said elsewhere in the article
- A paragraph that sounds authoritative but gives the reader nothing decision-useful to act on

If a section has none of these problems, do not mention it — say nothing about sections that are fine.
If the article genuinely has none of these problems anywhere, output exactly: NO_ISSUES_FOUND

DRAFT:
{assembled}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": critique_prompt},
    ]
    try:
        critique, _ = await router.generate_with_fallback("assembly", messages)
    except (BudgetExceededError, RuntimeError) as exc:
        log.warning("step3b_critique_call_failed", error=str(exc))
        return assembled

    critique = critique.strip()
    if not critique or critique.upper().startswith("NO_ISSUES_FOUND"):
        log.info("step3b_critique_no_issues")
        return assembled

    revision_prompt = f"""Edit the article below based on a compliance officer's critique. Fix ONLY the specific problems listed — do not touch sections that weren't flagged, do not rewrite for style, and do not add new content beyond what's needed to fix a flagged issue. Often the correct fix is simply deleting the flagged sentence, or making a vague claim specific using a fact already present elsewhere in the draft.

CRITIQUE (fix only these):
{critique}

OUTPUT RULE: Start your response IMMEDIATELY with the '# ' H1 heading. No preamble, no explanation of what you changed.

DRAFT TO FIX:
{assembled}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": revision_prompt},
    ]
    try:
        revised, _ = await router.generate_with_fallback("assembly", messages)
    except (BudgetExceededError, RuntimeError) as exc:
        log.warning("step3b_revision_call_failed", error=str(exc))
        return assembled

    revised = revised.strip()
    if not revised or len(revised.split()) < 100:
        log.warning("step3b_revision_empty_keeping_original")
        return assembled

    log.info("step3b_critique_and_revise_complete", critique_chars=len(critique))
    return revised


async def _step3_assembly_pass(
    router: ModelRouter,
    sections: List[dict],
    outline: dict,
    keyword: str,
    tier: int = 1,
) -> str:
    """Step 3: LLM editing pass — deletes redundancy/filler, preserves byline/'Last Updated'.

    This pass does NOT write new prose. Its job is almost entirely deletion:
    cross-section restatements and filler transition sentences (spec CHANGE-A2).
    Incoherent sections are flagged inline as [ASSEMBLY_FLAG: description].
    Model: Groq at temperature 0.1 (spec 2.9.A / CHANGE-A2 — near-deterministic editing).
    """
    log.info("step3_assembly_started")
    iso_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    h1 = outline.get("h1_title", keyword)

    raw_text = f"# {h1}\n\n"
    raw_text += f"*By Mr Rudraksh Tatwal | Founder & CEO, KensaraAI | {iso_date}*\n\n"
    raw_text += f"*Last Updated: {iso_date}*\n\n"

    for s in sections:
        raw_text += s["content"].strip() + "\n\n"

    about_author_md = (
        "---\n\n"
        "**About the Author**\n\n"
        "This article is published by KensaraAI leadership. Mr Rudraksh Tatwal (Founder & CEO) "
        "and Mr Prince (Co-founder & COO) lead KensaraAI's India-focused DPDPA compliance strategy "
        "for enterprises and MSMEs.\n"
    )

    prompt = f"""Act as a professional compliance content editor, not a writer. You are editing an article draft on: '{keyword}'

RULES (DO NOT BREAK ANY):
1. This is an EDITING pass, not a writing pass — your job is almost entirely to DELETE, not to add. Creativity is suppressed for this task.
2. DELETE any sentence whose entire purpose is to introduce or summarise another section (e.g. "Moving forward, it is essential to...", "To further understand the implications of...", "As discussed above...", "As mentioned earlier...", "In conclusion...", "It is worth noting that..."). Sections should follow each other directly under their headings.
3. DELETE any sentence whose meaning is already fully expressed elsewhere in the document — keep only the FIRST occurrence of a given fact, figure, penalty amount, or rule; delete every later restatement of it.
4. You may add at most ONE short connective phrase (never a full sentence) between two sections, and only if omitting it would make the transition genuinely confusing. Sections were written aware of each other, so this should rarely be needed.
5. The byline 'By Mr Rudraksh Tatwal | Founder & CEO, KensaraAI' MUST remain immediately under the H1.
6. The 'Last Updated' line MUST remain in place.
7. Verify the featured_snippet_block / Quick Answer is correctly placed as the first content after the byline.
8. Ensure all Markdown tables, numbered lists, and bullet lists are correctly formatted.
9. Ensure H1 → H2 → H3 heading hierarchy is strict (no H4 or H5).
10. If any section is incoherent with surrounding context, flag it as: [ASSEMBLY_FLAG: description].
11. The CTA section MUST be the last content section.
12. Do NOT rewrite or paraphrase sentences that are fine as-is — only delete or lightly trim. Do NOT summarise.
13. OUTPUT RULE: Start your response IMMEDIATELY with the '# ' H1 heading. Do NOT write any preamble, explanation, or acknowledgement before the H1.

DRAFT:
{raw_text}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    assembled, _ = await router.generate_with_fallback("assembly", messages)
    assembled = assembled.strip()

    if not assembled or len(assembled.split()) < 100:
        log.warning("step3_empty_response_using_raw")
        assembled = raw_text.strip()

    # Step 3b: skeptical second pass (spec Phase 1). Skipped for Tier 3 —
    # speed requirement, spec 2.3 — where a single assembly pass is final.
    if tier != 3:
        assembled = await _step3b_critique_and_revise(router, assembled, keyword)

    # FAQ is generated AFTER assembly, grounded in the final body text (spec
    # CHANGE-A3) so its facts cannot drift from what the article actually says,
    # then appended programmatically — never routed back through an LLM
    # rewrite pass that could strip or reword the answers.
    faq_md = ""
    faq = outline.get("faq_section", {})
    if faq.get("include") and faq.get("questions"):
        faq_md = await _generate_faq_answers(
            router, faq["questions"], keyword, article_body=assembled
        )

    if faq_md:
        assembled += "\n\n" + faq_md.strip()
    assembled += "\n\n" + about_author_md.strip() + "\n"

    log.info("step3_assembly_complete", word_count=len(assembled.split()))
    return assembled


# ---------------------------------------------------------------------------
# STEP 4 — On-Page SEO Injection (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _step4_seo_injection(markdown: str, keyword: str, gap_topics: List[str]) -> str:
    """Step 4: Deterministic NLP/regex logic for semantic keyword injection.

    Per spec:
      - Verify keyword in first 150 words; inject if missing.
      - Use spaCy or regex to insert semantic variant parentheticals.
      - Maximum 3 semantic variant insertions per post.
      - Verify all H2s have at least one H3 (flag if missing).
      - Check keyword density 0.5%-1.5%.
    """
    log.info("step4_seo_injection_started", spacy_available=SPACY_AVAILABLE)

    # 1. Ensure keyword appears in first 150 words
    words = markdown.split()
    if keyword.lower() not in " ".join(words[:150]).lower():
        parts = markdown.split("\n\n")
        if len(parts) > 2:
            parts[2] = (
                f"{parts[2].rstrip()} "
                f"In the context of {keyword}, this is a critical consideration."
            )
        markdown = "\n\n".join(parts)

    # 2. Semantic variant injection
    if SPACY_AVAILABLE and gap_topics:
        doc = _nlp(markdown)
        sentences = [sent.text.strip() for sent in doc.sents]
        injected = 0
        new_sentences = []
        for sent in sentences:
            new_sentences.append(sent)
            if (
                sent.endswith(".")
                and injected < min(3, len(gap_topics))
                and "dpdpa" in sent.lower()
                and not sent.startswith("#")
                and not sent.startswith("*")
            ):
                new_sentences.append(f" — also referred to as {gap_topics[injected]}.")
                injected += 1
        markdown = " ".join(new_sentences)
    elif gap_topics:
        pattern = re.compile(r"(DPDPA [a-zA-Z ]+ compliance\.)", re.IGNORECASE)
        replacements_done = 0

        def inject_variant(m: re.Match) -> str:
            nonlocal replacements_done
            if replacements_done >= min(3, len(gap_topics)):
                return m.group(0)
            variant = gap_topics[replacements_done]
            replacements_done += 1
            return f"{m.group(0)} (in the context of {variant})"

        markdown = pattern.sub(inject_variant, markdown)

    # 3. Check H2s have at least one H3 — log warning (do NOT embed flags in content)
    h2_missing_h3: List[str] = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## ") and not line.startswith("### "):
            j = i + 1
            found_h3 = False
            while j < len(lines):
                if lines[j].startswith("### "):
                    found_h3 = True
                    break
                if lines[j].startswith("## ") or lines[j].startswith("# "):
                    break
                j += 1
            if not found_h3:
                h2_missing_h3.append(line.strip())
        i += 1
    if h2_missing_h3:
        log.warning("step4_h2_missing_h3", headings=h2_missing_h3)

    # 4. Keyword density check (0.5%-1.5% per spec)
    full_text_lower = markdown.lower()
    total_words = len(full_text_lower.split())
    kw_count = full_text_lower.count(keyword.lower())
    density = kw_count / total_words if total_words > 0 else 0.0
    if density > 0.015:
        log.warning("step4_keyword_density_high", density=f"{density:.2%}", count=kw_count)
    elif density < 0.005:
        log.warning("step4_keyword_density_low", density=f"{density:.2%}", count=kw_count)

    # 5. Gov authority link injection — first occurrence in a plain paragraph only
    _GOV_LINKS: List[Tuple[str, str]] = [
        ("DPBI", "https://dpboard.gov.in"),
        ("MeitY", "https://www.meity.gov.in"),
        ("CERT-In", "https://www.cert-in.org.in"),
    ]
    out_lines = markdown.split("\n")
    injected_gov = 0
    for anchor, url in _GOV_LINKS:
        if injected_gov >= 3:
            break
        replaced = False
        for idx, line in enumerate(out_lines):
            if replaced:
                break
            stripped = line.strip()
            # Skip headings, metadata lines, table rows, bylines, blank lines, already-linked
            if (
                stripped.startswith("#")
                or stripped.startswith("*")
                or stripped.startswith("|")
                or stripped.startswith("---")
                or stripped.startswith("- ")
                or not stripped
                or f"]({url})" in line
            ):
                continue
            if anchor in line and f"[{anchor}]" not in line:
                out_lines[idx] = line.replace(anchor, f"[{anchor}]({url})", 1)
                replaced = True
                injected_gov += 1
    markdown = "\n".join(out_lines)

    log.info("step4_seo_injection_complete", keyword_density=f"{density:.2%}", gov_links_injected=injected_gov)
    return markdown


# ---------------------------------------------------------------------------
# STEP 4c — QA Gate & Targeted Fix (spec Phase 2)
# ---------------------------------------------------------------------------

async def _step4c_qa_gate_and_fix(
    router: ModelRouter,
    markdown: str,
    outline: dict,
    keyword: str,
    intent_type: str,
    tier: int,
) -> str:
    """Run the Module 2.6 QA scorer (src/quality/checker.py) and, if it fails,
    apply ONE targeted fix pass addressing exactly what it found.

    Previously check_blog_quality() was never called anywhere in the
    generation path — a QA score nobody acts on can't improve anything. This
    makes it a real gate: a failing score triggers a single revision seeded
    with the checker's own structured findings (banned filler phrases,
    low-density sections, penalty inconsistencies) — not a generic rewrite.
    Skipped for Tier 3 (speed requirement, spec 2.3) and degrades safely to
    the unfixed draft on any failure, so this never blocks publication.
    """
    if tier == 3:
        return markdown

    from src.quality.checker import check_blog_quality

    def _make_temp_post(content: str) -> BlogPost:
        return BlogPost(
            title=outline.get("h1_title", keyword),
            meta_description="",
            slug="qa-check",
            primary_keyword=keyword,
            content_markdown=content,
            word_count=len(content.split()),
        )

    result = check_blog_quality(_make_temp_post(markdown), keyword, intent_type)
    log.info("step4c_qa_gate_scored", score=round(result.score, 3), status=result.status)

    if result.passed:
        return markdown

    findings = list(result.issues) + list(result.warnings)
    if not findings:
        return markdown

    findings_text = "\n".join(f"- {f}" for f in findings[:12])

    fix_prompt = f"""A quality checker scored this DPDPA compliance article {result.score:.2f}/1.00 (pass threshold 0.55) and found these specific problems. Fix ONLY these — do not rewrite or touch anything not listed, and do NOT remove or alter any existing Markdown hyperlink of the form [text](url).

FINDINGS TO FIX:
{findings_text}

OUTPUT RULE: Start your response IMMEDIATELY with the '# ' H1 heading. No preamble, no explanation of what you changed.

ARTICLE:
{markdown}"""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": fix_prompt},
    ]
    try:
        fixed, _ = await router.generate_with_fallback("assembly", messages)
    except (BudgetExceededError, RuntimeError) as exc:
        log.warning("step4c_qa_fix_call_failed", error=str(exc))
        return markdown

    fixed = fixed.strip()
    if not fixed or len(fixed.split()) < 100:
        log.warning("step4c_qa_fix_empty_keeping_original")
        return markdown

    try:
        result_after = check_blog_quality(_make_temp_post(fixed), keyword, intent_type)
        log.info("step4c_qa_gate_after_fix", before=round(result.score, 3), after=round(result_after.score, 3))
    except Exception:
        pass

    return fixed


# ---------------------------------------------------------------------------
# STEP 5 — Metadata & Structured Data Generation
# ---------------------------------------------------------------------------

async def _step5_metadata_and_schema(
    router: ModelRouter,
    markdown: str,
    outline: dict,
    keyword: str,
    intent_type: str,
) -> Tuple[dict, dict]:
    """Step 5: Generate and validate metadata + all required JSON-LD schema blocks.

    Schema types per spec 2.2 STEP 5:
      - BlogPosting (always)
      - BreadcrumbList (always)
      - FAQPage (if FAQ section present)
      - HowTo (if how_to section present)
      - SpeakableSpecification (always)

    Meta description validated: 130-155 chars, action verb, specific data point.
    Retried once via LLM if it fails character count or content requirements.
    Model: Groq JSON mode at temperature 0.1 (spec 2.9.A — metadata task).
    """
    log.info("step5_metadata_started", keyword=keyword)
    publish_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

    raw_meta_title = outline.get("meta_title", f"{keyword} | KensaraAI")
    if len(raw_meta_title) > 60:
        raw_meta_title = raw_meta_title[:57] + "..."

    meta_desc = outline.get("meta_description", "")
    meta_desc = await _validate_or_regenerate_meta_desc(router, meta_desc, keyword)

    raw_slug = outline.get("url_slug", _slugify(keyword))
    slug = _clean_slug(raw_slug)

    meta_data = {
        "title": raw_meta_title,
        "description": meta_desc,
        "slug": slug,
        "keyword": keyword,
        "canonical_url": f"https://kensara.in/blogs/{slug}",
        "intent": intent_type,
    }

    # --- Article / BlogPosting (always required)
    article_schema = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": raw_meta_title,
        "description": meta_desc,
        "datePublished": publish_date,
        "dateModified": publish_date,
        "author": {
            "@type": "Person",
            "name": "Mr Rudraksh Tatwal",
            "jobTitle": "Founder & CEO",
            "worksFor": {
                "@type": "Organization",
                "name": "KensaraAI",
                "url": "https://www.kensara.in",
            },
            "knowsAbout": [
                "DPDPA", "Data Privacy India", "Consent Management",
                "Consent Management", "GDPR",
            ],
        },
        "publisher": {
            "@type": "Organization",
            "name": "KensaraAI",
            "logo": {"@type": "ImageObject", "url": "https://kensara.in/logo.png"},
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"https://kensara.in/blogs/{slug}",
        },
        "keywords": keyword,
        "inLanguage": "en-IN",
    }

    # --- BreadcrumbList (always required)
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://www.kensara.in"},
            {"@type": "ListItem", "position": 2, "name": "Blogs", "item": "https://www.kensara.in/blogs"},
            {
                "@type": "ListItem",
                "position": 3,
                "name": raw_meta_title,
                "item": f"https://www.kensara.in/blogs/{slug}",
            },
        ],
    }

    schema_blocks: dict = {
        "Article": article_schema,
        "BreadcrumbList": breadcrumb_schema,
    }

    # --- FAQPage (keep even after Google deprecated rich results — helps GEO citation)
    faq_questions = outline.get("faq_section", {}).get("questions", [])
    if faq_questions:
        schema_blocks["FAQPage"] = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": f"See the FAQ section in this post for a detailed answer to: {q}",
                    },
                }
                for q in faq_questions
            ],
        }

    # --- HowTo (if how_to section present)
    sections = outline.get("sections", [])
    how_to_sections = [s for s in sections if s.get("section_type") == "how_to"]
    if how_to_sections:
        ht = how_to_sections[0]
        schema_blocks["HowTo"] = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": ht.get("h2_heading", f"How to implement {keyword}"),
            "step": [
                {"@type": "HowToStep", "name": point, "text": point}
                for point in ht.get("key_points_to_cover", [])
            ],
        }

    # --- SpeakableSpecification (voice search + AI assistant extraction)
    schema_blocks["Speakable"] = {
        "@context": "https://schema.org",
        "@type": "SpeakableSpecification",
        "cssSelector": [".featured-snippet", ".speakable-1", ".speakable-2"],
    }

    log.info(
        "step5_schema_generated",
        schema_types=list(schema_blocks.keys()),
        meta_desc_len=len(meta_desc),
    )
    return meta_data, schema_blocks


async def _validate_or_regenerate_meta_desc(
    router: ModelRouter,
    meta_desc: str,
    keyword: str,
) -> str:
    """Validate meta description; regenerate once if it fails requirements.

    Requirements: 130-155 chars, contains specific data point, contains action verb.
    Model: Groq at temperature 0.1 (metadata task).
    """
    def _is_valid(desc: str) -> bool:
        has_length = 130 <= len(desc) <= 155
        has_data_point = bool(re.search(r"\d+|₹|%|crore|lakh", desc, re.IGNORECASE))
        action_verbs = ["get", "discover", "learn", "download", "book", "see", "find",
                        "understand", "ensure", "check", "explore"]
        has_action_verb = any(v in desc.lower() for v in action_verbs)
        return has_length and has_data_point and has_action_verb

    if _is_valid(meta_desc):
        return meta_desc

    log.warning("step5_meta_desc_invalid", length=len(meta_desc), preview=meta_desc[:60])

    prompt = (
        f"Write a meta description for the keyword: '{keyword}'\n\n"
        "REQUIREMENTS (ALL MUST BE MET):\n"
        "- Exactly 130-155 characters (count carefully)\n"
        "- Must include the primary keyword\n"
        "- Must include at least one specific number, ₹ figure, or percentage\n"
        "- Must include one action verb (get, discover, learn, download, book, see)\n"
        "- Must end with a compelling reason to click\n"
        "Output ONLY the meta description text, no quotes, no explanation."
    )

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    regenerated, _ = await router.generate_with_fallback("metadata", messages)
    regenerated = regenerated.strip().strip('"').strip("'")

    if _is_valid(regenerated):
        log.info("step5_meta_desc_regenerated_valid", length=len(regenerated))
        return regenerated

    log.warning("step5_meta_desc_still_invalid_after_retry", length=len(regenerated))
    return regenerated[:155] if len(regenerated) > 155 else regenerated


# ---------------------------------------------------------------------------
# STEP 7 — Final Document Assembly & Frontmatter Generation
# ---------------------------------------------------------------------------

def _step7_final_assembly(
    markdown: str,
    meta: dict,
    schema: dict,
    outline: dict,
    keyword: str,
    intent: str,
    tier: int,
    cluster: str,
    geo_score: int,
    geo_flags: List[str],
    risk_level: str,
    approved: bool,
    flagged: bool = False,
    flag_reason: str = "",
) -> BlogPost:
    """Step 7: Inject full YAML frontmatter and build BlogPost object.

    Frontmatter fields per spec 2.2 STEP 7 (all required fields included).
    """
    log.info("step7_final_assembly_started")
    # Strip debug/flag lines — logged warnings that must not appear in published content
    markdown = re.sub(r'^\[SEO_FLAG:[^\n]*\]\n?', '', markdown, flags=re.MULTILINE)
    markdown = re.sub(r'^\[ASSEMBLY_FLAG:[^\n]*\]\n?', '', markdown, flags=re.MULTILINE)
    # Strip any LLM preamble before the H1 heading (e.g. "Here is the edited draft...")
    h1_match = re.search(r'^# ', markdown, flags=re.MULTILINE)
    if h1_match:
        markdown = markdown[h1_match.start():]
    iso_date = datetime.datetime.now(datetime.timezone.utc).isoformat()
    word_count = len(markdown.split())
    qa_score = round(min(1.0, geo_score / 20.0), 3)

    schema_json_str = json.dumps(schema, ensure_ascii=False)
    schema_json_escaped = schema_json_str.replace('"', '\\"')

    geo_flags_yaml = (
        "\n".join(f"  # {f}" for f in geo_flags) if geo_flags else "  # none"
    )

    frontmatter = f"""---
title: "{meta['title'].replace('"', "'")}"
slug: "{meta['slug']}"
meta_title: "{meta['title'].replace('"', "'")}"
meta_description: "{meta['description'].replace('"', "'")}"
canonical_url: "{meta.get('canonical_url', f'https://kensara.in/blogs/{meta["slug"]}')}"
primary_keyword: "{keyword.replace('"', "'")}"
secondary_keywords: []
cluster: "{cluster}"
intent: "{intent}"
tier: {tier}
word_count: {word_count}
qa_score: {qa_score}
geo_score: {geo_score}
geo_flags:
{geo_flags_yaml}
risk_level: "{risk_level}"
approved: {str(approved).lower()}
flagged: {str(flagged).lower()}
flag_reason: "{flag_reason.replace('"', "'")}"
status: "{'flagged' if flagged else 'pending'}"
author: "Mr Rudraksh Tatwal"
author_credentials: "Founder & CEO, KensaraAI"
date_created: "{iso_date}"
date_published: null
date_modified: null
featured_image_alt: "{keyword} — kensara.in"
schema_json: "{schema_json_escaped}"
internal_links_injected: []
source_story_url: null
wp_post_id: null
wp_post_url: null
---
"""
    full_document = frontmatter + "\n" + markdown

    return _assemble_post(keyword, full_document, {
        "title": meta["title"],
        "description": meta["description"],
        "slug": meta["slug"],
        "cta_url": get_cta(intent, keyword)["cta_url"],
        "cluster": cluster,
        "intent": intent,
        "tier": tier,
        "geo_score": geo_score,
        "qa_score": qa_score,
        "risk_level": risk_level,
        "approved": approved,
        "flagged": flagged,
        "flag_reason": flag_reason,
        "date_created": iso_date,
        "schema_json": schema_json_str,
        "featured_image_alt": f"{keyword} — kensara.in",
    })


# ---------------------------------------------------------------------------
# File I/O & Utility Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert text to URL-safe slug. Removes stop words per spec 2.2 STEP 7."""
    stop_words = {"the", "and", "a", "for", "of", "in", "to", "is", "are", "with", "on", "at"}
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    words = [w for w in text.split() if w not in stop_words]
    slug = re.sub(r"[-\s]+", "-", " ".join(words))
    return slug[:60].rstrip("-")


def _clean_slug(raw_slug: str) -> str:
    """Ensure slug is clean: lowercase, hyphenated, no special chars, max 60 chars."""
    slug = raw_slug.lower().strip()
    slug = re.sub(r"[^\w-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60]


def _write_to_drafts(post: BlogPost) -> None:
    """Write the final Markdown file to drafts/blogs/, or drafts/flagged/ when
    the post failed the penalty-consistency hard blocker (spec CHANGE-B2) —
    it must never sit alongside normal pending-review posts.
    """
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}-{post.slug}.md"
    target_folder = "flagged" if post.flagged else "blogs"
    drafts_dir = os.path.join(settings.content_output_dir, target_folder)
    os.makedirs(drafts_dir, exist_ok=True)
    filepath = os.path.join(drafts_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(post.content_markdown)
    if post.flagged:
        log.warning("draft_flagged_for_review", path=filepath, reason=post.flag_reason)
    else:
        log.info("draft_written", path=filepath, word_count=post.word_count)


def _log_to_sqlite(
    post: BlogPost,
    geo_score: int,
    job_id: str,
    elapsed_seconds: float,
    tier: int,
    cluster: str,
    fallback_used: bool,
    tokens_spent: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Log generation metrics to generation_log table (spec 2.9.B / DATABASE ADDITIONS).

    token_cost_log is written per-call by ModelRouter._ledger.record().
    This function writes the job-level summary row.
    """
    db_path = os.path.join(settings.content_output_dir, ".cache", "jobs.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generation_log (
            job_id                    TEXT,
            keyword                   TEXT,
            tier                      INTEGER,
            cluster                   TEXT,
            qa_score                  REAL,
            geo_score                 INTEGER,
            risk_level                TEXT,
            word_count                INTEGER,
            time_to_generate_seconds  REAL,
            model_primary             TEXT,
            model_fallback_used       INTEGER,
            tokens_spent              INTEGER,
            cost_usd                  REAL,
            timestamp                 TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO generation_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            post.primary_keyword,
            tier,
            cluster,
            post.qa_score,
            geo_score,
            post.risk_level,
            post.word_count,
            round(elapsed_seconds, 2),
            settings.groq_model,
            int(fallback_used),
            tokens_spent,
            round(cost_usd, 6),
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    log.info("generation_log_written", job_id=job_id, tokens=tokens_spent, cost_usd=round(cost_usd, 4))
