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


def platform_name() -> str:
    if os.getenv("VERCEL"):
        return "vercel"
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return "aws-lambda"
    return "server"
