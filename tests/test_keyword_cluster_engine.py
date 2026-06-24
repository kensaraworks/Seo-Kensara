import pytest
from datetime import date
from unittest.mock import patch
from src.agents.keyword_cluster_engine import (
    _calculate_coverage_score,
    _get_deadline_boost,
    run_cluster_gap_auto_queue,
    initialize_clusters
)
from src.queue.job_queue import job_queue

def test_calculate_coverage_score():
    # Formula: (published / total) * (ranking / published) = ranking / total
    # Provided there are no zeros.
    stats1 = {"total": 10, "published": 5, "ranking": 2}
    # (5/10) * (2/5) = 0.5 * 0.4 = 0.2
    assert abs(_calculate_coverage_score(stats1) - 0.2) < 0.001

    stats2 = {"total": 10, "published": 0, "ranking": 0}
    assert _calculate_coverage_score(stats2) == 0.0

@patch("src.agents.keyword_cluster_engine.date")
def test_get_deadline_boost(mock_date):
    # Mock today to be Oct 1, 2026 (31 days before Nov 1, 2026)
    mock_date.today.return_value = date(2026, 10, 1)
    # Also patch the constructor so we can create dates inside the function
    mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
    
    # Needs to match the word '2026' or 'november'
    boost = _get_deadline_boost("DPDPA compliance November 2026")
    assert boost == 50.0

    # Outside the 90 day window or no keyword match
    mock_date.today.return_value = date(2026, 1, 1)
    boost2 = _get_deadline_boost("DPDPA compliance November 2026")
    assert boost2 == 0.0

@pytest.mark.asyncio
async def test_run_cluster_gap_auto_queue():
    # Setup test DB
    job_queue._init_db()
    with job_queue._connect() as conn:
        conn.execute("DELETE FROM content_queue")
    
    # Run the auto queue logic
    await run_cluster_gap_auto_queue()
    
    # Verify that content_queue was populated with at most 3 items
    # (Because the script is configured to queue 3 items total)
    with job_queue._connect() as conn:
        count = conn.execute("SELECT count(*) as c FROM content_queue").fetchone()["c"]
        assert count > 0
        assert count <= 3
