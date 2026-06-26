"""Tests for Module 2.5 — Internal Linking Engine.

Each test creates a fresh temp SQLite file via pytest's `tmp_path` fixture.
This means each test is fully isolated while using a real file-backed
connection — matching how the engine works in production.
"""
import pytest
import sqlite3
import os
from src.engines.internal_linker import (
    get_connection,
    register_post,
    increment_link_counts,
    query_cluster_posts,
    query_pillar_for_cluster,
    query_relevant_tier1_or_2_post,
    validate_optional_link,
    inject_mandatory_links,
    validate_and_inject_optional_links,
    detect_orphans,
    _extract_all_anchors,
    _inject_after_nth_h2,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> str:
    """Return a fresh per-test SQLite DB path."""
    return str(tmp_path / "test_link_map.db")


def _seed(db: str, posts: list[dict]) -> None:
    for p in posts:
        register_post(
            post_url=p["url"],
            post_title=p["title"],
            primary_keyword=p["keyword"],
            cluster_id=p.get("cluster", "dpdpa"),
            intent_type=p.get("intent", "informational"),
            tier=p.get("tier", 2),
            is_pillar=p.get("is_pillar", False),
            db_path=db,
        )


_SAMPLE_MD = """# DPDPA Compliance Software

## What is DPDPA?

The Digital Personal Data Protection Act 2023 is India's landmark privacy law.
Penalties reach ₹250 crore for significant violations.

## How Must Indian Companies Comply?

1. Appoint a DPO.
2. Register with DPBI.

## Which Industries Are Most Affected?

| Industry | Risk |
|---|---|
| Fintech | High |

## How Kensara Helps You Implement DPDPA

Book a free assessment today.

[Book Your Free Assessment](https://kensara.in/book-assessment)
"""


# ---------------------------------------------------------------------------
# 2.5.A — Database Tests
# ---------------------------------------------------------------------------

def test_register_post_creates_record(db):
    register_post(
        post_url="/blog/dpdpa-overview",
        post_title="DPDPA Overview Guide",
        primary_keyword="DPDPA overview",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    conn = get_connection(db)
    row = conn.execute("SELECT * FROM internal_link_map WHERE post_url = ?", ("/blog/dpdpa-overview",)).fetchone()
    conn.close()
    assert row is not None
    assert row["post_title"] == "DPDPA Overview Guide"
    assert row["is_pillar"] == 0


def test_register_post_upserts_on_duplicate(db):
    _seed(db, [{"url": "/blog/test", "title": "Old Title", "keyword": "test kw"}])
    register_post(
        post_url="/blog/test",
        post_title="New Title",
        primary_keyword="test kw",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    conn = get_connection(db)
    rows = conn.execute("SELECT * FROM internal_link_map WHERE post_url = ?", ("/blog/test",)).fetchall()
    conn.close()
    assert len(rows) == 1          # no duplicate row
    assert rows[0]["post_title"] == "New Title"


def test_query_cluster_posts_excludes_same_keyword(db):
    _seed(db, [
        {"url": "/blog/a", "title": "Post A", "keyword": "DPDPA compliance", "cluster": "dpdpa"},
        {"url": "/blog/b", "title": "Post B", "keyword": "DPDPA penalty", "cluster": "dpdpa"},
    ])
    results = query_cluster_posts("dpdpa", exclude_keyword="DPDPA compliance", db_path=db)
    keywords = [r["primary_keyword"] for r in results]
    assert "DPDPA compliance" not in keywords
    assert "DPDPA penalty" in keywords


def test_query_pillar_for_cluster_returns_none_when_missing(db):
    result = query_pillar_for_cluster("no-cluster-exists", db_path=db)
    assert result is None


def test_query_pillar_for_cluster_returns_pillar(db):
    _seed(db, [
        {"url": "/dpdpa", "title": "DPDPA Pillar", "keyword": "DPDPA", "cluster": "dpdpa", "is_pillar": True, "tier": 1},
        {"url": "/blog/c", "title": "Post C", "keyword": "DPDPA fines", "cluster": "dpdpa"},
    ])
    pillar = query_pillar_for_cluster("dpdpa", db_path=db)
    assert pillar is not None
    assert pillar["is_pillar"] == 1
    assert pillar["post_url"] == "/dpdpa"


def test_validate_optional_link_blocks_cannibalization(db):
    _seed(db, [{"url": "/blog/dpdpa", "title": "DPDPA Guide", "keyword": "DPDPA compliance"}])
    result = validate_optional_link("/blog/dpdpa", "DPDPA compliance", db_path=db)
    assert result is None  # same keyword → cannibalization blocked


def test_validate_optional_link_returns_post_when_safe(db):
    _seed(db, [{"url": "/blog/dsar", "title": "DSAR Guide", "keyword": "DSAR processing"}])
    result = validate_optional_link("/blog/dsar", "DPDPA compliance", db_path=db)
    assert result is not None
    assert result["post_url"] == "/blog/dsar"


def test_validate_optional_link_returns_none_for_unknown_url(db):
    result = validate_optional_link("/blog/nonexistent", "any keyword", db_path=db)
    assert result is None


# ---------------------------------------------------------------------------
# 2.5.B — Mandatory Link Injection Rules
# ---------------------------------------------------------------------------

def test_rule1_pillar_link_injected(db):
    """Rule 1: Every cluster post MUST link to its cluster pillar page."""
    _seed(db, [{
        "url": "/dpdpa-compliance-guide", "title": "DPDPA Pillar",
        "keyword": "DPDPA", "cluster": "dpdpa", "is_pillar": True, "tier": 1
    }])
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="DPDPA compliance software",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    assert "/dpdpa-compliance-guide" in updated
    assert "/dpdpa-compliance-guide" in injected


def test_rule2_service_link_consent_keyword(db):
    """Rule 2: consent keywords → expertise service page."""
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="consent management DPDPA",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    assert "kensara.in/expertise" in updated


def test_rule2_service_link_dsar_keyword(db):
    """Rule 2: DSAR keyword → expertise service page."""
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="DSAR automation tool India",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    assert "kensara.in/expertise" in updated


def test_rule3_compare_link_for_commercial_intent(db):
    """Rule 3: commercial intent posts must link to /benefits."""
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="DPDPA compliance software",
        cluster_id="dpdpa",
        intent_type="commercial",
        tier=2,
        db_path=db,
    )
    assert "kensara.in/benefits" in updated or "kensara.in/benefits" in injected


def test_rule3_pricing_link_for_transactional_intent(db):
    """Rule 3: transactional intent posts must also link to /benefits."""
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="buy DPDPA compliance software",
        cluster_id="dpdpa",
        intent_type="transactional",
        tier=2,
        db_path=db,
    )
    assert "kensara.in/benefits" in updated or "kensara.in/benefits" in injected


def test_rule4_tier3_links_to_tier1_or_2(db):
    """Rule 4: Tier 3 newsjack MUST link to most relevant Tier 1/2 post."""
    _seed(db, [
        {"url": "/blog/dpdpa-deep-dive", "title": "DPDPA Deep Dive",
         "keyword": "DPDPA in-depth", "cluster": "dpdpa", "tier": 1},
    ])
    updated, injected = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="DPBI enforcement action today",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=3,
        db_path=db,
    )
    assert "/blog/dpdpa-deep-dive" in injected


