"""Module 1.8 — Intelligence Feedback Loop.

Closes the loop between what the agent PUBLISHES and what it generates NEXT.

Sub-systems:
  1.8.A  Content Performance Intelligence Feed
         — 30-day GSC performance pulled into keyword cluster intelligence.
         — Classifies posts as 'winner', 'dead', or 'link_earner'.
         — Dead keywords removed from active rotation.
         — Winning templates extracted for blog_writer.py guidance.

  1.8.B  Failed Story Pattern Recognition
         — Tracks rejected posts per source.
         — Auto-downgrades feeds with >30% rejection rate (score_multiplier -= 1).
         — Weekly source health analysis produces an actionable brief.

  1.8.C  Seasonal Performance Calendar
         — Defines DPDPA enforcement spike windows.
         — 8 weeks before each window opens, auto-enqueues a content burst.
         — Calendar is DB-driven so new windows can be added without code changes.
"""
from __future__ import annotations

import json
import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
import structlog

from src.queue.job_queue import job_queue
from src.config import settings
from src.agents.intent_classifier import classify_intent

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# 1.8.C — Seasonal Calendar Definitions
# ---------------------------------------------------------------------------

SEASONAL_WINDOWS: list[dict] = [
    {
        "window_name": "Q4_2026_Compliance_Rush",
        "window_start": "2026-10-01",
        "window_end": "2026-12-31",
        "theme_keywords": [
            "DPDPA compliance deadline 2026",
            "compliance audit Q4 2026",
            "DPDPA year-end review",
            "data protection annual assessment India",
            "DPDPA compliance checklist 2026",
            "data fiduciary obligations Q4",
        ],
        "preload_weeks": 8,
        "burst_count": 6,
    },
    {
        "window_name": "Jan_2027_New_Year_Compliance",
        "window_start": "2027-01-01",
        "window_end": "2027-01-31",
        "theme_keywords": [
            "data privacy 2027 checklist",
            "DPDPA new year compliance",
            "DPDPA 2027 what to expect",
            "data protection resolutions 2027",
            "DPDPA compliance roadmap 2027",
            "India data privacy outlook 2027",
        ],
        "preload_weeks": 8,
        "burst_count": 6,
    },
    {
        "window_name": "Pre_Enforcement_Sept_Oct_2026",
        "window_start": "2026-09-01",
        "window_end": "2026-10-31",
        "theme_keywords": [
            "DPDPA November 2026 deadline",
            "consent manager registration November 2026",
            "DPDPA compliance before November",
            "data fiduciary readiness audit India",
            "DPDPA pre-enforcement checklist",
            "Consent Manager DPDPA registration steps",
        ],
        "preload_weeks": 8,
        "burst_count": 6,
    },
    {
        "window_name": "Pre_Enforcement_Mar_Apr_2027",
        "window_start": "2027-03-01",
        "window_end": "2027-04-30",
        "theme_keywords": [
            "DPDPA May 2027 deadline",
            "full DPDPA compliance 2027",
            "data principal rights enforcement 2027",
            "DPDPA compliance deadline May 2027",
            "Indian data protection full enforcement",
            "DPDPA implementation final deadline",
        ],
        "preload_weeks": 8,
        "burst_count": 6,
    },
    {
        "window_name": "DPDPA_Rules_Gazette_Window",
        "window_start": "2026-07-01",
        "window_end": "2026-08-31",
        "theme_keywords": [
            "DPDP Rules 2025 explained",
            "DPDP Rules implementation guide",
            "how DPDP Rules affect your business",
            "DPDPA rules vs GDPR differences",
            "DPDPA rules consent requirements",
            "DPDPA rules significant data fiduciary",
        ],
        "preload_weeks": 8,
        "burst_count": 6,
    },
]


# ---------------------------------------------------------------------------
# 1.8.C — Seasonal Preload Check
# ---------------------------------------------------------------------------

