"""ASGI application factory with a recovery fallback.

Both entry points (`api/index.py` for Vercel, `app.py` at the root) import
`app` from here, because Vercel's zero-config detection may pick either one —
it chose the root `app.py` on the first deploy, which bypassed the recovery
handling that lived only in `api/index.py`. Keeping one implementation means
whichever file the platform picks behaves the same.

If the real application cannot be imported, a recovery app takes its place and
still serves the enforcement tracker dataset. That page is the site's main
backlink asset; a bad deploy should degrade it, never 404 it.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

SECTIONS = (
    "enforcement_actions",
    "cert_in_enforcement",
    "pre_dpdpa_actions",
    "international_gdpr_fines_india_relevant",
)

UNVERIFIED_MARKERS = ("[unconfirmed", "needs verification", "auto-detected")


def _bundled_tracker() -> dict:
    """Bundled dataset with unverified rows stripped.

    The verification gate lives in src.store, which is exactly what failed to
    import if we are here, so the check is repeated inline: recovery mode must
    not become the one path that publishes scraped placeholders.
    """
    try:
        data = json.loads((ROOT_DIR / "data" / "enforcement_tracker.json").read_text(encoding="utf-8"))
    except Exception:
        return {}

    def verified(action: dict) -> bool:
        if action.get("_auto_detected") or action.get("_needs_review"):
            return False
        if action.get("auto_detected") or action.get("needs_review"):
            return False
        blob = " ".join(
            str(action.get(field, "")) for field in ("company", "authority", "summary", "outcome")
        ).lower()
        return not any(marker in blob for marker in UNVERIFIED_MARKERS)

    payload: dict = {"metadata": data.get("metadata", {})}
    for section in SECTIONS:
        payload[section] = [a for a in data.get(section, []) if verified(a)]
    return payload


def _build_recovery_app(startup_traceback: str):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="KensaraAI Content Hub (recovery mode)")

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        # Tracebacks name internal paths and settings, so they are returned only
        # when someone explicitly asks via DEBUG_STARTUP.
        payload = {"status": "startup_failed", "mode": "recovery"}
        if os.getenv("DEBUG_STARTUP") == "1":
            payload["traceback"] = startup_traceback
        return JSONResponse(payload, status_code=503)

    @app.get("/enforcement-tracker/data.json")
    async def dataset() -> JSONResponse:
        data = _bundled_tracker()
        if not data:
            return JSONResponse({"error": "dataset_unavailable"}, status_code=503)
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})

    @app.get("/{full_path:path}")
    async def recovery_page(full_path: str) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='en-IN'><head><meta charset='utf-8'>"
            "<title>KensaraAI — temporarily unavailable</title>"
            "<meta name='robots' content='noindex'></head>"
            "<body style='font-family:system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem'>"
            "<h1>Temporarily unavailable</h1>"
            "<p>The service is being redeployed. The DPDPA enforcement dataset is still "
            "available at <a href='/enforcement-tracker/data.json'>"
            "/enforcement-tracker/data.json</a>.</p></body></html>",
            status_code=503,
            headers={"Retry-After": "300", "Cache-Control": "no-store"},
        )

    return app


def build_app():
    """The real application, or a recovery app if it cannot be imported."""
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    try:
        from src.ui.app import app

        return app
    except Exception:
        startup_traceback = traceback.format_exc()
        print("FATAL: application import failed\n" + startup_traceback, file=sys.stderr)
        return _build_recovery_app(startup_traceback)


app = build_app()
