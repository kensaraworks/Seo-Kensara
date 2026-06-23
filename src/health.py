"""Health check module — probes all external service dependencies.

Used by the scheduler and UI to surface degraded or unavailable services
before they silently break content generation pipelines.

Each check has a hard 5-second timeout to prevent blocking the scheduler.
"""
import time
from datetime import datetime, timezone

import httpx
import structlog
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()

_PROBE_TIMEOUT = 5.0  # seconds per service check


class ServiceStatus(BaseModel):
    name: str
    status: str          # "ok" | "degraded" | "down"
    latency_ms: float | None = None
    error: str | None = None


class HealthReport(BaseModel):
    overall: str         # "healthy" | "degraded" | "down"
    services: list[ServiceStatus]
    timestamp: str


# -----------------------------------------------------------------------
# Individual service probes
# -----------------------------------------------------------------------

async def _check_nvidia_nim() -> ServiceStatus:
    """Ping the NVIDIA NIM OpenAI-compatible models listing endpoint."""
    name = "NVIDIA NIM"
    if not settings.nvidia_api_key:
        return ServiceStatus(name=name, status="degraded", error="NVIDIA_API_KEY not configured")

    url = "https://integrate.api.nvidia.com/v1/models"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
            )
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if response.status_code == 200:
            log.debug("health_nvidia_ok", latency_ms=latency_ms)
            return ServiceStatus(name=name, status="ok", latency_ms=latency_ms)

        log.warning("health_nvidia_non200", status_code=response.status_code)
        return ServiceStatus(
            name=name,
            status="degraded",
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    except httpx.TimeoutException:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        log.error("health_nvidia_timeout", latency_ms=latency_ms)
        return ServiceStatus(name=name, status="down", latency_ms=latency_ms, error="Timeout")
    except httpx.RequestError as exc:
        log.error("health_nvidia_error", error=str(exc))
        return ServiceStatus(name=name, status="down", error=str(exc))


async def _check_groq() -> ServiceStatus:
    """Ping Groq API models endpoint."""
    name = "Groq"
    if not settings.groq_api_key:
        return ServiceStatus(name=name, status="degraded", error="GROQ_API_KEY not configured")

    url = "https://api.groq.com/openai/v1/models"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if response.status_code == 200:
            log.debug("health_groq_ok", latency_ms=latency_ms)
            return ServiceStatus(name=name, status="ok", latency_ms=latency_ms)

        log.warning("health_groq_non200", status_code=response.status_code)
        return ServiceStatus(
            name=name,
            status="degraded",
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    except httpx.TimeoutException:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        log.error("health_groq_timeout", latency_ms=latency_ms)
        return ServiceStatus(name=name, status="down", latency_ms=latency_ms, error="Timeout")
    except httpx.RequestError as exc:
        log.error("health_groq_error", error=str(exc))
        return ServiceStatus(name=name, status="down", error=str(exc))


async def _check_wordpress() -> ServiceStatus:
    """Ping WordPress REST API /wp-json/ discovery endpoint."""
    name = "WordPress"
    if not settings.wordpress_user or not settings.wordpress_app_password:
        return ServiceStatus(name=name, status="degraded", error="WordPress credentials not configured")

    url = f"{settings.wordpress_url.rstrip('/')}/wp-json/"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.get(url)
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if response.status_code in (200, 401):
            # 401 = auth required but API is reachable
            status = "ok" if response.status_code == 200 else "degraded"
            log.debug("health_wordpress_ok", latency_ms=latency_ms, http=response.status_code)
            return ServiceStatus(name=name, status=status, latency_ms=latency_ms)

        log.warning("health_wordpress_non200", status_code=response.status_code)
        return ServiceStatus(
            name=name,
            status="degraded",
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    except httpx.TimeoutException:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        log.error("health_wordpress_timeout", latency_ms=latency_ms)
        return ServiceStatus(name=name, status="down", latency_ms=latency_ms, error="Timeout")
    except httpx.RequestError as exc:
        log.error("health_wordpress_error", error=str(exc))
        return ServiceStatus(name=name, status="down", error=str(exc))


async def _check_tavily() -> ServiceStatus:
    """Validate Tavily API key with a minimal search probe."""
    name = "Tavily"
    if not settings.tavily_api_key:
        return ServiceStatus(name=name, status="degraded", error="TAVILY_API_KEY not configured")

    url = "https://api.tavily.com/search"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.post(
                url,
                json={
                    "api_key": settings.tavily_api_key,
                    "query": "DPDPA India",
                    "max_results": 1,
                    "search_depth": "basic",
                },
            )
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if response.status_code == 200:
            log.debug("health_tavily_ok", latency_ms=latency_ms)
            return ServiceStatus(name=name, status="ok", latency_ms=latency_ms)

        log.warning("health_tavily_non200", status_code=response.status_code)
        return ServiceStatus(
            name=name,
            status="degraded" if response.status_code == 429 else "down",
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    except httpx.TimeoutException:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        log.error("health_tavily_timeout", latency_ms=latency_ms)
        return ServiceStatus(name=name, status="down", latency_ms=latency_ms, error="Timeout")
    except httpx.RequestError as exc:
        log.error("health_tavily_error", error=str(exc))
        return ServiceStatus(name=name, status="down", error=str(exc))


# -----------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------

async def check_health() -> HealthReport:
    """Check all external service dependencies in parallel.

    Returns a HealthReport with per-service statuses and an overall verdict:
    - "healthy"  — all configured services responded OK
    - "degraded" — at least one service is degraded or unconfigured
    - "down"     — at least one service is unreachable
    """
    import asyncio

    checks = [
        _check_nvidia_nim(),
        _check_groq(),
        _check_wordpress(),
        _check_tavily(),
    ]

    results = await asyncio.gather(*checks, return_exceptions=True)
    services: list[ServiceStatus] = []

    for result in results:
        if isinstance(result, BaseException):
            # Defensive: a probe itself crashed — treat as down
            log.error("health_probe_crashed", error=str(result))
            services.append(ServiceStatus(name="unknown", status="down", error=str(result)))
        else:
            services.append(result)

    # Determine overall status (worst-case wins)
    if any(s.status == "down" for s in services):
        overall = "down"
    elif any(s.status == "degraded" for s in services):
        overall = "degraded"
    else:
        overall = "healthy"

    log.info("health_check_complete", overall=overall, service_count=len(services))

    return HealthReport(
        overall=overall,
        services=services,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
