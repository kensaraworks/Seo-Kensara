import pytest
import datetime
from src.agents.blog_writer import BlogPost
from src.agents.content_refresher import (
    step1_refresh_audit,
    step2_generate_refresh_brief,
    step3_targeted_regeneration,
    step4_post_refresh_actions,
    enqueue_refresh,
    process_pending_refreshes,
    RefreshBrief
)

@pytest.fixture
def mock_decaying_post():
    content = """# DPDPA Compliance Guide

## Introduction
This is an intro.

## The Old Stat Section
Here is an outdated stat from 2023. Compliance is at 45%.
Also, refer to section 4 of the act.

## Unchanged Section
Here is a [broken link](https://example.com/broken-page).
This section is perfectly fine and should not be touched at all.
It preserves link equity.

Last updated: January 2024
"""
    return BlogPost(
        title="DPDPA Guide",
        slug="dpdpa-guide",
        meta_description="A guide",
        schema_json='{"@type": "Article", "dateModified": "2024-01-01T00:00:00Z"}',
        content_markdown=content,
        word_count=100,
        tier=1,
        cluster_id="dpdpa-core",
        intent_type="informational",
        primary_keyword="compliance"
    )

def test_step1_refresh_audit(mock_decaying_post):
    context = {"emerging_topics": ["AI Consent"]}
    audit = step1_refresh_audit(mock_decaying_post, context)

    assert "The Old Stat Section" in audit["existing_sections"]
    assert "45%" in audit["statistics_found"]
    assert "section 4" in [s.lower() for s in audit["regulatory_references"]]
    assert len(audit["broken_links_simulated"]) == 1
    assert audit["broken_links_simulated"][0] == "https://example.com/broken-page"

@pytest.mark.asyncio
async def test_step2_and_3_targeted_regeneration(mock_decaying_post):
    audit = {
        "existing_sections": ["The Old Stat Section", "Unchanged Section"],
        "broken_links_simulated": ["https://example.com/broken-page"]
    }
    context = {"emerging_topics": ["New AI Rules"]}

    brief = await step2_generate_refresh_brief(audit, context)

    assert "The Old Stat Section" in brief.sections_to_update
    # LLM may expand the raw topic to a richer H2 title; verify non-empty and thematically related
    assert len(brief.new_sections_to_add) > 0
    assert any("ai" in s.lower() or "rule" in s.lower() for s in brief.new_sections_to_add)

    # Run step 3 without an LLM client — exercises the offline fallback path
    new_content = await step3_targeted_regeneration(mock_decaying_post, brief)

    # Offline fallback for sections_to_update produces deterministic marker text
    assert "surgically updated" in new_content
    # The new section's H2 title (whatever the LLM named it) must appear in the content
    assert any(sec in new_content for sec in brief.new_sections_to_add)
    assert "broken-page-fixed" in new_content

    # Crucially, ensure the "Unchanged Section" remains completely mathematically identical
    assert "This section is perfectly fine and should not be touched at all." in new_content

def test_step4_post_refresh_actions(mock_decaying_post):
    res = step4_post_refresh_actions(mock_decaying_post)

    assert res["word_count"] > 0

    current_month = datetime.datetime.now().strftime("%B %Y")
    assert current_month in mock_decaying_post.content_markdown
    assert res["date_modified"] == datetime.date.today().isoformat()

    import json
    schema = json.loads(mock_decaying_post.schema_json)
    assert schema["dateModified"].startswith(str(datetime.datetime.now().year))


@pytest.mark.asyncio
async def test_process_pending_refresh_updates_file_and_queue(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.content_output_dir", str(tmp_path))
    # Remove both keys to guarantee offline/rule-based path throughout
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    blogs = tmp_path / "blogs"
    blogs.mkdir(parents=True)
    post_url = "https://kensara.in/blogs/dpdpa-guide"
    post_path = blogs / "2026-01-01-dpdpa-guide.md"
    post_path.write_text(
        # Section title deliberately contains "statistics" and "deadline" so the
        # rule-based stale_keywords heuristic picks it up without an API key.
        """---
title: "DPDPA Guide"
slug: "dpdpa-guide"
canonical_url: "https://kensara.in/blogs/dpdpa-guide"
primary_keyword: "DPDPA compliance"
cluster: "dpdpa"
intent: "informational"
tier: 1
word_count: 40
date_modified: null
schema_json: "{\\"@type\\": \\"Article\\", \\"dateModified\\": \\"2024-01-01T00:00:00Z\\"}"
---

# DPDPA Guide

## DPDPA Statistics and Compliance Deadlines
This outdated section says compliance is at 45%.

## Stable Section
Do not rewrite this paragraph.
""",
        encoding="utf-8",
    )

    enqueue_refresh(post_url, "dead_post_zero_impressions_30d", priority=2)
    result = await process_pending_refreshes(limit=1, new_context={})

    updated = post_path.read_text(encoding="utf-8")
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert f'date_modified: "{datetime.date.today().isoformat()}"' in updated
    assert "surgically updated" in updated
    assert "Do not rewrite this paragraph." in updated
