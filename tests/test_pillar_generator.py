"""Tests for Module 2.4 Pillar Page Generation System."""
import pytest
from unittest.mock import AsyncMock, patch
from src.agents.pillar_generator import generate_pillar_page, PillarPage

@pytest.fixture
def mock_topic_map():
    return {
        "sub_topics": [
            {"topic": "DPDPA Overview", "tag": "definitive"},
            {"topic": "Data Fiduciary Obligations", "tag": "definitive"},
            {"topic": "Fintech Compliance", "tag": "supporting"},
            {"topic": "Cross-border data transfer", "tag": "gap"}
        ]
    }

@pytest.fixture
def mock_outline():
    return {
        "h1_title": "The Ultimate Guide to DPDPA Compliance",
        "url_slug": "ultimate-dpdpa-guide",
        "meta_title": "DPDPA Guide",
        "meta_description": "A comprehensive 5000 word guide with 10 actionable steps.",
        "sections": [
            {"h2_heading": "Introduction", "section_type": "introduction", "target_words": 300},
            {"h2_heading": "Key Definitions", "section_type": "definition_block", "target_words": 400},
            {"h2_heading": "Regulatory Requirements", "section_type": "regulatory_explainer", "target_words": 500},
            {"h2_heading": "Implementation Checklist", "section_type": "checklist", "target_words": 400},
            {"h2_heading": "Frequently Asked Questions", "section_type": "faq_block", "target_words": 500},
            {"h2_heading": "Book a Demo", "section_type": "cta_section", "target_words": 100}
        ]
    }

@pytest.fixture
def mock_sections():
    return [
        {"type": "introduction", "content": "## Introduction\n\nIntro content.", "h2": "Introduction"},
        {"type": "definition_block", "content": "## Key Definitions\n\n**Data Fiduciary:** An entity.", "h2": "Key Definitions"},
        {"type": "regulatory_explainer", "content": "## Regulatory Requirements\n\nSection 5 rules.", "h2": "Regulatory Requirements"},
        {"type": "checklist", "content": "## Implementation Checklist\n\n- [ ] Step 1", "h2": "Implementation Checklist"},
        {"type": "faq_block", "content": "## Frequently Asked Questions\n\n### What is it?", "h2": "Frequently Asked Questions"},
    ]

@pytest.mark.asyncio
@patch("src.agents.pillar_generator.get_full_serp_intelligence", new_callable=AsyncMock)
@patch("src.agents.pillar_generator._stage1_cluster_synthesis", new_callable=AsyncMock)
@patch("src.agents.pillar_generator._stage2_pillar_outline", new_callable=AsyncMock)
@patch("src.agents.pillar_generator._stage3_pillar_sections", new_callable=AsyncMock)
@patch("src.agents.pillar_generator._stage5_schemas_and_meta", new_callable=AsyncMock)
@patch("src.agents.pillar_generator._write_to_drafts")
async def test_generate_pillar_page(
    m_write, m_stage5, m_stage3, m_stage2, m_stage1, m_serp,
    mock_topic_map, mock_outline, mock_sections
):
    m_serp.return_value = {}
    m_stage1.return_value = mock_topic_map
    m_stage2.return_value = mock_outline
    m_stage3.return_value = mock_sections
    
    # Mock stage 5 returns (meta, schemas)
    m_stage5.return_value = (
        {"title": "Meta Title", "description": "Meta Desc", "slug": "slug"},
        {"Article": {}, "DefinedTermSet": {}}
    )

    page = await generate_pillar_page(
        cluster_id="dpdpa-core",
        cluster_keyword="DPDPA compliance",
        paa_questions=["What is DPDPA?"],
        gap_topics=["Cross border transfer"],
        existing_cluster_urls=[{"title": "Fintech post", "url": "/blog/fintech"}]
    )

    assert isinstance(page, PillarPage)
    assert page.is_pillar is True
    assert page.tier == 1
    assert page.risk_level == "HIGH"
    assert page.approved is False
    assert "DefinedTermSet" in page.schema_json
    assert "slug" in page.slug
