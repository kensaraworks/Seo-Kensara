import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.agents.trending_monitor import (
    monitor_google_trends,
    monitor_google_autocomplete,
    monitor_reddit_quora,
    monitor_linkedin
)
from src.agents.intent_classifier import IntentType

pytestmark = pytest.mark.asyncio

class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        
    def json(self):
        return self._json_data

@patch("src.agents.trending_monitor.job_queue")
@patch("src.agents.trending_monitor.classify_intent", return_value=MagicMock(value="informational"))
async def test_monitor_google_autocomplete(mock_classify, mock_queue):
    mock_data = ["DPDPA compliance", ["DPDPA compliance software", "DPDPA compliance checklist"]]
    
    with patch("httpx.AsyncClient.get", return_value=MockResponse(mock_data)):
        await monitor_google_autocomplete()
        
    # We have 4 seed keywords, each mock returns the 2 same suggestions
    # Only unique suggestions (not matching seed exactly) are kept
    assert mock_queue.upsert_keyword_cluster.call_count > 0
    calls = mock_queue.upsert_keyword_cluster.call_args_list
    upserted_keywords = [c.kwargs['keyword'] for c in calls]
    assert "DPDPA compliance software" in upserted_keywords
    assert "DPDPA compliance checklist" in upserted_keywords

@patch("src.agents.trending_monitor.job_queue")
@patch("src.agents.trending_monitor.classify_intent", return_value=MagicMock(value="informational"))
@patch("src.agents.trending_monitor._get_tavily_client")
@patch("src.agents.trending_monitor._get_groq_client")
async def test_monitor_reddit_quora(mock_groq, mock_tavily, mock_classify, mock_queue):
    mock_tavily_instance = AsyncMock()
    mock_tavily_instance.search.return_value = {
        "results": [
            {"title": "Question 1", "content": "Blah", "url": "reddit.com/1", "raw_content": "Full question details"}
        ]
    }
    mock_tavily.return_value = mock_tavily_instance
    
    mock_groq_instance = AsyncMock()
    # Mock groq LLM returning a JSON array of questions
    mock_msg = MagicMock()
    mock_msg.content = '["What is DPDPA?", "How to comply with DPDPA?"]'
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_groq_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
    mock_groq.return_value = mock_groq_instance

    await monitor_reddit_quora()
    
    assert mock_queue.upsert_keyword_cluster.call_count > 0
    upserted_keywords = [c.kwargs['keyword'] for c in mock_queue.upsert_keyword_cluster.call_args_list]
    assert "What is DPDPA?" in upserted_keywords

@patch("src.agents.trending_monitor.job_queue")
@patch("src.agents.trending_monitor.classify_intent", return_value=MagicMock(value="commercial"))
@patch("src.agents.trending_monitor._get_tavily_client")
@patch("src.agents.trending_monitor._get_groq_client")
async def test_monitor_linkedin(mock_groq, mock_tavily, mock_classify, mock_queue):
    mock_tavily_instance = AsyncMock()
    mock_tavily_instance.search.return_value = {
        "results": [{"content": "We need a better tool for DPDPA."}]
    }
    mock_tavily.return_value = mock_tavily_instance
    
    mock_groq_instance = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = '["Better DPDPA Tool"]'
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_groq_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
    mock_groq.return_value = mock_groq_instance

    await monitor_linkedin()
    
    assert mock_queue.upsert_keyword_cluster.call_count > 0
    upserted_keywords = [c.kwargs['keyword'] for c in mock_queue.upsert_keyword_cluster.call_args_list]
    assert "Better DPDPA Tool" in upserted_keywords

@patch("src.agents.trending_monitor.job_queue")
@patch("src.agents.trending_monitor.classify_intent", return_value=MagicMock(value="informational"))
async def test_monitor_google_trends(mock_classify, mock_queue):
    # Mock pytrends internal behavior
    class MockTrendReq:
        def __init__(self, *args, **kwargs): pass
        def build_payload(self, *args, **kwargs): pass
        def related_queries(self):
            import pandas as pd
            df = pd.DataFrame({"query": ["Breakout Query", "Normal Query"], "value": ["Breakout", 100]})
            return {"Digital Personal Data Protection Act": {"rising": df}}
    
    with patch("src.agents.trending_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = ["Breakout Query"]
        await monitor_google_trends()
        
    assert mock_queue.upsert_keyword_cluster.call_count == 1
    assert mock_queue.upsert_keyword_cluster.call_args[1]['keyword'] == "Breakout Query"
