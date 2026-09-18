"""Root-level ASGI export.

Vercel's zero-config detection looks here first, and `uvicorn app:app` expects
it too. It shares :mod:`src.asgi` with ``api/index.py`` so whichever entry point
the platform picks, the behaviour — including the recovery fallback — is the same.
"""
from __future__ import annotations

import os
import sys

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from src.asgi import app  # noqa: E402,F401
