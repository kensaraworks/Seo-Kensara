"""Tests for court_judgment_tracker.py and india_business_news.py."""
import pytest

from src.scrapers.court_judgment_tracker import (
    fetch_indiankanoon_judgments,
    fetch_supreme_court_privacy_orders,
    fetch_high_court_privacy_judgments,
    fetch_data_protection_board_orders,
    fetch_all_court_judgments,
    _extract_court_name,
    _extract_date_from_text,
)
from src.scrapers.india_business_news import (
    fetch_et_business_news,
    fetch_inc42_news,
    fetch_yourstory_news,
    fetch_entrackr_news,
    fetch_all_india_business_news,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_response(mocker, html: str, status_code: int = 200):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = html
    mock_resp.raise_for_status = mocker.MagicMock()
    return mock_resp


# ---------------------------------------------------------------------------
# court_judgment_tracker — IndiaKanoon direct scrape
# ---------------------------------------------------------------------------

_INDIANKANOON_HTML = """
<html><body>
  <div class="result">
    <h2><a href="/doc/12345/">Aarogya Setu vs Privacy Petitioner — Delhi High Court — 15 Jan 2025</a></h2>
    <p class="headnote">The court held that collection of health data without explicit consent violates
    the right to privacy under Article 21. DPDPA obligations apply to government entities.</p>
  </div>
  <div class="result">
    <h2><a href="/doc/67890/">Data Breach case — Supreme Court of India — 20 Feb 2026</a></h2>
    <p class="headnote">Supreme Court ruling on data fiduciary obligations under DPDPA section 8.</p>
  </div>
</body></html>
"""


@pytest.mark.asyncio
async def test_fetch_indiankanoon_judgments_direct_scrape(mocker):
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_get.return_value = _make_response(mocker, _INDIANKANOON_HTML)

    items = await fetch_indiankanoon_judgments()

    assert len(items) == 2
    assert items[0].url == "https://indiankanoon.org/doc/12345/"
    assert "Delhi High Court" in items[0].source or "IndiaKanoon" in items[0].source
    assert "DPDPA" in items[0].summary or "consent" in items[0].summary
    assert items[1].url == "https://indiankanoon.org/doc/67890/"


@pytest.mark.asyncio
async def test_fetch_indiankanoon_judgments_tavily_fallback(mocker):
    """When HTTP scrape fails, falls back to Tavily search."""
    mocker.patch("httpx.AsyncClient.get", side_effect=Exception("connection refused"))
    mock_tavily = mocker.patch(
        "src.scrapers.court_judgment_tracker._tavily_fallback_search",
        return_value=[],
    )

    items = await fetch_indiankanoon_judgments()

    assert items == []
    mock_tavily.assert_called_once()
    assert "indiankanoon.org" in mock_tavily.call_args[0][0]


# ---------------------------------------------------------------------------
# court_judgment_tracker — Supreme Court (Tavily only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_supreme_court_privacy_orders_uses_tavily(mocker):
    mock_tavily = mocker.patch(
        "src.scrapers.court_judgment_tracker._tavily_fallback_search",
        return_value=[],
    )

    await fetch_supreme_court_privacy_orders()

    assert mock_tavily.call_count >= 1
    first_query = mock_tavily.call_args_list[0][0][0]
    assert "sci.gov.in" in first_query or "supremecourt" in first_query.lower()


# ---------------------------------------------------------------------------
# court_judgment_tracker — High Courts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_high_court_privacy_judgments_queries_four_courts(mocker):
    mock_tavily = mocker.patch(
        "src.scrapers.court_judgment_tracker._tavily_fallback_search",
        return_value=[],
    )

    await fetch_high_court_privacy_judgments()

    # Should issue one Tavily query per High Court (Delhi, Bombay, Madras, Karnataka)
    assert mock_tavily.call_count == 4
    court_names_called = [call[0][1] for call in mock_tavily.call_args_list]
    assert "Delhi High Court" in court_names_called
    assert "Bombay High Court" in court_names_called


# ---------------------------------------------------------------------------
# court_judgment_tracker — DPBI adjudication orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_data_protection_board_orders_deduplicates(mocker):
    from src.scrapers.regulatory_scrapers import NewsItem

    shared_item = NewsItem(
        title="DPBI Order No. 1/2026",
        url="https://dpboard.gov.in/order/1",
        summary="Penalty order against data fiduciary for DPDPA section 12 violation.",
        published_date="2026-01-15",
        source="Data Protection Board (Adjudication)",
    )

    # All three Tavily queries return the same item
    mocker.patch(
        "src.scrapers.court_judgment_tracker._tavily_fallback_search",
        return_value=[shared_item],
    )

    items = await fetch_data_protection_board_orders()

    # Deduplication by URL should yield exactly one item
    assert len(items) == 1
    assert items[0].url == "https://dpboard.gov.in/order/1"


# ---------------------------------------------------------------------------
# court_judgment_tracker — fetch_all_court_judgments aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_all_court_judgments_aggregates_and_deduplicates(mocker):
    from src.scrapers.regulatory_scrapers import NewsItem

    item_a = NewsItem(
        title="SC Judgment A",
        url="https://indiankanoon.org/doc/111/",
        summary="Supreme Court on DPDPA.",
        published_date="2026-01-01",
        source="Supreme Court of India",
    )
    item_b = NewsItem(
        title="HC Judgment B",
        url="https://indiankanoon.org/doc/222/",
        summary="Delhi HC on data breach.",
        published_date="2026-02-01",
        source="Delhi High Court",
    )

    mocker.patch(
        "src.scrapers.court_judgment_tracker.fetch_indiankanoon_judgments",
        return_value=[item_a],
    )
    mocker.patch(
        "src.scrapers.court_judgment_tracker.fetch_supreme_court_privacy_orders",
        return_value=[item_a],  # duplicate of item_a
    )
    mocker.patch(
        "src.scrapers.court_judgment_tracker.fetch_high_court_privacy_judgments",
        return_value=[item_b],
    )
    mocker.patch(
        "src.scrapers.court_judgment_tracker.fetch_data_protection_board_orders",
        return_value=[],
    )

    items = await fetch_all_court_judgments()

    assert len(items) == 2
    urls = {i.url for i in items}
    assert "https://indiankanoon.org/doc/111/" in urls
    assert "https://indiankanoon.org/doc/222/" in urls


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_extract_court_name_supreme():
    assert _extract_court_name("Supreme Court of India ruling 2025") == "Supreme Court of India"


def test_extract_court_name_delhi_hc():
    assert _extract_court_name("Delhi High Court judgment on privacy") == "Delhi High Court"


def test_extract_court_name_dpbi():
    assert _extract_court_name("Data Protection Board of India order") == "Data Protection Board of India"


def test_extract_court_name_unknown():
    assert _extract_court_name("random unrelated text") == ""


def test_extract_date_iso_format():
    assert _extract_date_from_text("Decided on 2025-03-14") == "2025-03-14"


def test_extract_date_slash_format():
    result = _extract_date_from_text("Order dated 15/01/2025 by the court")
    assert "2025" in result


def test_extract_date_fallback_is_today():
    from datetime import date
    assert _extract_date_from_text("no date here") == str(date.today())


# ---------------------------------------------------------------------------
# india_business_news — ET direct scrape
# ---------------------------------------------------------------------------

_ET_HTML = """
<html><body>
  <div class="eachStory">
    <h3><a href="/tech/startups/dpdpa-compliance-guide">
      DPDPA Compliance: A guide for Indian startups in 2025
    </a></h3>
    <p class="synopsis">
      With DPDPA rules imminent, Indian startups must appoint a DPO and implement
      a consent management framework before the deadline.
    </p>
  </div>
  <div class="eachStory">
    <h3><a href="/tech/fintech/data-breach-penalty">
      Fintech firm faces ₹5 crore penalty for data breach under DPDPA
    </a></h3>
    <p class="synopsis">The Data Protection Board issued its first penalty order against a Mumbai-based fintech.</p>
  </div>
</body></html>
"""


@pytest.mark.asyncio
async def test_fetch_et_business_news_direct_scrape(mocker):
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_get.return_value = _make_response(mocker, _ET_HTML)

    items = await fetch_et_business_news()

    assert len(items) == 2
    assert items[0].source == "ET Business News"
    assert "dpdpa-compliance-guide" in items[0].url
    assert "DPDPA" in items[0].title or "DPDPA" in items[0].summary
    assert items[1].source == "ET Business News"
    assert "penalty" in items[1].title.lower()


@pytest.mark.asyncio
async def test_fetch_et_business_news_tavily_fallback(mocker):
    mocker.patch("httpx.AsyncClient.get", side_effect=Exception("timeout"))
    mock_tavily = mocker.patch(
        "src.scrapers.india_business_news._tavily_fallback_search",
        return_value=[],
    )

    await fetch_et_business_news()

    mock_tavily.assert_called_once()
    assert "economictimes" in mock_tavily.call_args[0][0]


# ---------------------------------------------------------------------------
# india_business_news — Inc42 direct scrape
# ---------------------------------------------------------------------------

_INC42_HTML = """
<html><body>
  <article>
    <h2><a href="/features/dpdpa-startup-compliance-guide/">
      DPDPA For Startups: What You Need To Know Before The Rules Are Notified
    </a></h2>
    <p class="excerpt">Indian startups must start building consent infrastructure now.</p>
  </article>
</body></html>
"""


@pytest.mark.asyncio
async def test_fetch_inc42_news_direct_scrape(mocker):
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_get.return_value = _make_response(mocker, _INC42_HTML)

    items = await fetch_inc42_news()

    assert len(items) == 1
    assert items[0].source == "Inc42"
    assert "dpdpa-startup-compliance-guide" in items[0].url
    assert "DPDPA" in items[0].title


@pytest.mark.asyncio
async def test_fetch_inc42_news_tavily_fallback(mocker):
    mocker.patch("httpx.AsyncClient.get", side_effect=Exception("timeout"))
    mock_tavily = mocker.patch(
        "src.scrapers.india_business_news._tavily_fallback_search",
        return_value=[],
    )

    await fetch_inc42_news()

    mock_tavily.assert_called_once()
    assert "inc42.com" in mock_tavily.call_args[0][0]


# ---------------------------------------------------------------------------
# india_business_news — YourStory direct scrape
# ---------------------------------------------------------------------------

_YOURSTORY_HTML = """
<html><body>
  <article>
    <h3><a href="/2025/06/dpdpa-compliance-yourstory">
      How Indian SaaS companies are preparing for DPDPA compliance in 2025
    </a></h3>
    <p class="description">A look at how Zoho, Freshworks, and other Indian SaaS firms are
    approaching DPDPA compliance ahead of the rules notification.</p>
  </article>
</body></html>
"""


@pytest.mark.asyncio
async def test_fetch_yourstory_news_direct_scrape(mocker):
    mock_session_instance = mocker.MagicMock()
    mock_session_instance.get = mocker.AsyncMock(return_value=_make_response(mocker, _YOURSTORY_HTML))
    
    mock_session_class = mocker.patch("curl_cffi.requests.AsyncSession")
    mock_session_class.return_value.__aenter__.return_value = mock_session_instance

    items = await fetch_yourstory_news()

    assert len(items) == 1
    assert items[0].source == "YourStory"
    assert "dpdpa-compliance-yourstory" in items[0].url


# ---------------------------------------------------------------------------
# india_business_news — fetch_all_india_business_news aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_all_india_business_news_aggregates_and_deduplicates(mocker):
    from src.scrapers.regulatory_scrapers import NewsItem

    item_et = NewsItem(
        title="ET: DPDPA Guide",
        url="https://economictimes.indiatimes.com/dpdpa",
        summary="ET article on DPDPA.",
        published_date="2026-01-01",
        source="ET Business News",
    )
    item_inc42 = NewsItem(
        title="Inc42: Privacy Rules",
        url="https://inc42.com/dpdpa-rules",
        summary="Inc42 on DPDPA rules.",
        published_date="2026-01-02",
        source="Inc42",
    )

    mocker.patch("src.scrapers.india_business_news.fetch_et_business_news", return_value=[item_et])
    mocker.patch("src.scrapers.india_business_news.fetch_inc42_news", return_value=[item_inc42])
    mocker.patch("src.scrapers.india_business_news.fetch_yourstory_news", return_value=[item_et])  # dup
    mocker.patch("src.scrapers.india_business_news.fetch_entrackr_news", return_value=[])
    mocker.patch(
        "src.scrapers.india_business_news._tavily_fallback_search", return_value=[]
    )

    items = await fetch_all_india_business_news()

    # item_et should only appear once despite being returned twice
    assert len(items) == 2
    urls = {i.url for i in items}
    assert "https://economictimes.indiatimes.com/dpdpa" in urls
    assert "https://inc42.com/dpdpa-rules" in urls


# ---------------------------------------------------------------------------
# news_scout integration — scoring boosts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_court_source_gets_scoring_bonus(mocker):
    """Court judgment sources receive a +4 scoring bonus in news_scout."""
    from src.agents.news_scout import score_relevance
    from src.scrapers.regulatory_scrapers import NewsItem

    court_item = NewsItem(
        title="Delhi HC rules data fiduciary must provide breach notice within 72 hours under DPDPA",
        url="https://indiankanoon.org/doc/999/",
        summary="DPDPA section 8 obligation — consent and breach notification — data principal rights.",
        published_date="2026-01-10",
        source="IndiaKanoon — Delhi High Court",
    )
    baseline_item = NewsItem(
        title="Delhi HC rules data fiduciary must provide breach notice within 72 hours under DPDPA",
        url="https://example.com/generic-article",
        summary="DPDPA section 8 obligation — consent and breach notification — data principal rights.",
        published_date="2026-01-10",
        source="Generic Blog",
    )

    court_score = score_relevance(court_item)
    baseline_score = score_relevance(baseline_item)

    assert court_score > baseline_score
    assert court_score - baseline_score >= 4


@pytest.mark.asyncio
async def test_india_biz_source_gets_scoring_bonus():
    """Indian business news sources receive a +2 scoring bonus in news_scout."""
    from src.agents.news_scout import score_relevance
    from src.scrapers.regulatory_scrapers import NewsItem

    biz_item = NewsItem(
        title="DPDPA compliance for Indian startups — what founders need to know",
        url="https://inc42.com/dpdpa-guide",
        summary="Inc42 deep dive into DPDPA compliance obligations for Indian startups.",
        published_date="2026-01-10",
        source="Inc42",
    )
    baseline_item = NewsItem(
        title="DPDPA compliance for Indian startups — what founders need to know",
        url="https://randomblog.com/dpdpa",
        summary="Inc42 deep dive into DPDPA compliance obligations for Indian startups.",
        published_date="2026-01-10",
        source="Random Blog",
    )

    biz_score = score_relevance(biz_item)
    baseline_score = score_relevance(baseline_item)

    assert biz_score > baseline_score
    assert biz_score - baseline_score >= 2
