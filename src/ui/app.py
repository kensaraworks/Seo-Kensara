"""KensaraAI Content Hub — FastAPI application."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.main import run_news_scan, run_blog_generate, run_regulatory_poll
from src.ui.routers import queue, schedule, context_editor, api

log = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(run_news_scan, CronTrigger(hour=8, minute=0), id="news_scan", name="Daily news scan")
    scheduler.add_job(run_blog_generate, CronTrigger(hour=8, minute=15), id="blog_generate", name="Daily blog generation")
    scheduler.add_job(run_regulatory_poll, CronTrigger(hour="*/4", minute=0), id="regulatory_poll", name="Regulatory feed poll")
    scheduler.start()
    log.info("seo_agent_started_via_fastapi", jobs=scheduler.get_jobs())
    yield
    scheduler.shutdown()
    log.info("seo_agent_stopped")

app = FastAPI(title="KensaraAI Content Hub", version="1.0.0", lifespan=lifespan)

templates = Jinja2Templates(directory="src/ui/templates")

# ── Mount routers ─────────────────────────────────────────────────────────────
app.include_router(queue.router)
app.include_router(schedule.router)
app.include_router(context_editor.router)
app.include_router(api.router)



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
    items = _collect_drafts()
    job_history = _load_job_history()
    activity_log = _load_activity_log()

    # Counters
    pending = [i for i in items if i["status"] in ("draft", "pending_review")]
    pending_blogs = sum(1 for i in pending if i["type"] == "blog")
    pending_linkedin = sum(1 for i in pending if i["type"] == "linkedin")
    pending_newsletters = sum(1 for i in pending if i["type"] == "newsletter")
    total_approved = sum(1 for i in items if i["approved"] is True)
    total_rejected = sum(1 for i in items if i["status"] == "rejected")
    total_published = sum(1 for i in items if i["status"] == "published")

    # This-week summary (items generated within last 7 days)
    from datetime import date, timedelta
    week_ago = str(date.today() - timedelta(days=7))
    this_week = [i for i in items if str(i.get("date", "")) >= week_ago]

    # Last news scan info
    news_scan = job_history.get("news_scan", {})
    latest_news = job_history.get("latest_news", [])

    context = {
        "request": request,
        "active_page": "dashboard",
        "pending_blogs": pending_blogs,
        "pending_linkedin": pending_linkedin,
        "pending_newsletters": pending_newsletters,
        "total_pending": len(pending),
        "total_approved": total_approved,
        "total_rejected": total_rejected,
        "total_published": total_published,
        "this_week_count": len(this_week),
        "this_week_items": this_week[:5],
        "news_scan_last_run": news_scan.get("last_run", "Never"),
        "news_scan_item_count": news_scan.get("item_count", 0),
        "news_scan_status": news_scan.get("status", "unknown"),
        "latest_news": latest_news,
        "activity_log": activity_log,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M IST"),
    }
    return templates.TemplateResponse("dashboard.html", context)
