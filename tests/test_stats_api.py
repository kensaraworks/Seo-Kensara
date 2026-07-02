import sqlite3
import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ui.routers.api import router
from src.config import settings


def _prepare_stats_db(base: Path) -> None:
    cache_dir = base / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "jobs.db"
    conn = sqlite3.connect(str(db_path))
    
    # 1. Create generation_log table and insert dummy rows
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generation_log (
            job_id TEXT,
            keyword TEXT,
            tier INTEGER,
            cluster TEXT,
            qa_score REAL,
            geo_score INTEGER,
            risk_level TEXT,
            word_count INTEGER,
            time_to_generate_seconds REAL,
            model_primary TEXT,
            model_fallback_used INTEGER,
            tokens_spent INTEGER,
            cost_usd REAL,
            timestamp TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO generation_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job1", "kw1", 1, "compliance", 0.85, 15, "low", 1000, 15.2, "groq", 0, 5000, 0.003, "2026-06-25T12:00:00Z")
    )
    conn.execute(
        "INSERT INTO generation_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job2", "kw2", 2, "compliance", 0.75, 14, "medium", 800, 10.1, "groq", 1, 3000, 0.002, "2026-06-26T12:00:00Z")
    )
    
    # 2. Create token_cost_log table and insert dummy rows
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_cost_log (
            job_id TEXT,
            model_used TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            timestamp TEXT,
            tier INTEGER,
            cluster_id TEXT,
            task TEXT
        )
        """
    )
    # Current month matches dynamically
    current_month_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    conn.execute(
        "INSERT INTO token_cost_log VALUES (?,?,?,?,?,?,?,?,?)",
        ("job1", "groq", 3000, 2000, 0.003, f"{current_month_prefix}-25T12:00:00Z", 1, "compliance", "section")
    )
    conn.execute(
        "INSERT INTO token_cost_log VALUES (?,?,?,?,?,?,?,?,?)",
        ("job2", "groq", 2000, 1000, 0.002, f"{current_month_prefix}-26T12:00:00Z", 2, "compliance", "outline")
    )
    # Past month to test filtering
    conn.execute(
        "INSERT INTO token_cost_log VALUES (?,?,?,?,?,?,?,?,?)",
        ("job3", "groq", 1000, 1000, 0.001, "2026-05-01T12:00:00Z", 1, "general", "section")
    )
    
    conn.commit()
    conn.close()


def test_get_stats_endpoint(monkeypatch, tmp_path):
    # Setup test DB
    _prepare_stats_db(tmp_path)
    monkeypatch.setattr(settings, "content_output_dir", str(tmp_path))

    # Mock drafts collection
    import src.ui.app as ui_app
    monkeypatch.setattr(
        ui_app,
        "_collect_drafts",
        lambda: [
            {"approved": True, "folder": "blogs", "path": "drafts/blogs/1.md"},
            {"approved": False, "status": "approved", "folder": "blogs", "path": "drafts/blogs/2.md"},
            {"approved": False, "status": "draft", "folder": "blogs", "path": "drafts/blogs/3.md"},
            {"approved": True, "folder": "linkedin", "path": "drafts/linkedin/1.md"}, # should be ignored (folder is linkedin)
        ],
    )

    # Setup app client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/v1/stats")
    assert response.status_code == 200
    payload = response.json()

    # Assert correct values
    assert payload["total_posts_generated"] == 2
    assert payload["total_approved"] == 2  # blogs path and approved or status approved
    assert payload["total_tokens_used_this_month"] == 8000  # 3000+2000 + 2000+1000 (past month job3 is excluded)
    assert payload["total_cost_usd_this_month"] == 0.005  # 0.003 + 0.002
    assert payload["top_cluster_by_volume"] == "compliance"
    assert payload["avg_qa_score"] == 0.8  # (0.85 + 0.75) / 2
