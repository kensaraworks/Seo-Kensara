import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

from src.geo.entity_monitor import (
    check_knowledge_panel,
    monitor_brand_mentions,
    audit_third_party_listings,
    monitor_founder_brand
)
from src.agents.intent_classifier import IntentType

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

@patch("src.geo.entity_monitor.job_queue")
async def test_check_knowledge_panel(mock_queue):
    mock_data = {
        "knowledgeGraph": {
            "title": "Kensara AI",
            "website": "kensara.in"
        }
    }
    with patch("src.geo.entity_monitor._serper_search", return_value=mock_data):
        await check_knowledge_panel()
        
    assert mock_queue.record_entity_status.call_count > 0
    # First call args
    args = mock_queue.record_entity_status.call_args_list[0][0]
    assert args[0] == "Google Knowledge Graph"
    assert args[1] == "Verified"

@patch("src.geo.entity_monitor.job_queue")
@patch("src.geo.entity_monitor.monitor_linkedin_metrics", new_callable=AsyncMock)
async def test_monitor_brand_mentions(mock_linkedin, mock_queue):
    mock_data = {
        "organic": [
            {"link": "https://otherblog.com/top-dpdpa-tools", "snippet": "KensaraAI is great."}
        ]
    }
    with patch("src.geo.entity_monitor._serper_search", return_value=mock_data):
        await monitor_brand_mentions()
        
    assert mock_queue.record_unlinked_mention.call_count > 0
    args = mock_queue.record_unlinked_mention.call_args_list[0][0]
    assert args[0] == "otherblog.com"
    assert args[1] == "https://otherblog.com/top-dpdpa-tools"
    assert args[2] in ["KensaraAI", "KensaraAI Private Limited"]

@patch("src.geo.entity_monitor.job_queue")
async def test_audit_third_party_listings(mock_queue):
    mock_data = {
        "organic": [
            {"link": "https://www.g2.com/products/kensaraai", "snippet": "KensaraAI reviews."}
        ]
    }
    with patch("src.geo.entity_monitor._serper_search", return_value=mock_data):
        await audit_third_party_listings()
        
    assert mock_queue.record_entity_status.call_count > 0
    # First directory is G2
    args = mock_queue.record_entity_status.call_args_list[0][0]
    kwargs = mock_queue.record_entity_status.call_args_list[0][1]
    assert args[0] == "g2"
    assert args[1] == "Listed"
    assert kwargs["profile_url"] == "https://www.g2.com/products/kensaraai"

@patch("src.geo.entity_monitor.job_queue")
async def test_monitor_founder_brand(mock_queue):
    with patch("src.geo.entity_monitor.settings.tavily_api_key", "mock_key"):
        mock_tavily = AsyncMock()
        mock_tavily.search.return_value = {
            "results": [
                {"url": "https://news.com/prince", "content": "Prince Raj discusses data breach laws."}
            ]
        }
        with patch("tavily.AsyncTavilyClient", return_value=mock_tavily, create=True):
            mock_groq = AsyncMock()
            mock_msg = MagicMock()
            # Valid JSON string representing the topic
            mock_msg.content = '"Data breach laws"'
            mock_choice = MagicMock()
            mock_choice.message = mock_msg
            mock_groq.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
            
            with patch("groq.AsyncGroq", return_value=mock_groq, create=True):
                await monitor_founder_brand()
                
    assert mock_queue.record_founder_mention.call_count == 1
    assert mock_queue.upsert_keyword_cluster.call_count == 1
    upsert_kwargs = mock_queue.upsert_keyword_cluster.call_args[1]
    assert upsert_kwargs["keyword"] == "Data breach laws"
    assert upsert_kwargs["cluster_id"] == "founder_momentum"
