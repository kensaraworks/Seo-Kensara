"""Vercel serverless entry point.

Vercel's Python runtime picks up the module-level ``app``. The repository root
has to be on ``sys.path`` before ``src`` is importable; everything else lives in
:mod:`src.asgi`, which the root-level ``app.py`` shares so both entry points
behave identically.
"""
from __future__ import annotations

import os
import sys

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from src.asgi import app  # noqa: E402,F401
