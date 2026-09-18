"""Enforcement tracker agent.

Sweeps Indian regulatory sources for new DPDPA / IT Act / CERT-In enforcement
actions and files them into the tracker store.

Discovered rows are written with ``needs_review=True``. They are leads, not
facts: the public page only renders rows a human has confirmed, so a bad search
result can never turn into a published claim about a named company.

All persistence goes through :mod:`src.store.enforcement_store`, which uses
Supabase when configured and falls back to bundled JSON otherwise. Nothing in
this module is imported at application start-up.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from src.store import enforcement_store as store

log = structlog.get_logger()

#: Search queries sent to Tavily to find new enforcement actions.
ENFORCEMENT_SEARCH_QUERIES = [
    "Data Protection Board India enforcement action penalty",
    "DPDPA enforcement fine penalty India",
    "CERT-In breach notification penalty India",
    "IT Act Section 43A data breach fine India",
    "MeitY enforcement action data protection India",
    "India data privacy fine penalty regulatory action",
    "CCI data protection fine India",
    "RBI data localisation enforcement action",
]

#: Sources trusted enough to be worth surfacing to a reviewer.
TRUSTED_DOMAINS = [
    "cert-in.org.in",
    "meity.gov.in",
    "cci.gov.in",
    "rbi.org.in",
    "irdai.gov.in",
    "sebi.gov.in",
    "thehindu.com",
    "economictimes.indiatimes.com",
    "medianama.com",
    "hindustantimes.com",
    "livemint.com",
    "ndtv.com",
    "businessstandard.com",
]

ENFORCEMENT_KEYWORDS = (
    "penalty", "fine", "enforcement", "breach", "violation", "compliance",
    "data protection", "cert-in", "meity", "dpdpa", "it act", "43a",
    "data fiduciary", "board", "cci order", "rbi action",
)

UNCONFIRMED = "[Unconfirmed — needs verification]"


class EnforcementCandidate(BaseModel):
    """A potential new enforcement action found via search."""

    title: str
    url: str
    content_snippet: str
    published_date: str | None
    query_used: str


class ParsedEnforcementAction(BaseModel):
    """A structured enforcement action parsed from a search result."""

    date: str
    authority: str
    company: str
    sector: str
    violation_type: str
    dpdpa_section: str
    summary: str
    penalty_amount: str
    outcome: str
    source_url: str
    notes: str
    confidence: str  # "high" | "medium" | "low"
    needs_verification: bool


# ── Public read helpers ──────────────────────────────────────────────────────

def get_confirmed_precedents(sector: str | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Human-confirmed enforcement precedents, for grounding blog generation.

    Only verified rows are returned — never an auto-detected placeholder. These
    are historical precedent, never a claim that a company is currently
    non-compliant.
    """
    try:
        snapshot = store.public_snapshot()
    except Exception as exc:
        log.warning("enforcement_precedent_load_failed", error=str(exc))
        return []

    confirmed = [
        action
        for action in store.all_actions(snapshot)
        if action.get("company") and not action["company"].startswith("N/A")
    ]

    if sector:
        needle = sector.lower()
        matches = [a for a in confirmed if needle in (a.get("sector") or "").lower()]
        if matches:
            confirmed = matches

    return confirmed[:limit]


def _load_tracker() -> dict[str, Any]:
    """Back-compat shim: the full (unfiltered) tracker snapshot."""
    return store.load_snapshot()


