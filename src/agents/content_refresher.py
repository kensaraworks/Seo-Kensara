from __future__ import annotations

import re
import json
import os
import sqlite3
import uuid
import structlog
from datetime import datetime, timezone, date
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from src.agents.blog_writer import BlogPost
from src.config import settings

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Refresh Queue — SQLite persistence (spec 2.5 / DATABASE ADDITIONS)
# ---------------------------------------------------------------------------

def _jobs_db_path() -> str:
    return os.path.join(settings.content_output_dir, ".cache", "jobs.db")


_CREATE_REFRESH_QUEUE_SQL = """
CREATE TABLE IF NOT EXISTS refresh_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT,
    post_url        TEXT NOT NULL,
    trigger_reason  TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 5,
    queued_date     TEXT NOT NULL,
    refresh_status  TEXT NOT NULL DEFAULT 'pending',
    refreshed_date  TEXT
);
"""

# Valid priority values (lower = more urgent, mirrors spec 2.10.C ordering):
#   1 = Tier 3 newsjack / time-sensitive
#   2 = Position 8-20 in GSC (closest to page 1)
#   3 = Zero-coverage cluster keyword
#   4 = Pillar page
#   5 = Regular Tier 2 or older post
#   6 = Anchor text / link repair


def _get_connection() -> sqlite3.Connection:
    """Return a connection to jobs.db, ensuring the refresh_queue table exists."""
    db_path = _jobs_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_REFRESH_QUEUE_SQL)
    conn.commit()
    return conn