async def run_seasonal_preload_check() -> None:
    """Check if any seasonal window needs a pre-scheduled content burst (1.8.C).

    Triggered daily at 07:15 IST via scheduler.
    Looks 56 days (8 weeks) ahead — if window_start is within that horizon
    and not yet triggered, enqueues a burst of themed keywords.
    """
    log.info("seasonal_preload_check_start")

    # Ensure calendar is seeded
    job_queue.seed_seasonal_calendar(SEASONAL_WINDOWS)

    windows = job_queue.get_untriggered_seasonal_windows(days_ahead=56)
    if not windows:
        log.info("seasonal_preload_no_windows_due")
        return

    for window in windows:
        window_name = window["window_name"]
        try:
            theme_keywords = json.loads(window.get("theme_keywords", "[]"))
        except (json.JSONDecodeError, TypeError):
            theme_keywords = []

        burst_count = int(window.get("burst_count", 6))
        keywords_to_enqueue = theme_keywords[:burst_count]

        log.info(
            "seasonal_burst_starting",
            window=window_name,
            keyword_count=len(keywords_to_enqueue),
        )

        for kw in keywords_to_enqueue:
            try:
                intent = (await classify_intent(kw)).value
                # High priority seasonal content
                job_queue.enqueue_content(
                    keyword=kw,
                    intent_type=intent,
                    cluster_id="SEASONAL",
                    priority_score=200.0,   # Highest possible — seasonal beats all
                    paa_questions=[],
                )
                log.info("seasonal_keyword_enqueued", keyword=kw, window=window_name)
            except Exception as exc:
                log.error("seasonal_keyword_enqueue_failed", keyword=kw, error=str(exc))

        job_queue.mark_seasonal_window_triggered(window["id"])
        log.info("seasonal_window_triggered", window=window_name)

    log.info("seasonal_preload_check_done", windows_processed=len(windows))


# ---------------------------------------------------------------------------
# 1.8.A — Content Performance Intelligence Feed
# ---------------------------------------------------------------------------

