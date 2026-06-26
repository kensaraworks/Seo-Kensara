"""GEO monitor page router — detailed AI visibility results."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.ui.dashboard_data import get_geo_monitor_summary, get_geo_monitor_details

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")


@router.get("/geo-monitor/", response_class=HTMLResponse)
async def geo_monitor_page(request: Request) -> HTMLResponse:
    days_raw = request.query_params.get("days", "30")
    engine = request.query_params.get("engine", "all").strip()
    mentioned = request.query_params.get("mentioned", "0")

    try:
        days = int(days_raw)
    except ValueError:
        days = 30
    if days not in (7, 30, 90):
        days = 30

    selected_engine = None if not engine or engine == "all" else engine
    mentioned_only = mentioned == "1"

    summary = get_geo_monitor_summary(days=days, engine=selected_engine, mentioned_only=mentioned_only)
    details = get_geo_monitor_details(days=days, limit=200, engine=selected_engine, mentioned_only=mentioned_only)

    engine_options = sorted({r["engine"] for r in summary.get("engine_breakdown", [])})

    return templates.TemplateResponse(
        "geo_monitor.html",
        {
            "request": request,
            "active_page": "geo_monitor",
            "now": datetime.now(tz=__import__("zoneinfo", fromlist=["ZoneInfo"]).ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"),
            "geo_summary": summary,
            "geo_rows": details["rows"],
            "geo_top_queries": details["top_queries"],
            "geo_top_competitors": details["top_competitors"],
            "geo_days": days,
            "geo_selected_engine": engine,
            "geo_mentioned_only": mentioned_only,
            "geo_engine_options": engine_options,
        },
    )
