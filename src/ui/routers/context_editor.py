"""Context editor router — edit KensaraAI platform stats injected into all content."""
from __future__ import annotations

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
    "breach_clocks_managed": 0,
    "clients_onboarded": 0,
    "compliance_score_avg": 0,
    "countries_covered": 3,
    "modules_active": 3,
    "pricing_inr_low": 1500000,
    "pricing_inr_high": 4000000,
    "competitor_price_inr": 7500000,
    "incubators": "MeitY GENESIS EIR 2.0, IITG TIC",
    "demo_url": "https://kensara.in/request-demo",
    "azure_region": "Azure India (Central India)",
    "custom_note": "",
}


def _load_stats() -> dict:
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        # Merge defaults so new fields always present
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
    return templates.TemplateResponse(
        "context_editor.html",
        {
            "request": request,
            "active_page": "context",
            "stats": stats,
        },
    )


@router.post("/save", response_class=JSONResponse)
async def save_context(
    dsars_processed: int = Form(0),
    consents_recorded: int = Form(0),
    breach_clocks_managed: int = Form(0),
    clients_onboarded: int = Form(0),
    compliance_score_avg: int = Form(0),
    countries_covered: int = Form(3),
    modules_active: int = Form(3),
    pricing_inr_low: int = Form(1500000),
    pricing_inr_high: int = Form(4000000),
    competitor_price_inr: int = Form(7500000),
    incubators: str = Form("MeitY GENESIS EIR 2.0, IITG TIC"),
    demo_url: str = Form("https://kensara.in/request-demo"),
    azure_region: str = Form("Azure India (Central India)"),
    custom_note: str = Form(""),
) -> JSONResponse:
    stats = {
        "dsars_processed": dsars_processed,
        "consents_recorded": consents_recorded,
        "breach_clocks_managed": breach_clocks_managed,
        "clients_onboarded": clients_onboarded,
        "compliance_score_avg": compliance_score_avg,
        "countries_covered": countries_covered,
        "modules_active": modules_active,
        "pricing_inr_low": pricing_inr_low,
        "pricing_inr_high": pricing_inr_high,
        "competitor_price_inr": competitor_price_inr,
        "incubators": incubators.strip(),
        "demo_url": demo_url.strip(),
        "azure_region": azure_region.strip(),
        "custom_note": custom_note.strip(),
    }
    try:
        _save_stats(stats)
        log.info("platform_stats_saved", clients=clients_onboarded, dsars=dsars_processed)
        return JSONResponse({"ok": True, "message": "Platform context saved successfully."})
    except OSError as exc:
        log.error("platform_stats_save_error", error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
