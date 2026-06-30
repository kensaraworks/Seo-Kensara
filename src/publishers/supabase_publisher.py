"""Supabase blog publisher — inserts/upserts approved BlogPost into public.blogs.

Schema reference (seo_agent_post_guide.md):
    slug          text  NOT NULL UNIQUE
    title         text  NOT NULL
    description   text  NOT NULL   (SEO meta description, 120-160 chars)
    content       text  NOT NULL   (Markdown body — NO H1, starts with ##)
    pillar        text  NOT NULL   (must match exact router pillar slug)
    category      text  NOT NULL   (badge tag, e.g. "Fintech", "Guide")
    read_time     text  NOT NULL   (e.g. "8 min read")
    image_url     text  OPTIONAL   (cover banner URL)
    published_at  timestamptz      (NOW() for immediate release)

The publisher:
  1. Resolves the correct `pillar` from BlogPost.cluster using CLUSTER_TO_PILLAR.
  2. Derives `category` from the pillar + keyword context.
  3. Computes `read_time` from word_count (200 wpm average reading speed).
  4. Strips the YAML frontmatter block from content_markdown so only the
     Markdown body is stored (the Next.js template renders its own H1 from title).
  5. Performs an upsert (INSERT ... ON CONFLICT(slug) DO UPDATE) so re-publishing
     an edited draft updates the row rather than erroring.
  6. Gracefully degrades: if SUPABASE_URL / SUPABASE_SERVICE_KEY are missing,
     logs a warning and returns {"status": "skipped", "reason": "no_credentials"}.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from src.agents.blog_writer import BlogPost
from src.data.shell_slugs import CLUSTER_TO_PILLAR, CLUSTER_TO_CATEGORY

log = structlog.get_logger()

# Regex to strip YAML frontmatter from the top of a Markdown document.
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_supabase_credentials() -> tuple[str, str] | tuple[None, None]:
    """Return (supabase_url, service_key) from settings, or (None, None)."""
    try:
        from src.config import settings
        url = getattr(settings, "supabase_url", "") or ""
        key = getattr(settings, "supabase_service_key", "") or ""
        if url and key and url != "replace_me" and key != "replace_me":
            return url.rstrip("/"), key
    except Exception:
        pass
    return None, None


def _strip_frontmatter(markdown: str) -> str:
    """Remove YAML frontmatter block so only the Markdown body is stored."""
    match = _FRONTMATTER_RE.match(markdown)
    if match:
        return markdown[match.end():].lstrip()
    return markdown


def _strip_h1(markdown: str) -> str:
    """Remove the leading H1 heading from the body.

    The Next.js template renders its own <h1> from the `title` DB field,
    so the `content` body must NOT start with a `# Heading 1`.
    """
    lines = markdown.splitlines(keepends=True)
    cleaned = []
    skip_first_h1 = True
    for line in lines:
        if skip_first_h1 and line.startswith("# ") and not line.startswith("## "):
            skip_first_h1 = False
            continue
        cleaned.append(line)
    return "".join(cleaned).lstrip()


def _compute_read_time(word_count: int) -> str:
    """Compute approximate read time at 200 wpm (returns e.g. '8 min read')."""
    minutes = max(1, round(word_count / 200))
    return f"{minutes} min read"


def _resolve_pillar(cluster: str) -> str:
    """Map agent cluster ID → exact Supabase pillar slug."""
    return CLUSTER_TO_PILLAR.get(cluster, "fundamentals")


def _resolve_category(cluster: str, keyword: str) -> str:
    """Derive a category badge from cluster + keyword context.

    Industry-specific keywords get a more specific badge based on keyword
    presence (e.g. 'fintech' → 'Fintech', 'healthcare' → 'Healthcare').
    All others fall back to the cluster's default category.
    """
    keyword_lower = keyword.lower()
    industry_keywords: dict[str, str] = {
        "fintech": "Fintech",
        "payment": "Fintech",
        "healthcare": "Healthcare",
        "hospital": "Healthcare",
        "edtech": "Edtech",
        "saas": "SaaS",
        "ecommerce": "E-commerce",
        "e-commerce": "E-commerce",
    }
    for kw_signal, badge in industry_keywords.items():
        if kw_signal in keyword_lower:
            return badge
    return CLUSTER_TO_CATEGORY.get(cluster, "Guide")


def _build_supabase_row(post: BlogPost) -> dict:
    """Build the dict to upsert into public.blogs."""
    # Strip frontmatter + H1 from the content body
    body = _strip_frontmatter(post.content_markdown)
    body = _strip_h1(body)

    pillar = _resolve_pillar(post.cluster)
    category = _resolve_category(post.cluster, post.primary_keyword)
    read_time = _compute_read_time(post.word_count)

    # image_url: use dedicated field if set, otherwise fall back to None
    image_url: Optional[str] = getattr(post, "image_url", None) or None

    return {
        "slug": post.slug,
        "title": post.title,
        "description": post.meta_description,
        "content": body,
        "pillar": pillar,
        "category": category,
        "read_time": read_time,
        "image_url": image_url,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def publish_to_supabase(post: BlogPost) -> dict:
    """Upsert a BlogPost into Supabase public.blogs.

    Returns a dict with at least:
        status  — "published" | "skipped" | "error"
        slug    — the blog slug attempted
        url     — the live article URL (if published)
        id      — the Supabase row UUID (if published)
        reason  — (if skipped) why it was skipped
        error   — (if error) the error message
    """
    url, key = _get_supabase_credentials()

    if not url or not key:
        log.warning(
            "supabase_publish_skipped",
            slug=post.slug,
            reason="SUPABASE_URL or SUPABASE_SERVICE_KEY not configured in .env",
        )
        return {
            "status": "skipped",
            "slug": post.slug,
            "reason": "no_credentials",
        }

    row = _build_supabase_row(post)
    pillar = row["pillar"]

    log.info(
        "supabase_publish_start",
        slug=post.slug,
        pillar=pillar,
        category=row["category"],
        read_time=row["read_time"],
        word_count=post.word_count,
    )

    # Supabase REST API endpoint for public.blogs table
    endpoint = f"{url}/rest/v1/blogs"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(endpoint, json=row, headers=headers)

        if resp.status_code in (200, 201):
            data = resp.json()
            # Supabase returns a list when Prefer: return=representation
            row_data = data[0] if isinstance(data, list) and data else {}
            row_id = row_data.get("id", "")
            live_url = f"https://kensara.in/blogs/{pillar}/{post.slug}"
            log.info(
                "supabase_publish_success",
                slug=post.slug,
                pillar=pillar,
                row_id=row_id,
                url=live_url,
            )
            return {
                "status": "published",
                "slug": post.slug,
                "pillar": pillar,
                "url": live_url,
                "id": row_id,
            }
        else:
            log.error(
                "supabase_publish_failed",
                slug=post.slug,
                status_code=resp.status_code,
                body=resp.text[:500],
            )
            return {
                "status": "error",
                "slug": post.slug,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }

    except httpx.TimeoutException as exc:
        log.error("supabase_publish_timeout", slug=post.slug, error=str(exc))
        return {"status": "error", "slug": post.slug, "error": "Request timed out"}
    except Exception as exc:
        log.error("supabase_publish_exception", slug=post.slug, error=str(exc))
        return {"status": "error", "slug": post.slug, "error": str(exc)}


def publish_to_supabase_sync(post: BlogPost) -> dict:
    """Synchronous wrapper around publish_to_supabase for non-async call sites."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, publish_to_supabase(post))
                return future.result(timeout=35)
        else:
            return loop.run_until_complete(publish_to_supabase(post))
    except Exception as exc:
        log.error("supabase_publish_sync_failed", slug=post.slug, error=str(exc))
        return {"status": "error", "slug": post.slug, "error": str(exc)}