def test_no_duplicate_anchor_text_within_post(db):
    """Link equity rule: no two links in same post may share identical anchor text."""
    _seed(db, [{
        "url": "/dpdpa-guide", "title": "Pillar", "keyword": "DPDPA",
        "cluster": "dpdpa", "is_pillar": True, "tier": 1
    }])
    updated, _ = inject_mandatory_links(
        markdown=_SAMPLE_MD,
        keyword="DPDPA compliance software",
        cluster_id="dpdpa",
        intent_type="informational",
        tier=2,
        db_path=db,
    )
    anchors = list(_extract_all_anchors(updated))
    lower_anchors = [a.lower() for a in anchors]
    assert len(lower_anchors) == len(set(lower_anchors)), "Duplicate anchors found"


# ---------------------------------------------------------------------------
# 2.5.C — Orphan Post Detection
# ---------------------------------------------------------------------------

def test_detect_orphans_returns_zero_incoming_posts(db):
    _seed(db, [
        {"url": "/blog/orphan-post", "title": "Orphan", "keyword": "orphan kw", "cluster": "dpdpa"},
    ])
    orphans = detect_orphans(db_path=db)
    orphan_urls = [o["post_url"] for o in orphans]
    assert "/blog/orphan-post" in orphan_urls


def test_detect_orphans_recommends_up_to_3_linkers(db):
    _seed(db, [
        {"url": "/blog/orphan", "title": "Orphan", "keyword": "orphan kw", "cluster": "dpdpa"},
        {"url": "/blog/p1", "title": "Post 1", "keyword": "p1 kw", "cluster": "dpdpa"},
        {"url": "/blog/p2", "title": "Post 2", "keyword": "p2 kw", "cluster": "dpdpa"},
        {"url": "/blog/p3", "title": "Post 3", "keyword": "p3 kw", "cluster": "dpdpa"},
    ])
    # Give p1, p2, p3 at least 1 incoming link each so they are not orphans themselves
    conn = get_connection(db)
    with conn:
        conn.execute(
            "UPDATE internal_link_map SET incoming_link_count=1 WHERE post_url IN ('/blog/p1', '/blog/p2', '/blog/p3')"
        )
    conn.close()

    orphans = detect_orphans(db_path=db)
    orphan = next((o for o in orphans if o["post_url"] == "/blog/orphan"), None)
    assert orphan is not None
    assert 1 <= len(orphan["recommended_linkers"]) <= 3


def test_pillar_posts_are_not_flagged_as_orphans(db):
    _seed(db, [
        {"url": "/dpdpa-pillar", "title": "Pillar", "keyword": "DPDPA",
         "cluster": "dpdpa", "is_pillar": True, "tier": 1},
    ])
    orphans = detect_orphans(db_path=db)
    urls = [o["post_url"] for o in orphans]
    assert "/dpdpa-pillar" not in urls  # pillars are explicitly excluded


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_extract_all_anchors():
    md = "See [DPDPA compliance guide](https://kensara.in/guide) and [DSAR tool](https://kensara.in/dsar)."
    anchors = _extract_all_anchors(md)
    assert "DPDPA compliance guide" in anchors
    assert "DSAR tool" in anchors


def test_inject_after_nth_h2_correct_position():
    md = "## Section One\n\nContent here.\n\n## Section Two\n\nMore content.\n"
    updated, ok = _inject_after_nth_h2(md, "[Link](https://example.com)", n=1)
    assert ok is True
    assert "[Link](https://example.com)" in updated
    assert updated.index("[Link]") > updated.index("## Section One")


def test_inject_after_nth_h2_returns_false_when_section_not_found():
    md = "## Only One Section\n\nContent.\n"
    _, ok = _inject_after_nth_h2(md, "[Link](https://example.com)", n=3)
    assert ok is False
