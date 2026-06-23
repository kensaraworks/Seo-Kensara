"""File publisher — saves blog drafts as Markdown for human review."""
from datetime import date
from pathlib import Path

import structlog

from src.agents.blog_writer import BlogPost
from src.config import settings

log = structlog.get_logger()


async def save_blog_draft(post: BlogPost) -> Path:
    """Save blog post to drafts/blogs/YYYY-MM-DD-slug.md"""
    output_dir = Path(settings.content_output_dir) / "blogs"
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{date.today()}-{post.slug}.md"
    filepath = output_dir / filename

    frontmatter = f"""---
title: "{post.title}"
date: {date.today()}
status: draft
primary_keyword: "{post.primary_keyword}"
secondary_keywords: {post.secondary_keywords}
meta_description: "{post.meta_description}"
word_count: {post.word_count}
cta_url: "{post.cta_url}"
reviewed_by: ""
approved: false
---

"""
    filepath.write_text(frontmatter + post.content_markdown, encoding="utf-8")
    log.info("blog_draft_saved", path=str(filepath), word_count=post.word_count)
    return filepath
