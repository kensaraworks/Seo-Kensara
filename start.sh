#!/bin/bash
# Start background cron scheduler
python -m src.main &

# Start FastAPI UI
python -m uvicorn src.ui.app:app --host 0.0.0.0 --port ${PORT:-8000}
