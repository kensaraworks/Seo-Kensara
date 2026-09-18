"""ASGI application factory with a dependency-free recovery path.

Both entry points (`api/index.py` for Vercel, `app.py` at the root) import `app`
from here, and Vercel's zero-config detection imports this module directly as
well — its traceback showed `/var/task/src/asgi.py` in the import chain, with
neither entry point in it. So this module, not just the entry points, has to be
the thing that cannot raise.

The recovery app is deliberately raw ASGI built from the standard library only.
An earlier version used FastAPI and crashed while being constructed: this module
has `from __future__ import annotations`, so `-> JSONResponse` on a handler is a
string that FastAPI resolves at module scope, where the function-local import of
`JSONResponse` does not exist:

    pydantic.errors.PydanticUndefinedAnnotation: name 'JSONResponse' is not defined

The recovery path failing turns a diagnosable error into an opaque
FUNCTION_INVOCATION_FAILED, which is the worst possible outcome — so it now
depends on nothing that could be missing or misbehaving.
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


def _recovery_body(path: str, startup_traceback: str) -> tuple[int, bytes, bytes]:
    """`(status, content_type, body)` for one recovery request."""
    debug = os.environ.get("DEBUG_STARTUP") == "1"

    if path == "/healthz":
        payload = {"status": "startup_failed", "mode": "recovery"}
        if debug:
            payload["traceback"] = startup_traceback
        return 503, b"application/json", json.dumps(payload).encode()

    if path in ("/enforcement-tracker/data.json", "/enforcement-tracker/data.json/"):
        data = _bundled_tracker()
        if data:
            return 200, b"application/json", json.dumps(data).encode()
        return 503, b"application/json", b'{"error":"dataset_unavailable"}'

    detail = (
        "<pre style='white-space:pre-wrap;font-size:12px'>"
        + startup_traceback.replace("&", "&amp;").replace("<", "&lt;")
        + "</pre>"
        if debug
        else "<p>Set DEBUG_STARTUP=1 to see the cause here, or read the function log.</p>"
    )
    html = (
        "<!doctype html><html lang='en-IN'><head><meta charset='utf-8'>"
        "<title>KensaraAI — temporarily unavailable</title>"
        "<meta name='robots' content='noindex'></head>"
        "<body style='font-family:system-ui;max-width:48rem;margin:4rem auto;padding:0 1rem'>"
        "<h1>Temporarily unavailable</h1>"
        "<p>The application failed to start. The DPDPA enforcement dataset is still "
        "available at <a href='/enforcement-tracker/data.json'>"
        "/enforcement-tracker/data.json</a>.</p>" + detail + "</body></html>"
    )
    return 503, b"text/html; charset=utf-8", html.encode()


def build_recovery_app(startup_traceback: str):
    """Raw ASGI recovery app. Imports nothing beyond the standard library."""
    print("FATAL: application import failed\n" + startup_traceback, file=sys.stderr)

    async def recovery_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return

        status, content_type, body = _recovery_body(scope.get("path", "/"), startup_traceback)
        cache = b"public, max-age=300" if status == 200 else b"no-store"
        headers = [
            (b"content-type", content_type),
            (b"cache-control", cache),
            (b"access-control-allow-origin", b"*"),
        ]
        if status != 200:
            headers.append((b"retry-after", b"120"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return recovery_app


def build_app():
    """The real application, or a recovery app if it cannot be imported."""
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    try:
        from src.ui.app import app

        return app
    except Exception:
        return build_recovery_app(traceback.format_exc())


app = build_app()
