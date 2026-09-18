#!/bin/bash
set -euo pipefail

# The FastAPI app owns the scheduler (src/ui/scheduler.py) on long-running
# hosts, so this starts one process, not two. Running `python -m src.main`
# alongside it would register the same jobs a second time.
exec python -m uvicorn src.ui.app:app --host 0.0.0.0 --port "${PORT:-8000}"
