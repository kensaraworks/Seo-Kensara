from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

INDEXING_SCOPES = ["https://www.googleapis.com/auth/indexing"]
INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"


def ping_indexing_api(page_url: str) -> dict:
    """
    Submit a URL to the Google Indexing API for immediate crawl.

    Returns:
        {
            "success": bool,
            "url": str,
            "response": dict | None,
            "error": str | None,
        }

    This function never raises exceptions to callers. Approval flow should not
    be blocked if indexing ping fails.
    """
    key_file = os.environ.get(
        "GSC_SERVICE_ACCOUNT_FILE",
        "config/gsc_service_account.json",
    )

    if not Path(key_file).exists():
        logger.info(
            "Indexing API ping skipped for %s: GSC_SERVICE_ACCOUNT_FILE not configured.",
            page_url,
        )
        return {
            "success": False,
            "url": page_url,
            "response": None,
            "error": "Not configured",
        }

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            key_file,
            scopes=INDEXING_SCOPES,
        )
        service = build("indexing", "v3", credentials=credentials, cache_discovery=False)
        response = service.urlNotifications().publish(
            body={"url": page_url, "type": "URL_UPDATED"}
        ).execute()

        logger.info(
            "Indexing API: Pinged Google to crawl %s. Response: %s",
            page_url,
            response,
        )
        return {
            "success": True,
            "url": page_url,
            "response": response,
            "error": None,
        }

    except Exception as exc:
        logger.warning(
            "Indexing API ping failed for %s: %s. "
            "The post is still approved. Google will crawl it on its normal schedule.",
            page_url,
            exc,
        )
        return {
            "success": False,
            "url": page_url,
            "response": None,
            "error": str(exc),
        }
