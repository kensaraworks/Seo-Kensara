import pytest
from unittest.mock import AsyncMock, patch
from src.agents.blog_writer import generate_blog_post, BlogPost, _assemble_post


def _mock_post(keyword: str = "DPDPA compliance software") -> BlogPost:
    content = (
        f"# {keyword.title()}\n\n"
        "## What is DPDPA?\n\nThe Digital Personal Data Protection Act 2023...\n\n"
        "## How Indian Companies Must Comply\n\n1. Appoint a DPO.\n2. Register as Data Fiduciary.\n\n"
        "## Common Mistakes\n\nIgnoring consent management.\n\n"
        "## How KensaraAI Solves This\n\nKensaraAI's 12 AI agents scan your infrastructure.\n\n"
        "Book a demo: https://kensara.in/request-demo\n\n" + ("word " * 800)
    )
    return _assemble_post(keyword, content, {
        "title": f"{keyword.title()} — India Guide 2026",
        "meta_description": f"Complete guide to {keyword} for Indian enterprises. MeitY-incubated KensaraAI automates compliance. Book a demo.",
        "secondary_keywords": ["DPDPA tool India", "data protection compliance"],
    })


@pytest.mark.asyncio
async def test_blog_post_has_required_fields(sample_scored_item):
    """Generated blog must have all required fields."""
    keyword = "DPDPA compliance software"
    mock = _mock_post(keyword)

    with patch("src.agents.blog_writer._generate_with_groq", new_callable=AsyncMock, return_value=mock):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert isinstance(post, BlogPost)
    assert post.title
    assert post.meta_description
    assert post.slug
    assert post.primary_keyword == keyword
    assert post.content_markdown
    assert post.word_count > 0
    assert post.cta_url == "https://kensara.in/request-demo"


@pytest.mark.asyncio
async def test_blog_slug_from_keyword(sample_scored_item):
    """Slug must be URL-safe version of keyword."""
    keyword = "DPDPA compliance software"
    mock = _mock_post(keyword)

    with patch("src.agents.blog_writer._generate_with_groq", new_callable=AsyncMock, return_value=mock):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert post.slug == "dpdpa-compliance-software"
    assert " " not in post.slug


@pytest.mark.asyncio
async def test_blog_falls_back_to_nvidia_on_groq_failure(sample_scored_item):
    """If Groq call fails, NVIDIA NIM fallback must be used."""
    keyword = "DPDPA compliance software"
    mock = _mock_post(keyword)

    with patch("src.agents.blog_writer._generate_with_groq", new_callable=AsyncMock, side_effect=Exception("Groq timeout")), \
         patch("src.agents.blog_writer._generate_with_nvidia", new_callable=AsyncMock, return_value=mock):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert isinstance(post, BlogPost)
    assert post.primary_keyword == keyword
