"""Search Performance page router — GSC widgets, refresh queue, source health."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.analytics.gsc_widgets import (
    get_high_impression_low_ctr_queries,
    get_pages_near_page_one,
    get_zero_impression_posts,
)
from src.analytics.search_console import gsc_client
from src.ui.dashboard_data import (
    get_pending_refresh_items,
    get_refresh_queue_count,
    get_source_health,
)

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")


@router.get("/performance/", response_class=HTMLResponse)
async def performance_page(request: Request) -> HTMLResponse:
    gsc_configured = gsc_client.is_configured()
    gsc_widget_1 = get_high_impression_low_ctr_queries() if gsc_configured else []
    gsc_widget_2 = get_pages_near_page_one() if gsc_configured else []
    gsc_widget_3 = get_zero_impression_posts() if gsc_configured else []

    refresh_items = get_pending_refresh_items(limit=20)
    refresh_count = get_refresh_queue_count()
    source_health = get_source_health(limit=20)

    return templates.TemplateResponse(
        "performance.html",
        {
            "request": request,
            "active_page": "performance",
            "now": datetime.now(tz=__import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M IST"),
            "gsc_configured": gsc_configured,
            "gsc_widget_1": gsc_widget_1,
            "gsc_widget_2": gsc_widget_2,
            "gsc_widget_3": gsc_widget_3,
            "refresh_items": refresh_items,
            "refresh_count": refresh_count,
            "source_health": source_health,
        },
    )