async def _check_backlinks_via_serper(keyword: str) -> int:
    """Check if any posts for this keyword have earned backlinks using Serper.dev.

    Returns count of unique domains linking to kensara.in pages mentioning the keyword.
    Degrades gracefully if SERPER_API_KEY is not set.
    """
    if not settings.serper_api_key:
        log.debug("serper_api_key_missing_for_backlink_check", keyword=keyword[:50])
        return 0

    try:
        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}
        payload = {"q": f'site:kensara.in "{keyword}"', "gl": "in"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        # Count results with organic hits — proxy for indexed backlink-attracting content
        organic = data.get("organic", [])
        return len(organic)
    except Exception as exc:
        log.warning("backlink_check_failed", keyword=keyword[:50], error=str(exc))
        return 0


async def _get_gsc_metrics(keyword: str) -> dict[str, int]:
    """Fetch 30-day GSC metrics for a keyword.

    Returns dict with impressions_30d, clicks_30d, ranked_keywords.
    Degrades gracefully when GSC is not yet configured.
    """
    from src.analytics.search_console import SearchConsoleClient
    client = SearchConsoleClient()

    if not client.is_configured():
        log.debug("gsc_not_configured_for_performance_check", keyword=keyword[:50])
        return {"impressions_30d": 0, "clicks_30d": 0, "ranked_keywords": 0}

    try:
        metrics = await client.get_keyword_performance(keyword, days=30)
        return {
            "impressions_30d": metrics.get("impressions", 0),
            "clicks_30d": metrics.get("clicks", 0),
            "ranked_keywords": metrics.get("ranked_keywords", 0),
        }
    except Exception as exc:
        log.warning("gsc_performance_fetch_failed", keyword=keyword[:50], error=str(exc))
        return {"impressions_30d": 0, "clicks_30d": 0, "ranked_keywords": 0}


def _classify_performance(impressions: int, ranked_keywords: int, backlinks: int) -> str:
    """Classify a post's performance tag based on its metrics (1.8.A).

    Rules (from spec):
      - winner       : ranked_keywords >= 3
      - link_earner  : backlinks >= 1 (even if not a winner by ranking)
      - dead         : impressions == 0 after 30 days
      - pending      : GSC not yet configured (cannot evaluate)
    """
    # If GSC is configured and returned zeros — genuine dead content
    if impressions == 0 and ranked_keywords == 0 and backlinks == 0:
        return "dead"
    if ranked_keywords >= 3:
        return "winner"
    if backlinks >= 1:
        return "link_earner"
    # Has some impressions but not enough to be a winner yet
    return "active"


async def evaluate_content_performance() -> dict[str, int]:
    """Main 1.8.A job: evaluate 30-day performance for all pending published posts.

    Called monthly on the 1st of the month at 04:00 IST.
    Returns summary counts of classification outcomes.
    """
    from src.analytics.search_console import gsc_client

    log.info("evaluate_content_performance_start")

    if not gsc_client.is_configured():
        log.warning(
            "gsc_not_connected_skipping_performance_classification",
            detail=(
                "Posts remain pending until GSC is configured. "
                "See Part 1 of the GSC implementation plan."
            ),
        )
        return {"winner": 0, "dead": 0, "link_earner": 0, "pending": 0}

    pending = job_queue.get_pending_performance_reviews()

    if not pending:
        log.info("evaluate_content_performance_no_pending")
        return {"winner": 0, "dead": 0, "link_earner": 0, "pending": 0}

    counts: dict[str, int] = {"winner": 0, "dead": 0, "link_earner": 0, "pending": 0}

    for entry in pending:
        keyword = entry.get("keyword", "")
        if not keyword:
            continue

        post_url = (entry.get("post_url") or "").strip()

        # Keep keyword-level metrics for click/ranking context and fallback.
        gsc = await _get_gsc_metrics(keyword)
        # Check backlinks (existing behavior).
        backlinks = await _check_backlinks_via_serper(keyword)

        impressions = gsc_client.get_page_impressions_30d(post_url) if post_url else 0
        if impressions == 0 and not post_url:
            # Backward-compatible fallback while legacy rows are still keyword-only.
            impressions = gsc.get("impressions_30d", 0)

        clicks = gsc.get("clicks_30d", 0)
        ranked = gsc.get("ranked_keywords", 0)

        recorded_at = str(entry.get("recorded_at", "") or "")
        try:
            published_date = date.fromisoformat(recorded_at.split("T")[0])
            days_since_publish = (date.today() - published_date).days
        except Exception:
            days_since_publish = 0

        # Part 7 classification thresholds.
        if impressions == 0 and days_since_publish > 30:
            tag = "dead"
        elif impressions >= 100:
            tag = "winner"
        elif impressions >= 50:
            tag = "link_earner"
        else:
            tag = "pending"

        counts[tag] = counts.get(tag, 0) + 1

        # Update the record
        job_queue.record_content_performance(
            keyword=keyword,
            cluster_id=entry.get("cluster_id", ""),
            intent_type=entry.get("intent_type", ""),
            word_count=entry.get("word_count", 0),
            h2_structure=json.loads(entry.get("h2_structure", "[]") or "[]"),
            impressions_30d=impressions,
            clicks_30d=clicks,
            ranked_keywords=ranked,
            backlinks_found=backlinks,
            performance_tag=tag,
        )

        # Action on dead keywords
        if tag == "dead":
            job_queue.update_keyword_coverage(keyword, "non_viable")
            log.info("keyword_flagged_non_viable", keyword=keyword[:50])
            if post_url:
                try:
                    from src.agents.content_refresher import enqueue_refresh

                    enqueue_refresh(
                        post_url=post_url,
                        trigger_reason="dead_post_zero_impressions_30d",
                        priority=2,
                    )
                    log.info("auto_enqueued_refresh_for_dead_post", post_url=post_url)
                except Exception as exc:
                    log.warning("auto_enqueue_refresh_failed", post_url=post_url, error=str(exc))
            else:
                log.warning(
                    "dead_post_missing_url_refresh_skipped",
                    keyword=keyword[:50],
                )

        # Action on winners — also ensure cluster coverage is marked
        if tag in ("winner", "link_earner"):
            job_queue.update_keyword_coverage(keyword, "published")
            log.info("keyword_confirmed_winning", keyword=keyword[:50], tag=tag)

        # Small delay to avoid hammering APIs
        await asyncio.sleep(0.5)

    log.info("evaluate_content_performance_done", **counts)
    return counts


def extract_winning_templates(cluster_id: str | None = None) -> list[dict[str, Any]]:
    """Extract structured template parameters from winner posts (1.8.A).

    Returns a list of dicts with keys: keyword, intent_type, word_count, h2_structure.
    blog_writer.py can call this to get a style guide for the next post in a cluster.
    """
    winners = job_queue.get_winning_templates(cluster_id=cluster_id)
    templates = []
    for w in winners:
        try:
            h2 = json.loads(w.get("h2_structure", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            h2 = []
        templates.append({
            "keyword": w.get("keyword"),
            "intent_type": w.get("intent_type"),
            "word_count": w.get("word_count", 0),
            "h2_structure": h2,
            "impressions_30d": w.get("impressions_30d", 0),
            "ranked_keywords": w.get("ranked_keywords", 0),
        })
    return templates


# ---------------------------------------------------------------------------
# 1.8.B — Failed Story Pattern Recognition
# ---------------------------------------------------------------------------

_REJECTION_RATE_THRESHOLD = 0.30  # 30% rejection rate triggers downgrade


async def run_source_health_analysis() -> list[dict[str, Any]]:
    """Weekly 1.8.B job: analyse rejection rates for all sources.

    For every source with rejection rate > 30%, downgrades its score_multiplier
    in feeds_catalog by 1 (capped at minimum 0.5).
    Returns a source health brief for the dashboard.
    """
    log.info("source_health_analysis_start")

    sources = job_queue.get_all_sources_with_rejections()
    if not sources:
        log.info("source_health_no_rejections_found")
        return []

    health_brief = []
    for source in sources:
        stats = job_queue.get_source_rejection_rate(source)
        rate = stats.get("rate", 0.0)
        total = stats.get("total", 0)
        rejected = stats.get("rejected", 0)

        status = "healthy"
        action_taken = None

        if rate > _REJECTION_RATE_THRESHOLD and total >= 5:
            # Auto-downgrade: use source as feed_id (consistent with feeds_catalog naming)
            job_queue.downgrade_feed_score_multiplier(
                feed_id=source, amount=1.0, min_value=0.5
            )
            status = "downgraded"
            action_taken = f"score_multiplier reduced by 1 (min 0.5) — rejection rate {rate:.1%}"
            log.warning(
                "feed_auto_downgraded",
                source=source,
                rejection_rate=rate,
                total=total,
                rejected=rejected,
            )

        entry = {
            "source": source,
            "total_processed": total,
            "rejected": rejected,
            "rejection_rate": f"{rate:.1%}",
            "status": status,
            "action": action_taken,
        }
        health_brief.append(entry)
        log.info("source_health_checked", **{k: v for k, v in entry.items() if v is not None})

    log.info("source_health_analysis_done", sources_analysed=len(health_brief))
    return health_brief


def record_rejection(story_id: str, source: str, headline: str, reason: str = "") -> None:
    """Convenience function to record a story rejection (called from UI review route).

    This is the entry point for 1.8.B — it must be called by the human review
    approval/rejection endpoint whenever a post is rejected.
    """
    job_queue.record_story_rejection(
        source=source,
        story_id=story_id,
        headline=headline,
        rejection_reason=reason,
    )
    log.info("rejection_recorded", source=source, story_id=story_id)


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------

def seed_calendar_on_startup() -> None:
    """Idempotent startup seeding of the seasonal calendar.

    Called once from main.py startup — uses INSERT OR IGNORE so safe to repeat.
    """
    job_queue.seed_seasonal_calendar(SEASONAL_WINDOWS)
    log.info("seasonal_calendar_ready", windows=len(SEASONAL_WINDOWS))


# ---------------------------------------------------------------------------
# Direct module execution for CLI testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def _cli_test() -> None:
        print("=== Module 1.8 Feedback Loop — CLI Test ===\n")

        print("[1.8.C] Seeding seasonal calendar...")
        seed_calendar_on_startup()

        print("[1.8.C] Running seasonal preload check...")
        await run_seasonal_preload_check()

        print("[1.8.A] Evaluating content performance...")
        perf_counts = await evaluate_content_performance()
        print(f"  Performance results: {perf_counts}")

        print("[1.8.B] Running source health analysis...")
        brief = await run_source_health_analysis()
        for entry in brief:
            print(f"  {entry['source']}: {entry['rejection_rate']} rejection rate — {entry['status']}")

        print("\n=== Done ===")

    asyncio.run(_cli_test())
