"""Web-layer tests for the public enforcement tracker.

The tracker is the site's main backlink asset, so these assert the properties
that matter for that: it is reachable without logging in, it never publishes
unverified rows, and it degrades to a readable page instead of a 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.store import enforcement_store as store
from src.ui.app import _AUTH_COOKIE, _VALID_TOKEN, app


@pytest.fixture
def client():
    store.invalidate_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    store.invalidate_cache()


@pytest.fixture
def authed_client():
    store.invalidate_cache()
    with TestClient(app, cookies={_AUTH_COOKIE: _VALID_TOKEN}, raise_server_exceptions=False) as c:
        yield c
    store.invalidate_cache()


# ── Public access ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/enforcement-tracker.html",
        "/dpdpa-enforcement-tracker",
        "/enforcement-tracker/data.json",
        "/api/v1/enforcement/actions",
        "/robots.txt",
        "/sitemap.xml",
        "/healthz",
    ],
)
def test_public_paths_do_not_require_auth(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_dashboard_still_requires_auth(client):
    """Unauthenticated GETs are answered with the login page in place (401).

    They used to redirect, which looped forever on Vercel when the path came
    back rewritten: the redirect target failed the same check and redirected
    again. Serving the page in place cannot loop.
    """
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 401
    assert "auth_key" in response.text


def test_authentication_never_redirects(client):
    for path in ("/", "/queue/", "/schedule/", "/intelligence/", "/api/index"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code not in (301, 302, 303, 307, 308), f"{path} redirected"


def test_review_queue_requires_auth(client):
    assert client.get("/api/v1/enforcement/review-queue", follow_redirects=False).status_code == 401


def test_unauthenticated_writes_are_rejected_not_rendered(client):
    response = client.post("/api/v1/enforcement/verify/ANY", json={}, follow_redirects=False)
    assert response.status_code == 401
    assert response.json()["ok"] is False


# ── Page contents ─────────────────────────────────────────────────────────────

def test_tracker_page_renders_without_placeholder_rows(client):
    html = client.get("/enforcement-tracker.html").text
    assert "DPDPA Enforcement Tracker" in html
    assert "[Unconfirmed" not in html
    assert "AUTO-DETECTED" not in html


def test_tracker_page_carries_seo_metadata(client):
    html = client.get("/enforcement-tracker.html").text
    assert '<link rel="canonical"' in html
    assert '"@type": "Dataset"' in html
    # The headline count must be the number of rows actually published.
    published = store.public_snapshot()["statistics"]["total_all_sections"]
    assert f"{published} verified cases tracked" in html


def test_tracker_page_is_edge_cacheable(client):
    cache_control = client.get("/enforcement-tracker.html").headers.get("cache-control", "")
    assert "s-maxage" in cache_control


def test_canonical_alias_serves_the_page_not_a_redirect(client):
    response = client.get("/dpdpa-enforcement-tracker", follow_redirects=False)
    assert response.status_code == 200
    assert "DPDPA Enforcement Tracker" in response.text


# ── Dataset & API ─────────────────────────────────────────────────────────────

def test_dataset_is_public_and_verified_only(client):
    response = client.get("/enforcement-tracker/data.json")
    payload = response.json()

    assert response.headers["access-control-allow-origin"] == "*"
    assert payload["license"].startswith("https://creativecommons.org/")
    for section in store.SECTIONS:
        for action in payload[section]:
            assert action["needs_review"] is False
            assert "[Unconfirmed" not in action["company"]


def test_actions_api_pagination_and_filters(client):
    total = client.get("/api/v1/enforcement/actions").json()["total"]

    page = client.get("/api/v1/enforcement/actions?limit=2&offset=1").json()
    assert page["returned"] <= 2
    assert page["total"] == total

    filtered = client.get("/api/v1/enforcement/actions?authority=cert-in").json()
    assert all("cert-in" in a["authority"].lower() for a in filtered["actions"])


def test_actions_api_rejects_unknown_section(client):
    response = client.get("/api/v1/enforcement/actions?section=not_a_section")
    assert response.status_code == 400
    assert "valid_sections" in response.json()


def test_actions_api_validates_limit(client):
    assert client.get("/api/v1/enforcement/actions?limit=0").status_code == 422
    assert client.get("/api/v1/enforcement/actions?limit=99999").status_code == 422


# ── Resilience ────────────────────────────────────────────────────────────────

def test_page_degrades_to_503_instead_of_raising(client, monkeypatch):
    """If the snapshot itself blows up, visitors get a page, not a stack trace."""
    monkeypatch.setattr(
        store, "public_snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    response = client.get("/enforcement-tracker.html")
    assert response.status_code == 503
    assert "DPDPA Enforcement Tracker" in response.text
    assert "db down" not in response.text  # no internals leaked to visitors


def test_healthz_reports_tracker_state(client):
    payload = client.get("/healthz").json()
    assert payload["status"] in ("ok", "degraded")
    assert payload["router_errors"] == {}
    assert payload["enforcement_tracker"]["published_actions"] > 0


# ── Cron ──────────────────────────────────────────────────────────────────────

def test_cron_is_closed_without_a_secret(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/api/cron/enforcement-tracker").status_code == 401


def test_cron_rejects_a_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")
    response = client.get(
        "/api/cron/enforcement-tracker", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_cron_accepts_the_configured_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")
    response = client.get(
        "/api/cron/enforcement-tracker", headers={"Authorization": "Bearer right"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ── Authenticated review workflow ─────────────────────────────────────────────

def test_review_queue_lists_unverified_rows(authed_client):
    payload = authed_client.get("/api/v1/enforcement/review-queue").json()
    assert payload["count"] > 0
    for action in payload["actions"]:
        assert not store.is_verified(action)


def test_verify_rejects_an_unknown_id(authed_client):
    response = authed_client.post("/api/v1/enforcement/verify/DOES-NOT-EXIST", json={})
    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
