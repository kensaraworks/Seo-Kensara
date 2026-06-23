"""Google Search Console API client — scaffold for CEO to connect when ready.

Setup required (one-time, CEO does this):
1. Go to Google Search Console → Add kensara.in as property
2. Open Google Cloud Console → Create a service account
3. Download credentials.json for the service account
4. Set env var: GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json
5. In Search Console → Settings → Users and permissions → Add service account email
6. Grant the service account "Full" or "Restricted" access to the property

Dependencies (add to requirements.txt when credentials are ready):
    google-auth>=2.29.0
    google-auth-httplib2>=0.2.0
    google-api-python-client>=2.129.0

Once credentials are in place, uncomment the TODO blocks below.
"""
import structlog
from pathlib import Path
from pydantic import BaseModel

log = structlog.get_logger()

# TODO: Uncomment when google-api-python-client is installed:
# from googleapiclient.discovery import build
# from google.oauth2 import service_account

SEARCH_CONSOLE_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "sc-domain:kensara.in"  # Use domain property format


class SearchConsoleMetrics(BaseModel):
    url: str
    impressions: int
    clicks: int
    ctr: float
    avg_position: float
    date_range: str


class SearchConsoleClient:
    """
    Google Search Console API client.

    Currently a scaffold — returns empty data until CEO completes OAuth setup.
    All methods degrade gracefully with warning logs rather than crashing.
    """

    def __init__(self) -> None:
        self._service = None
        self._credentials_path = Path("credentials.json")

        # TODO: Replace with real initialization once credentials.json is present:
        # try:
        #     credentials = service_account.Credentials.from_service_account_file(
        #         str(self._credentials_path),
        #         scopes=SEARCH_CONSOLE_SCOPES,
        #     )
        #     self._service = build("searchconsole", "v1", credentials=credentials)
        #     log.info("search_console_connected", site=SITE_URL)
        # except FileNotFoundError:
        #     log.warning("search_console_credentials_missing",
        #                 path=str(self._credentials_path),
        #                 action="See src/analytics/search_console.py setup instructions")
        # except Exception as exc:
        #     log.error("search_console_init_failed", error=str(exc))

        log.info(
            "search_console_init",
            status="scaffold_only",
            action="complete OAuth setup to enable — see module docstring",
        )

    def is_configured(self) -> bool:
        """True if the Search Console API client was successfully initialized."""
        return self._service is not None

    async def get_top_pages(self, days: int = 28) -> list[SearchConsoleMetrics]:
        """
        Get top performing pages by clicks over the last N days.
        Returns empty list until Search Console credentials are configured.

        TODO: When configured, use:
            request = {
                "startDate": (date.today() - timedelta(days=days)).isoformat(),
                "endDate": date.today().isoformat(),
                "dimensions": ["page"],
                "rowLimit": 25,
            }
            response = self._service.searchanalytics().query(
                siteUrl=SITE_URL, body=request
            ).execute()
        """
        if not self.is_configured():
            log.warning(
                "search_console_not_configured",
                method="get_top_pages",
                action="returning_empty_data",
            )
            return []

        # TODO: Implement real API call when credentials are available.
        return []  # pragma: no cover

    async def get_keyword_performance(self, keyword: str, days: int = 28) -> dict:
        """
        Get impressions, clicks, CTR, and avg position for a specific query.
        Returns empty dict until Search Console credentials are configured.

        TODO: When configured, use:
            request = {
                "startDate": (date.today() - timedelta(days=days)).isoformat(),
                "endDate": date.today().isoformat(),
                "dimensions": ["query"],
                "dimensionFilterGroups": [{
                    "filters": [{"dimension": "query", "expression": keyword}]
                }],
            }
        """
        if not self.is_configured():
            log.warning(
                "search_console_not_configured",
                method="get_keyword_performance",
                keyword=keyword[:50],
                action="returning_empty_data",
            )
            return {}

        # TODO: Implement real API call when credentials are available.
        return {}  # pragma: no cover

    async def get_impression_trend(self, days: int = 90) -> list[dict]:
        """
        Get daily impression/click trend for the whole site.
        Returns empty list until configured.

        Useful for the CEO dashboard to show organic traffic growth over time.
        """
        if not self.is_configured():
            log.warning(
                "search_console_not_configured",
                method="get_impression_trend",
                action="returning_empty_data",
            )
            return []

        # TODO: Implement real API call when credentials are available.
        return []  # pragma: no cover
