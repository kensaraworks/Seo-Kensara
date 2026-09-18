"""KensaraAI Content Hub — FastAPI application.

Import discipline
    Nothing at module scope may pull in the scraping, LLM or Google API stack.
    Those live behind lazy imports inside route handlers and the scheduler, so a
    Vercel cold start only pays for FastAPI, Jinja2 and httpx — and a missing
    optional dependency degrades one page instead of taking the site down.

Routers are registered defensively: if one fails to import, it is recorded and
skipped, and ``/healthz`` reports it. The public enforcement tracker is
registered first so the site's main backlink page survives any other breakage.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from src.runtime import (
    IST,
    APP_VERSION,
    deployment_info,
    is_serverless,
    now_ist,
    now_ist_label,
    platform_name,
)

log = structlog.get_logger()

_ROOT_DIR = Path(__file__).resolve().parents[2]
_STATIC_DIR = _ROOT_DIR / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

#: Populated by _include_routers(); surfaced by /healthz.
ROUTER_ERRORS: dict[str, str] = {}


# ── Auth ──────────────────────────────────────────────────────────────────────

_AUTH_KEY = "COO@Kensara"
_AUTH_COOKIE = "kensara_auth_session_v2"
# The cookie value is the SHA-256 of the auth key — no server-side session store.
_VALID_TOKEN = hashlib.sha256(_AUTH_KEY.encode()).hexdigest()

#: Reachable without the dashboard login. The tracker paths are added from the
#: tracker router so the two lists cannot drift apart.
_PUBLIC_PREFIXES = ("/static", "/uploads", "/api/cron/", "/enforcement-tracker")
_PUBLIC_PATHS = {
    "/auth/login",
    "/healthz",
    "/robots.txt",
    "/sitemap.xml",
    "/favicon.ico",
}

try:
    from src.ui.routers.tracker import PUBLIC_PATHS as _TRACKER_PUBLIC_PATHS

    _PUBLIC_PATHS |= set(_TRACKER_PUBLIC_PATHS)
except Exception as exc:  # pragma: no cover - import guard
    log.warning("tracker_public_paths_unavailable", error=str(exc))


def is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Cookie gate for the dashboard. Public SEO routes pass straight through."""

    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path):
            return await call_next(request)
        if request.cookies.get(_AUTH_COOKIE) != _VALID_TOKEN:
            return RedirectResponse(url="/auth/login", status_code=302)
        return await call_next(request)


# ── Lifespan ──────────────────────────────────────────────────────────────────

