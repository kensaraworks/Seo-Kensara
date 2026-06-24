'''LinkedIn post monitoring scraper.
Collect recent posts for the company page and the two founders using the LinkedIn API.
The function returns a list of metric dictionaries ready for storage.
'''
import json
from datetime import datetime, timezone
import httpx
from src.context.kensarai_facts import (
    LINKEDIN_API_URL,
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_ORGANIZATION_ID,
    LINKEDIN_FOUNDERS,
)

async def fetch_linkedin_posts(entity_id: str) -> list[dict]:
    """Fetch recent posts for a LinkedIn entity (company or person).
    Returns a list of raw post JSON objects.
    """
    url = f"{LINKEDIN_API_URL}/ugcPosts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    params = {
        "q": "authors",
        "authors": f"List(urn:li:person:{entity_id})",
        "count": 10,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("elements", [])

async def gather_linkedin_metrics() -> list[dict]:
    """Gather metrics for company and founders.
    Returns a list of dicts with keys: entity, metrics, recorded_at.
    """
    results = []
    # Company (organization posts)
    company_posts = await fetch_linkedin_posts(LINKEDIN_ORGANIZATION_ID)
    results.append({
        "entity": "company",
        "metrics": json.dumps(company_posts),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    # Founders
    for founder_id in LINKEDIN_FOUNDERS:
        posts = await fetch_linkedin_posts(founder_id)
        results.append({
            "entity": f"founder_{founder_id}",
            "metrics": json.dumps(posts),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
    return results

async def monitor_linkedin_metrics() -> None:
    """Fetch LinkedIn metrics and store them using JobQueue."""
    from src.queue.job_queue import job_queue
    metrics = await gather_linkedin_metrics()
    for entry in metrics:
        try:
            job_queue.record_linkedin_metric(
                entity=entry["entity"],
                metrics=entry["metrics"],
                recorded_at=entry.get("recorded_at"),
            )
        except Exception as exc:
            import structlog
            log = structlog.get_logger()
            log.error("linkedin_metric_store_failed", entity=entry.get("entity"), error=str(exc))
