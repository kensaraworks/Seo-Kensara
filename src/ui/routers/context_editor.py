"""Context editor router — edit KensaraAI platform stats injected into all content."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

log = structlog.get_logger()

router = APIRouter(prefix="/context", tags=["context"])
templates = Jinja2Templates(directory="src/ui/templates")

DRAFTS_ROOT = Path("drafts")
STATS_PATH = DRAFTS_ROOT / ".cache" / "platform_stats.json"

DEFAULT_STATS: dict = {
    "dsars_processed": 0,
    "consents_recorded": 0,
    "breach_clocks_started": 0,
    "clients_onboarded": 0,
    "compliance_score_avg": 0,
    "countries_covered": 3,
    "pricing_inr_low": 1500000,
    "pricing_inr_high": 4000000,
    "competitor_price_inr": 7500000,
    "tagline": "India's first AI-native DPDPA + GDPR + GRC compliance platform",
    "incubators": "MeitY GENESIS EIR 2.0, IITG TIC",
    "demo_url": "https://www.kensara.in/book-demo",
    "azure_region": "Azure India (Central + South)",
    "news_max_age_days": 90,
    "keyword_rotation": [],
    "custom_note": "",
}


def _load_stats() -> dict:
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_STATS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_STATS)


def _save_stats(stats: dict) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def context_editor_view(request: Request) -> HTMLResponse:
    stats = _load_stats()
    kw_list = stats.get("keyword_rotation", [])
    stats["keyword_rotation_text"] = "\n".join(kw_list) if isinstance(kw_list, list) else str(kw_list)
    return templates.TemplateResponse(
        "context_editor.html",
        {"request": request, "active_page": "context", "stats": stats},
    )


@router.post("/save", response_class=JSONResponse)
async def save_context(
    dsars_processed: int = Form(0),
    consents_recorded: int = Form(0),
    breach_clocks_started: int = Form(0),
    clients_onboarded: int = Form(0),
    compliance_score_avg: int = Form(0),
    countries_covered: int = Form(3),
    pricing_inr_low: int = Form(1500000),
    pricing_inr_high: int = Form(4000000),
    competitor_price_inr: int = Form(7500000),
    tagline: str = Form("India's first AI-native DPDPA + GDPR + GRC compliance platform"),
    incubators: str = Form("MeitY GENESIS EIR 2.0, IITG TIC"),
    demo_url: str = Form("https://www.kensara.in/book-demo"),
    azure_region: str = Form("Azure India (Central + South)"),
    news_max_age_days: int = Form(90),
    keyword_rotation_text: str = Form(""),
    custom_note: str = Form(""),
) -> JSONResponse:
    keyword_rotation = [k.strip() for k in keyword_rotation_text.splitlines() if k.strip()]
    stats = {
        "dsars_processed": dsars_processed,
        "consents_recorded": consents_recorded,
        "breach_clocks_started": breach_clocks_started,
        "clients_onboarded": clients_onboarded,
        "compliance_score_avg": compliance_score_avg,
        "countries_covered": countries_covered,
        "pricing_inr_low": pricing_inr_low,
        "pricing_inr_high": pricing_inr_high,
        "competitor_price_inr": competitor_price_inr,
        "tagline": tagline.strip(),
        "incubators": incubators.strip(),
        "demo_url": demo_url.strip(),
        "azure_region": azure_region.strip(),
        "news_max_age_days": news_max_age_days,
        "keyword_rotation": keyword_rotation,
        "custom_note": custom_note.strip(),
        "last_updated": datetime.date.today().isoformat(),
    }
    try:
        _save_stats(stats)
        log.info("platform_stats_saved", clients=clients_onboarded, dsars=dsars_processed)
        return JSONResponse({"ok": True, "message": "Platform context saved successfully."})
    except OSError as exc:
        log.error("platform_stats_save_error", error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
