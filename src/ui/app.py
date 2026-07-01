"""KensaraAI Content Hub — FastAPI application."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.main import (
    run_news_scan,
    run_blog_generate,
    run_regulatory_poll,
    run_content_gap_check,
    run_trending_monitors,
    run_feedback_loop_monthly,
)
from src.agents.content_gap_analyzer import run_competitor_intelligence
from src.agents.content_refresher import process_pending_refreshes
from src.analytics.gsc_widgets import (
    get_high_impression_low_ctr_queries,
    get_pages_near_page_one,
    get_zero_impression_posts,
    sync_gsc_query_data_to_db,
)
from src.analytics.search_console import gsc_client, init_gsc_tables
from src.ui.routers import queue, schedule, context_editor, api
from src.ui.routers import intelligence as intelligence_router
from src.ui.routers import strategy as strategy_router
from src.ui.routers import performance as performance_router
from src.ui.routers import geo_monitor as geo_monitor_router
from src.ui.dashboard_data import (
    get_gsc_stat_cards,
    get_pipeline_health,
    get_content_queue_depth,
    get_api_costs,
    get_geo_monitor_summary,
)

log = structlog.get_logger()

# ── Auth ──────────────────────────────────────────────────────────────────────
_AUTH_KEY = "COO@Kensara"
_AUTH_COOKIE = "kensara_auth_session_v2"
# Cookie value is the SHA-256 of the auth key — no server-side storage needed.
_VALID_TOKEN = hashlib.sha256(_AUTH_KEY.encode()).hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow the login page, static assets, and the public enforcement tracker through unauthenticated
        if path == "/auth/login" or path == "/enforcement-tracker.html" or path.startswith("/static"):
            return await call_next(request)
        token = request.cookies.get(_AUTH_COOKIE)
        if token != _VALID_TOKEN:
            return RedirectResponse(url="/auth/login", status_code=302)
        return await call_next(request)


REQUIRED_ENV_VARS = ["GROQ_API_KEY", "NVIDIA_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"]


def _validate_required_env_vars() -> None:
    """Fail fast at startup when required API keys are missing."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(
            "Cannot start KensaraAI SEO Agent. "
            f"Missing required environment variables: {missing}. "
            "Set these in your .env file."
        )


def _ensure_drafts_structure() -> None:
    """Create required drafts directory structure on startup."""
    root = Path(settings.content_output_dir)
    required_paths = [
        root / "blogs",
        root / "linkedin",
        root / "newsletters",
        root / "reports",
        root / ".cache",
    ]
    for path in required_paths:
        path.mkdir(parents=True, exist_ok=True)


