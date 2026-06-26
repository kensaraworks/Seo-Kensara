"""Intelligence page router — news feed, trending signals, enforcement tracker."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.ui.dashboard_data import (
    get_scored_news_feed,
    get_recent_relevant_news,
    get_trending_keywords,
    get_recent_enforcement_actions,
    get_enforcement_tracker_meta,
)

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")

_CACHE = Path("drafts/.cache")


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

    return templates.TemplateResponse(
        "intelligence.html",
        {
            "request": request,
            "active_page": "intelligence",
            "now": datetime.now(tz=__import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M IST"),
            "news_scan_last_run": news_scan.get("last_run", "Never"),
            "news_scan_item_count": news_scan.get("item_count", 0),
            "news_scan_status": news_scan.get("status", "unknown"),
            "scored_news": scored_news,
            "news_tracker_items": news_tracker_items,
            "news_tracker_count": len(news_tracker_items),
            "trending_keywords": trending_keywords,
            "enforcement_actions": enforcement_actions,
            "enforcement_meta": enforcement_meta,
        },
    )


@router.get("/enforcement-tracker.html", response_class=HTMLResponse)
async def enforcement_tracker_static_override(request: Request):
    """Dynamic override route to keep static/enforcement-tracker.html up-to-date in real-time."""
    from src.agents.enforcement_tracker import _load_tracker
    from datetime import datetime
    
    try:
        data = _load_tracker()
        dt_str = data.get("metadata", {}).get("last_updated", "")
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            formatted_date = dt.strftime("%d %B %Y")
        except Exception:
            formatted_date = dt_str
            
        stats = data.get("statistics", {})
        
        # Compute dynamic sector counts
        by_sector = stats.get("by_sector", {})
        social_tech = by_sector.get("Social Media / Tech", 0) + by_sector.get("Regulatory", 0)
        healthcare = by_sector.get("Insurance / Healthcare", 0) + by_sector.get("Healthcare", 0)
        fintech = by_sector.get("Payments / Fintech", 0) + by_sector.get("Fintech", 0) + by_sector.get("Banking / Payments", 0)
        gov = by_sector.get("Government", 0)
        
        other_sectors = sum(v for k, v in by_sector.items() if k not in ("Social Media / Tech", "Regulatory", "Insurance / Healthcare", "Healthcare", "Payments / Fintech", "Fintech", "Banking / Payments", "Government"))
        
        return templates.TemplateResponse(
            "enforcement_tracker.html",
            {
                "request": request,
                "enforcement_actions": data.get("enforcement_actions", []),
                "cert_in_enforcement": data.get("cert_in_enforcement", []),
                "pre_dpdpa_actions": data.get("pre_dpdpa_actions", []),
                "stats": stats,
                "last_updated_formatted": formatted_date,
                "social_tech_count": social_tech,
                "healthcare_count": healthcare,
                "fintech_count": fintech,
                "gov_count": gov,
                "other_sectors_count": other_sectors,
            }
        )
    except Exception as exc:
        # Fallback to serving the static file directly if DB fails
        from fastapi.responses import FileResponse
        return FileResponse("static/enforcement-tracker.html")

