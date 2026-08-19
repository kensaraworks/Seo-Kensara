"""Unified Supabase Database Client for KensaraAI SEO Pipeline.

Handles connections and CRUD operations against all Supabase PostgreSQL tables:
- jobs, token_cost_log, internal_link_map, feeds_catalog, stories_processed,
  keyword_clusters, content_queue, content_calendar_alerts, ai_visibility,
  linkedin_metrics, entity_status, unlinked_mentions, founder_brand_mentions,
  content_performance, enforcement_tracker, seen_articles, activity_log,
  job_history, platform_stats, rag_embeddings, public.blogs

Uses httpx for high-performance async/sync PostgREST API communication.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
import httpx
import structlog

log = structlog.get_logger()


def is_supabase_configured() -> bool:
    """Check whether valid SUPABASE_URL and SUPABASE_SERVICE_KEY are present."""
    try:
        from src.config import settings
        url = getattr(settings, "supabase_url", "") or os.getenv("SUPABASE_URL", "")
        key = getattr(settings, "supabase_service_key", "") or os.getenv("SUPABASE_SERVICE_KEY", "")
        return bool(url and key and url != "replace_me" and key != "replace_me")
    except Exception:
        return False


def _get_credentials() -> tuple[str, str]:
    """Retrieve validated Supabase URL and Service Role Key."""
    from src.config import settings
    url = (getattr(settings, "supabase_url", "") or os.getenv("SUPABASE_URL", "")).rstrip("/")
    key = getattr(settings, "supabase_service_key", "") or os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_SERVICE_KEY missing from settings or environment.")
    return url, key


def _get_headers(key: str, prefer: Optional[str] = "return=representation") -> Dict[str, str]:
    """Build standard PostgREST headers."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


class SupabaseDB:
    """Client for executing PostgREST queries against Supabase tables."""

    # ── Async Methods ─────────────────────────────────────────────────────────

    @staticmethod
    async def select_async(
        table: str,
        select: str = "*",
        filters: Optional[Dict[str, str]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Asynchronously query rows from a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"

        params: Dict[str, Any] = {"select": select}
        if filters:
            for k, v in filters.items():
                params[k] = v
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)

        headers = _get_headers(key, prefer=None)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(endpoint, headers=headers, params=params)
                if res.status_code in (200, 206):
                    return res.json()
                log.warning("supabase_select_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_select_exception", table=table, error=str(exc))
            return []

    @staticmethod
    async def insert_async(table: str, data: Dict[str, Any] | List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Asynchronously insert row(s) into a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer="return=representation")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(endpoint, headers=headers, json=data)
                if res.status_code in (200, 201):
                    return res.json()
                log.warning("supabase_insert_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_insert_exception", table=table, error=str(exc))
            return []

    @staticmethod
    async def upsert_async(
        table: str,
        data: Dict[str, Any] | List[Dict[str, Any]],
        on_conflict: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Asynchronously upsert row(s) into a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"

        prefer = "return=representation,resolution=merge-duplicates"
        headers = _get_headers(key, prefer=prefer)
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(endpoint, headers=headers, params=params, json=data)
                if res.status_code in (200, 201):
                    return res.json()
                log.warning("supabase_upsert_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_upsert_exception", table=table, error=str(exc))
            return []

    @staticmethod
    async def update_async(
        table: str,
        data: Dict[str, Any],
        filters: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Asynchronously update row(s) matching filters in a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer="return=representation")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.patch(endpoint, headers=headers, params=filters, json=data)
                if res.status_code in (200, 204):
                    return res.json() if res.text else []
                log.warning("supabase_update_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_update_exception", table=table, error=str(exc))
            return []

    @staticmethod
    async def delete_async(table: str, filters: Dict[str, str]) -> bool:
        """Asynchronously delete row(s) matching filters in a Supabase table."""
        if not is_supabase_configured():
            return False
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer=None)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.delete(endpoint, headers=headers, params=filters)
                return res.status_code in (200, 204)
        except Exception as exc:
            log.error("supabase_delete_exception", table=table, error=str(exc))
            return False

    # ── Synchronous Methods ───────────────────────────────────────────────────

    @staticmethod
    def select_sync(
        table: str,
        select: str = "*",
        filters: Optional[Dict[str, str]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronously query rows from a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"

        params: Dict[str, Any] = {"select": select}
        if filters:
            for k, v in filters.items():
                params[k] = v
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)

        headers = _get_headers(key, prefer=None)
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(endpoint, headers=headers, params=params)
                if res.status_code in (200, 206):
                    return res.json()
                log.warning("supabase_select_sync_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_select_sync_exception", table=table, error=str(exc))
            return []

    @staticmethod
    def insert_sync(table: str, data: Dict[str, Any] | List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Synchronously insert row(s) into a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer="return=representation")

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(endpoint, headers=headers, json=data)
                if res.status_code in (200, 201):
                    return res.json()
                log.warning("supabase_insert_sync_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_insert_sync_exception", table=table, error=str(exc))
            return []

    @staticmethod
    def upsert_sync(
        table: str,
        data: Dict[str, Any] | List[Dict[str, Any]],
        on_conflict: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronously upsert row(s) into a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"

        prefer = "return=representation,resolution=merge-duplicates"
        headers = _get_headers(key, prefer=prefer)
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(endpoint, headers=headers, params=params, json=data)
                if res.status_code in (200, 201):
                    return res.json()
                log.warning("supabase_upsert_sync_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_upsert_sync_exception", table=table, error=str(exc))
            return []

    @staticmethod
    def update_sync(
        table: str,
        data: Dict[str, Any],
        filters: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Synchronously update row(s) matching filters in a Supabase table."""
        if not is_supabase_configured():
            return []
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer="return=representation")

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.patch(endpoint, headers=headers, params=filters, json=data)
                if res.status_code in (200, 204):
                    return res.json() if res.text else []
                log.warning("supabase_update_sync_failed", table=table, status=res.status_code, body=res.text)
                return []
        except Exception as exc:
            log.error("supabase_update_sync_exception", table=table, error=str(exc))
            return []

    @staticmethod
    def delete_sync(table: str, filters: Dict[str, str]) -> bool:
        """Synchronously delete row(s) matching filters in a Supabase table."""
        if not is_supabase_configured():
            return False
        url, key = _get_credentials()
        endpoint = f"{url}/rest/v1/{table}"
        headers = _get_headers(key, prefer=None)

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.delete(endpoint, headers=headers, params=filters)
                return res.status_code in (200, 204)
        except Exception as exc:
            log.error("supabase_delete_sync_exception", table=table, error=str(exc))
            return False


def get_supabase_db() -> type[SupabaseDB]:
    """Factory helper returning SupabaseDB class."""
    return SupabaseDB