def _sync_page_summaries_to_content_performance(page_summaries: list) -> int:
    """Write page-level GSC summaries to content_performance with schema-safe upsert."""
    from src.config import settings_database_path
    db_path = Path(settings_database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(content_performance)").fetchall()}

        for col_sql in (
            "ALTER TABLE content_performance ADD COLUMN post_url TEXT",
            "ALTER TABLE content_performance ADD COLUMN avg_position_30d REAL DEFAULT 0.0",
            "ALTER TABLE content_performance ADD COLUMN avg_ctr_30d REAL DEFAULT 0.0",
            "ALTER TABLE content_performance ADD COLUMN top_query TEXT DEFAULT ''",
            "ALTER TABLE content_performance ADD COLUMN last_checked TEXT",
        ):
            col_name = col_sql.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if col_name not in cols:
                try:
                    conn.execute(col_sql)
                    cols.add(col_name)
                except sqlite3.OperationalError:
                    pass

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_post_url_unique ON content_performance(post_url)"
        )

        updated = 0
        for summary in page_summaries:
            page_url = getattr(summary, "page_url", "")
            if not page_url:
                continue

            conn.execute(
                """
                INSERT INTO content_performance
                    (keyword, post_url, impressions_30d, clicks_30d, avg_position_30d,
                     avg_ctr_30d, top_query, last_checked, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, date('now'), datetime('now'))
                ON CONFLICT(post_url) DO UPDATE SET
                    impressions_30d  = excluded.impressions_30d,
                    clicks_30d       = excluded.clicks_30d,
                    avg_position_30d = excluded.avg_position_30d,
                    avg_ctr_30d      = excluded.avg_ctr_30d,
                    top_query        = excluded.top_query,
                    last_checked     = date('now')
                """,
                (
                    page_url,
                    page_url,
                    getattr(summary, "impressions_30d", 0),
                    getattr(summary, "clicks_30d", 0),
                    getattr(summary, "avg_position_30d", 0.0),
                    getattr(summary, "avg_ctr_30d", 0.0),
                    getattr(summary, "top_query", ""),
                ),
            )
            updated += 1

        conn.commit()
        return updated
    finally:
        conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_required_env_vars()
    _ensure_drafts_structure()
    init_gsc_tables()

    async def run_gsc_sync() -> None:
        if not gsc_client.is_configured():
            log.warning("gsc_sync_skipped_not_configured")
            return
        try:
            log.info("gsc_sync_started")
            page_summaries = gsc_client.get_blog_performance_30d()
            updated_pages = _sync_page_summaries_to_content_performance(page_summaries)

            query_rows = gsc_client.get_query_performance_30d(max_queries=500)
            synced_queries = sync_gsc_query_data_to_db(query_rows)

            log.info(
                "gsc_sync_completed",
                pages=len(page_summaries),
                pages_updated=updated_pages,
                query_rows=synced_queries,
            )
        except Exception as exc:
            log.error("gsc_sync_failed", error=str(exc), exc_info=True)

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    app.state.scheduler = scheduler
    scheduler.add_job(run_news_scan, CronTrigger(hour=8, minute=0), id="news_scan", name="Daily news scan")
    scheduler.add_job(run_blog_generate, CronTrigger(hour=8, minute=15), id="blog_generate", name="Daily blog generation")
    scheduler.add_job(run_regulatory_poll, CronTrigger(hour="*/4", minute=0), id="regulatory_poll", name="Regulatory feed poll")
    scheduler.add_job(
        run_content_gap_check,
        CronTrigger(hour=7, minute=45, timezone="Asia/Kolkata"),
        id="content_gap_check",
        replace_existing=True,
        name="Daily content gap check",
    )
    scheduler.add_job(
        run_competitor_intelligence,
        CronTrigger(day_of_week="mon", hour=6, minute=0, timezone="Asia/Kolkata"),
        id="competitor_intelligence",
        replace_existing=True,
        name="Weekly competitor intelligence",
    )
    scheduler.add_job(
        run_trending_monitors,
        CronTrigger(hour=6, minute=30, timezone="Asia/Kolkata"),
        id="trending_monitor",
        replace_existing=True,
        name="Daily trending monitor",
    )
    scheduler.add_job(
        run_gsc_sync,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone="Asia/Kolkata"),
        id="gsc_sync",
        replace_existing=True,
        name="Weekly GSC sync",
    )
    scheduler.add_job(
        process_pending_refreshes,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone="Asia/Kolkata"),
        id="content_refresh",
        replace_existing=True,
        name="Weekly content refresh queue drain",
    )
    scheduler.add_job(
        run_feedback_loop_monthly,
        CronTrigger(day=1, hour=4, minute=0, timezone="Asia/Kolkata"),
        id="feedback_loop_monthly",
        replace_existing=True,
        name="Monthly content performance feedback loop",
    )
    scheduler.start()
    log.info("seo_agent_started_via_fastapi", jobs=scheduler.get_jobs())
    yield
    scheduler.shutdown()
    log.info("seo_agent_stopped")

app = FastAPI(title="KensaraAI Content Hub", version="1.0.0", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory="src/ui/templates")

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/auth/login", response_class=HTMLResponse)
async def auth_page(request: Request) -> HTMLResponse:
    if request.cookies.get(_AUTH_COOKIE) == _VALID_TOKEN:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("auth.html", {"request": request})


