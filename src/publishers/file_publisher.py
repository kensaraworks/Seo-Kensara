"""File publisher — saves blog drafts as Markdown for human review.

Responsibilities handled here that cannot be done at generation time:
  1. Fresh inject_mandatory_links pass: the internal_link_map grows as new posts
     register, so posts published after this draft was initially generated can now
     be linked in. The injection is idempotent — URLs already in the body are skipped.
  2. Spec-compliant frontmatter rebuilt from BlogPost fields (spec 2.2 STEP 7):
     avoids double-frontmatter regardless of which code path created the post
     (7-step pipeline, pillar generator, refresh agent all produce BlogPost objects).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

import structlog

from src.agents.blog_writer import BlogPost
from src.config import settings
from src.engines.internal_linker import inject_mandatory_links

log = structlog.get_logger()

# Matches a YAML frontmatter block at the very start of a Markdown document.
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _build_frontmatter(post: BlogPost, injected_links: List[str]) -> str:
    """Construct full spec-compliant YAML frontmatter (spec 2.2 STEP 7)."""
    def _q(s: str) -> str:
        """Escape a string for use inside a YAML double-quoted scalar."""
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    secondary_kw = (
        "[" + ", ".join(f'"{kw}"' for kw in post.secondary_keywords) + "]"
        if post.secondary_keywords
        else "[]"
    )
    injected_yaml = (
        "[" + ", ".join(f'"{u}"' for u in injected_links) + "]"
        if injected_links
        else "[]"
    )
    iso_now = post.date_created or datetime.now(timezone.utc).isoformat()
    source_url_yaml = f'"{_q(post.source_story_url)}"' if post.source_story_url else "null"

    return (
        "---\n"
        f'title: "{_q(post.title)}"\n'
        f'slug: "{_q(post.slug)}"\n'
        f'meta_title: "{_q(post.title)}"\n'
        f'meta_description: "{_q(post.meta_description)}"\n'
        f'canonical_url: "https://kensara.in/blogs/{_q(post.slug)}"\n'
        f'primary_keyword: "{_q(post.primary_keyword)}"\n'
        f"secondary_keywords: {secondary_kw}\n"
        f'cluster: "{_q(post.cluster)}"\n'
        f'intent: "{_q(post.intent)}"\n'
        f"tier: {post.tier}\n"
        f"word_count: {post.word_count}\n"
        f"qa_score: {post.qa_score}\n"
        f"geo_score: {post.geo_score}\n"
        f'risk_level: "{_q(post.risk_level)}"\n'
        f"approved: {str(post.approved).lower()}\n"
        'status: "pending"\n'
        f'author: "{_q(post.author)}"\n'
        f'author_credentials: "{_q(post.author_credentials)}"\n'
        f'date_created: "{iso_now}"\n'
        "date_published: null\n"
        "date_modified: null\n"
        f'schema_json: "{_q(post.schema_json)}"\n'
        f"internal_links_injected: {injected_yaml}\n"
        f"source_story_url: {source_url_yaml}\n"
        f'featured_image_alt: "{_q(post.featured_image_alt)}"\n'
        "wp_post_id: null\n"
        "wp_post_url: null\n"
        "---\n\n"
    )


async def save_blog_draft(post: BlogPost) -> Path:
    """Save blog post to drafts/blogs/YYYY-MM-DD-slug.md.

    Strips any embedded frontmatter from post.content_markdown, runs a fresh
    idempotent link-injection pass, then rebuilds the full spec frontmatter so
    the file on disk always reflects the current BlogPost state.
    """
    output_dir = Path(settings.content_output_dir) / "blogs"
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{date.today()}-{post.slug}.md"
    filepath = output_dir / filename

    # Strip any embedded frontmatter so we can regenerate it cleanly.
    # post.content_markdown from _step7_final_assembly already contains frontmatter;
    # posts from other pipelines (pillar, refresh) may or may not.
    fm_match = _FRONTMATTER_RE.match(post.content_markdown)
    body_md = (
        post.content_markdown[fm_match.end():].lstrip()
        if fm_match
        else post.content_markdown
    )

    # Fresh link injection pass — idempotent (internal_linker skips URLs already present).
    # Picks up any cluster posts registered after the draft was first generated.
    body_md, newly_injected = inject_mandatory_links(
        markdown=body_md,
        keyword=post.primary_keyword,
        cluster_id=post.cluster,
        intent_type=post.intent,
        tier=post.tier,
    )

    # Merge prior tracked injections with newly found ones (preserve order, deduplicate).
    all_injected: List[str] = list(
        dict.fromkeys(list(post.internal_links_injected) + newly_injected)
    )

    frontmatter = _build_frontmatter(post, all_injected)
    filepath.write_text(frontmatter + body_md, encoding="utf-8")

    log.info(
        "blog_draft_saved",
        path=str(filepath),
        word_count=post.word_count,
        links_injected=len(all_injected),
    )
    return filepath
