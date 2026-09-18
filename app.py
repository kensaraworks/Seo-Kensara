"""Root-level ASGI export.

Some hosts (and `uvicorn app:app`) expect the application at the repository
root. The real application lives in :mod:`src.ui.app`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ui.app import app  # noqa: E402,F401
