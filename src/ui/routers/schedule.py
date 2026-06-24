"""Schedule router — job schedule view and manual triggers."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

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
    },
    {
        "id": "blog_generate",
        "name": "SEO blog generator",
        "description": "Takes top-scored news → generates 800–1200 word blog post → saves to drafts/.",
        "schedule": "08:15 IST daily",
        "cron": "15 8 * * *",
    },
    {
        "id": "linkedin_posts",
        "name": "LinkedIn post drafts",
        "description": "Generates 3 LinkedIn posts (fear, educational, social proof) and saves to drafts/linkedin/.",
        "schedule": "09:00 IST Tue / Wed / Thu",
        "cron": "0 9 * * 2,3,4",
    },
    {
        "id": "newsletter",
        "name": "Monthly newsletter digest",
        "description": "Generates 'KensaraAI Privacy Digest' from top stories + platform stats.",
        "schedule": "1st of month 09:00 IST",
        "cron": "0 9 1 * *",
    },
    {
        "id": "regulatory_poll",
        "name": "Regulatory feed poll",
        "description": "Polls regulatory feeds every 4 hours for critical stories (score >= 12) to trigger immediate newsjacking.",
        "schedule": "Every 4 hours",
        "cron": "0 */4 * * *",
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
    """Server-Sent Events endpoint for a live 11-step visual blog generation workflow."""
    from fastapi.responses import StreamingResponse
    
    async def event_generator():
        q = asyncio.Queue()
        
        async def cb(step, msg):
            await q.put({"step": step, "message": msg})
            
        async def task():
            try:
                from src.scrapers.rss_scraper import fetch_rss_feeds
                from src.agents.news_scout import score_news_items
                from src.publishers.file_publisher import save_blog_draft
                from src.context.builder import build_context
                from src.agents.blog_writer import KEYWORD_ROTATION
                from datetime import date
                
                await cb(1, "Initializing AI Engine")
                await asyncio.sleep(0.5)
                
                await cb(2, "Fetching global RSS feeds")
                items = await fetch_rss_feeds()
                
                await cb(3, "Scoring news relevance via LLM")
                scored = await score_news_items(items)
                if not scored:
                    await cb(11, "Error: No scored news items found")
                    await q.put(None)
                    return
                
                await cb(4, "Selecting top news story")
                top_story = scored[0]
                await asyncio.sleep(0.5)
                
                await cb(5, "Selecting target keyword")
                if not keyword:
                    week = date.today().isocalendar()[1]
                    target_keyword = KEYWORD_ROTATION[week % len(KEYWORD_ROTATION)]
                else:
                    target_keyword = keyword
                await asyncio.sleep(0.5)
                
                context_str = build_context(keyword=target_keyword, news_angle=top_story.suggested_angle)
                
                try:
                    from src.agents.blog_writer import _get_groq_client, _generate_outline_groq, _generate_content_groq, _generate_meta_groq, _assemble_post
                    client = _get_groq_client()
                    
                    await cb(6, "Generating SEO Outline")
                    outline = await _generate_outline_groq(client, top_story, target_keyword)
                    
                    await cb(7, "Writing Hook & Intro")
                    await asyncio.sleep(0.5)
                    await cb(8, "Generating Core Body")
                    content = await _generate_content_groq(client, outline, top_story, target_keyword, context_str)
                    
                    await cb(9, "Synthesizing Conclusion")
                    await asyncio.sleep(0.5)
                    
                    await cb(10, "Formatting Markdown & Metadata")
                    meta = await _generate_meta_groq(client, content, target_keyword)
                    post = _assemble_post(target_keyword, content, meta)
                except Exception as exc:
                    log.warning("stream_groq_failed", error=str(exc))
                    from src.agents.blog_writer import _get_nvidia_client, _generate_outline_nvidia, _generate_content_nvidia, _generate_meta_nvidia, _assemble_post
                    client = _get_nvidia_client()
                    
                    await cb(6, "Generating SEO Outline (Fallback)")
                    outline = await _generate_outline_nvidia(client, top_story, target_keyword)
                    
                    await cb(8, "Generating Core Body (Fallback)")
                    content = await _generate_content_nvidia(client, outline, top_story, target_keyword, context_str)
                    
                    await cb(10, "Formatting Markdown & Metadata (Fallback)")
                    meta = await _generate_meta_nvidia(client, content, target_keyword)
                    post = _assemble_post(target_keyword, content, meta)
                
                await cb(11, f"Saving to Drafts Queue: {post.slug}")
                await save_blog_draft(post)
                
                # Write to job history
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
            except Exception as e:
                await q.put({"error": str(e)})
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
    if job_id == "news_scan":
        from src.scrapers.rss_scraper import fetch_rss_feeds
        from src.agents.news_scout import score_news_items
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        return {
            "count": len(scored), 
            "message": f"Scanned {len(items)} items, scored top {len(scored)}",
            "latest_news": [{"title": s.item.title, "url": s.item.url, "source": s.item.source} for s in scored[:3]]
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
        week = date.today().isocalendar()[1]
        keyword = KEYWORD_ROTATION[week % len(KEYWORD_ROTATION)]
        post = await generate_blog_post(scored[0], keyword)
        path = await save_blog_draft(post)
        return {"count": 1, "message": f"Blog saved: {path.name}", "keyword": keyword}

    if job_id == "linkedin_posts":
        return {"count": 0, "message": "LinkedIn writer not yet implemented (Phase 2)"}

    if job_id == "regulatory_poll":
        from src.main import run_regulatory_poll
        await run_regulatory_poll()
        return {"count": 1, "message": "Regulatory poll run completed."}

    return {"count": 0, "message": f"No handler for job {job_id}"}