def _ensure_drafts_structure() -> None:
    """Create the drafts tree. A read-only filesystem is not an error."""
    from src.config import settings

    try:
        root = Path(settings.content_output_dir)
        for name in ("blogs", "linkedin", "newsletters", "reports", "flagged", ".cache"):
            (root / name).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.info("drafts_structure_skipped", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = None
    app.state.started_at = now_ist().isoformat()

    _ensure_drafts_structure()

    if is_serverless():
        # No durable disk and no long-lived process: scheduled work runs through
        # Vercel Cron against /api/cron/* instead of an in-process scheduler.
        log.info("serverless_startup", platform=platform_name(), version=APP_VERSION)
        yield
        return

    try:
        from src.analytics.search_console import init_gsc_tables

        init_gsc_tables()
    except Exception as exc:
        log.warning("init_gsc_tables_failed", error=str(exc))

    try:
        from src.ui.scheduler import build_scheduler

        app.state.scheduler = build_scheduler()
    except Exception as exc:
        log.error("scheduler_startup_failed", error=str(exc))

    yield

    if app.state.scheduler is not None:
        try:
            app.state.scheduler.shutdown()
            log.info("scheduler_stopped")
        except Exception as exc:
            log.warning("scheduler_shutdown_failed", error=str(exc))


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(title="KensaraAI Content Hub", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(AuthMiddleware)

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:  # pragma: no cover - only if the deploy bundle excludes static/
    log.warning("static_dir_missing", path=str(_STATIC_DIR))


def _mount_uploads(application: FastAPI) -> None:
    """Serve uploaded banners from the writable drafts tree.

    They cannot be written into the static bundle: it is read-only on
    serverless and replaced on every deploy.
    """
    from src.config import settings

    uploads_dir = Path(settings.content_output_dir) / "uploads"
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.info("uploads_dir_unavailable", path=str(uploads_dir), error=str(exc))
        return
    application.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


_mount_uploads(app)


#: Ordered by importance — the public tracker is registered first on purpose.
_ROUTER_MODULES = (
    "src.ui.routers.tracker",
    "src.ui.routers.api",
    "src.ui.routers.queue",
    "src.ui.routers.schedule",
    "src.ui.routers.context_editor",
    "src.ui.routers.intelligence",
    "src.ui.routers.strategy",
    "src.ui.routers.performance",
    "src.ui.routers.geo_monitor",
)


def _include_routers(application: FastAPI) -> None:
    for module_name in _ROUTER_MODULES:
        try:
            module = importlib.import_module(module_name)
            application.include_router(module.router)
        except Exception as exc:
            ROUTER_ERRORS[module_name] = f"{type(exc).__name__}: {exc}"
            log.error("router_registration_failed", module=module_name, error=str(exc))
    if ROUTER_ERRORS:
        log.warning("routers_degraded", failed=sorted(ROUTER_ERRORS))
    else:
        log.info("routers_registered", count=len(_ROUTER_MODULES))


_include_routers(app)


# ── Health & SEO endpoints ────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness plus a readable summary of what is and is not wired up."""
    payload: dict[str, Any] = {
        "status": "ok" if not ROUTER_ERRORS else "degraded",
        "version": APP_VERSION,
        "platform": platform_name(),
        "serverless": is_serverless(),
        "deployment": deployment_info(),
        "python": sys.version.split()[0],
        "router_errors": ROUTER_ERRORS,
    }

    try:
        from src.config import SETTINGS_ERRORS

        if SETTINGS_ERRORS:
            payload["status"] = "degraded"
            payload["settings_errors"] = SETTINGS_ERRORS
    except Exception as exc:
        payload["settings_errors"] = [f"could not read settings: {exc}"]

    try:
        from src.db.supabase_client import is_supabase_configured

        payload["supabase_configured"] = is_supabase_configured()
    except Exception as exc:
        payload["supabase_configured"] = False
        payload["supabase_error"] = str(exc)

    try:
        from src.store import enforcement_store as store

        snapshot = store.load_snapshot()
        public = store.public_snapshot(snapshot)
        payload["enforcement_tracker"] = {
            "source": snapshot.get("source"),
            "published_actions": public["statistics"]["total_all_sections"],
            "pending_review": public["statistics"].get("pending_review", 0),
            "last_updated": snapshot.get("metadata", {}).get("last_updated", ""),
        }
    except Exception as exc:
        payload["status"] = "degraded"
        payload["enforcement_tracker"] = {"error": str(exc)}

    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> PlainTextResponse:
    """Let crawlers have the tracker and the dataset; keep the dashboard out."""
    from src.ui.tracker_view import canonical_urls

    page_url, _ = canonical_urls()
    base = page_url.rsplit("/", 1)[0]
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /enforcement-tracker.html",
            "Allow: /dpdpa-enforcement-tracker",
            "Allow: /enforcement-tracker/data.json",
            "Disallow: /auth/",
            "Disallow: /queue/",
            "Disallow: /schedule/",
            "Disallow: /context/",
            "Disallow: /api/",
            "Allow: /api/v1/enforcement/actions",
            "",
            f"Sitemap: {base}/sitemap.xml",
            "",
        ]
    )
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
async def sitemap() -> Response:
    """Single-entry sitemap for the public tracker page."""
    from src.store import enforcement_store as store
    from src.ui.tracker_view import canonical_urls

    page_url, _ = canonical_urls()
    try:
        last_updated = store.load_snapshot().get("metadata", {}).get("last_updated", "")
    except Exception:
        last_updated = ""

    lastmod = f"    <lastmod>{last_updated}</lastmod>\n" if last_updated else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{page_url}</loc>\n"
        f"{lastmod}"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>0.9</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return Response(
        xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import FileResponse

    icon = _STATIC_DIR / "images" / "kensara-icon.ico"
    if icon.exists():
        return FileResponse(str(icon), headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=204)


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/auth/login", response_class=HTMLResponse)
async def auth_page(request: Request):
    if request.cookies.get(_AUTH_COOKIE) == _VALID_TOKEN:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("auth.html", {"request": request})


@app.post("/auth/login")
async def auth_submit(auth_key: str = Form(...)) -> JSONResponse:
    if not hmac.compare_digest(auth_key, _AUTH_KEY):
        return JSONResponse({"ok": False}, status_code=401)
    response = JSONResponse({"ok": True, "redirect": "/"})
    response.set_cookie(key=_AUTH_COOKIE, value=_VALID_TOKEN, httponly=True, samesite="lax")
    return response


@app.get("/auth/logout")
async def auth_logout() -> RedirectResponse:
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(_AUTH_COOKIE)
    return response


# ── Dashboard helpers ─────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _drafts_root() -> Path:
    from src.config import settings

    return Path(settings.content_output_dir)


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML-style frontmatter from a Markdown string."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        value: Any = raw.strip().strip('"').strip("'")
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                pass
        fields[key.strip()] = value
    return fields


def _collect_drafts() -> list[dict]:
    """Walk drafts/ and return the content items backing the dashboard counters."""
    items: list[dict] = []
    type_map = {
        "blogs": ("blog", "\U0001F4C4"),
        "linkedin": ("linkedin", "\U0001F4F1"),
        "newsletters": ("newsletter", "\U0001F4E7"),
        "flagged": ("blog", "\U0001F6A9"),
    }
    root = _drafts_root()
    for folder, (content_type, icon) in type_map.items():
        folder_path = root / folder
        if not folder_path.exists():
            continue
        for md_file in sorted(folder_path.glob("*.md"), reverse=True):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("draft_read_error", path=str(md_file), error=str(exc))
                continue
            fm = _parse_frontmatter(text)
            items.append(
                {
                    "filename": md_file.name,
                    "folder": folder,
                    "type": content_type,
                    "icon": icon,
                    "title": fm.get("title", md_file.stem),
                    "status": fm.get("status", "draft"),
                    "approved": fm.get("approved", False),
                    "date": fm.get("date", ""),
                    "primary_keyword": fm.get("primary_keyword", ""),
                    "word_count": fm.get("word_count", 0),
                    "meta_description": fm.get("meta_description", ""),
                    "model": fm.get("model", ""),
                    "path": str(md_file),
                }
            )
    return items


def _supabase_rows(table: str, **kwargs) -> list[dict]:
    """Query Supabase, returning [] when it is not configured or errors."""
    try:
        from src.db.supabase_client import SupabaseDB, is_supabase_configured

        if not is_supabase_configured():
            return []
        return SupabaseDB.select_sync(table, **kwargs) or []
    except Exception as exc:
        log.warning("supabase_query_failed", table=table, error=str(exc))
        return []


def _load_job_history() -> dict:
    rows = _supabase_rows("job_history", order="run_at.desc", limit=20)
    if rows:
        return {row.get("job_id") or "unknown": row for row in rows}
    try:
        return json.loads((_drafts_root() / ".cache" / "job_history.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_activity_log() -> list[dict]:
    """The five most recent activity entries."""
    rows = _supabase_rows("activity_log", order="created_at.desc", limit=5)
    if rows:
        return [
            {
                "timestamp": (row.get("details") or {}).get("timestamp") or (row.get("created_at") or "")[:16],
                "action": row.get("action", ""),
                "title": row.get("item", ""),
                "type": (row.get("details") or {}).get("type", "system"),
            }
            for row in rows
        ]
    try:
        data = json.loads((_drafts_root() / ".cache" / "activity_log.json").read_text(encoding="utf-8"))
        return data[-5:] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _gsc_context() -> dict:
    """Search Console widgets, or empty values when GSC is unavailable."""
    empty = {
        "gsc_configured": False,
        "gsc_widget_1": [],
        "gsc_widget_2": [],
        "gsc_widget_3": [],
        "gsc_summary": {},
        "gsc_stat_cards": [],
    }
    try:
        from src.analytics.gsc_widgets import (
            get_high_impression_low_ctr_queries,
            get_pages_near_page_one,
            get_zero_impression_posts,
        )
        from src.analytics.search_console import gsc_client
        from src.ui.dashboard_data import get_gsc_stat_cards

        if not gsc_client.is_configured():
            return empty

        summary = gsc_client.get_weekly_site_summary()
        return {
            "gsc_configured": True,
            "gsc_widget_1": get_high_impression_low_ctr_queries(),
            "gsc_widget_2": get_pages_near_page_one(),
            "gsc_widget_3": get_zero_impression_posts(),
            "gsc_summary": summary,
            "gsc_stat_cards": get_gsc_stat_cards(summary),
        }
    except Exception as exc:
        log.warning("gsc_context_failed", error=str(exc))
        return empty


# ── Dashboard route ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from datetime import date, timedelta

    items = _collect_drafts()
    job_history = _load_job_history()

    pending = [i for i in items if i["status"] in ("draft", "pending_review")]
    week_ago = str(date.today() - timedelta(days=7))
    this_week = [i for i in items if str(i.get("date", "")) >= week_ago]

    context: dict[str, Any] = {
        "request": request,
        "active_page": "dashboard",
        "now": now_ist_label(),
        "pending_blogs": sum(1 for i in pending if i["type"] == "blog"),
        "pending_linkedin": sum(1 for i in pending if i["type"] == "linkedin"),
        "pending_newsletters": sum(1 for i in pending if i["type"] == "newsletter"),
        "total_pending": len(pending),
        "total_approved": sum(1 for i in items if i["approved"] is True),
        "total_rejected": sum(1 for i in items if i["status"] == "rejected"),
        "total_published": sum(1 for i in items if i["status"] == "published"),
        "total_flagged": sum(1 for i in items if i["status"] == "flagged"),
        "this_week_count": len(this_week),
        "this_week_items": this_week[:5],
        "latest_news": job_history.get("latest_news", []),
        "activity_log": _load_activity_log(),
        **_gsc_context(),
    }

    # Every widget below is optional: a failure degrades one card, not the page.
    try:
        from src.ui.dashboard_data import (
            get_api_costs,
            get_content_queue_depth,
            get_geo_monitor_summary,
            get_pipeline_health,
        )

        context["pipeline_health"] = get_pipeline_health(
            news_scan_status=job_history.get("news_scan", {}).get("status", "unknown"),
            job_history=job_history,
        )
        context["queue_depth"] = get_content_queue_depth()
        context["api_costs"] = get_api_costs()
        context["geo_summary"] = get_geo_monitor_summary(days=30)
    except Exception as exc:
        log.warning("dashboard_widgets_failed", error=str(exc))
        context.setdefault("pipeline_health", {})
        context.setdefault("queue_depth", {})
        context.setdefault("api_costs", {})
        context.setdefault("geo_summary", {})

    return templates.TemplateResponse("dashboard.html", context)
