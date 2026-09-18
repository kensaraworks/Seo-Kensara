"""Intelligence page router — news feed, trending signals, enforcement tracker."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.runtime import now_ist_label
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.ui.dashboard_data import (
    get_scored_news_feed,
    get_recent_relevant_news,
    get_trending_keywords,
    get_recent_enforcement_actions,
    get_enforcement_tracker_meta,
    get_enforcement_review_queue,
)

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
router = APIRouter(tags=["intelligence"])
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


from src.config import settings

_CACHE = Path(settings.content_output_dir) / ".cache"


def _load_job_history() -> dict:
    cache_path = _CACHE / "job_history.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@router.get("/intelligence/", response_class=HTMLResponse)
async def intelligence_page(request: Request) -> HTMLResponse:
    job_history = _load_job_history()
    news_scan = job_history.get("news_scan", {})

    news_tracker_items = get_recent_relevant_news(limit=300, days=7)
    scored_news = news_tracker_items[:8] if news_tracker_items else get_scored_news_feed(job_history, limit=8)
    trending_keywords = get_trending_keywords(limit=20)
    enforcement_actions = get_recent_enforcement_actions(limit=20)
    enforcement_meta = get_enforcement_tracker_meta()
    enforcement_review_queue = get_enforcement_review_queue(limit=25)

    return templates.TemplateResponse(
        "intelligence.html",
        {
            "request": request,
            "active_page": "intelligence",
            "now": now_ist_label(),
            "news_scan_last_run": news_scan.get("last_run", "Never"),
            "news_scan_item_count": news_scan.get("item_count", 0),
            "news_scan_status": news_scan.get("status", "unknown"),
            "scored_news": scored_news,
            "news_tracker_items": news_tracker_items,
            "news_tracker_count": len(news_tracker_items),
            "trending_keywords": trending_keywords,
            "enforcement_actions": enforcement_actions,
            "enforcement_meta": enforcement_meta,
            "enforcement_review_queue": enforcement_review_queue,
            "enforcement_review_count": len(enforcement_review_queue),
        },
    )