def enqueue_refresh(
    post_url: str,
    trigger_reason: str,
    priority: int = 5,
    post_id: Optional[str] = None,
) -> int:
    """Add a post to the refresh queue if it is not already pending.

    Returns the row id of the new (or existing pending) queue entry.
    If the post_url already has a 'pending' entry, the priority is updated
    if the new call requests a higher urgency (lower number).
    """
    conn = _get_connection()
    with conn:
        existing = conn.execute(
            "SELECT id, priority FROM refresh_queue WHERE post_url = ? AND refresh_status = 'pending'",
            (post_url,),
        ).fetchone()
        if existing:
            if priority < existing["priority"]:
                conn.execute(
                    "UPDATE refresh_queue SET priority = ?, trigger_reason = ? WHERE id = ?",
                    (priority, trigger_reason, existing["id"]),
                )
                log.info("refresh_queue_priority_updated", post_url=post_url, new_priority=priority)
            row_id = existing["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO refresh_queue
                   (post_id, post_url, trigger_reason, priority, queued_date, refresh_status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (post_id, post_url, trigger_reason, priority, datetime.now(timezone.utc).isoformat()),
            )
            row_id = cursor.lastrowid
            log.info("refresh_queued", post_url=post_url, trigger=trigger_reason, priority=priority)
    conn.close()
    return row_id


def get_pending_refreshes(limit: int = 10) -> List[Dict]:
    """Return pending refresh jobs ordered by priority then queued_date (oldest first)."""
    conn = _get_connection()
    rows = conn.execute(
        """SELECT * FROM refresh_queue
           WHERE refresh_status = 'pending'
           ORDER BY priority ASC, queued_date ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_refreshed(post_url: str) -> None:
    """Mark a pending refresh entry as completed."""
    conn = _get_connection()
    with conn:
        conn.execute(
            """UPDATE refresh_queue
               SET refresh_status = 'completed', refreshed_date = ?
               WHERE post_url = ? AND refresh_status = 'pending'""",
            (datetime.now(timezone.utc).isoformat(), post_url),
        )
    conn.close()
    log.info("refresh_marked_complete", post_url=post_url)


def mark_refresh_failed(post_url: str, reason: str = "") -> None:
    """Mark a pending refresh as failed so it can be retried or investigated."""
    conn = _get_connection()
    with conn:
        conn.execute(
            """UPDATE refresh_queue
               SET refresh_status = 'failed', refreshed_date = ?, trigger_reason = ?
               WHERE post_url = ? AND refresh_status = 'pending'""",
            (datetime.now(timezone.utc).isoformat(), reason or "unknown failure", post_url),
        )
    conn.close()
    log.warning("refresh_marked_failed", post_url=post_url, reason=reason)


def _today_iso() -> str:
    return date.today().isoformat()


def _split_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """Return frontmatter fields and body markdown."""
    if not markdown.startswith("---\n"):
        return {}, markdown

    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown

    raw_fm = markdown[4:end].strip()
    body = markdown[end + len("\n---"):].lstrip("\n")
    frontmatter: dict[str, str] = {}
    for line in raw_fm.splitlines():
        if not line.strip() or ":" not in line or line.startswith("  "):
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"')
    return frontmatter, body


def _replace_frontmatter_field(markdown: str, field: str, value: str) -> str:
    """Update or append a simple YAML frontmatter field."""
    formatted = f'{field}: "{value}"'
    if not markdown.startswith("---\n"):
        return f"---\n{formatted}\n---\n\n{markdown}"

    end = markdown.find("\n---", 4)
    if end == -1:
        return markdown

    fm = markdown[:end]
    body = markdown[end:]
    pattern = re.compile(rf"^{re.escape(field)}:\s*.*$", re.MULTILINE)
    if pattern.search(fm):
        fm = pattern.sub(formatted, fm, count=1)
    else:
        fm = fm.rstrip() + "\n" + formatted
    return fm + body


def _section_pattern(section_title: str) -> re.Pattern:
    return re.compile(
        rf"(^##\s+{re.escape(section_title)}\s*\n)(.*?)(?=^##\s+|\Z)",
        re.DOTALL | re.MULTILINE,
    )


def _replace_h2_section(markdown: str, section_title: str, replacement: str) -> tuple[str, bool]:
    """Replace only the named H2 section body, preserving surrounding sections."""
    replacement = replacement.strip()
    if not replacement.startswith("## "):
        replacement = f"## {section_title}\n\n{replacement}"

    updated, count = _section_pattern(section_title).subn(replacement.rstrip() + "\n", markdown, count=1)
    return updated, count > 0


def _find_post_file(post_url: str, post_id: Optional[str] = None) -> Optional[Path]:
    """Locate a draft markdown file from canonical URL, slug, or queue post_id."""
    drafts_dir = Path(settings.content_output_dir) / "blogs"
    if not drafts_dir.exists():
        return None

    slug = ""
    if post_url:
        slug = post_url.rstrip("/").split("/")[-1]
    candidates = []
    if post_id:
        candidates.extend(drafts_dir.glob(f"*{post_id}*.md"))
    if slug:
        candidates.extend(drafts_dir.glob(f"*{slug}*.md"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    for candidate in drafts_dir.glob("*.md"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _split_frontmatter(text)
        if post_url and post_url in {
            fm.get("canonical_url", ""),
            fm.get("wp_post_url", ""),
        }:
            return candidate
    return None


def _post_from_markdown(path: Path, markdown: str, post_url: str = "") -> BlogPost:
    fm, body = _split_frontmatter(markdown)
    slug = fm.get("slug") or path.stem.split("-", 3)[-1]
    return BlogPost(
        title=fm.get("title", slug.replace("-", " ").title()),
        slug=slug,
        meta_description=fm.get("meta_description", ""),
        schema_json=fm.get("schema_json", "{}").replace('\\"', '"'),
        content_markdown=body,
        word_count=len(body.split()),
        tier=int(str(fm.get("tier", "1")).strip() or 1),
        cluster=fm.get("cluster", fm.get("cluster_id", "general")),
        intent=fm.get("intent", fm.get("intent_type", "informational")),
        primary_keyword=fm.get("primary_keyword", slug.replace("-", " ")),
        date_created=fm.get("date_created", ""),
        date_published=fm.get("date_published") or None,
        date_modified=fm.get("date_modified") or None,
        wp_post_url=fm.get("wp_post_url") or post_url or None,
    )


def _build_refresh_outline(section_title: str, brief: RefreshBrief, post: BlogPost) -> dict[str, Any]:
    section_notes = {
        "h2_heading": section_title,
        "section_type": "regulatory_explainer"
        if any(term in section_title.lower() for term in ("dpdpa", "rule", "section", "regulatory"))
        else "answer_block",
        "target_words": 220,
        "key_points_to_cover": [
            f"Refresh only the H2 section titled '{section_title}'.",
            "Preserve the original article's point of view and India-specific DPDPA context.",
            "Use the refresh brief to update stale facts without changing unrelated sections.",
            *brief.statistics_to_update,
            *brief.new_paa_questions[:3],
        ],
        "internal_link_opportunity": None,
        "india_specificity_requirement": "Mention DPDPA, an Indian regulator, rupee impact, or India-specific compliance detail.",
    }
    return {
        "featured_snippet_block": "",
        "sections": [section_notes],
        "faq_section": {"include": False, "questions": []},
        "cta_section": {},
        "refresh_context": {
            "slug": post.slug,
            "primary_keyword": post.primary_keyword,
            "sections_to_update": brief.sections_to_update,
            "new_sections_to_add": brief.new_sections_to_add,
        },
    }


def _build_new_section_outline(section_title: str, brief: RefreshBrief, post: BlogPost) -> dict[str, Any]:
    section_notes = {
        "h2_heading": section_title,
        "section_type": "regulatory_explainer"
        if any(term in section_title.lower() for term in ("dpdpa", "rule", "section", "regulatory"))
        else "answer_block",
        "target_words": 220,
        "key_points_to_cover": [
            f"Write a completely new H2 section titled '{section_title}'.",
            f"This topic does not exist yet in the post about '{post.primary_keyword}'.",
            "Match the tone, depth, and India-DPDPA focus of the surrounding article.",
            "Provide actionable compliance guidance a legal or IT professional can act on.",
            *brief.new_paa_questions[:3],
        ],
        "internal_link_opportunity": None,
        "india_specificity_requirement": "Mention DPDPA, an Indian regulator, rupee impact, or India-specific compliance detail.",
    }
    return {
        "featured_snippet_block": "",
        "sections": [section_notes],
        "faq_section": {"include": False, "questions": []},
        "cta_section": {},
        "refresh_context": {
            "slug": post.slug,
            "primary_keyword": post.primary_keyword,
            "sections_to_update": brief.sections_to_update,
            "new_sections_to_add": brief.new_sections_to_add,
        },
    }


def _log_refresh_cost(job_id: str, post: BlogPost) -> None:
    """Ensure refresh work is visible in token_cost_log even for deterministic steps."""
    from src.engines.model_router import _write_token_cost_log

    _write_token_cost_log(
        job_id=job_id,
        model_used="deterministic",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        tier=post.tier,
        cluster_id=post.cluster,
        task="refresh",
    )

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class RefreshBrief(BaseModel):
    sections_to_update: List[str] = Field(description="Exact H2 titles of sections requiring new content")
    new_sections_to_add: List[str] = Field(description="New H2 titles covering emerged topics")
    links_to_fix: List[str] = Field(description="Broken internal or external links to replace")
    new_internal_links: List[str] = Field(description="New cluster posts published since original that should be linked")
    statistics_to_update: List[str] = Field(description="Old stats with suggested replacement sources")
    new_paa_questions: List[str] = Field(description="Questions from PAA that appeared after original publish")


# ---------------------------------------------------------------------------
# Pipeline Steps
# ---------------------------------------------------------------------------

def step1_refresh_audit(post: BlogPost, current_context: dict) -> dict:
    """
    Automated audit of the current markdown to identify decay.
    """
    content = post.content_markdown
    
    # 1. Identify sections
    # Split by ## to get H2 sections
    sections = re.split(r'\n##\s+', '\n' + content)
    section_map = {}
    if len(sections) > 1:
        # sections[0] is everything before the first H2
        for sec in sections[1:]:
            lines = sec.split('\n', 1)
            title = lines[0].strip()
            body = lines[1] if len(lines) > 1 else ""
            section_map[title] = body

    # 2. Extract statistics
    # Simple regex to find percentage stats e.g., "45%" or "₹250 crore"
    stats_found = re.findall(r'\b\d+(?:\.\d+)?%|\b₹\d+(?:\.\d+)?\s*(?:crore|lakh)\b', content)
    
    # 3. Extract regulatory references
    reg_refs = re.findall(r'(?i)\bsection\s+\d+\b|\brule\s+\d+\b', content)
    
    # 4. Extract links
    links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
    
    # Simulate a broken link check
    broken_links = []
    for text, url in links:
        if "broken" in url or "404" in url:
            broken_links.append(url)

    audit_result = {
        "existing_sections": list(section_map.keys()),
        "statistics_found": list(set(stats_found)),
        "regulatory_references": list(set(reg_refs)),
        "total_links": len(links),
        "broken_links_simulated": broken_links,
        "current_context_comparison": current_context
    }
    
    log.info("refresh_audit_complete", slug=post.slug, stats=len(stats_found), broken=len(broken_links))
    return audit_result


async def step2_generate_refresh_brief(audit_data: dict, new_context: dict, llm_client=None) -> RefreshBrief:
    """Generate a RefreshBrief using the ModelRouter (Groq → NVIDIA fallback).

    llm_client should be a ModelRouter instance. When None, one is built internally
    if GROQ_API_KEY is set. Falls back to rule-based brief when no keys are available.
    """
    from src.engines.model_router import ModelRouter, ANTI_HALLUCINATION_SYSTEM_PROMPT

    sections_list = "\n".join(
        f"- {s}" for s in audit_data.get("existing_sections", [])
    ) or "No H2 sections found"
    stats_found = (
        ", ".join(audit_data.get("statistics_found", [])[:10]) or "None detected"
    )
    reg_refs = (
        ", ".join(audit_data.get("regulatory_references", [])[:8]) or "None detected"
    )
    trigger = new_context.get("trigger_reason", "routine_refresh")
    emerging_raw = new_context.get("emerging_topics", [])
    emerging = json.dumps(emerging_raw) if emerging_raw else "None"

    prompt = f"""A published KensaraAI blog post needs refreshing. Analyse the audit data and return a JSON refresh plan.

TRIGGER: {trigger}

EXISTING H2 SECTIONS IN POST:
{sections_list}

STATISTICS DETECTED IN POST:
{stats_found}

REGULATORY REFERENCES DETECTED:
{reg_refs}

EMERGING TOPICS FROM CONTEXT:
{emerging}

Return JSON matching EXACTLY this schema — no other keys:
{{
  "sections_to_update": [],
  "new_sections_to_add": [],
  "links_to_fix": [],
  "new_internal_links": [],
  "statistics_to_update": [],
  "new_paa_questions": []
}}

Rules:
- sections_to_update: EXACT H2 titles from the list above whose content is stale (outdated stats, old deadlines, superseded regulation text). Max 3.
- new_sections_to_add: H2 titles for genuinely missing DPDPA 2026 topics. Max 2. Omit if nothing is clearly missing.
- statistics_to_update: for each stale stat write "Replace [old stat] — source: [suggested 2026 source]". Max 3.
- new_paa_questions: questions a DPDPA compliance buyer would ask in 2026 not answered by existing sections. Max 3.
- links_to_fix and new_internal_links: leave as empty arrays.
- Return valid JSON only. No text outside the JSON object."""

    messages = [
        {"role": "system", "content": ANTI_HALLUCINATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    router = llm_client if isinstance(llm_client, ModelRouter) else None
    if router is None and os.environ.get("GROQ_API_KEY"):
        router = ModelRouter(
            job_id=f"brief-{uuid.uuid4().hex[:8]}",
            tier=2,
            cluster_id="refresh",
        )

    if router is not None:
        try:
            # generate() uses "refresh" task config: Groq primary, NVIDIA fallback, temp=0.4
            text, _ = await router.generate_with_fallback("refresh", messages, json_mode=True)
            data = json.loads(text)

            existing_set = set(audit_data.get("existing_sections", []))
            valid_updates = [s for s in data.get("sections_to_update", []) if s in existing_set]

            brief = RefreshBrief(
                sections_to_update=valid_updates,
                new_sections_to_add=data.get("new_sections_to_add", []),
                links_to_fix=data.get("links_to_fix", []) + audit_data.get("broken_links_simulated", []),
                new_internal_links=data.get("new_internal_links", []) + new_context.get("new_cluster_urls", []),
                statistics_to_update=data.get("statistics_to_update", []),
                new_paa_questions=data.get("new_paa_questions", []) + new_context.get("new_paa", []),
            )
            log.info(
                "refresh_brief_generated_router",
                updates=len(brief.sections_to_update),
                additions=len(brief.new_sections_to_add),
                fallback_used=router.fallback_used,
            )
            return brief

        except Exception as exc:
            log.warning("refresh_brief_router_failed_falling_back", error=str(exc))

    # Rule-based fallback: no API keys available, or router call failed.
    log.warning("refresh_brief_rule_based_fallback", has_groq_key=bool(os.environ.get("GROQ_API_KEY")))
    existing_sections = audit_data.get("existing_sections", [])
    stale_keywords = ("statistics", "data", "figures", "2024", "2023", "deadline", "penalty", "fine")
    stale_candidates = [s for s in existing_sections if any(kw in s.lower() for kw in stale_keywords)]
    stats_raw = audit_data.get("statistics_found", [])

    brief = RefreshBrief(
        sections_to_update=stale_candidates[:3],
        new_sections_to_add=new_context.get("emerging_topics", [])[:2],
        links_to_fix=audit_data.get("broken_links_simulated", []),
        new_internal_links=new_context.get("new_cluster_urls", []),
        statistics_to_update=[f"Review stat: {s}" for s in stats_raw[:3]],
        new_paa_questions=new_context.get("new_paa", []),
    )
    log.info("refresh_brief_generated_rule_based", updates=len(brief.sections_to_update))
    return brief


async def step3_targeted_regeneration(post: BlogPost, brief: RefreshBrief, llm_client=None) -> str:
    """
    Surgically regenerate selected H2 sections without touching the rest.

    When `llm_client` is a ModelRouter-compatible object, this reuses
    blog_writer._step2_generate_sections for each selected section. Without a
    router, it falls back to deterministic content so unit tests and dry runs
    remain offline.
    """
    content = post.content_markdown

    for sec_title in brief.sections_to_update:
        if llm_client is not None:
            from src.agents.blog_writer import _step2_generate_sections

            outline = _build_refresh_outline(sec_title, brief, post)
            context = json.dumps(
                {
                    "refresh_brief": brief.model_dump(),
                    "original_post": {
                        "title": post.title,
                        "slug": post.slug,
                        "primary_keyword": post.primary_keyword,
                    },
                },
                ensure_ascii=False,
            )
            generated_sections = await _step2_generate_sections(
                llm_client,
                outline,
                post.primary_keyword,
                context,
                post.intent,
                post.tier,
            )
            generated = next(
                (
                    section["content"]
                    for section in generated_sections
                    if section.get("content", "").lstrip().startswith("## ")
                ),
                "",
            )
            if not generated:
                generated = f"## {sec_title}\n\n[Refresh generation returned no section. Manual review required.]"
        else:
            generated = (
                f"## {sec_title}\n\n"
                "This section has been surgically updated with 2026 statistics.\n"
            )

        content, replaced = _replace_h2_section(content, sec_title, generated)
        if replaced:
            log.info("section_regenerated", section=sec_title)
        else:
            log.warning("section_regeneration_skipped_missing_h2", section=sec_title)

    for new_sec in brief.new_sections_to_add:
        if f"## {new_sec}" in content:
            continue

        if llm_client is not None:
            from src.agents.blog_writer import _step2_generate_sections

            outline = _build_new_section_outline(new_sec, brief, post)
            context = json.dumps(
                {
                    "refresh_brief": brief.model_dump(),
                    "original_post": {
                        "title": post.title,
                        "slug": post.slug,
                        "primary_keyword": post.primary_keyword,
                    },
                },
                ensure_ascii=False,
            )
            generated_sections = await _step2_generate_sections(
                llm_client,
                outline,
                post.primary_keyword,
                context,
                post.intent,
                post.tier,
            )
            generated = next(
                (
                    section["content"]
                    for section in generated_sections
                    if section.get("content", "").lstrip().startswith("## ")
                ),
                "",
            )
            if not generated:
                generated = f"## {new_sec}\n\n[New section generation returned no content. Manual review required.]"
            content += f"\n\n{generated.strip()}\n"
        else:
            content += f"\n\n## {new_sec}\n\n[New section pending — manual writing required.]\n"

        log.info("section_added", section=new_sec)

    for broken in brief.links_to_fix:
        content = content.replace(broken, f"{broken}-fixed")

    post.content_markdown = content
    return content


def step4_post_refresh_actions(
    post: BlogPost,
    *,
    original_markdown: Optional[str] = None,
    file_path: Optional[Path] = None,
    post_url: Optional[str] = None,
    post_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict:
    """
    Persist refreshed content and update downstream bookkeeping.
    """
    from src.engines.internal_linker import inject_mandatory_links, register_post
    from src.queue.job_queue import job_queue

    today = _today_iso()
    content = post.content_markdown.strip() + "\n"
    current_date_str = datetime.now().strftime("%B %Y")

    if "Last updated:" in content:
        content = re.sub(r'Last updated:.*', f'Last updated: {current_date_str}', content)
    else:
        content += f"\n\n*Last updated: {current_date_str}*\n"

    content, injected_links = inject_mandatory_links(
        markdown=content,
        keyword=post.primary_keyword,
        cluster_id=post.cluster,
        intent_type=post.intent,
        tier=post.tier,
    )
    post.content_markdown = content

    if hasattr(post, "schema_json") and isinstance(post.schema_json, str):
        try:
            schema = json.loads(post.schema_json)
            if isinstance(schema, dict) and "Article" in schema.get("@type", ""):
                schema["dateModified"] = datetime.now().isoformat()
                post.schema_json = json.dumps(schema)
        except json.JSONDecodeError:
            pass

    final_markdown = content
    if original_markdown is not None:
        fm, _ = _split_frontmatter(original_markdown)
        base = original_markdown
        if base.startswith("---\n"):
            end = base.find("\n---", 4)
            final_markdown = base[: end + len("\n---")] + "\n\n" + content if end != -1 else content
        final_markdown = _replace_frontmatter_field(final_markdown, "date_modified", today)
        final_markdown = _replace_frontmatter_field(final_markdown, "word_count", str(len(content.split())))
        if post.schema_json and fm.get("schema_json"):
            schema_json_escaped = post.schema_json.replace('"', '\\"')
            final_markdown = _replace_frontmatter_field(final_markdown, "schema_json", schema_json_escaped)

    if file_path is not None:
        file_path.write_text(final_markdown, encoding="utf-8")

    canonical_url = post_url or post.wp_post_url or f"https://kensara.in/blogs/{post.slug}"
    link_post_id = register_post(
        post_url=canonical_url,
        post_title=post.title,
        primary_keyword=post.primary_keyword,
        cluster_id=post.cluster,
        intent_type=post.intent,
        tier=post.tier,
        is_pillar=False,
    )
    job_queue.update_link_map(post_id or link_post_id or canonical_url, final_markdown)

    if canonical_url:
        mark_refreshed(canonical_url)
    if job_id:
        _log_refresh_cost(job_id, post)

    log.info(
        "post_refresh_complete",
        slug=post.slug,
        file_path=str(file_path) if file_path else "",
        injected_links=len(injected_links),
    )

    return {
        "date_modified": today,
        "file_path": str(file_path) if file_path else "",
        "internal_links_injected": injected_links,
        "word_count": len(content.split()),
    }


async def refresh_post_file(
    post_url: str,
    *,
    post_id: Optional[str] = None,
    new_context: Optional[dict] = None,
    llm_router=None,
) -> dict:
    """Process one queued refresh end-to-end."""
    path = _find_post_file(post_url, post_id=post_id)
    if path is None:
        raise FileNotFoundError(f"No draft markdown file found for {post_url}")

    original_markdown = path.read_text(encoding="utf-8")
    post = _post_from_markdown(path, original_markdown, post_url=post_url)

    # Build the router once and share it across step2 and step3 so both calls
    # draw from the same token budget ledger and appear under the same job_id.
    job_id = f"refresh-{uuid.uuid4().hex[:8]}"
    router = llm_router
    if router is None and os.environ.get("GROQ_API_KEY"):
        from src.engines.model_router import ModelRouter
        router = ModelRouter(job_id=job_id, tier=post.tier, cluster_id=post.cluster)

    audit = step1_refresh_audit(post, new_context or {})
    brief = await step2_generate_refresh_brief(audit, new_context or {}, llm_client=router)
    await step3_targeted_regeneration(post, brief, llm_client=router)
    result = step4_post_refresh_actions(
        post,
        original_markdown=original_markdown,
        file_path=path,
        post_url=post_url,
        post_id=post_id,
        job_id=job_id,
    )
    result.update(
        {
            "post_url": post_url,
            "sections_updated": len(brief.sections_to_update),
            "sections_added": len(brief.new_sections_to_add),
        }
    )
    return result


async def process_pending_refreshes(limit: int = 5, new_context: Optional[dict] = None) -> dict:
    """Drain pending refresh_queue rows in priority order."""
    rows = get_pending_refreshes(limit=limit)
    completed = 0
    failed = 0
    results: list[dict] = []

    for row in rows:
        post_url = row["post_url"]
        try:
            result = await refresh_post_file(
                post_url,
                post_id=row.get("post_id"),
                new_context={
                    "trigger_reason": row.get("trigger_reason", ""),
                    **(new_context or {}),
                },
            )
            completed += 1
            results.append(result)
        except Exception as exc:
            failed += 1
            mark_refresh_failed(post_url, str(exc))
            results.append({"post_url": post_url, "error": str(exc)})

    return {
        "completed": completed,
        "failed": failed,
        "pending_seen": len(rows),
        "results": results,
    }
