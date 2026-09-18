"""Public DPDPA enforcement tracker routes.

This is the site's main backlink asset, so every handler here is written to
degrade instead of failing: a Supabase outage falls back to bundled JSON, and a
template error still returns a readable page rather than a 500.

Routes
    GET  /enforcement-tracker.html          the public page (also the WP mirror source)
    GET  /dpdpa-enforcement-tracker         canonical alias used in the page's own <link>
    GET  /enforcement-tracker/data.json     open dataset advertised in the JSON-LD
    GET  /api/v1/enforcement/actions        JSON API with filters
    GET  /api/v1/enforcement/review-queue   unverified leads (dashboard, authenticated)
    POST /api/v1/enforcement/verify/{id}    promote a reviewed lead to the public page
    GET  /api/cron/enforcement-tracker      Vercel Cron entry point (CRON_SECRET)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.store import enforcement_store as store
from src.ui.tracker_view import build_template_context

log = structlog.get_logger()

router = APIRouter(tags=["enforcement-tracker"])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

#: Served from the edge cache for 10 minutes, and stale copies may be served for
#: a day while a fresh one is fetched — a crawler never waits on Supabase.
_PUBLIC_CACHE_CONTROL = "public, max-age=300, s-maxage=600, stale-while-revalidate=86400"

#: Paths that must stay reachable without the dashboard login.
PUBLIC_PATHS = frozenset(
    {
        "/enforcement-tracker.html",
        "/dpdpa-enforcement-tracker",
        "/enforcement-tracker/data.json",
        "/api/v1/enforcement/actions",
        "/api/cron/enforcement-tracker",
    }
)


def _fallback_page(error: str) -> HTMLResponse:
    """Minimal always-renderable page, used only if the template itself fails."""
    log.error("enforcement_tracker_render_failed", error=error)
    return HTMLResponse(
        "<!doctype html><html lang='en-IN'><head><meta charset='utf-8'>"
        "<title>DPDPA Enforcement Tracker India — KensaraAI</title>"
        "<meta name='robots' content='noindex'>"
        "<link rel='canonical' href='https://kensara.in/dpdpa-enforcement-tracker'></head>"
        "<body style='font-family:system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem'>"
        "<h1>DPDPA Enforcement Tracker</h1>"
        "<p>The tracker is being refreshed and will be back shortly. "
        "The dataset stays available at "
        "<a href='/enforcement-tracker/data.json'>/enforcement-tracker/data.json</a>.</p>"
        "</body></html>",
        status_code=503,
        headers={"Cache-Control": "no-store", "Retry-After": "120"},
    )


def _render_tracker(request: Request) -> HTMLResponse:
    """Render the public page from verified rows only."""
    try:
        snapshot = store.public_snapshot()
        context = build_template_context(snapshot)
        response = templates.TemplateResponse(
            "enforcement_tracker.html",
            {"request": request, **context},
        )
        response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
        return response
    except Exception as exc:
        return _fallback_page(str(exc))


@router.get("/enforcement-tracker.html", response_class=HTMLResponse)
async def enforcement_tracker_page(request: Request) -> HTMLResponse:
    return _render_tracker(request)


@router.get("/dpdpa-enforcement-tracker", response_class=HTMLResponse)
async def enforcement_tracker_canonical(request: Request) -> HTMLResponse:
    """Canonical path. The page's own <link rel=canonical> points here, so it
    has to serve the page rather than redirect to the .html variant."""
    return _render_tracker(request)


@router.get("/enforcement-tracker/data.json")
async def enforcement_tracker_dataset() -> JSONResponse:
    """Open dataset behind the page's schema.org ``DataDownload`` distribution.

    Publishing the raw data is deliberate: it is what earns citations from
    researchers and journalists, which is the point of the tracker.
    """
    try:
        snapshot = store.public_snapshot()
        payload: dict[str, Any] = {
            "metadata": snapshot.get("metadata", {}),
            "statistics": snapshot.get("statistics", {}),
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "KensaraAI DPDPA Enforcement Tracker — https://kensara.in/dpdpa-enforcement-tracker",
        }
        for section in store.SECTIONS:
            payload[section] = snapshot.get(section, [])
        return JSONResponse(
            payload,
            headers={
                "Cache-Control": _PUBLIC_CACHE_CONTROL,
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as exc:
        log.error("enforcement_dataset_failed", error=str(exc))
        return JSONResponse({"error": "dataset_unavailable"}, status_code=503)


@router.get("/api/v1/enforcement/actions")
async def enforcement_actions_api(
    section: str | None = Query(None, description="Restrict to one tracker section"),
    sector: str | None = Query(None, description="Case-insensitive sector substring"),
    authority: str | None = Query(None, description="Case-insensitive authority substring"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """Filtered JSON view over the verified actions."""
    try:
        snapshot = store.public_snapshot()
        if section:
            if section not in store.SECTIONS:
                return JSONResponse(
                    {"error": "unknown_section", "valid_sections": list(store.SECTIONS)},
                    status_code=400,
                )
            actions = list(snapshot.get(section) or [])
        else:
            actions = store.all_actions(snapshot)

        if sector:
            actions = [a for a in actions if sector.lower() in (a.get("sector") or "").lower()]
        if authority:
            actions = [a for a in actions if authority.lower() in (a.get("authority") or "").lower()]

        total = len(actions)
        return JSONResponse(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "returned": len(actions[offset : offset + limit]),
                "actions": actions[offset : offset + limit],
                "last_updated": snapshot.get("metadata", {}).get("last_updated", ""),
            },
            headers={"Cache-Control": _PUBLIC_CACHE_CONTROL, "Access-Control-Allow-Origin": "*"},
        )
    except Exception as exc:
        log.error("enforcement_actions_api_failed", error=str(exc))
        return JSONResponse({"error": "actions_unavailable"}, status_code=503)


# ── Authenticated review workflow ────────────────────────────────────────────

@router.get("/api/v1/enforcement/review-queue")
async def enforcement_review_queue(limit: int = Query(100, ge=1, le=1000)) -> JSONResponse:
    """Auto-detected leads awaiting human verification."""
    try:
        pending = store.review_queue(limit=limit)
        return JSONResponse({"count": len(pending), "actions": pending})
    except Exception as exc:
        log.error("enforcement_review_queue_failed", error=str(exc))
        return JSONResponse({"error": "review_queue_unavailable"}, status_code=503)


@router.post("/api/v1/enforcement/verify/{action_id}")
async def enforcement_verify(action_id: str, updates: dict[str, Any] = Body(default={})) -> JSONResponse:
    """Apply a reviewer's corrections and publish the row."""
    try:
        if store.mark_verified(action_id, updates):
            return JSONResponse({"status": "verified", "id": action_id})
        return JSONResponse(
            {
                "status": "rejected",
                "id": action_id,
                "reason": "unknown id, or placeholder fields are still unfilled",
            },
            status_code=400,
        )
    except Exception as exc:
        log.error("enforcement_verify_failed", id=action_id, error=str(exc))
        return JSONResponse({"error": "verify_failed", "detail": str(exc)}, status_code=500)


# ── Cron ─────────────────────────────────────────────────────────────────────

def _cron_authorized(request: Request) -> bool:
    """Vercel Cron sends ``Authorization: Bearer $CRON_SECRET``.

    When no secret is configured the endpoint stays closed rather than open —
    an unauthenticated refresh would let anyone burn the Tavily quota.
    """
    secret = os.getenv("CRON_SECRET", "")
    if not secret:
        return False
    header = request.headers.get("authorization", "")
    return header == f"Bearer {secret}" or request.headers.get("x-cron-secret", "") == secret


@router.get("/api/cron/enforcement-tracker")
async def enforcement_tracker_cron(request: Request) -> JSONResponse:
    """Weekly refresh entry point for Vercel Cron."""
    if not _cron_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from src.agents.enforcement_tracker import update_enforcement_tracker

    summary = await update_enforcement_tracker()
    status_code = 200 if summary.get("status") == "ok" else 500
    return JSONResponse(summary, status_code=status_code, headers={"Cache-Control": "no-store"})
