"""REST API for external portals and LLM diagnostics."""
import os
import asyncio
from datetime import datetime
import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["api"])



class GenerateRequest(BaseModel):
    job_id: str = "blog_generate"

@router.get("/blogs")
async def get_blogs(status: str = "approved"):
    """Fetch blogs from the drafts folder based on status."""
    from src.ui.app import _collect_drafts
    items = _collect_drafts()
    # Filter based on requested status
    if status == "approved":
        filtered = [i for i in items if i.get("approved") is True]
    else:
        filtered = [i for i in items if i.get("status") == status]
        
    # Include content by reading the markdown file
    from pathlib import Path
    results = []
    for item in filtered:
        try:
            content = Path(item["path"]).read_text(encoding="utf-8")
            item["content"] = content
        except Exception:
            item["content"] = ""
        results.append(item)
        
    return {"count": len(results), "data": results}


@router.get("/blogs/export")
async def export_approved_blogs():
    """Download all approved Markdown posts as a ZIP file."""
    import io
    import zipfile
    from pathlib import Path
    from src.ui.app import _collect_drafts

    approved_items = [item for item in _collect_drafts() if item.get("approved") is True]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in approved_items:
            file_path = Path(item.get("path", ""))
            if not file_path.exists() or file_path.suffix.lower() != ".md":
                continue
            archive_name = f"{item.get('folder', 'posts')}/{file_path.name}"
            zf.writestr(archive_name, file_path.read_text(encoding="utf-8"))

    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="approved-posts-{timestamp}.zip"'},
    )

