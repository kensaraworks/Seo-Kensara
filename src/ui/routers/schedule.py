"""Schedule router — job schedule view and manual triggers."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.analytics.gsc_widgets import sync_gsc_query_data_to_db
from src.analytics.search_console import gsc_client

log = structlog.get_logger()

router = APIRouter(prefix="/schedule", tags=["schedule"])
templates = Jinja2Templates(directory="src/ui/templates")

DRAFTS_ROOT = Path("drafts")

# ── Job definitions ───────────────────────────────────────────────────────────

JOBS = [
    {
        "id": "news_scan",
        "name": "Daily news scan",
        "description": "Fetches RSS feeds from ICO, EDPB, IAPP and scores relevance.",
        "schedule": "08:00 IST daily",
        "cron": "0 8 * * *",
        "phase": "1",
    },
    {
        "id": "blog_generate",
        "name": "SEO blog generator",
        "description": "Takes top-scored news → generates 800–1200 word blog post → saves to drafts/.",
        "schedule": "08:15 IST daily",
        "cron": "15 8 * * *",
        "phase": "1",
    },
    {
        "id": "linkedin_posts",
        "name": "LinkedIn post drafts",
        "description": "Generates 3 LinkedIn posts (fear, educational, social proof) and saves to drafts/linkedin/.",
        "schedule": "09:00 IST Tue / Wed / Thu",
        "cron": "0 9 * * 2,3,4",
        "phase": "2",
    },
    {
        "id": "newsletter",
        "name": "Monthly newsletter digest",
        "description": "Generates 'KensaraAI Privacy Digest' from top stories + platform stats.",
        "schedule": "1st of month 09:00 IST",
        "cron": "0 9 1 * *",
        "phase": "2",
    },
    {
        "id": "regulatory_poll",
        "name": "Regulatory feed poll",
        "description": "Polls regulatory feeds every 4 hours for critical stories (score >= 12) to trigger immediate newsjacking.",
        "schedule": "Every 4 hours",
        "cron": "0 */4 * * *",
        "phase": "1",
    },
    {
        "id": "content_gap_check",
        "name": "Content gap alert",
        "description": "Checks the next 7 days and alerts CEO if no content is scheduled and queue depth is low.",
        "schedule": "07:45 IST daily",
        "cron": "45 7 * * *",
        "phase": "1",
    },
    {
        "id": "competitor_intelligence",
        "name": "Competitor intelligence",
        "description": "Runs weekly competitor crawl, rankings, backlink surges, and writes monday-brief report.",
        "schedule": "Monday 06:00 IST",
        "cron": "0 6 * * 1",
        "phase": "1",
    },
    {
        "id": "trending_monitor",
        "name": "Trending monitor",
        "description": "Runs Google Trends, Autocomplete, Reddit/Quora, and LinkedIn trend monitors.",
        "schedule": "06:30 IST daily",
        "cron": "30 6 * * *",
        "phase": "1",
    },
    {
        "id": "geo_monitor_weekly",
        "name": "GEO monitor weekly",
        "description": "Runs AI visibility checks across ChatGPT, Claude, Gemini, Perplexity, and Google AIO.",
        "schedule": "Tuesday 07:30 IST",
        "cron": "30 7 * * 2",
        "phase": "1",
    },
    {
        "id": "gsc_sync",
        "name": "GSC Weekly Sync",
        "description": "Pulls clicks, impressions, CTR, and position data from Google Search Console for all blog URLs. Updates content performance classifications.",
        "schedule": "Every Sunday 07:00 IST",
        "cron": "0 7 * * 0",
        "status": "active" if gsc_client.is_configured() else "not_configured",
        "phase": "1",
    },
    {
        "id": "content_refresh",
        "name": "Content refresh queue drain",
        "description": "Processes pending refresh_queue rows — regenerates stale H2 sections in approved posts flagged by the feedback loop.",
        "schedule": "Every Sunday 08:00 IST",
        "cron": "0 8 * * 0",
        "phase": "1",
    },
    {
        "id": "feedback_loop_monthly",
        "name": "Monthly feedback loop",
        "description": "Evaluates 30-day GSC performance for all published posts. Tags winners, dead posts, and link earners. Auto-queues dead posts for refresh.",
        "schedule": "1st of month 04:00 IST",
        "cron": "0 4 1 * *",
        "phase": "1",
    },
]


def _load_job_history() -> dict:
    cache_path = DRAFTS_ROOT / ".cache" / "job_history.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_job_history(history: dict) -> None:
    cache_path = DRAFTS_ROOT / ".cache" / "job_history.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _enrich_jobs(history: dict) -> list[dict]:
    enriched = []
    for job in JOBS:
        h = history.get(job["id"], {})
        enriched.append(
            {
                **job,
                "last_run": h.get("last_run", "Never"),
                "last_status": h.get("status", "unknown"),
                "item_count": h.get("item_count", 0),
                "duration_ms": h.get("duration_ms", 0),
            }
        )
    return enriched


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def schedule_view(request: Request) -> HTMLResponse:
    history = _load_job_history()
    jobs = _enrich_jobs(history)
    return templates.TemplateResponse(
        "schedule.html",
        {
            "request": request,
            "active_page": "schedule",
            "jobs": jobs,
        },
    )


@router.post("/run/{job_id}", response_class=JSONResponse)
async def run_job_now(job_id: str) -> JSONResponse:
    """Trigger a job manually — runs the corresponding agent function."""
    job_def = next((j for j in JOBS if j["id"] == job_id), None)
    if job_def is None:
        return JSONResponse({"ok": False, "error": f"Unknown job: {job_id}"}, status_code=404)

    log.info("manual_job_trigger", job_id=job_id)
    start = datetime.now()

    try:
        result = await _dispatch_job(job_id)
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)

        history = _load_job_history()
        history[job_id] = {
            "last_run": start.strftime("%Y-%m-%d %H:%M"),
            "status": "ok",
            "item_count": result.get("count", 0),
            "duration_ms": duration_ms,
            "triggered_by": "manual",
        }
        if "latest_news" in result:
            history["latest_news"] = result["latest_news"]
        _save_job_history(history)

        log.info("manual_job_done", job_id=job_id, duration_ms=duration_ms)
        return JSONResponse(
            {
                "ok": True,
                "job_id": job_id,
                "duration_ms": duration_ms,
                "result": result,
            }
        )
    except Exception as exc:
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        history = _load_job_history()
        history[job_id] = {
            "last_run": start.strftime("%Y-%m-%d %H:%M"),
            "status": "error",
            "item_count": 0,
            "duration_ms": duration_ms,
            "error": str(exc)[:200],
            "triggered_by": "manual",
        }
        _save_job_history(history)
        log.error("manual_job_failed", job_id=job_id, error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

@router.get("/run/blog_generate/stream")
async def run_blog_generate_stream(request: Request, keyword: str = None):
    """Server-Sent Events endpoint for the 11-step visual blog generation workflow.

    Steps 1–5 emit immediately as each phase completes.
    Steps 6–10 are timed progress events emitted every 25 s while the 7-step
    LLM pipeline runs (generate_blog_post is a single long-running coroutine —
    we cannot introspect its internal steps from outside without modifying it).
    Step 11 fires on successful save.
    """
    from fastapi.responses import StreamingResponse

    async def event_generator():
        q: asyncio.Queue = asyncio.Queue()

        async def cb(step: int, msg: str) -> None:
            await q.put({"step": step, "message": msg})

        async def task() -> None:
            try:
                from datetime import date as _date
                from src.scrapers.rss_scraper import fetch_rss_feeds
                from src.agents.news_scout import score_news_items
                from src.agents.blog_writer import generate_blog_post, _get_keyword_rotation
                from src.publishers.file_publisher import save_blog_draft

                await cb(1, "Initializing AI Engine")

                await cb(2, "Fetching RSS feeds & regulatory sources")
                items = await fetch_rss_feeds()

                await cb(3, "Scoring news relevance")
                scored = await score_news_items(items)
                if not scored:
                    await q.put({
                        "error": (
                            "No relevant news items found. "
                            "Run 'Daily news scan' first to populate the cache."
                        )
                    })
                    await q.put(None)
                    return

                await cb(4, f"Top story: {scored[0].item.title[:60]}")

                await cb(5, "Selecting this week's target keyword")
                if keyword:
                    target_keyword = keyword
                else:
                    rotation = _get_keyword_rotation()
                    week = _date.today().isocalendar()[1]
                    target_keyword = rotation[week % len(rotation)]

                await cb(6, f"Building keyword brief — {target_keyword[:50]}")

                # ── 7-Step GEO pipeline (single long-running coroutine) ────────
                # Emit steps 7–10 as timed progress events every 25 s while the
                # pipeline runs.  asyncio.shield() prevents cancellation of the
                # underlying task when wait_for() raises TimeoutError.
                gen_task = asyncio.create_task(generate_blog_post(scored[0], target_keyword))

                _progress = [
                    (7,  "Generating SERP-informed outline (Step 1/7)"),
                    (8,  "Writing section-by-section body (Steps 2–3/7)"),
                    (9,  "SEO injection + GEO optimisation (Steps 4–6/7)"),
                    (10, "Assembling final document + schema (Step 7/7)"),
                ]
                for step_num, step_msg in _progress:
                    try:
                        await asyncio.wait_for(asyncio.shield(gen_task), timeout=25.0)
                        break  # pipeline finished before this step's timeout
                    except asyncio.TimeoutError:
                        await cb(step_num, step_msg)

                post = await gen_task
                # ── end pipeline ──────────────────────────────────────────────

                await cb(11, f"Saving draft: {post.slug}")
                await save_blog_draft(post)

                history = _load_job_history()
                history["blog_generate"] = {
                    "last_run": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "status": "ok",
                    "item_count": 1,
                    "duration_ms": 0,
                    "triggered_by": "api_stream",
                }
                history["latest_news"] = [
                    {"title": s.item.title, "url": s.item.url, "source": s.item.source}
                    for s in scored[:3]
                ]
                _save_job_history(history)

                await q.put(None)

            except Exception as exc:
                log.error("stream_blog_generate_failed", error=str(exc))
                await q.put({"error": str(exc)})
                await q.put(None)

        asyncio.create_task(task())

        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")




async def _dispatch_job(job_id: str) -> dict:
    """Import and run the relevant agent function for the job."""

    def _sync_page_summaries_to_content_performance(page_summaries: list) -> int:
        db_path = Path("drafts/.cache/jobs.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(content_performance)").fetchall()}
            for col_sql in (
                "ALTER TABLE content_performance ADD COLUMN post_url TEXT",
                "ALTER TABLE content_performance ADD COLUMN avg_position_30d REAL DEFAULT 0.0",
                "ALTER TABLE content_performance ADD COLUMN avg_ctr_30d REAL DEFAULT 0.0",
                "ALTER TABLE content_performance ADD COLUMN top_query TEXT DEFAULT ''",
                "ALTER TABLE content_performance ADD COLUMN last_checked TEXT",
            ):
                col_name = col_sql.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
                if col_name not in cols:
                    try:
                        conn.execute(col_sql)
                        cols.add(col_name)
                    except sqlite3.OperationalError:
                        pass

            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_post_url_unique ON content_performance(post_url)"
            )

            updated = 0
            for summary in page_summaries:
                page_url = getattr(summary, "page_url", "")
                if not page_url:
                    continue
                conn.execute(
                    """
                    INSERT INTO content_performance
                        (keyword, post_url, impressions_30d, clicks_30d, avg_position_30d,
                         avg_ctr_30d, top_query, last_checked, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, date('now'), datetime('now'))
                    ON CONFLICT(post_url) DO UPDATE SET
                        impressions_30d  = excluded.impressions_30d,
                        clicks_30d       = excluded.clicks_30d,
                        avg_position_30d = excluded.avg_position_30d,
                        avg_ctr_30d      = excluded.avg_ctr_30d,
                        top_query        = excluded.top_query,
                        last_checked     = date('now')
                    """,
                    (
                        page_url,
                        page_url,
                        getattr(summary, "impressions_30d", 0),
                        getattr(summary, "clicks_30d", 0),
                        getattr(summary, "avg_position_30d", 0.0),
                        getattr(summary, "avg_ctr_30d", 0.0),
                        getattr(summary, "top_query", ""),
                    ),
                )
                updated += 1

            conn.commit()
            return updated
        finally:
            conn.close()

    if job_id == "news_scan":
        from src.main import run_news_scan

        result = await run_news_scan()
        return {
            "count": int(result.get("count", 0)),
            "message": result.get("message", "News scan completed."),
            "latest_news": result.get("latest_news", []),
        }

    if job_id == "blog_generate":
        from src.scrapers.rss_scraper import fetch_rss_feeds
        from src.agents.news_scout import score_news_items
        from src.agents.blog_writer import generate_blog_post, KEYWORD_ROTATION
        from src.publishers.file_publisher import save_blog_draft
        from datetime import date
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        if not scored:
            return {"count": 0, "message": "No scored news items found — blog not generated"}
        rotation = _get_keyword_rotation()
        week = date.today().isocalendar()[1]
        keyword = rotation[week % len(rotation)]
        post = await generate_blog_post(scored[0], keyword)
        path = await save_blog_draft(post)
        return {"count": 1, "message": f"Blog saved: {path.name}", "keyword": keyword}

    if job_id == "linkedin_posts":
        return {
            "count": 0,
            "status": "not_implemented",
            "message": "LinkedIn generation is a Phase 2 feature. It has not been built yet.",
        }

    if job_id == "newsletter":
        return {
            "count": 0,
            "status": "not_implemented",
            "message": "Newsletter generation is a Phase 2 feature. It has not been built yet.",
        }

    if job_id == "regulatory_poll":
        from src.main import run_regulatory_poll
        await run_regulatory_poll()
        return {"count": 1, "message": "Regulatory poll run completed."}

    if job_id == "content_gap_check":
        from src.main import run_content_gap_check
        await run_content_gap_check()
        return {"count": 1, "message": "Content calendar gap check completed."}

    if job_id == "competitor_intelligence":
        from src.agents.content_gap_analyzer import run_competitor_intelligence
        await run_competitor_intelligence()
        return {"count": 1, "message": "Competitor intelligence run completed."}

    if job_id == "trending_monitor":
        from src.main import run_trending_monitors
        await run_trending_monitors()
        return {"count": 1, "message": "Trending monitors run completed."}

    if job_id == "geo_monitor_weekly":
        from src.main import run_geo_monitor_weekly
        await run_geo_monitor_weekly()
        return {"count": 1, "message": "GEO monitor weekly run completed."}

    if job_id == "gsc_sync":
        if not gsc_client.is_configured():
            return {
                "count": 0,
                "status": "not_configured",
                "message": "GSC sync skipped — not configured",
            }

        page_summaries = gsc_client.get_blog_performance_30d()
        updated_pages = _sync_page_summaries_to_content_performance(page_summaries)
        query_rows = gsc_client.get_query_performance_30d(max_queries=500)
        synced_queries = sync_gsc_query_data_to_db(query_rows)
        return {
            "count": synced_queries,
            "pages": len(page_summaries),
            "pages_updated": updated_pages,
            "message": "GSC weekly sync completed.",
        }

    if job_id == "content_refresh":
        from src.agents.content_refresher import process_pending_refreshes
        result = await process_pending_refreshes(limit=5)
        return {
            "count": result.get("completed", 0),
            "message": (
                f"Refresh queue: {result['completed']} completed, "
                f"{result['failed']} failed of {result['pending_seen']} pending."
            ),
        }

    if job_id == "feedback_loop_monthly":
        from src.main import run_feedback_loop_monthly
        await run_feedback_loop_monthly()
        return {"count": 1, "message": "Monthly feedback loop completed."}

    return {"count": 0, "message": f"No handler for job {job_id}"}
