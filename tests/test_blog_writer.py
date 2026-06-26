import pytest
import json
from unittest.mock import AsyncMock, patch
from src.agents.blog_writer import generate_blog_post, BlogPost, _assemble_post

def _mock_post(keyword: str = "DPDPA compliance software") -> BlogPost:
    content = (
        f"# {keyword.title()}\n\n"
        "## What is DPDPA?\n\nThe Digital Personal Data Protection Act 2023...\n\n"
        "## How Indian Companies Must Comply\n\n1. Appoint a DPO.\n2. Register as Data Fiduciary.\n\n"
        "## Common Mistakes\n\nIgnoring consent management.\n\n"
        "## How KensaraAI Solves This\n\nKensaraAI's 12 AI agents scan your infrastructure.\n\n"
        "Book a demo: https://www.kensara.in/book-demo\n\n" + ("word " * 800)
    )
    return _assemble_post(keyword, content, {
        "title": f"{keyword.title()} — India Guide 2026",
        "meta_description": f"Complete guide to {keyword} for Indian enterprises. MeitY-incubated KensaraAI automates compliance. Book a demo.",
        "secondary_keywords": ["DPDPA tool India", "data protection compliance"],
    })

async def _mock_call_groq(self, task, messages, temperature, json_mode=False):
    # Simulate outline generation
    if task == "outline":
        return json.dumps({
            "h1_title": "Ultimate DPDPA Compliance Software Guide",
            "url_slug": "dpdpa-compliance-software",
            "meta_title": "DPDPA Compliance Software | KensaraAI",
            "meta_description": "Complete guide to DPDPA compliance software. Book a demo.",
            "featured_snippet_block": "DPDPA compliance software is a dedicated solution designed for Indian enterprises to meet the stringent requirements of the Digital Personal Data Protection Act 2023. By automating consent management, data principal rights, and breach notification clocks, it helps organizations minimize risk and maintain comprehensive compliance logs for regulatory audits.",
            "sections": [
                {
                    "h2_heading": "What is DPDPA compliance software?",
                    "section_type": "answer_block",
                    "target_words": 500,
                    "key_points_to_cover": ["point 1"]
                },
                {
                    "h2_heading": "How to Comply?",
                    "section_type": "regulatory_explainer",
                    "target_words": 500,
                    "key_points_to_cover": ["point 2"]
                },
                {
                    "h2_heading": "Why choose KensaraAI?",
                    "section_type": "how_to",
                    "target_words": 500,
                    "key_points_to_cover": ["point 3"]
                },
                {
                    "h2_heading": "Ready to get started?",
                    "section_type": "cta_section",
                    "target_words": 650,
                    "key_points_to_cover": ["point 4"]
                }
            ],
            "faq_section": {"include": False, "questions": []},
            "cta_section": {
                "heading": "Ready to get started?",
                "body_instruction": "Book",
                "cta_url": "https://www.kensara.in/book-demo",
                "cta_text": "Book"
            }
        })
    # Simulate metadata generation
    elif task == "metadata":
        return json.dumps({
            "title": "DPDPA Compliance Software — India Guide 2026",
            "meta_description": "Complete guide to DPDPA compliance software for Indian enterprises. MeitY-incubated KensaraAI automates compliance. Book a demo.",
            "secondary_keywords": ["DPDPA tool India", "data protection compliance"],
        })
    # Simulate sections / assembly
    elif task == "assembly":
        return "# DPDPA Compliance Software\n\n## What is DPDPA?\n\nFeatured answer here.\n\n## How to Comply?\n\nBook a demo: https://www.kensara.in/book-demo"
    
    # Return generic section content passing all validators
    return (
        "## Section Heading\n\n"
        "This DPDPA compliance software guide explains requirements in India.\n"
        "Under Section 43, non-compliance can lead to a penalty of ₹50 lakh.\n\n"
        "1. Appoint a DPO in India.\n"
        "2. Set up consent dashboards.\n\n"
        "| Feature | KensaraAI | OneTrust |\n"
        "|---|---|---|\n"
        "| Price | ₹15L | ₹75L |"
    )

async def _mock_call_nvidia(self, task, messages, temperature, model_key):
    return (
        "## NVIDIA Section Heading\n\n"
        "This DPDPA compliance software guide explains requirements in India.\n"
        "Under Section 43, non-compliance can lead to a penalty of ₹50 lakh.\n\n"
        "1. Appoint a DPO in India.\n"
        "2. Set up consent dashboards.\n\n"
        "| Feature | KensaraAI | OneTrust |\n"
        "|---|---|---|\n"
        "| Price | ₹15L | ₹75L |"
    )


@pytest.mark.asyncio
async def test_blog_post_has_required_fields(sample_scored_item):
    """Generated blog must have all required fields."""
    keyword = "DPDPA compliance software"

    with patch("src.engines.model_router.ModelRouter._call_groq", new=_mock_call_groq), \
         patch("src.engines.model_router.ModelRouter._call_nvidia", new=_mock_call_nvidia):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert isinstance(post, BlogPost)
    assert post.title
    assert post.meta_description
    assert post.slug
    assert post.primary_keyword == keyword
    assert post.content_markdown
    assert post.word_count > 0
    assert post.cta_url.startswith("https://www.kensara.in/dpdpa")


@pytest.mark.asyncio
async def test_blog_slug_from_keyword(sample_scored_item):
    """Slug must be URL-safe version of keyword."""
    keyword = "DPDPA compliance software"

    with patch("src.engines.model_router.ModelRouter._call_groq", new=_mock_call_groq), \
         patch("src.engines.model_router.ModelRouter._call_nvidia", new=_mock_call_nvidia):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert post.slug == "dpdpa-compliance-software"
    assert " " not in post.slug


@pytest.mark.asyncio
async def test_blog_falls_back_to_nvidia_on_groq_failure(sample_scored_item):
    """If Groq call fails, NVIDIA NIM fallback must be used."""
    keyword = "DPDPA compliance software"
    
    # Force Groq to raise an error *only* for body sections (where fallback is tested)
    async def mock_call_groq_fail(self, task, messages, temperature, json_mode=False):
        if task in ("section", "regulatory_section"):
            raise Exception("Groq connection timeout")
        return await _mock_call_groq(self, task, messages, temperature, json_mode)

    with patch("src.engines.model_router.ModelRouter._call_groq", new=mock_call_groq_fail), \
         patch("src.engines.model_router.ModelRouter._call_nvidia", new=_mock_call_nvidia):
        post = await generate_blog_post(sample_scored_item, keyword)

    assert isinstance(post, BlogPost)
    assert post.primary_keyword == keyword