def _save_tracker(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Back-compat shim: persist a full snapshot."""
    return store.save_snapshot(snapshot)


# ── WordPress mirror ─────────────────────────────────────────────────────────

def build_wordpress_page_payload(tracker_data: dict[str, Any], slug: str | None = None) -> dict[str, Any]:
    """Build the payload that creates or updates the tracker page on WordPress."""
    from src.ui.tracker_view import render_tracker_html

    return {
        "title": "DPDPA Enforcement Tracker India",
        "content": render_tracker_html(tracker_data),
        "slug": slug or "enforcement-tracker",
        "status": "publish",
    }


async def _sync_wordpress_enforcement_page(tracker_data: dict[str, Any]) -> dict[str, Any]:
    """Publish or update the tracker page on WordPress. Never raises."""
    from src.config import settings

    if not settings.wordpress_user or not settings.wordpress_app_password:
        return {"status": "skipped", "reason": "wordpress_credentials_not_configured"}

    slug = getattr(settings, "wordpress_enforcement_tracker_slug", "enforcement-tracker")
    payload = build_wordpress_page_payload(tracker_data, slug=slug)
    endpoint = f"{settings.wordpress_url.rstrip('/')}/wp-json/wp/v2/pages"
    auth = httpx.BasicAuth(settings.wordpress_user, settings.wordpress_app_password)

    try:
        async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
            lookup = await client.get(endpoint, params={"slug": slug, "per_page": 1})
            if lookup.status_code >= 400:
                return {
                    "status": "error",
                    "reason": "wordpress_lookup_failed",
                    "status_code": lookup.status_code,
                }

            existing = lookup.json()
            if existing:
                response = await client.post(f"{endpoint}/{existing[0].get('id')}", json=payload)
            else:
                response = await client.post(endpoint, json=payload)

            if response.status_code >= 400:
                return {
                    "status": "error",
                    "reason": "wordpress_publish_failed",
                    "status_code": response.status_code,
                }

            page = response.json()
            return {
                "status": "synced",
                "page_id": page.get("id"),
                "slug": page.get("slug"),
                "url": page.get("link"),
            }
    except Exception as exc:
        log.warning("wordpress_sync_failed", error=str(exc))
        return {"status": "error", "reason": "wordpress_request_failed", "detail": str(exc)}


# ── Discovery ────────────────────────────────────────────────────────────────

async def _search_tavily(query: str, client: httpx.AsyncClient) -> list[EnforcementCandidate]:
    """Search Tavily for enforcement actions. Returns [] when unconfigured."""
    from src.config import settings

    if not settings.tavily_api_key:
        log.warning("tavily_key_not_configured", query=query)
        return []

    try:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "search_depth": "advanced",
                "include_domains": TRUSTED_DOMAINS,
                "max_results": 5,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except httpx.HTTPStatusError as exc:
        log.error("tavily_search_http_error", query=query, status_code=exc.response.status_code)
        return []
    except httpx.TimeoutException:
        log.error("tavily_search_timeout", query=query)
        return []
    except Exception as exc:
        log.error("tavily_search_unexpected_error", query=query, error=str(exc))
        return []

    candidates = [
        EnforcementCandidate(
            title=r.get("title", ""),
            url=r.get("url", ""),
            content_snippet=(r.get("content") or "")[:800],
            published_date=r.get("published_date"),
            query_used=query,
        )
        for r in results
        if r.get("url")
    ]
    log.info("tavily_search_complete", query=query, results_found=len(candidates))
    return candidates


def _detect_authority(text: str) -> str:
    """Best-effort authority attribution from a search snippet."""
    rules = (
        ("CERT-In", ("cert-in",)),
        ("Data Protection Board of India", ("data protection board", "dpbi")),
        ("MeitY", ("meity", "ministry of electronics")),
        ("CCI", ("competition commission", "cci ")),
        ("RBI", ("reserve bank", "rbi ")),
        ("SEBI", ("sebi",)),
        ("IRDAI", ("irdai",)),
    )
    for authority, needles in rules:
        if any(needle in text for needle in needles):
            return authority
    return "Unknown — needs verification"


def _normalize_published_date(raw: str | None) -> str:
    """Coerce a feed date to YYYY-MM-DD, defaulting to today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not raw:
        return today
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return raw[:10] if len(raw) >= 10 else today


def _parse_candidate_to_action(candidate: EnforcementCandidate) -> ParsedEnforcementAction | None:
    """Conservatively turn a search result into a review-queue entry.

    Nothing here is treated as fact. Every substantive field is left explicitly
    unconfirmed so a reviewer must fill it in before the row can be published.
    """
    text = f"{candidate.title} {candidate.content_snippet}".lower()
    if not any(keyword in text for keyword in ENFORCEMENT_KEYWORDS):
        log.debug("candidate_not_enforcement_related", url=candidate.url)
        return None

    return ParsedEnforcementAction(
        date=_normalize_published_date(candidate.published_date),
        authority=_detect_authority(text),
        company="[Unconfirmed — see source URL]",
        sector=UNCONFIRMED,
        violation_type=UNCONFIRMED,
        dpdpa_section=UNCONFIRMED,
        summary=f"AUTO-DETECTED: {candidate.title}. Snippet: {candidate.content_snippet[:300]}",
        penalty_amount=UNCONFIRMED,
        outcome=UNCONFIRMED,
        source_url=candidate.url,
        notes=(
            f"Awaiting review. Found via query: '{candidate.query_used}'. "
            "Every field needs manual verification before this entry is published."
        ),
        confidence="low",
        needs_verification=True,
    )


def _section_for(authority: str) -> str:
    """Route a parsed action into the right tracker section (conservatively)."""
    if authority == "Data Protection Board of India":
        return "enforcement_actions"
    if authority == "CERT-In":
        return "cert_in_enforcement"
    return "pre_dpdpa_actions"


def _build_entry(snapshot: dict[str, Any], action: ParsedEnforcementAction, section: str) -> dict[str, Any]:
    """Materialise a tracker row (with a fresh sequential ID) for a parsed action."""
    entry = {
        "id": store.next_action_id(snapshot, section),
        "section": section,
        "date": action.date,
        "authority": action.authority,
        "company": action.company,
        "sector": action.sector,
        "violation_type": action.violation_type,
        "dpdpa_section": action.dpdpa_section,
        "summary": action.summary,
        "penalty_amount": action.penalty_amount,
        "outcome": action.outcome,
        "source_url": action.source_url,
        "notes": action.notes,
        "auto_detected": True,
        "needs_review": action.needs_verification,
        "confidence": action.confidence,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized = store.normalize_action(entry, section)
    # Keep the in-memory snapshot current so the next generated ID is unique.
    snapshot.setdefault(section, []).append(normalized)
    return normalized


async def update_enforcement_tracker() -> dict[str, Any]:
    """Check for new enforcement actions and update the tracker.

    Returns a summary dict for scheduler and cron logging. Never raises: a
    failed run reports ``status="error"`` rather than taking the caller down.
    """
    log.info("enforcement_tracker_update_started")
    started = datetime.now(timezone.utc)

    try:
        snapshot = store.load_snapshot(use_cache=False)
        existing_urls = store.existing_source_urls(snapshot)

        new_entries: list[dict[str, Any]] = []
        candidates_found = 0
        duplicates_skipped = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            for query in ENFORCEMENT_SEARCH_QUERIES:
                for candidate in await _search_tavily(query, client):
                    candidates_found += 1
                    url_key = candidate.url.strip().lower()
                    if url_key in existing_urls:
                        duplicates_skipped += 1
                        continue

                    parsed = _parse_candidate_to_action(candidate)
                    if parsed is None:
                        continue

                    section = _section_for(parsed.authority)
                    new_entries.append(_build_entry(snapshot, parsed, section))
                    existing_urls.add(url_key)  # prevent a double-add within one run

        rows_written = 0
        for entry in new_entries:
            rows_written += store.upsert_actions([entry], entry["section"])

        snapshot["statistics"] = store.compute_statistics(snapshot)
        metadata = {**snapshot.get("metadata", {}), "last_updated": started.strftime("%Y-%m-%d")}
        snapshot["metadata"] = metadata
        store.save_metadata(metadata)
        store.save_snapshot_to_disk(snapshot)
        store.invalidate_cache()

        # The public page and the WordPress mirror only ever carry verified rows.
        wordpress_sync = await _sync_wordpress_enforcement_page(store.public_snapshot(snapshot))

        summary = {
            "status": "ok",
            "queries_run": len(ENFORCEMENT_SEARCH_QUERIES),
            "candidates_found": candidates_found,
            "duplicates_skipped": duplicates_skipped,
            "new_actions_added": len(new_entries),
            "new_action_ids": [entry["id"] for entry in new_entries],
            "rows_written": rows_written,
            "pending_review": len(store.review_queue(snapshot, limit=10_000)),
            "published_actions": store.public_snapshot(snapshot)["statistics"]["total_all_sections"],
            "supabase_configured": store._supabase()[1],
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "wordpress_sync": wordpress_sync,
        }
    except Exception as exc:
        log.error("enforcement_tracker_update_failed", error=str(exc), exc_info=True)
        return {
            "status": "error",
            "error": str(exc),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    log.info(
        "enforcement_tracker_update_complete",
        **{k: v for k, v in summary.items() if k not in ("new_action_ids", "wordpress_sync")},
    )
    if summary["new_actions_added"]:
        log.warning(
            "new_enforcement_actions_need_review",
            count=summary["new_actions_added"],
            ids=summary["new_action_ids"],
            message="Auto-detected leads. They stay off the public page until verified.",
        )
    return summary


if __name__ == "__main__":  # manual run: python -m src.agents.enforcement_tracker
    print(json.dumps(asyncio.run(update_enforcement_tracker()), indent=2))
