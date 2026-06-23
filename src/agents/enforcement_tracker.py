"""Enforcement tracker agent — monitors for new DPDPA/IT Act enforcement actions and updates the tracker database."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()

TRACKER_PATH = Path("data/enforcement_tracker.json")

# Search queries sent to Tavily to find new enforcement actions
ENFORCEMENT_SEARCH_QUERIES = [
    "Data Protection Board India enforcement action penalty 2025 2026",
    "DPDPA enforcement fine penalty India",
    "CERT-In breach notification penalty India",
    "IT Act Section 43A data breach fine India",
    "MeitY enforcement action data protection India",
    "India data privacy fine penalty regulatory action",
    "CCI data protection fine India",
    "RBI data localisation enforcement action",
]


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
    confidence: str  # "high" | "medium" | "low" — for CEO review
    needs_verification: bool


def _load_tracker() -> dict[str, Any]:
    """Load the enforcement tracker JSON from disk."""
    if not TRACKER_PATH.exists():
        raise FileNotFoundError(
            f"Enforcement tracker not found at {TRACKER_PATH}. "
            "Ensure data/enforcement_tracker.json exists."
        )
    with TRACKER_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    log.info(
        "enforcement_tracker_loaded",
        pre_dpdpa_count=len(data.get("pre_dpdpa_actions", [])),
        cert_in_count=len(data.get("cert_in_enforcement", [])),
        dpdpa_count=len(data.get("enforcement_actions", [])),
    )
    return data


def _save_tracker(data: dict[str, Any]) -> None:
    """Save the enforcement tracker JSON to disk, updating metadata."""
    data["metadata"]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACKER_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("enforcement_tracker_saved", path=str(TRACKER_PATH))


def _collect_all_source_urls(tracker_data: dict[str, Any]) -> set[str]:
    """Collect all source URLs already in the tracker to avoid duplicates."""
    urls: set[str] = set()
    for section_key in ("enforcement_actions", "pre_dpdpa_actions", "cert_in_enforcement"):
        for action in tracker_data.get(section_key, []):
            url = action.get("source_url", "")
            if url:
                urls.add(url.strip().lower())
    return urls


def _generate_next_id(tracker_data: dict[str, Any], section: str) -> str:
    """Generate a new sequential ID for an enforcement action."""
    prefix_map = {
        "enforcement_actions": "IND-DPDPA",
        "pre_dpdpa_actions": "IND-IT43A",
        "cert_in_enforcement": "CERT",
    }
    prefix = prefix_map.get(section, "IND")
    existing = tracker_data.get(section, [])
    # Find max numeric suffix among existing IDs for this prefix
    max_num = 0
    for action in existing:
        action_id = action.get("id", "")
        if action_id.startswith(prefix):
            try:
                parts = action_id.rsplit("-", 1)
                num = int(parts[-1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass
    return f"{prefix}-{max_num + 1:03d}"


async def _search_tavily(query: str, client: httpx.AsyncClient) -> list[EnforcementCandidate]:
    """
    Search Tavily API for enforcement actions matching the query.
    Returns empty list if Tavily key is not configured.
    """
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
                "include_domains": [
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
                ],
                "max_results": 5,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        candidates = []
        for r in results:
            candidates.append(
                EnforcementCandidate(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    content_snippet=r.get("content", "")[:800],
                    published_date=r.get("published_date"),
                    query_used=query,
                )
            )
        log.info(
            "tavily_search_complete",
            query=query,
            results_found=len(candidates),
        )
        return candidates
    except httpx.HTTPStatusError as exc:
        log.error(
            "tavily_search_http_error",
            query=query,
            status_code=exc.response.status_code,
            detail=str(exc),
        )
        return []
    except httpx.TimeoutException:
        log.error("tavily_search_timeout", query=query)
        return []
    except Exception as exc:
        log.error("tavily_search_unexpected_error", query=query, error=str(exc))
        return []


def _is_duplicate(candidate: EnforcementCandidate, existing_urls: set[str]) -> bool:
    """Check if a search result URL is already tracked."""
    return candidate.url.strip().lower() in existing_urls


def _parse_candidate_to_action(candidate: EnforcementCandidate) -> ParsedEnforcementAction | None:
    """
    Best-effort parse of a Tavily search result snippet into a structured enforcement action.
    Marks uncertain fields for CEO review.
    This is intentionally conservative — better to flag for human review than insert bad data.
    """
    snippet = candidate.content_snippet.lower()
    title = candidate.title.lower()

    # Quick relevance filter — must mention enforcement-related terms
    enforcement_keywords = [
        "penalty", "fine", "enforcement", "breach", "violation", "compliance",
        "data protection", "cert-in", "meity", "dpdpa", "it act", "43a",
        "data fiduciary", "board", "cci order", "rbi action",
    ]
    if not any(kw in snippet or kw in title for kw in enforcement_keywords):
        log.debug("candidate_not_enforcement_related", url=candidate.url)
        return None

    # Determine authority
    authority = "Unknown — needs verification"
    if "cert-in" in snippet or "cert-in" in title:
        authority = "CERT-In"
    elif "data protection board" in snippet or "dpbi" in snippet:
        authority = "Data Protection Board of India"
    elif "meity" in snippet or "ministry of electronics" in snippet:
        authority = "MeitY"
    elif "cci" in snippet or "competition commission" in snippet:
        authority = "CCI"
    elif "rbi" in snippet or "reserve bank" in snippet:
        authority = "RBI"
    elif "sebi" in snippet:
        authority = "SEBI"
    elif "irdai" in snippet:
        authority = "IRDAI"

    # Determine date
    date_str = candidate.published_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if candidate.published_date:
        try:
            # Normalise to YYYY-MM-DD
            parsed = datetime.fromisoformat(candidate.published_date.replace("Z", "+00:00"))
            date_str = parsed.strftime("%Y-%m-%d")
        except ValueError:
            date_str = candidate.published_date[:10] if len(candidate.published_date) >= 10 else date_str

    return ParsedEnforcementAction(
        date=date_str,
        authority=authority,
        company="[Unconfirmed — see source URL]",
        sector="[Unconfirmed — needs verification]",
        violation_type="[Unconfirmed — needs verification]",
        dpdpa_section="[Unconfirmed — needs verification]",
        summary=f"AUTO-DETECTED: {candidate.title}. Snippet: {candidate.content_snippet[:300]}",
        penalty_amount="[Unconfirmed — needs verification]",
        outcome="[Unconfirmed — needs verification]",
        source_url=candidate.url,
        notes=f"FLAGGED FOR CEO REVIEW. Found via query: '{candidate.query_used}'. All fields need manual verification before this entry is considered accurate.",
        confidence="low",
        needs_verification=True,
    )


def _append_action_to_tracker(
    tracker_data: dict[str, Any],
    action: ParsedEnforcementAction,
    section: str = "pre_dpdpa_actions",
) -> str:
    """Append a new enforcement action to the tracker. Returns the new ID."""
    new_id = _generate_next_id(tracker_data, section)
    entry = {
        "id": new_id,
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
        "_auto_detected": True,
        "_needs_review": action.needs_verification,
        "_confidence": action.confidence,
        "_detected_at": datetime.now(timezone.utc).isoformat(),
    }
    tracker_data.setdefault(section, []).append(entry)
    log.info(
        "new_enforcement_action_added",
        id=new_id,
        authority=action.authority,
        source_url=action.source_url,
        confidence=action.confidence,
        needs_review=action.needs_verification,
    )
    return new_id


def _recalculate_statistics(tracker_data: dict[str, Any]) -> None:
    """Recalculate and update the statistics block in the tracker."""
    all_actions = (
        tracker_data.get("enforcement_actions", [])
        + tracker_data.get("pre_dpdpa_actions", [])
        + tracker_data.get("cert_in_enforcement", [])
    )

    by_sector: dict[str, int] = {}
    by_violation: dict[str, int] = {}
    by_outcome: dict[str, int] = {}

    for action in all_actions:
        sector = action.get("sector", "Unknown")
        by_sector[sector] = by_sector.get(sector, 0) + 1

        vtype = action.get("violation_type", "Unknown")
        by_violation[vtype] = by_violation.get(vtype, 0) + 1

        outcome = action.get("outcome", "Unknown")
        # Group outcomes broadly
        if "fine" in outcome.lower() or "penalty" in outcome.lower():
            key = "Fine imposed"
        elif "ban" in outcome.lower():
            key = "Business ban"
        elif "ongoing" in outcome.lower() or "investigation" in outcome.lower():
            key = "Investigation ongoing"
        elif "compliance" in outcome.lower():
            key = "Compliance achieved"
        elif "enacted" in outcome.lower() or "drafted" in outcome.lower():
            key = "Law enacted / Rule drafted"
        else:
            key = outcome[:50]
        by_outcome[key] = by_outcome.get(key, 0) + 1

    stats = tracker_data.setdefault("statistics", {})
    stats["total_enforcement_actions"] = len(tracker_data.get("enforcement_actions", []))
    stats["total_pre_dpdpa_actions"] = len(tracker_data.get("pre_dpdpa_actions", []))
    stats["total_cert_in_actions"] = len(tracker_data.get("cert_in_enforcement", []))
    stats["total_all_sections"] = len(all_actions)
    stats["by_sector"] = by_sector
    stats["by_violation_type"] = by_violation
    stats["by_outcome"] = by_outcome
    stats["last_recalculated"] = datetime.now(timezone.utc).isoformat()


async def update_enforcement_tracker() -> dict[str, Any]:
    """
    Main entry point — check for new DPDPA enforcement actions and update tracker.

    Workflow:
    1. Load existing tracker JSON
    2. Collect all existing source URLs (for dedup)
    3. Search Tavily for each query
    4. Filter duplicates
    5. Parse candidates into structured actions
    6. Append new actions (flagged for CEO review)
    7. Recalculate statistics
    8. Save updated JSON

    Returns a summary dict for scheduler logging.
    """
    log.info("enforcement_tracker_update_started")

    tracker_data = _load_tracker()
    existing_urls = _collect_all_source_urls(tracker_data)
    log.info("existing_source_urls_loaded", count=len(existing_urls))

    new_actions_added: list[str] = []
    candidates_found: int = 0
    duplicates_skipped: int = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in ENFORCEMENT_SEARCH_QUERIES:
            candidates = await _search_tavily(query, client)
            candidates_found += len(candidates)

            for candidate in candidates:
                if _is_duplicate(candidate, existing_urls):
                    duplicates_skipped += 1
                    log.debug("duplicate_candidate_skipped", url=candidate.url)
                    continue

                parsed = _parse_candidate_to_action(candidate)
                if parsed is None:
                    log.debug("candidate_not_relevant", url=candidate.url)
                    continue

                # Determine which section to add to
                # DPDPA Board actions → enforcement_actions
                # CERT-In → cert_in_enforcement
                # Everything else → pre_dpdpa_actions (conservative)
                section = "pre_dpdpa_actions"
                if parsed.authority == "Data Protection Board of India":
                    section = "enforcement_actions"
                elif parsed.authority == "CERT-In":
                    section = "cert_in_enforcement"

                new_id = _append_action_to_tracker(tracker_data, parsed, section)
                new_actions_added.append(new_id)
                existing_urls.add(candidate.url.strip().lower())  # prevent double-add in same run

    # Always recalculate statistics
    _recalculate_statistics(tracker_data)
    _save_tracker(tracker_data)

    summary = {
        "queries_run": len(ENFORCEMENT_SEARCH_QUERIES),
        "candidates_found": candidates_found,
        "duplicates_skipped": duplicates_skipped,
        "new_actions_added": len(new_actions_added),
        "new_action_ids": new_actions_added,
        "needs_ceo_review": len(new_actions_added) > 0,
        "tracker_path": str(TRACKER_PATH),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "enforcement_tracker_update_complete",
        **{k: v for k, v in summary.items() if k != "new_action_ids"},
    )

    if new_actions_added:
        log.warning(
            "new_enforcement_actions_need_ceo_review",
            count=len(new_actions_added),
            ids=new_actions_added,
            message="These entries are auto-detected and must be manually verified before treating as accurate.",
        )

    return summary


if __name__ == "__main__":
    # Allow running directly for manual testing
    result = asyncio.run(update_enforcement_tracker())
    print(json.dumps(result, indent=2))
