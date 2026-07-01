from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
GSC_API_SERVICE = "searchconsole"
GSC_API_VERSION = "v1"
MAX_ROWS_PER_CALL = 1000
GSC_DATA_DELAY_DAYS = 3


@dataclass
class GSCRow:
    """A single row returned by searchAnalytics.query()."""

    query: str = ""
    page: str = ""
    clicks: int = 0
    impressions: int = 0
    ctr: float = 0.0
    position: float = 0.0
    date: str = ""


@dataclass
class GSCPerformanceSummary:
    """Summary of a single page's performance over a date range."""

    page_url: str = ""
    clicks_30d: int = 0
    impressions_30d: int = 0
    avg_ctr_30d: float = 0.0
    avg_position_30d: float = 0.0
    top_query: str = ""
    top_query_impressions: int = 0
    date_range_start: str = ""
    date_range_end: str = ""


class SearchConsoleClient:
    """
    Client for the Google Search Console API.

    Authentication uses a service account JSON key file. The service account
    email must be added as a user in Search Console for the property.
    """

    def __init__(self):
        self._service = None
        self._site_url = os.environ.get("GSC_SITE_URL", "")
        self._key_file = os.environ.get(
            "GSC_SERVICE_ACCOUNT_FILE", "config/gsc_service_account.json"
        )
        self._configured: Optional[bool] = None

    def is_configured(self) -> bool:
        """
        Returns True only if env vars and service account file are valid.

        This validates local configuration only and does not make network calls.
        """
        if self._configured is not None:
            return self._configured

        if not self._site_url:
            logger.warning(
                "GSC not configured: GSC_SITE_URL environment variable is not set. "
                "Performance data will be unavailable until this is configured."
            )
            self._configured = False
            return False

        key_path = Path(self._key_file)
        if not key_path.exists():
            logger.warning(
                "GSC not configured: Service account file not found at '%s'. "
                "Set GSC_SERVICE_ACCOUNT_FILE to the path of your service account "
                "JSON key.",
                self._key_file,
            )
            self._configured = False
            return False

        try:
            with open(key_path, "r", encoding="utf-8") as f:
                key_data = json.load(f)
            required_fields = ["type", "client_email", "private_key", "token_uri"]
            missing = [field for field in required_fields if field not in key_data]
            if missing:
                logger.warning(
                    "GSC not configured: Service account JSON is missing required "
                    "fields: %s",
                    missing,
                )
                self._configured = False
                return False
            if key_data.get("type") != "service_account":
                logger.warning(
                    "GSC not configured: JSON file is not a service account key "
                    "(type != 'service_account')."
                )
                self._configured = False
                return False
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("GSC not configured: Cannot read key file: %s", exc)
            self._configured = False
            return False

        self._configured = True
        return True

    def _get_service(self):
        """Lazily build and cache the Google API service object."""
        if not self.is_configured():
            raise RuntimeError(
                "GSC client is not configured. Call is_configured() before making "
                "API requests."
            )

        if self._service is None:
            credentials = service_account.Credentials.from_service_account_file(
                self._key_file,
                scopes=GSC_SCOPES,
            )
            self._service = build(
                GSC_API_SERVICE,
                GSC_API_VERSION,
                credentials=credentials,
                cache_discovery=False,
            )
            logger.info("GSC service initialized for property: %s", self._site_url)

        return self._service

    def verify_connection(self) -> dict:
        """Make a real API call to verify service-account access."""
        if not self.is_configured():
            return {
                "success": False,
                "site_url": self._site_url,
                "error": (
                    "Not configured - check GSC_SITE_URL and "
                    "GSC_SERVICE_ACCOUNT_FILE environment variables."
                ),
            }

        try:
            service = self._get_service()
            sites = service.sites().list().execute()
            site_entries = sites.get("siteEntry", [])
            matching = [s for s in site_entries if s.get("siteUrl") == self._site_url]
            if not matching:
                available = [s.get("siteUrl") for s in site_entries]
                return {
                    "success": False,
                    "site_url": self._site_url,
                    "error": (
                        f"Site '{self._site_url}' not found in service account "
                        f"properties. Available: {available}. Add the service "
                        "account email under Search Console users and permissions."
                    ),
                }

            permission = matching[0].get("permissionLevel", "unknown")
            return {
                "success": True,
                "site_url": self._site_url,
                "permission_level": permission,
                "error": None,
            }
        except HttpError as exc:
            return {
                "success": False,
                "site_url": self._site_url,
                "error": f"GSC API HTTP error: {exc.status_code} - {exc.reason}",
            }
        except Exception as exc:
            return {
                "success": False,
                "site_url": self._site_url,
                "error": str(exc),
            }

    def _query(self, body: dict) -> list[GSCRow]:
        """Execute searchAnalytics.query() with pagination."""
        service = self._get_service()
        all_rows: list[GSCRow] = []
        start_row = 0

        while True:
            paginated_body = {**body, "rowLimit": MAX_ROWS_PER_CALL, "startRow": start_row}
            try:
                response = (
                    service.searchanalytics()
                    .query(siteUrl=self._site_url, body=paginated_body)
                    .execute()
                )
            except HttpError as exc:
                if exc.status_code == 403:
                    raise PermissionError(
                        f"GSC API 403 Forbidden for '{self._site_url}'. Ensure the "
                        "service account is added in Search Console users and permissions."
                    ) from exc
                raise

            rows = response.get("rows", [])
            if not rows:
                break

            dimensions = body.get("dimensions", [])
            for row in rows:
                keys = row.get("keys", [])
                gsc_row = GSCRow(
                    clicks=row.get("clicks", 0),
                    impressions=row.get("impressions", 0),
                    ctr=row.get("ctr", 0.0),
                    position=row.get("position", 0.0),
                )

                for i, dim in enumerate(dimensions):
                    if i >= len(keys):
                        continue
                    if dim == "query":
                        gsc_row.query = keys[i]
                    elif dim == "page":
                        gsc_row.page = keys[i]
                    elif dim == "date":
                        gsc_row.date = keys[i]

                all_rows.append(gsc_row)

            if len(rows) < MAX_ROWS_PER_CALL:
                break
            start_row += MAX_ROWS_PER_CALL

        return all_rows

    def get_blog_performance_30d(self) -> list[GSCPerformanceSummary]:
        """Return page-level performance for /blog/ URLs over the last 30 days."""
        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=30)

        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "page",
                            "operator": "contains",
                            "expression": "/blog/",
                        }
                    ]
                }
            ],
            "dataState": "final",
        }

        rows = self._query(body)
        summaries: list[GSCPerformanceSummary] = []

        for row in sorted(rows, key=lambda r: r.impressions, reverse=True):
            top_query, top_impressions = self._get_top_query_for_page(
                row.page,
                start_date.isoformat(),
                end_date.isoformat(),
            )
            summaries.append(
                GSCPerformanceSummary(
                    page_url=row.page,
                    clicks_30d=row.clicks,
                    impressions_30d=row.impressions,
                    avg_ctr_30d=row.ctr,
                    avg_position_30d=row.position,
                    top_query=top_query,
                    top_query_impressions=top_impressions,
                    date_range_start=start_date.isoformat(),
                    date_range_end=end_date.isoformat(),
                )
            )

        logger.info(
            "GSC: Retrieved performance for %d blog URLs (%s to %s)",
            len(summaries),
            start_date,
            end_date,
        )
        return summaries

    def _get_top_query_for_page(
        self,
        page_url: str,
        start_date: str,
        end_date: str,
    ) -> tuple[str, int]:
        """Return the highest-impression query for a page in a date range."""
        try:
            body = {
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["query"],
                "dimensionFilterGroups": [
                    {
                        "filters": [
                            {
                                "dimension": "page",
                                "operator": "equals",
                                "expression": page_url,
                            }
                        ]
                    }
                ],
                "dataState": "final",
                "rowLimit": 1,
            }
            rows = self._query(body)
            if rows:
                return rows[0].query, rows[0].impressions
            return "", 0
        except Exception:
            return "", 0

    def get_query_performance_30d(self, max_queries: int = 200) -> list[GSCRow]:
        """Return query+page performance across the site for the last 30 days."""
        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=30)

        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["query", "page"],
            "dataState": "final",
            "rowLimit": max_queries,
        }

        return self._query(body)

    def get_page_impressions_30d(self, page_url: str) -> int:
        """Return total impressions for one page URL over the last 30 days."""
        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=30)

        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "page",
                            "operator": "equals",
                            "expression": page_url,
                        }
                    ]
                }
            ],
            "dataState": "final",
            "rowLimit": 1,
        }

        try:
            rows = self._query(body)
            return rows[0].impressions if rows else 0
        except Exception as exc:
            logger.warning("GSC: Could not get impressions for %s: %s", page_url, exc)
            return 0

    def get_weekly_site_summary(self) -> dict:
        """Return 7-day site-wide clicks/impressions/CTR/position summary."""
        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=7)

        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date"],
            "dataState": "final",
        }

        try:
            rows = self._query(body)
            total_clicks = sum(r.clicks for r in rows)
            total_impressions = sum(r.impressions for r in rows)
            avg_ctr = (total_clicks / total_impressions) if total_impressions else 0.0
            avg_position = (sum(r.position for r in rows) / len(rows)) if rows else 0.0

            return {
                "total_clicks_7d": total_clicks,
                "total_impressions_7d": total_impressions,
                "avg_ctr_7d": round(avg_ctr * 100, 2),
                "avg_position_7d": round(avg_position, 1),
                "date_range_start": start_date.isoformat(),
                "date_range_end": end_date.isoformat(),
                "data_available": True,
            }
        except Exception as exc:
            logger.warning("GSC: Could not get weekly summary: %s", exc)
            return {
                "total_clicks_7d": 0,
                "total_impressions_7d": 0,
                "avg_ctr_7d": 0.0,
                "avg_position_7d": 0.0,
                "data_available": False,
                "error": str(exc),
            }

    # Compatibility helpers for existing async callers.
    async def get_top_pages(self, days: int = 28) -> list[dict]:
        """Compatibility wrapper returning top pages for legacy call sites."""
        if not self.is_configured():
            return []

        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=days)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "dataState": "final",
            "rowLimit": 25,
        }
        rows = self._query(body)
        return [
            {
                "url": r.page,
                "impressions": r.impressions,
                "clicks": r.clicks,
                "ctr": r.ctr,
                "avg_position": r.position,
                "date_range": f"{start_date.isoformat()}:{end_date.isoformat()}",
            }
            for r in rows
        ]

    async def get_keyword_performance(self, keyword: str, days: int = 28) -> dict:
        """Compatibility wrapper returning aggregate query metrics."""
        if not self.is_configured():
            return {"impressions": 0, "clicks": 0, "ranked_keywords": 0}

        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=days)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["query"],
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": keyword,
                        }
                    ]
                }
            ],
            "dataState": "final",
            "rowLimit": 50,
        }
        rows = self._query(body)
        return {
            "impressions": sum(r.impressions for r in rows),
            "clicks": sum(r.clicks for r in rows),
            "ranked_keywords": len(rows),
        }

    async def get_impression_trend(self, days: int = 90) -> list[dict]:
        """Compatibility wrapper returning daily site trend."""
        if not self.is_configured():
            return []

        end_date = date.today() - timedelta(days=GSC_DATA_DELAY_DAYS)
        start_date = end_date - timedelta(days=days)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date"],
            "dataState": "final",
        }
        rows = self._query(body)
        return [
            {
                "date": r.date,
                "impressions": r.impressions,
                "clicks": r.clicks,
                "ctr": r.ctr,
                "position": r.position,
            }
            for r in rows
        ]


def init_gsc_tables(db_path: str | None = None) -> None:
    """Create query-performance table used by dashboard widgets and weekly sync."""
    import sqlite3
    if db_path is None:
        from src.config import settings_database_path
        db_path = settings_database_path


    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsc_query_performance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            query       TEXT NOT NULL,
            page_url    TEXT NOT NULL,
            clicks      INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            ctr         REAL DEFAULT 0.0,
            position    REAL DEFAULT 0.0,
            date_synced TEXT NOT NULL,
            date_range_start TEXT NOT NULL,
            date_range_end   TEXT NOT NULL,
            UNIQUE (query, page_url, date_synced)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gsc_impressions
        ON gsc_query_performance (impressions DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gsc_position
        ON gsc_query_performance (position)
        """
    )
    conn.commit()
    conn.close()


gsc_client = SearchConsoleClient()