@router.post("/generate")
async def trigger_generation(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Trigger a generation job manually via API."""
    from src.ui.routers.schedule import _dispatch_job, _load_job_history, _save_job_history
    
    def run_in_bg():
        asyncio.run(run_job(req.job_id))

    async def run_job(job_id):
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
                "triggered_by": "api",
            }
            _save_job_history(history)
        except Exception as exc:
            duration_ms = int((datetime.now() - start).total_seconds() * 1000)
            history = _load_job_history()
            history[job_id] = {
                "last_run": start.strftime("%Y-%m-%d %H:%M"),
                "status": "error",
                "error": str(exc),
                "duration_ms": duration_ms,
                "triggered_by": "api",
            }
            _save_job_history(history)

    background_tasks.add_task(run_in_bg)
    return {"message": f"Job {req.job_id} triggered in background.", "status": "processing"}

@router.get("/health/llms")
async def check_llm_health():
    """Diagnose LLM connections (NVIDIA, Groq, Tavily)."""
    results = {}
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Check NVIDIA
        nvidia_key = os.getenv("NVIDIA_API_KEY")
        if not nvidia_key:
            results["nvidia"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {nvidia_key}"})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["nvidia"] = {"status": "ok", "latency_ms": latency}
                else:
                    results["nvidia"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["nvidia"] = {"status": "error", "message": str(e), "latency_ms": 0}

        # Check Groq
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            results["groq"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq_key}"})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["groq"] = {"status": "ok", "latency_ms": latency}
                else:
                    results["groq"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["groq"] = {"status": "error", "message": str(e), "latency_ms": 0}
                
        # Check Tavily
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not tavily_key:
            results["tavily"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.post("https://api.tavily.com/search", json={"api_key": tavily_key, "query": "test", "include_answer": False, "max_results": 1})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["tavily"] = {"status": "ok", "latency_ms": latency}
                else:
                    results["tavily"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["tavily"] = {"status": "error", "message": str(e), "latency_ms": 0}
                
    return results

@router.get("/health/full")
async def check_full_health(request: Request):
    """
    Full pipeline health check for deployment readiness.

    Required checks (fail => unhealthy):
    - DB read from stories_processed
    - drafts/blogs writable
    - Groq, NVIDIA, Tavily, Serper connectivity
    - APScheduler running

    Optional checks (warn/fail => degraded):
    - Perplexity
    - AllToken
    - GSC
    """
    from src.config import settings
    from src.analytics.search_console import gsc_client
    import sqlite3
    from pathlib import Path

    # 1. Database check (stories_processed read)
    db_ok = False
    try:
        db_path = os.path.join(settings.content_output_dir, ".cache", "jobs.db")
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1 FROM stories_processed LIMIT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception as e:
        log.error("health_db_check_failed", error=str(e))

    # 2. Output directory writable check (drafts/blogs)
    disk_ok = False
    blogs_dir = Path(settings.content_output_dir) / "blogs"
    try:
        blogs_dir.mkdir(parents=True, exist_ok=True)
        test_file = blogs_dir / ".health_check_temp"
        test_file.write_text("health_check")
        test_file.unlink()
        disk_ok = True
    except Exception as e:
        log.error("health_disk_check_failed", error=str(e))

    def _has_key(value: str | None) -> bool:
        return bool(value and value.strip() and value.strip() != "replace_me")

    required_keys = {
        "groq": os.getenv("GROQ_API_KEY"),
        "nvidia": os.getenv("NVIDIA_API_KEY"),
        "tavily": os.getenv("TAVILY_API_KEY"),
        "serper": os.getenv("SERPER_API_KEY"),
    }
    optional_keys = {
        "perplexity": os.getenv("PERPLEXITY_API_KEY"),
        "alltoken": os.getenv("ALLTOKEN_API_KEY"),
    }

    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Required API checks
        nvidia_key = required_keys["nvidia"]
        nvidia_ok = False
        if not _has_key(nvidia_key):
            results["nvidia"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {nvidia_key}"})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["nvidia"] = {"status": "ok", "latency_ms": latency}
                    nvidia_ok = True
                else:
                    results["nvidia"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["nvidia"] = {"status": "error", "message": str(e), "latency_ms": 0}

        groq_key = required_keys["groq"]
        groq_ok = False
        if not _has_key(groq_key):
            results["groq"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq_key}"})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["groq"] = {"status": "ok", "latency_ms": latency}
                    groq_ok = True
                else:
                    results["groq"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["groq"] = {"status": "error", "message": str(e), "latency_ms": 0}
                
        tavily_key = required_keys["tavily"]
        tavily_ok = False
        if not _has_key(tavily_key):
            results["tavily"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.post("https://api.tavily.com/search", json={"api_key": tavily_key, "query": "test", "include_answer": False, "max_results": 1})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["tavily"] = {"status": "ok", "latency_ms": latency}
                    tavily_ok = True
                else:
                    results["tavily"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["tavily"] = {"status": "error", "message": str(e), "latency_ms": 0}

        serper_key = required_keys["serper"]
        serper_ok = False
        if not _has_key(serper_key):
            results["serper"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": "test", "num": 1}
                )
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["serper"] = {"status": "ok", "latency_ms": latency}
                    serper_ok = True
                else:
                    results["serper"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["serper"] = {"status": "error", "message": str(e), "latency_ms": 0}

        # Optional API checks
        perplexity_key = optional_keys["perplexity"]
        if not _has_key(perplexity_key):
            results["perplexity"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {perplexity_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar",
                        "messages": [{"role": "user", "content": "health check"}],
                        "max_tokens": 1,
                    },
                )
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["perplexity"] = {"status": "ok", "latency_ms": latency}
                else:
                    results["perplexity"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["perplexity"] = {"status": "error", "message": str(e), "latency_ms": 0}

        alltoken_key = optional_keys["alltoken"]
        if not _has_key(alltoken_key):
            results["alltoken"] = {"status": "missing_key", "latency_ms": 0}
        else:
            try:
                start = datetime.now()
                resp = await client.get(
                    f"{settings.alltoken_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {alltoken_key}"},
                )
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    results["alltoken"] = {"status": "ok", "latency_ms": latency}
                else:
                    results["alltoken"] = {"status": "error", "message": resp.text, "latency_ms": latency}
            except Exception as e:
                results["alltoken"] = {"status": "error", "message": str(e), "latency_ms": 0}

    # 7. GSC checks
    gsc_configured = gsc_client.is_configured()
    gsc_conn = {"success": False, "error": "Not configured"}
    if gsc_configured:
        gsc_conn = gsc_client.verify_connection()

    if not gsc_configured or not gsc_conn.get("success"):
        log.warning(
            "gsc_not_fully_connected_in_health_check",
            configured=gsc_configured,
            error=gsc_conn.get("error")
        )

    # 8. Scheduler checks
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_running = False
    scheduler_jobs_count = 0
    if scheduler:
        scheduler_running = scheduler.running
        scheduler_jobs_count = len(scheduler.get_jobs())

    # 9. Queue status check (pending CEO review)
    pending_review = 0
    try:
        from src.ui.routers.queue import _collect_all_items
        pending_review = len(_collect_all_items(status_filter=["draft", "pending_review"]))
    except Exception:
        # Fallback manual scan if queue import fails
        drafts_root = Path(settings.content_output_dir)
        for folder in ["blogs", "linkedin", "newsletters"]:
            folder_path = drafts_root / folder
            if folder_path.exists():
                for md_file in folder_path.glob("*.md"):
                    try:
                        text = md_file.read_text(encoding="utf-8")
                        if "status: approved" not in text and "status: rejected" not in text:
                            pending_review += 1
                    except Exception:
                        pass

    # Status classification:
    # unhealthy => any required check fails.
    # degraded  => required checks pass but optional checks are warn/fail.
    # healthy   => all required pass and optional pass.

    checks = {
        "database": "pass" if db_ok else "fail",
        "disk": "pass" if disk_ok else "fail",
        "groq": "pass" if groq_ok else "fail",
        "nvidia": "pass" if nvidia_ok else "fail",
        "tavily": "pass" if tavily_ok else "fail",
        "serper": "pass" if serper_ok else "fail",
        "scheduler": "pass" if scheduler_running else "fail",
        "perplexity": "pass" if results.get("perplexity", {}).get("status") == "ok" else (
            "warn" if results.get("perplexity", {}).get("status") == "missing_key" else "fail"
        ),
        "alltoken": "pass" if results.get("alltoken", {}).get("status") == "ok" else (
            "warn" if results.get("alltoken", {}).get("status") == "missing_key" else "fail"
        ),
        "gsc": "pass" if (gsc_configured and gsc_conn.get("success")) else "warn",
    }

    required_fail = any(
        checks[name] == "fail"
        for name in ["database", "disk", "groq", "nvidia", "tavily", "serper", "scheduler"]
    )
    optional_issue = any(
        checks[name] in {"warn", "fail"}
        for name in ["perplexity", "alltoken", "gsc"]
    )

    if required_fail:
        status = "unhealthy"
    elif optional_issue:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "checks": checks,
        "pending_review": pending_review,
        "scheduler_jobs": scheduler_jobs_count,
        "gsc": {
            "configured": gsc_configured,
            "connection": gsc_conn
        },
        "llm_diagnostics": results
    }


@router.get("/stats")
async def get_stats():
    """Get system stats and performance metrics."""
    from src.config import settings
    from src.ui.app import _collect_drafts
    import sqlite3

    # 1. total_approved count from drafts/blogs
    items = _collect_drafts()
    total_approved = sum(
        1 for i in items
        if i.get("folder") == "blogs" and (i.get("approved") is True or i.get("status") == "approved")
    )

    # 2. Database metrics from generation_log and token_cost_log
    db_path = os.path.join(settings.content_output_dir, ".cache", "jobs.db")
    total_posts_generated = 0
    total_tokens_used_this_month = 0
    total_cost_usd_this_month = 0.0
    top_cluster_by_volume = None
    avg_qa_score = 0.0

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # check generation_log table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='generation_log'")
            if cursor.fetchone():
                row = cursor.execute(
                    "SELECT COUNT(*) as cnt, AVG(qa_score) as avg_qa FROM generation_log"
                ).fetchone()
                if row:
                    total_posts_generated = row["cnt"] or 0
                    avg_qa_score = round(row["avg_qa"], 4) if row["avg_qa"] is not None else 0.0

                cluster_row = cursor.execute(
                    "SELECT cluster, COUNT(*) as cnt FROM generation_log GROUP BY cluster ORDER BY cnt DESC LIMIT 1"
                ).fetchone()
                if cluster_row:
                    top_cluster_by_volume = cluster_row["cluster"]

            # check token_cost_log table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_cost_log'")
            if cursor.fetchone():
                from datetime import timezone
                current_month = datetime.now(timezone.utc).strftime("%Y-%m")
                cost_row = cursor.execute(
                    "SELECT SUM(input_tokens + output_tokens) as tokens, SUM(cost_usd) as cost FROM token_cost_log WHERE timestamp LIKE ?",
                    (f"{current_month}%",)
                ).fetchone()
                if cost_row:
                    total_tokens_used_this_month = cost_row["tokens"] or 0
                    total_cost_usd_this_month = round(cost_row["cost"], 6) if cost_row["cost"] is not None else 0.0

            conn.close()
        except Exception as e:
            log.error("stats_endpoint_db_query_failed", error=str(e))

    return {
        "total_posts_generated": total_posts_generated,
        "total_approved": total_approved,
        "total_tokens_used_this_month": total_tokens_used_this_month,
        "total_cost_usd_this_month": total_cost_usd_this_month,
        "top_cluster_by_volume": top_cluster_by_volume,
        "avg_qa_score": avg_qa_score
    }


@router.get("/usage/today")
async def get_today_usage():
    """Return today's token consumption split by Groq vs NVIDIA, from SQLite token_cost_log."""
    import sqlite3
    from datetime import timezone
    from src.config import settings

    db_path = os.path.join(settings.content_output_dir, ".cache", "jobs.db")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = {
        "groq_tokens_today": 0,
        "nvidia_tokens_today": 0,
        "total_cost_today_usd": 0.0,
        "groq_daily_limit": 100_000,
    }

    if not os.path.exists(db_path):
        return result

    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT model_used,
                      SUM(input_tokens + output_tokens) AS tokens,
                      SUM(cost_usd) AS cost
               FROM token_cost_log
               WHERE timestamp LIKE ?
               GROUP BY model_used""",
            (f"{today}%",),
        ).fetchall()
        conn.close()
        for model_used, tokens, cost in rows:
            ml = (model_used or "").lower()
            if "llama" in ml or "groq" in ml or "llama-3" in ml:
                result["groq_tokens_today"] += tokens or 0
            else:
                result["nvidia_tokens_today"] += tokens or 0
            result["total_cost_today_usd"] += cost or 0.0
        result["total_cost_today_usd"] = round(result["total_cost_today_usd"], 6)
    except Exception as e:
        log.error("today_usage_query_failed", error=str(e))

    return result


@router.post("/enforcement/update")
async def trigger_enforcement_update():
    """Trigger the DPDPA/IT Act enforcement tracker update."""
    from src.agents.enforcement_tracker import update_enforcement_tracker
    try:
        summary = await update_enforcement_tracker()
        return {"status": "success", "summary": summary}
    except Exception as exc:
        log.error("api_enforcement_update_failed", error=str(exc))
        return {"status": "error", "message": str(exc)}


@router.post("/blogs/publish/{slug}")
async def publish_blog_to_supabase(slug: str):
    """Manually publish a specific approved blog draft to Supabase public.blogs.

    Looks for a matching draft file in drafts/blogs/ by slug, reads its
    frontmatter, and calls the Supabase publisher. Works for both freshly
    approved posts and re-publishes (upsert on slug conflict).
    """
    from pathlib import Path
    from src.config import settings
    from src.agents.blog_writer import BlogPost
    from src.publishers.supabase_publisher import publish_to_supabase

    drafts_dir = Path(settings.content_output_dir) / "blogs"
    # Find the most recent file matching the slug
    matching = sorted(drafts_dir.glob(f"*-{slug}.md"), reverse=True)
    if not matching:
        return {"status": "error", "error": f"No draft found for slug: {slug}"}

    target_path = matching[0]
    try:
        full_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "error": str(exc)}

    # Parse frontmatter
    import re
    _FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    fm: dict = {}
    match = _FM_RE.match(full_text)
    if match:
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")

    post = BlogPost(
        title=str(fm.get("title", slug)),
        meta_description=str(fm.get("meta_description", "")),
        slug=str(fm.get("slug", slug)),
        primary_keyword=str(fm.get("primary_keyword", slug)),
        content_markdown=full_text,
        word_count=int(fm.get("word_count", len(full_text.split()))),
        cluster=str(fm.get("cluster", "general")),
        intent=str(fm.get("intent", "informational")),
        tier=int(fm.get("tier", 2) or 2),
        geo_score=int(fm.get("geo_score", 0) or 0),
        qa_score=float(fm.get("qa_score", 0.0) or 0.0),
        risk_level=str(fm.get("risk_level", "HIGH")),
        approved=True,
        schema_json=str(fm.get("schema_json", "{}")),
        image_url=fm.get("image_url") or None,
        pillar=str(fm.get("pillar", "")),
        category=str(fm.get("category", "")),
    )

    result = await publish_to_supabase(post)
    return result


@router.post("/blogs/publish-all")
async def publish_all_approved_to_supabase():
    """Batch-publish all approved blog drafts to Supabase public.blogs.

    Iterates drafts/blogs/, collects all files with `approved: true` and
    `status: approved`, and publishes each one. Already-published posts are
    safely upserted (Supabase ON CONFLICT slug DO UPDATE).
    Returns a summary dict with counts and per-slug results.
    """
    from pathlib import Path
    import re
    from src.config import settings
    from src.agents.blog_writer import BlogPost
    from src.publishers.supabase_publisher import publish_to_supabase

    _FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    drafts_dir = Path(settings.content_output_dir) / "blogs"

    if not drafts_dir.exists():
        return {"status": "error", "error": "drafts/blogs directory not found"}

    results = []
    published = skipped = errors = 0

    for md_file in sorted(drafts_dir.glob("*.md"), reverse=True):
        try:
            full_text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        fm: dict = {}
        match = _FM_RE.match(full_text)
        if match:
            for line in match.group(1).splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"').strip("'")

        # Only publish approved blog drafts
        is_approved = str(fm.get("approved", "false")).lower() == "true"
        status = str(fm.get("status", "draft")).lower()
        if not is_approved or status not in ("approved",):
            continue

        slug = str(fm.get("slug", md_file.stem))
        post = BlogPost(
            title=str(fm.get("title", slug)),
            meta_description=str(fm.get("meta_description", "")),
            slug=slug,
            primary_keyword=str(fm.get("primary_keyword", slug)),
            content_markdown=full_text,
            word_count=int(fm.get("word_count", len(full_text.split()))),
            cluster=str(fm.get("cluster", "general")),
            intent=str(fm.get("intent", "informational")),
            tier=int(fm.get("tier", 2) or 2),
            geo_score=int(fm.get("geo_score", 0) or 0),
            qa_score=float(fm.get("qa_score", 0.0) or 0.0),
            risk_level=str(fm.get("risk_level", "HIGH")),
            approved=True,
            schema_json=str(fm.get("schema_json", "{}")),
            image_url=fm.get("image_url") or None,
            pillar=str(fm.get("pillar", "")),
            category=str(fm.get("category", "")),
        )

        result = await publish_to_supabase(post)
        results.append({"slug": slug, **result})
        if result.get("status") == "published":
            published += 1
        elif result.get("status") == "skipped":
            skipped += 1
        else:
            errors += 1

    log.info(
        "supabase_publish_all_done",
        total=len(results),
        published=published,
        skipped=skipped,
        errors=errors,
    )
    return {
        "total": len(results),
        "published": published,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }
