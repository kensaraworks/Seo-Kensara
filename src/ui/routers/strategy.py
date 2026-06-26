"""Strategy page router — content gaps, keyword rankings, seasonal calendar, drafts."""
from __future__ import annotations

from datetime import datetime, date, timedelta
import json
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import settings
from src.engines.content_calendar import (
    build_calendar_window,
    detect_content_gap,
    capacity_alert_payload,
    CalendarAction,
)
from src.ui.dashboard_data import (
    get_monday_brief,
    get_competitor_gaps_from_db,
    get_latest_rankings,
    get_ranking_summary,
    get_upcoming_seasonal_windows,
    get_this_week_drafts,
)

router = APIRouter()
templates = Jinja2Templates(directory="src/ui/templates")

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_fm(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v.lower() == "true":
            v = True
        elif v.lower() == "false":
            v = False
        else:
            try:
                v = int(v)
            except ValueError:
                pass
        fm[k] = v
    return fm


def get_all_drafts_by_date() -> dict[str, list[dict[str, Any]]]:
    DRAFTS_ROOT = Path(settings.content_output_dir)
    type_map = {
        "blogs": ("blog", "📄"),
        "linkedin": ("linkedin", "📱"),
        "newsletters": ("newsletter", "📧"),
    }

    drafts_by_date: dict[str, list[dict[str, Any]]] = {}
    for folder, (content_type, icon) in type_map.items():
        folder_path = DRAFTS_ROOT / folder
        if not folder_path.exists():
            continue
        for md_file in folder_path.glob("*.md"):
            try:
                date_str = None
                if len(md_file.name) >= 10:
                    try:
                        date.fromisoformat(md_file.name[:10])
                        date_str = md_file.name[:10]
                    except ValueError:
                        pass

                text = md_file.read_text(encoding="utf-8")
                fm = _parse_fm(text)

                if not date_str:
                    created = fm.get("date_created") or fm.get("date", "")
                    if created and len(created) >= 10:
                        date_str = created[:10]

                if not date_str:
                    date_str = date.fromtimestamp(md_file.stat().st_mtime).isoformat()

                item = {
                    "filename": md_file.name,
                    "folder": folder,
                    "type": content_type,
                    "icon": icon,
                    "title": fm.get("title", md_file.stem),
                    "status": fm.get("status", "draft"),
                    "approved": fm.get("approved", False),
                    "date": date_str,
                    "word_count": fm.get("word_count", 0),
                    "tier": fm.get("tier", 0),
                }
                drafts_by_date.setdefault(date_str, []).append(item)
            except Exception:
                continue
    return drafts_by_date


@router.get("/strategy/", response_class=HTMLResponse)
async def strategy_page(request: Request, tab: str = "overview") -> HTMLResponse:
    monday_brief = get_monday_brief()
    content_gaps = get_competitor_gaps_from_db(limit=15)
    if not content_gaps and monday_brief["top_content_gaps"]:
        content_gaps = monday_brief["top_content_gaps"]

    rankings = get_latest_rankings()
    ranking_summary = get_ranking_summary(rankings)
    seasonal_windows = get_upcoming_seasonal_windows(days_ahead=180)
    this_week_items, this_week_count = get_this_week_drafts()

    # --- Calendar Tab Calculations ---
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())
    start_date = current_monday - timedelta(days=7)  # Starts previous week Monday

    slots = build_calendar_window(start_date=start_date, days=28)
    drafts_by_date = get_all_drafts_by_date()

    all_items = []
    for d_items in drafts_by_date.values():
        all_items.extend(d_items)
    pending_count = sum(1 for i in all_items if i["status"] in ("draft", "pending_review"))

    weeks = []
    for w_idx in range(4):
        w_slots = slots[w_idx * 7 : (w_idx + 1) * 7]
        week_start = start_date + timedelta(days=w_idx * 7)
        week_end = week_start + timedelta(days=6)

        week_items_count = 0
        days_list = []
        for slot in w_slots:
            slot_date_str = slot.run_date.isoformat()
            slot_drafts = drafts_by_date.get(slot_date_str, [])

            has_content = slot.action not in (CalendarAction.SKIP, CalendarAction.NEWSLETTER_DIGEST)
            if has_content or slot_drafts:
                week_items_count += max(1, len(slot_drafts))

            days_list.append({
                "date": slot.run_date,
                "date_str": slot_date_str,
                "is_today": slot.run_date == today,
                "slot": slot,
                "drafts": slot_drafts,
                "day_name": slot.run_date.strftime("%A"),
                "short_date": slot.run_date.strftime("%b %d"),
            })

        cadence_ok = week_items_count >= 2

        weeks.append({
            "label": f"Week of {week_start.strftime('%B %d, %Y')}",
            "start": week_start,
            "end": week_end,
            "days": days_list,
            "posts_count": week_items_count,
            "cadence_ok": cadence_ok,
            "is_current": week_start <= today <= week_end,
        })

    # Content gap alert
    from src.queue.job_queue import job_queue
    top_gap_keywords = job_queue.get_top_competitor_gaps(limit=3)
    next_7_slots = build_calendar_window(start_date=today, days=7)
    gap_alert = detect_content_gap(
        scheduled_slots=next_7_slots,
        pending_count=pending_count,
        top_gap_keywords=top_gap_keywords,
        start_date=today,
    )
    gap_alert_dict = gap_alert.to_dict() if gap_alert else None

    # Capacity Alert
    capacity_alert = capacity_alert_payload(pending_count)

    return templates.TemplateResponse(
        "strategy.html",
        {
            "request": request,
            "active_page": "strategy",
            "active_tab": tab,
            "now": datetime.now(tz=__import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M IST"),
            "monday_brief": monday_brief,
            "content_gaps": content_gaps,
            "rankings": rankings,
            "ranking_summary": ranking_summary,
            "seasonal_windows": seasonal_windows,
            "this_week_items": this_week_items,
            "this_week_count": this_week_count,
            "weeks": weeks,
            "gap_alert": gap_alert_dict,
            "capacity_alert": capacity_alert,
        },
    )

