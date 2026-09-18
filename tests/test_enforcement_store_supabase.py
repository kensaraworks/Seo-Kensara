"""Supabase round-trip tests for the enforcement store.

These drive the real httpx code path against a local fake PostgREST, so they
catch header, parameter and upsert-semantics regressions that a mocked client
would hide.
"""
from __future__ import annotations

import pytest

from src.store import enforcement_store as store
from tests.fake_postgrest import SERVICE_KEY, FakeSupabase


@pytest.fixture
def supabase(monkeypatch):
    """Point the store at a local fake Supabase for the duration of a test."""
    tables: dict[str, list[dict]] = {store.TABLE: [], store.META_TABLE: []}
    with FakeSupabase(tables) as fake:
        from src.config import settings

        monkeypatch.setattr(settings, "supabase_url", fake.url, raising=False)
        monkeypatch.setattr(settings, "supabase_service_key", SERVICE_KEY, raising=False)
        monkeypatch.setenv("SUPABASE_URL", fake.url)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", SERVICE_KEY)
        store.invalidate_cache()
        yield tables
    store.invalidate_cache()


def test_is_configured_when_credentials_present(supabase):
    from src.db.supabase_client import is_supabase_configured

    assert is_supabase_configured() is True


def test_seed_then_read_back_from_table(supabase):
    result = store.seed_from_bundle()
    assert result["status"] == "seeded"
    assert result["rows_written"] > 0

    # Every bundled row reached the table.
    bundled = store.load_bundled_snapshot()
    expected = sum(len(bundled[section]) for section in store.SECTIONS)
    assert len(supabase[store.TABLE]) == expected

    store.invalidate_cache()
    snapshot = store.load_snapshot(use_cache=False)
    assert snapshot["source"] == "supabase:table"
    assert snapshot["statistics"]["total_all_sections"] == bundled["statistics"]["total_all_sections"]


def test_public_snapshot_excludes_unverified_rows(supabase):
    store.seed_from_bundle()
    store.invalidate_cache()

    snapshot = store.load_snapshot(use_cache=False)
    public = store.public_snapshot(snapshot)

    assert public["statistics"]["pending_review"] > 0
    assert public["statistics"]["total_all_sections"] < snapshot["statistics"]["total_all_sections"]
    for section in store.SECTIONS:
        for action in public[section]:
            assert "[Unconfirmed" not in action["company"]
            assert action["needs_review"] is False


def test_upsert_is_idempotent_on_id(supabase):
    row = {
        "id": "IND-TEST-001",
        "date": "2026-01-01",
        "authority": "CERT-In",
        "company": "Example Ltd",
        "sector": "Fintech",
        "violation_type": "Breach notification failure",
        "summary": "Test row",
        "outcome": "Fine imposed",
        "source_url": "https://example.com/case-1",
    }
    assert store.upsert_actions([row], "cert_in_enforcement") == 1
    assert store.upsert_actions([{**row, "company": "Example Pvt Ltd"}], "cert_in_enforcement") == 1

    stored = [r for r in supabase[store.TABLE] if r["id"] == "IND-TEST-001"]
    assert len(stored) == 1
    assert stored[0]["company"] == "Example Pvt Ltd"


def test_metadata_round_trip(supabase):
    assert store.save_metadata({"last_updated": "2026-02-03", "maintained_by": "KensaraAI"}) is True
    store.invalidate_cache()

    store.upsert_actions(
        [{"id": "IND-TEST-002", "company": "Acme", "outcome": "Fine imposed", "date": "2026-02-01"}],
        "pre_dpdpa_actions",
    )
    store.invalidate_cache()
    snapshot = store.load_snapshot(use_cache=False)
    assert snapshot["metadata"]["last_updated"] == "2026-02-03"


def test_empty_source_url_is_stored_as_null(supabase):
    """A unique index guards source_url, so blanks must not collide."""
    store.upsert_actions(
        [
            {"id": "IND-TEST-003", "company": "A", "source_url": ""},
            {"id": "IND-TEST-004", "company": "B", "source_url": ""},
        ],
        "pre_dpdpa_actions",
    )
    urls = [r["source_url"] for r in supabase[store.TABLE] if r["id"].startswith("IND-TEST-00")]
    assert urls == [None, None]


def test_mark_verified_publishes_a_reviewed_row(supabase):
    store.upsert_actions(
        [
            {
                "id": "IND-TEST-010",
                "company": "[Unconfirmed — see source URL]",
                "authority": "Unknown — needs verification",
                "sector": "[Unconfirmed — needs verification]",
                "violation_type": "[Unconfirmed — needs verification]",
                "summary": "AUTO-DETECTED: something happened",
                "outcome": "[Unconfirmed — needs verification]",
                "source_url": "https://example.com/lead",
                "auto_detected": True,
                "needs_review": True,
            }
        ],
        "pre_dpdpa_actions",
    )
    store.invalidate_cache()

    # Still a placeholder: verifying without supplying real values is refused.
    assert store.mark_verified("IND-TEST-010") is False

    assert store.mark_verified(
        "IND-TEST-010",
        {
            "company": "Real Company Pvt Ltd",
            "authority": "CERT-In",
            "sector": "Fintech",
            "violation_type": "Breach notification failure",
            "summary": "CERT-In directed the company to report a breach within 6 hours.",
            "outcome": "Compliance directed",
        },
    ) is True

    store.invalidate_cache()
    public = store.public_snapshot(store.load_snapshot(use_cache=False))
    assert any(a["id"] == "IND-TEST-010" for a in public["pre_dpdpa_actions"])


def test_unknown_id_cannot_be_verified(supabase):
    assert store.mark_verified("NOPE-999", {"company": "X"}) is False
