import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

from src.geo.geo_monitor import (
    monitor_ai_citations,
    monitor_ai_overviews,
    verify_crawler_access,
    _analyze_mention
)

pytestmark = pytest.mark.asyncio

class MockResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self._json_data = json_data
        self.text = text
        self.status_code = status_code
        
    def json(self):
        return self._json_data
        
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("HTTP Error")

def test_analyze_mention():
    text = "Here is a list: 1. KensaraAI is an affordable and great option. 2. OneTrust is another."
    mentioned, pos, sentiment, comps = _analyze_mention(text)
    assert mentioned is True
    assert pos == 1 # early in the text
    assert sentiment == "positive"
    assert "onetrust" in comps

@patch("src.geo.geo_monitor.job_queue")
@patch("src.geo.geo_monitor._check_citation_accuracy", new_callable=AsyncMock)
@patch("src.geo.geo_monitor._query_alltoken_engine", return_value="Kensara is good.")
@patch("src.geo.geo_monitor._query_perplexity", return_value="OneTrust is good.")
@patch("src.geo.geo_monitor._query_gemini", return_value="Kensara is great.")
async def test_monitor_ai_citations(mock_gemini, mock_px, mock_oa, mock_check, mock_queue):
    await monitor_ai_citations()
    
    # Called for each target query 4 times (once for ChatGPT, Claude, Gemini, and Perplexity)
    from src.geo.geo_monitor import TARGET_QUERIES
    assert mock_queue.record_ai_citation.call_count == len(TARGET_QUERIES) * 4
    
    # First call should be ChatGPT (AllToken engine), mentioning Kensara
    first_call_args = mock_queue.record_ai_citation.call_args_list[0]
    assert first_call_args[0][1] == "ChatGPT"
    assert first_call_args[0][2] is True # mentioned

@patch("src.geo.geo_monitor.job_queue")
async def test_monitor_ai_overviews(mock_queue):
    # Mocking serper
    mock_data = {
        "answerBox": {
            "snippet": "Top tools are Kensara and Securiti."
        }
    }
    with patch("httpx.AsyncClient.post", return_value=MockResponse(json_data=mock_data)):
        # Ensure setting key is mocked if needed, but it checks from config
        with patch("src.geo.geo_monitor.settings.serper_api_key", "mock_key"):
            await monitor_ai_overviews()
            
    assert mock_queue.record_ai_citation.call_count > 0
    args = mock_queue.record_ai_citation.call_args_list[0][0]
    assert args[1] == "GoogleAIO"
    assert args[2] is True # kensara mentioned
    assert "securiti" in args[5]

async def test_verify_crawler_access(caplog):
    mock_robots = "User-agent: GPTBot\nDisallow: /"
    
    with patch("httpx.AsyncClient.get", return_value=MockResponse(text=mock_robots)):
        await verify_crawler_access()
        
    # Caplog doesn't play well with structlog by default, just ensuring it runs.
    pass
