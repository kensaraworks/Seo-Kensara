import os
import sys
import traceback

# Ensure repository root is in sys.path so Vercel serverless functions can resolve imports from src
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from src.ui.app import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="KensaraAI Error Handler")

    @app.get("/{full_path:path}")
    async def catch_all_errors(full_path: str):
        tb = traceback.format_exc()
        return HTMLResponse(
            content=f"<html><head><title>Startup Error</title></head><body style='font-family:monospace;padding:20px;background:#1e1e1e;color:#f8f8f2;'><h2>Vercel Serverless Function Startup Error</h2><pre style='white-space:pre-wrap;'>{tb}</pre></body></html>",
            status_code=500,
        )

