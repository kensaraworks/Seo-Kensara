"""KensaraAI platform stats — live loader from platform_stats.json.

The JSON file is written by the Context & Setup page in the KensaraAI Hub.
This module provides get_platform_stats() which reads from disk on every call
so that UI changes take effect on the next generation run without a server restart.
"""
import json
from pathlib import Path

_STATS_PATH = Path("drafts/.cache/platform_stats.json")

_DEFAULTS: dict = {
    "dsars_processed": 0,
    "consents_recorded": 0,
    "breach_clocks_started": 0,
    "clients_onboarded": 0,
    "compliance_score_avg": 0,
    "countries_covered": 3,
    "pricing_inr_low": 1500000,
    "pricing_inr_high": 4000000,
    "competitor_price_inr": 7500000,
    "incubators": "MeitY GENESIS EIR 2.0, IITG TIC",
    "demo_url": "https://www.kensara.in/book-demo",
    "azure_region": "Azure India (Central + South)",
    "tagline": "India's first AI-native DPDPA + GDPR + GRC compliance platform",
    "news_max_age_days": 90,
    "keyword_rotation": [],
    "custom_note": "",
    "last_updated": "2026-06-27",
}


def get_platform_stats() -> dict:
    """Load from platform_stats.json (written by UI). Falls back to hardcoded defaults.

    Called fresh on every generation run so UI edits take effect immediately.
    """
    try:
        data = json.loads(_STATS_PATH.read_text(encoding="utf-8"))
        return {**_DEFAULTS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)


# Backward-compatible module-level alias — builder.py and other callers that
# do `from src.context.platform_stats import PLATFORM_STATS` still work.
# Prefer get_platform_stats() for fresh reads inside generation functions.
PLATFORM_STATS: dict = _DEFAULTS
