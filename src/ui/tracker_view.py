"""Presentation helpers for the public DPDPA enforcement tracker page.

Kept separate from the router so the same context (and therefore the same
numbers) feeds the HTML page, the WordPress mirror and the JSON dataset.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.store import enforcement_store as store

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "enforcement_tracker.html"


def format_last_updated(value: str) -> str:
    """Render ``2026-08-17`` as ``17 August 2026``; pass anything else through."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
    except (ValueError, TypeError):
        return value or "Unknown"


def canonical_urls() -> tuple[str, str]:
    """``(page_url, dataset_url)`` for the canonical tags and JSON-LD.

    Derived from the WordPress settings so the canonical tag always points at
    the slug the page is actually mirrored to; a canonical pointing at a 404 is
    worse than none at all on the site's main backlink page.
    """
    try:
        from src.config import settings

        base = (settings.wordpress_url or "https://kensara.in").rstrip("/")
        slug = (
            getattr(settings, "wordpress_enforcement_tracker_slug", "") or "enforcement-tracker"
        ).strip("/")
    except Exception:
        base, slug = "https://kensara.in", "enforcement-tracker"
    page = f"{base}/{slug}"
    return page, f"{page}/data.json"


def build_template_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the Jinja context for the tracker template from a snapshot."""
    statistics = snapshot.get("statistics") or {}
    canonical_url, dataset_url = canonical_urls()
    buckets = store.sector_breakdown(statistics)
    metadata = snapshot.get("metadata") or {}

    return {
        "enforcement_actions": snapshot.get("enforcement_actions") or [],
        "cert_in_enforcement": snapshot.get("cert_in_enforcement") or [],
        "pre_dpdpa_actions": snapshot.get("pre_dpdpa_actions") or [],
        "international_actions": snapshot.get("international_gdpr_fines_india_relevant") or [],
        "stats": statistics,
        "metadata": metadata,
        "last_updated_formatted": format_last_updated(metadata.get("last_updated", "")),
        "social_tech_count": buckets["social_tech"],
        "healthcare_count": buckets["healthcare"],
        "fintech_count": buckets["fintech"],
        "gov_count": buckets["gov"],
        "other_sectors_count": buckets["other"],
        "canonical_url": canonical_url,
        "dataset_url": dataset_url,
        "data_source": snapshot.get("source", "unknown"),
    }


def render_tracker_html(snapshot: dict[str, Any]) -> str:
    """Render the tracker to a standalone HTML string (used by the WP mirror)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    context = build_template_context(snapshot)
    # The template takes `request` from Starlette when served over HTTP; the
    # standalone render has no request object and never uses url_for().
    context.setdefault("request", None)
    return env.get_template(TEMPLATE_NAME).render(**context)
