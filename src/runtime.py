"""Runtime environment detection.

Imported on every cold start, so it stays dependency-free.
"""
from __future__ import annotations

import os

APP_VERSION = "2.0.0"


def is_serverless() -> bool:
    """True on Vercel or AWS Lambda.

    Serverless invocations have no durable disk and no long-lived process, so
    the in-process scheduler and any write-behind cache must stay switched off
    there; Supabase and Vercel Cron cover those jobs instead.
    """
    return bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def deployment_info() -> dict:
    """Which build is actually serving, from Vercel's build-time env vars.

    Without this there is no way to tell a stale deployment from a fresh one
    when both return the same error.
    """
    sha = os.getenv("VERCEL_GIT_COMMIT_SHA", "")
    return {
        "commit": sha[:12] or "unknown",
        "branch": os.getenv("VERCEL_GIT_COMMIT_REF", "") or "unknown",
        "env": os.getenv("VERCEL_ENV", "") or "local",
        "region": os.getenv("VERCEL_REGION", "") or "local",
    }


def platform_name() -> str:
    if os.getenv("VERCEL"):
        return "vercel"
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return "aws-lambda"
    return "server"


# ── Timezone ──────────────────────────────────────────────────────────────────
# The whole product reports times in IST. `zoneinfo` reads the tz database from
# the operating system, and serverless images routinely ship without one — which
# makes `ZoneInfo("Asia/Kolkata")` raise ZoneInfoNotFoundError at import and take
# the entire application down. `tzdata` in requirements.txt supplies the database,
# and the fixed-offset fallback below means a missing database degrades the
# timezone rather than the site. IST is UTC+05:30 year-round with no DST, so the
# fallback is exact rather than approximate.

from datetime import timedelta, timezone as _timezone

IST_OFFSET = timedelta(hours=5, minutes=30)
IST_NAME = "Asia/Kolkata"


def _resolve_ist():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(IST_NAME)
    except Exception:
        return _timezone(IST_OFFSET, "IST")


#: Always a usable tzinfo. Import this instead of calling ZoneInfo directly.
IST = _resolve_ist()


def now_ist():
    """Current time in IST."""
    from datetime import datetime

    return datetime.now(tz=IST)


def now_ist_label() -> str:
    """`2026-09-19 04:31 IST` — the timestamp shown in the dashboard header."""
    return now_ist().strftime("%Y-%m-%d %H:%M IST")
