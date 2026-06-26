import os
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.analytics.search_console import gsc_client, SearchConsoleClient, init_gsc_tables, GSCRow
from src.analytics.gsc_widgets import (
    get_high_impression_low_ctr_queries,
    get_pages_near_page_one,
    get_zero_impression_posts,
    sync_gsc_query_data_to_db
)
from src.analytics.indexing_ping import ping_indexing_api
from src.analytics.feedback_loop import evaluate_content_performance
from src.ui.routers.schedule import JOBS



# TEST-GSC-01: is_configured() without credentials
def test_gsc_01_is_configured_no_credentials():
    client = SearchConsoleClient()
    # Mock unconfigured state
    client._site_url = ""
    client._configured = None
    assert client.is_configured() is False

# TEST-GSC-02: is_configured() with invalid key file path
def test_gsc_02_is_configured_invalid_key_file():
    client = SearchConsoleClient()
    client._site_url = "sc-domain:kensara.in"
    client._key_file = "non_existent_key_file.json"
    client._configured = None
    assert client.is_configured() is False

# TEST-GSC-03: verify_connection() with real credentials
def test_gsc_03_verify_connection_real():
    client = SearchConsoleClient()
    if not client.is_configured():
        pytest.skip("GSC not configured on this machine, skipping real connection test")
    
    res = client.verify_connection()
    assert res.get("success") is True
    assert res.get("site_url") == "sc-domain:kensara.in"
    assert res.get("permission_level") == "siteOwner"

# TEST-GSC-04: verify_connection() wrong site URL
def test_gsc_04_verify_connection_wrong_site():
    client = SearchConsoleClient()
    if not client.is_configured():
        pytest.skip("GSC not configured on this machine, skipping wrong site URL test")
    
    original_url = client._site_url
    try:
        client._site_url = "https://wrong-site.com/"
        res = client.verify_connection()
        assert res.get("success") is False
        assert "not found" in res.get("error").lower()
    finally:
        client._site_url = original_url

# TEST-GSC-05: get_blog_performance_30d() returns data or empty list
def test_gsc_05_get_blog_performance_30d():
    client = SearchConsoleClient()
    if not client.is_configured():
        pytest.skip("GSC not configured on this machine, skipping get_blog_performance_30d test")
        
    res = client.get_blog_performance_30d()
    assert isinstance(res, list)

# TEST-GSC-06: gsc_query_performance table created on startup
def test_gsc_06_table_creation():
    db_path = "drafts/.cache/test_jobs.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
        
    init_gsc_tables(db_path)
    
    conn = sqlite3.connect(db_path)
    res = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gsc_query_performance'").fetchone()
    conn.close()
    assert res is not None
    assert res[0] == "gsc_query_performance"
    
    if os.path.exists(db_path):
        os.remove(db_path)

# TEST-GSC-07: sync_gsc_query_data_to_db writes rows
def test_gsc_07_sync_query_data():
    db_path = "drafts/.cache/test_jobs_sync.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
        
    init_gsc_tables(db_path)
    
    mock_rows = [
        GSCRow(query="dpdpa compliance", page="https://kensara.in/blogs/dpdpa", clicks=5, impressions=100, ctr=0.05, position=2.3)
    ]
    
    count = sync_gsc_query_data_to_db(mock_rows, db_path=db_path)
    assert count == 1
    
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT query, clicks, impressions FROM gsc_query_performance").fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == "dpdpa compliance"
    assert row[1] == 5
    assert row[2] == 100
    
    if os.path.exists(db_path):
        os.remove(db_path)

# TEST-GSC-08: Dashboard widgets render without GSC data
def test_gsc_08_widgets_render():
    with patch("src.analytics.gsc_widgets.DB_PATH", "drafts/.cache/test_widgets.db"):
        init_gsc_tables("drafts/.cache/test_widgets.db")
        
        res1 = get_high_impression_low_ctr_queries()
        assert isinstance(res1, list)
        
        res2 = get_pages_near_page_one()
        assert isinstance(res2, list)
        
        res3 = get_zero_impression_posts()
        assert isinstance(res3, list)
        
        if os.path.exists("drafts/.cache/test_widgets.db"):
            os.remove("drafts/.cache/test_widgets.db")

# TEST-GSC-09: Indexing ping fires on queue approval
def test_gsc_09_indexing_ping():
    res = ping_indexing_api("https://www.kensara.in/blogs/test-post")
    assert isinstance(res, dict)
    assert "success" in res
    assert "url" in res

# TEST-GSC-10: Feedback loop skips gracefully when GSC not configured
@pytest.mark.asyncio
async def test_gsc_10_feedback_loop_skips_unconfigured():
    client = SearchConsoleClient()
    client._site_url = ""
    client._configured = None
    
    with patch("src.analytics.search_console.gsc_client", client):
        res = await evaluate_content_performance()
        assert isinstance(res, dict)
        assert res.get("winner") == 0
        assert res.get("dead") == 0

# TEST-GSC-11: Schedule page shows gsc_sync job
def test_gsc_11_schedule_shows_job():
    gsc_job = [j for j in JOBS if j["id"] == "gsc_sync"]
    assert len(gsc_job) == 1
    assert gsc_job[0]["name"] == "GSC Weekly Sync"
    assert gsc_job[0]["status"] in ["active", "not_configured"]

# TEST-GSC-12: Manual trigger of gsc_sync from schedule UI
@pytest.mark.asyncio
@patch("src.ui.routers.schedule.gsc_client")
@patch("src.ui.routers.schedule.sync_gsc_query_data_to_db", return_value=10)
async def test_gsc_12_manual_trigger(mock_sync, mock_client):
    mock_client.is_configured.return_value = True
    mock_client.get_blog_performance_30d.return_value = []
    mock_client.get_query_performance_30d.return_value = []
    
    from src.ui.routers.schedule import _dispatch_job
    res = await _dispatch_job("gsc_sync")
    assert res["count"] == 10
    assert "GSC weekly sync completed" in res["message"]
