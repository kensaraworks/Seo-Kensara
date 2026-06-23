"""Rank Tracker — weekly Google ranking check for KensaraAI target keywords.

Uses Serper.dev to search for each keyword and locate kensara.in in results.
Compares with last week's snapshot to calculate position changes.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import structlog
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()

KENSARAI_DOMAIN = "kensara.in"

TARGET_KEYWORDS = [
    "DPDPA compliance software",
    "DPDPA compliance tool India",
    "DSAR automation India",
    "consent management platform India",
    "data breach notification software India",
    "DPDPA compliance checklist",
    "what is DPDPA India",
    "DPDPA vs GDPR",
    "DPDPA penalty India",
    "GDPR compliance tool India",
]


class KeywordRank(BaseModel):
    keyword: str
    position: int | None  # None = not in top 100
    url: str | None
    change_from_last_week: int | None  # positive = improved (e.g. +3 = moved up 3 spots)
    date_checked: str


def _find_domain_in_results(organic: list[dict], domain: str) -> tuple[int | None, str | None]:
    """
    Find a domain in organic SERP results.
    Returns (position, url) — 1-based position. None if not found.
    """
    for i, result in enumerate(organic, start=1):
        link = result.get("link", "")
        if domain in link:
            return i, link
    return None, None


def _load_last_week_ranks(rankings_dir: Path) -> dict[str, int]:
    """
    Load last week's ranking file (most recent file before today).
    Returns dict of {keyword: position}. Empty dict if no previous data.
    """
    if not rankings_dir.exists():
        return {}

    today = date.today()
    last_week = today - timedelta(days=7)

    # Find the most recent ranking file that is not today's
    candidates = sorted(rankings_dir.glob("*-rankings.json"), reverse=True)
    for candidate in candidates:
        try:
            file_date_str = candidate.name.replace("-rankings.json", "")
            file_date = date.fromisoformat(file_date_str)
            if file_date < today:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                log.debug(
                    "rank_tracker_loaded_previous",
                    file=candidate.name,
                    age_days=(today - file_date).days,
                )
                return {entry["keyword"]: entry["position"] for entry in data if entry["position"] is not None}
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            log.warning("rank_tracker_previous_file_error", file=str(candidate), error=str(exc))
            continue

    return {}


async def _check_single_keyword(keyword: str, previous_ranks: dict[str, int]) -> KeywordRank:
    """
    Check Google rank for a single keyword using Serper.dev.
    Searches top 100 results for kensara.in.
    """
    today = date.today().isoformat()

    if not settings.serper_api_key:
        log.warning(
            "rank_check_skipped",
            keyword=keyword[:50],
            reason="SERPER_API_KEY not set",
        )
        return KeywordRank(
            keyword=keyword,
            position=None,
            url=None,
            change_from_last_week=None,
            date_checked=today,
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": keyword, "gl": "in", "hl": "en", "num": 100},
            )
            response.raise_for_status()
            data = response.json()

        organic = data.get("organic", [])
        position, url = _find_domain_in_results(organic, KENSARAI_DOMAIN)

        # Calculate week-over-week change (positive = improved = moved up)
        change: int | None = None
        prev_position = previous_ranks.get(keyword)
        if position is not None and prev_position is not None:
            change = prev_position - position  # positive if rank improved

        rank = KeywordRank(
            keyword=keyword,
            position=position,
            url=url,
            change_from_last_week=change,
            date_checked=today,
        )

        log.info(
            "rank_checked",
            keyword=keyword[:50],
            position=position,
            change=change,
        )
        return rank

    except httpx.HTTPStatusError as exc:
        log.error(
            "rank_check_http_error",
            keyword=keyword[:50],
            status_code=exc.response.status_code,
            error=str(exc),
        )
    except httpx.RequestError as exc:
        log.error("rank_check_request_error", keyword=keyword[:50], error=str(exc))

    return KeywordRank(
        keyword=keyword,
        position=None,
        url=None,
        change_from_last_week=None,
        date_checked=today,
    )


async def check_keyword_ranks(keywords: list[str]) -> list[KeywordRank]:
    """
    Check Google ranking for each keyword using Serper.dev.

    - Searches top 100 results per keyword (India, English)
    - Looks for kensara.in in organic results
    - Compares with last week's saved file for position change
    - Saves results to drafts/reports/rankings/YYYY-MM-DD-rankings.json

    Returns empty list with warning log if SERPER_API_KEY is not set.
    """
    if not settings.serper_api_key:
        log.warning(
            "rank_tracker_skipped",
            reason="SERPER_API_KEY not set",
            action="set SERPER_API_KEY to enable rank tracking",
        )
        return []

    rankings_dir = Path(settings.content_output_dir) / "reports" / "rankings"
    rankings_dir.mkdir(parents=True, exist_ok=True)

    previous_ranks = _load_last_week_ranks(rankings_dir)
    log.info(
        "rank_check_start",
        keyword_count=len(keywords),
        previous_data_available=bool(previous_ranks),
    )

    results: list[KeywordRank] = []
    for keyword in keywords:
        rank = await _check_single_keyword(keyword, previous_ranks)
        results.append(rank)

    # Persist today's snapshot
    today = date.today().isoformat()
    snapshot_path = rankings_dir / f"{today}-rankings.json"
    try:
        snapshot_data = [r.model_dump() for r in results]
        snapshot_path.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")
        log.info(
            "rank_snapshot_saved",
            path=str(snapshot_path),
            ranked_keywords=sum(1 for r in results if r.position is not None),
            not_found=sum(1 for r in results if r.position is None),
        )
    except OSError as exc:
        log.error("rank_snapshot_save_failed", error=str(exc))

    # Summary log
    ranked = [r for r in results if r.position is not None]
    improved = [r for r in results if r.change_from_last_week is not None and r.change_from_last_week > 0]
    log.info(
        "rank_check_done",
        total_keywords=len(results),
        ranked_in_top_100=len(ranked),
        improved_week_over_week=len(improved),
    )
    return results


async def run_rank_check() -> list[KeywordRank]:
    """Called weekly by APScheduler in main.py."""
    return await check_keyword_ranks(TARGET_KEYWORDS)