@app.post("/auth/login")
async def auth_submit(auth_key: str = Form(...)) -> JSONResponse:
    if not hmac.compare_digest(auth_key, _AUTH_KEY):
        return JSONResponse({"ok": False}, status_code=401)
    response = JSONResponse({"ok": True, "redirect": "/"})
    response.set_cookie(
        key=_AUTH_COOKIE,
        value=_VALID_TOKEN,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/auth/logout")
async def auth_logout() -> RedirectResponse:
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(_AUTH_COOKIE)
    return response


# ── Mount routers ─────────────────────────────────────────────────────────────
app.include_router(queue.router)
app.include_router(schedule.router)
app.include_router(context_editor.router)
app.include_router(api.router)
app.include_router(intelligence_router.router)
app.include_router(strategy_router.router)
app.include_router(performance_router.router)
app.include_router(geo_monitor_router.router)



# ── Helpers ───────────────────────────────────────────────────────────────────

from src.config import settings
DRAFTS_ROOT = Path(settings.content_output_dir)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML-style frontmatter from a Markdown string."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fm: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # booleans
        if val.lower() == "true":
            val = True  # type: ignore[assignment]
        elif val.lower() == "false":
            val = False  # type: ignore[assignment]
        else:
            # attempt integer
            try:
                val = int(val)  # type: ignore[assignment]
            except ValueError:
                pass
        fm[key] = val
    return fm


def _collect_drafts() -> list[dict]:
    """Walk drafts/ and return list of content item dicts."""
    items: list[dict] = []
    type_map = {
        "blogs": ("blog", "📄"),
        "linkedin": ("linkedin", "📱"),
        "newsletters": ("newsletter", "📧"),
    }
    for folder, (content_type, icon) in type_map.items():
        folder_path = DRAFTS_ROOT / folder
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


def _load_job_history() -> dict:
    cache_path = DRAFTS_ROOT / ".cache" / "job_history.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_activity_log() -> list[dict]:
    """Return last 5 activity entries from activity_log.json."""
    log_path = DRAFTS_ROOT / ".cache" / "activity_log.json"
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        return data[-5:] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# ── Dashboard route ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    from datetime import date, timedelta

    items = _collect_drafts()
    job_history = _load_job_history()
    activity_log = _load_activity_log()

    # ── Content counters ─────────────────────────────────────────────────────
    pending = [i for i in items if i["status"] in ("draft", "pending_review")]
    pending_blogs = sum(1 for i in pending if i["type"] == "blog")
    pending_linkedin = sum(1 for i in pending if i["type"] == "linkedin")
    pending_newsletters = sum(1 for i in pending if i["type"] == "newsletter")
    total_approved = sum(1 for i in items if i["approved"] is True)
    total_rejected = sum(1 for i in items if i["status"] == "rejected")
    total_published = sum(1 for i in items if i["status"] == "published")
    week_ago = str(date.today() - timedelta(days=7))
    this_week = [i for i in items if str(i.get("date", "")) >= week_ago]

    # ── GSC ──────────────────────────────────────────────────────────────────
    gsc_configured = gsc_client.is_configured()
    gsc_widget_1 = get_high_impression_low_ctr_queries() if gsc_configured else []
    gsc_widget_2 = get_pages_near_page_one() if gsc_configured else []
    gsc_widget_3 = get_zero_impression_posts() if gsc_configured else []
    gsc_summary = gsc_client.get_weekly_site_summary() if gsc_configured else {}
    gsc_stat_cards = get_gsc_stat_cards(gsc_summary)

    # ── News scan (for pipeline health) ────────────────────────────────────
    news_scan = job_history.get("news_scan", {})

    # ── Pipeline health ───────────────────────────────────────────────────────
    pipeline_health = get_pipeline_health(
        news_scan_status=news_scan.get("status", "unknown"),
        job_history=job_history,
    )

    # ── Content queue depth ───────────────────────────────────────────────────
    queue_depth = get_content_queue_depth()

    # ── API billing costs ─────────────────────────────────────────────────────
    api_costs = get_api_costs()

    # ── GEO monitor summary ───────────────────────────────────────────────────
    geo_summary = get_geo_monitor_summary(days=30)

    context = {
        "request": request,
        "active_page": "dashboard",
        "now": datetime.now(tz=__import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M IST"),
        # Content counters
        "pending_blogs": pending_blogs,
        "pending_linkedin": pending_linkedin,
        "pending_newsletters": pending_newsletters,
        "total_pending": len(pending),
        "total_approved": total_approved,
        "total_rejected": total_rejected,
        "total_published": total_published,
        "this_week_count": len(this_week),
        "this_week_items": this_week[:5],
        # GSC
        "gsc_configured": gsc_configured,
        "gsc_widget_1": gsc_widget_1,
        "gsc_widget_2": gsc_widget_2,
        "gsc_widget_3": gsc_widget_3,
        "gsc_summary": gsc_summary,
        "gsc_stat_cards": gsc_stat_cards,
        # Pipeline health
        "pipeline_health": pipeline_health,
        # Queue depth
        "queue_depth": queue_depth,
        # API billing
        "api_costs": api_costs,
        # GEO monitor
        "geo_summary": geo_summary,
        # Legacy
        "latest_news": job_history.get("latest_news", []),
        "activity_log": activity_log,
    }
    return templates.TemplateResponse("dashboard.html", context)
