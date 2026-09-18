"""Persistence layer for the KensaraAI SEO pipeline.

Every module in this package is safe to import on a cold serverless start:
nothing here touches the network, the filesystem or an LLM SDK at import time.
"""
from __future__ import annotations

__all__ = ["enforcement_store"]
