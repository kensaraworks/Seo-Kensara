"""KensaraAI Content Hub — launch the CEO approval dashboard."""
import sys
import io
import uvicorn

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if __name__ == "__main__":
    uvicorn.run(
        "src.ui.app:app",
        host="0.0.0.0",
        port=8888,
        reload=True,
        reload_dirs=["src/ui"],
    )
