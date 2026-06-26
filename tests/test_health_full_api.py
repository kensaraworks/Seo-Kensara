import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ui.routers.api import router


def _prepare_jobs_db(base: Path) -> None:
    cache_dir = base / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "jobs.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stories_processed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT
        )
        """
    )
    conn.commit()
    conn.close()


class _DummyScheduler:
    running = True

    def get_jobs(self):
        return ["job1", "job2"]


def test_health_full_unhealthy_when_groq_missing(monkeypatch, tmp_path):
    _prepare_jobs_db(tmp_path)

    monkeypatch.setenv("CONTENT_OUTPUT_DIR", str(tmp_path))

    # Required keys except GROQ to validate TODO-015 unhealthy behavior.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "replace_me")
    monkeypatch.setenv("TAVILY_API_KEY", "replace_me")
    monkeypatch.setenv("SERPER_API_KEY", "replace_me")

    app = FastAPI()
    app.include_router(router)
    app.state.scheduler = _DummyScheduler()

    client = TestClient(app)
    response = client.get("/api/v1/health/full")
    assert response.status_code == 200
    payload = response.json()

    assert payload["checks"]["groq"] == "fail"
    assert payload["status"] == "unhealthy"
