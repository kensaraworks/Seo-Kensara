"""Vercel serverless entry point.\n\nVercel's Python runtime picks up the module-level ``app``. The repository\nroot has to be on ``sys.path`` before ``src`` is importable; the application\nitself lives in :mod:`src.asgi`, which the root-level ``app.py`` shares so\nboth entry points behave identically.\n\nThis module must never raise at import: if it does, the platform returns an\nopaque FUNCTION_INVOCATION_FAILED with no indication of the cause."""
from __future__ import annotations

import os
import sys
import traceback

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


# ── Last-resort bootstrap ─────────────────────────────────────────────────────
# Everything below uses only the standard library. If importing the application
# raises — a missing dependency, a file left out of the deployment bundle — the
# platform would otherwise return FUNCTION_INVOCATION_FAILED, which says nothing
# about the cause. This returns a readable 503 and puts the traceback in the
# function log instead.
def _bootstrap_fallback(startup_traceback: str):
    import json as _json

    print("FATAL: could not import the application\n" + startup_traceback, file=sys.stderr)

    async def _fallback_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return

        debug = os.environ.get("DEBUG_STARTUP") == "1"
        if scope.get("path") == "/healthz":
            payload = {"status": "bootstrap_failed", "mode": "bootstrap"}
            if debug:
                payload["traceback"] = startup_traceback
            body = _json.dumps(payload).encode()
            content_type = b"application/json"
        else:
            detail = (
                "<pre style='white-space:pre-wrap;font-size:12px'>"
                + startup_traceback.replace("&", "&amp;").replace("<", "&lt;")
                + "</pre>"
                if debug
                else "<p>Set DEBUG_STARTUP=1 to see the cause here, or read the function log.</p>"
            )
            body = (
                "<!doctype html><html lang='en-IN'><head><meta charset='utf-8'>"
                "<title>KensaraAI — temporarily unavailable</title>"
                "<meta name='robots' content='noindex'></head>"
                "<body style='font-family:system-ui;max-width:48rem;margin:4rem auto;padding:0 1rem'>"
                "<h1>Temporarily unavailable</h1>"
                "<p>The application failed to start.</p>" + detail + "</body></html>"
            ).encode()
            content_type = b"text/html; charset=utf-8"

        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", content_type),
                (b"cache-control", b"no-store"),
                (b"retry-after", b"120"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    return _fallback_app

try:
    from src.asgi import app
except Exception:  # pragma: no cover - only on a broken deployment
    app = _bootstrap_fallback(traceback.format_exc())
