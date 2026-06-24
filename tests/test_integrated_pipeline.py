"""Integrated pipeline tests for scanning, scoring, and immediate newsjacking triggers."""
import asyncio
import hashlib
import json
import sqlite3
import pytest
from unittest.mock import patch, AsyncMock

from src.scrapers.rss_scraper import NewsItem
from src.agents.news_scout import ScoredNewsItem
from src.main import run_regulatory_poll, run_blog_generate
from src.queue.job_queue import job_queue
from src.scrapers.deduplicator import get_word_frequencies


@pytest.mark.asyncio
async def test_regulatory_poll_critical_newsjack_trigger(mocker):
    """If a story scores >= 12, run_regulatory_poll must immediately trigger run_blog_generate."""
    # 1. Prepare a high relevance news item (score >= 12)
    # Source: RBI (+2)
    # Title/Summary keywords: DPDPA, data protection board, penalty, Rupees, urgent, immediately
    # Expect score to be >= 12
    critical_item = NewsItem(
        title="RBI and Data Protection Board of India issue urgent DPDPA order",
        url="https://rbi.org.in/circulars/critical-newsjack",
        summary="RBI has penalised a major bank with a Rupees 5 crore penalty for severe violation of DPDPA consent rules.",
        published_date="2026-06-24",
        source="RBI",
    )

    # Clean the DB for this story
    story_id = hashlib.md5(critical_item.url.encode("utf-8")).hexdigest()
    try:
        with sqlite3.connect(str(job_queue.db_path)) as conn:
            conn.execute("DELETE FROM stories_processed WHERE story_id = ?", (story_id,))
            conn.commit()
    except Exception:
        pass

    # Mock fetch_rss_feeds to return our critical item
    mocker.patch("src.main.fetch_rss_feeds", AsyncMock(return_value=[critical_item]))

    # Mock generate_blog_post and save_blog_draft to avoid actual LLM calls and file writes
    mock_post = mocker.MagicMock()
    mock_post.title = "Mock SEO Blog Post"
    mock_post.slug = "mock-seo-blog-post"
    mock_post.word_count = 1000
    mock_post.content_markdown = "Mock Content"
    mock_post.primary_keyword = "dpdpa"
    mock_post.secondary_keywords = []
    mock_post.meta_description = "Mock description"
    mock_post.cta_url = ""

    mock_generate = mocker.patch("src.main.generate_blog_post", AsyncMock(return_value=mock_post))
    mock_save = mocker.patch("src.main.save_blog_draft", AsyncMock(return_value=mocker.MagicMock(name="mock_path")))

    # Run the regulatory poll
    await run_regulatory_poll()

    # Check that run_blog_generate was indirectly triggered and completed successfully
    assert mock_generate.await_count == 1
    assert mock_save.await_count == 1

    # Verify database entry has action_taken = 'newsjacked' or 'blog_generated'
    with sqlite3.connect(str(job_queue.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT action_taken, score FROM stories_processed WHERE story_id = ?",
            (story_id,)
        ).fetchone()

    assert row is not None
    assert row["action_taken"] == "newsjacked"
    assert row["score"] >= 12

    # Clean up
    try:
        with sqlite3.connect(str(job_queue.db_path)) as conn:
            conn.execute("DELETE FROM stories_processed WHERE story_id = ?", (story_id,))
            conn.commit()
    except Exception:
        pass
