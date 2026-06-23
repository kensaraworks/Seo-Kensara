"""KensaraAI Content Hub — launch the CEO approval dashboard."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.ui.app:app",
        host="0.0.0.0",
        port=8888,
        reload=True,
        reload_dirs=["src/ui"],
    )
