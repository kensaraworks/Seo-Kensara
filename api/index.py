"""Vercel serverless entry point.

Vercel's Python runtime picks up the module-level ``app``. Two things matter
here and nowhere else:

1. The repository root has to be on ``sys.path`` before ``src`` can be imported.
2. If importing the real application fails, the public enforcement tracker must
   still be served. It is the site's main backlink asset, so a bad deploy should
   degrade it, never 404 it.
"""
from __future__ import annotations

import os
import sys
import traceback

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from src.ui.app import app
except Exception:  # pragma: no cover - only runs on a broken deploy
    _STARTUP_TRACEBACK = traceback.format_exc()
    print("FATAL: application import failed\n" + _STARTUP_TRACEBACK, file=sys.stderr)

    import json
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="KensaraAI Content Hub (recovery mode)")

    _SECTIONS = (
        "enforcement_actions",
        "cert_in_enforcement",
        "pre_dpdpa_actions",
        "international_gdpr_fines_india_relevant",
    )

    def _bundled_tracker() -> dict:
        """Bundled dataset with unverified rows stripped.

        The verification gate lives in src.store, which is exactly what failed
        to import here, so the check is repeated inline: recovery mode must not
        become the one path that publishes scraped placeholders.
        """
        try:
            path = Path(_ROOT_DIR) / "data" / "enforcement_tracker.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        def verified(action: dict) -> bool:
            if action.get("_auto_detected") or action.get("_needs_review"):
                return False
            blob = " ".join(
                str(action.get(field, "")) for field in ("company", "authority", "summary", "outcome")
            ).lower()
            return not any(m in blob for m in ("[unconfirmed", "needs verification", "auto-detected"))

        payload = {"metadata": data.get("metadata", {})}
        for section in _SECTIONS:
            payload[section] = [a for a in data.get(section, []) if verified(a)]
        return payload

    @app.get("/healthz")
    async def _healthz() -> JSONResponse:
        # Tracebacks can name internal paths and settings, so they are only
        # returned when someone explicitly asks for them via DEBUG_STARTUP.
        payload = {"status": "startup_failed", "mode": "recovery"}
        if os.getenv("DEBUG_STARTUP") == "1":
            payload["traceback"] = _STARTUP_TRACEBACK
        return JSONResponse(payload, status_code=503)

    @app.get("/enforcement-tracker/data.json")
    async def _dataset() -> JSONResponse:
        data = _bundled_tracker()
        if not data:
            return JSONResponse({"error": "dataset_unavailable"}, status_code=503)
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})

    @app.get("/{full_path:path}")
    async def _recovery_page(full_path: str) -> HTMLResponse:
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
