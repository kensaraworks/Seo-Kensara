"""REST API for external portals and LLM diagnostics."""
import os
import asyncio
from datetime import datetime
import httpx
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

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
