"""Tests for news scout agent — relevance scoring and filtering.

The news scout uses a deterministic keyword-based scorer (no LLM). All tests
are fully synchronous where possible. Async tests cover the score_news_items
pipeline including filtering, sorting, and top-3 limiting.
"""
import pytest
from unittest.mock import patch, AsyncMock

from src.agents.news_scout import (
    score_relevance,
    score_news_items,
    ScoredNewsItem,
    _score_item_sync,
)
from src.scrapers.rss_scraper import NewsItem


# -----------------------------------------------------------------------
# score_relevance — synchronous, deterministic tests
# -----------------------------------------------------------------------

def test_dpdpa_news_scores_high(sample_news_item):
    """DPDPA enforcement news must score >= 7."""
    score = score_relevance(sample_news_item)
    assert score >= 7, (
        f"Expected score >= 7 for DPDPA enforcement news but got {score}. "
        f"Title: {sample_news_item.title}"
    )


def test_irrelevant_news_scores_low():
    """Non-privacy news should score <= 3."""
    item = NewsItem(
        title="Weather forecast for Mumbai this weekend",
        url="https://example.com/weather",
        summary=(
            "Heavy rain expected over Mumbai and surrounding areas this weekend. "
            "IMD issues orange alert for coastal Maharashtra."
        ),
        published_date="2026-06-08",
        source="Times of India",
    )
    score = score_relevance(item)
    assert score <= 3, f"Expected score <= 3 for weather news but got {score}"


def test_gdpr_news_scores_at_least_1():
    """GDPR/ICO news should score at least 1 (medium relevance keywords)."""
    item = NewsItem(
        title="EDPB publishes new guidelines on cookie consent banners",
        url="https://edpb.europa.eu/example",
        summary=(
            "The European Data Protection Board has released updated guidelines on cookie "
            "consent requirements under GDPR. Data protection officers should review their "
            "consent mechanisms by Q3 2026."
        ),
        published_date="2026-06-08",
        source="EDPB",
    )
    score = score_relevance(item)
    assert score >= 1, f"Expected score >= 1 for GDPR/EDPB news but got {score}"


def test_breach_news_scores_at_least_4():
    """Data breach news should score at least 4."""
    item = NewsItem(
        title="Major Indian fintech reports data breach affecting 1 million users",
        url="https://example.com/breach",
        summary=(
            "A leading Indian fintech company disclosed a data breach that exposed "
            "personal data of over 1 million users. The breach notification was filed "
            "under DPDPA requirements within the 72-hour window."
        ),
        published_date="2026-06-08",
        source="MediaNama",
    )
    score = score_relevance(item)
    assert score >= 4, f"Expected score >= 4 for breach news but got {score}"


def test_score_relevance_returns_int(sample_news_item):
    """score_relevance must return an int."""
    score = score_relevance(sample_news_item)
    assert isinstance(score, int), f"Expected int but got {type(score)}"


@patch("src.agents.news_scout.recency_score_delta", return_value=0)
def test_score_relevance_in_valid_range(mock_delta, sample_news_item):
    """score_relevance must return a value in [0, 20]."""
    score = score_relevance(sample_news_item)
    assert 0 <= score <= 20, f"Score {score} out of valid range [0, 20]"


@patch("src.agents.news_scout.recency_score_delta", return_value=0)
def test_score_relevance_capped_at_20(mock_delta):
    """Score must never exceed 20, even for an extremely keyword-heavy item."""
    item = NewsItem(
        title="DPDPA dpdpa meity data protection board enforcement penalty fine dsar",
        url="https://example.com/test",
        summary=(
            "DPDPA consent management breach notification 72-hour data principal "
            "data fiduciary digital personal data dsar privacy law india penalty "
            "enforcement gdpr ccpa ico edpb data breach compliance"
        ),
        published_date="2026-06-08",
        source="Test",
    )
    score = score_relevance(item)
    assert score <= 20, f"Score {score} exceeded maximum of 20"


@patch("src.agents.news_scout.recency_score_delta", return_value=0)
def test_score_relevance_empty_item_scores_zero(mock_delta):
    """A news item with empty title and summary should score 0."""
    item = NewsItem(
        title="",
        url="https://example.com/empty",
        summary="",
        published_date="2026-06-08",
        source="Unknown",
    )
    score = score_relevance(item)
    assert score == 0, f"Expected 0 for empty item but got {score}"


def test_rbi_dpdpa_intersection_scores_bonus():
    """RBI + DPDPA intersection stories must receive a +3 score bonus."""
    item = NewsItem(
        title="RBI issues draft guidelines on DPDPA compliance",
        url="https://rbi.org.in/circulars/123",
        summary="The Reserve Bank of India has released draft rules for digital personal data protection in banks.",
        published_date="2026-06-24",
        source="RBI",
    )
    # Base source: +2
    # DPDPA keywords (dpdpa, digital personal data): high relevance keywords: +4 (2 each)
    # India-origin source: +2
    # Named Indian company: 0
    # Rule/Section: 0
    # Enforcement: 0
    # Urgency: 0
    # RBI + DPDPA intersection bonus: +3
    # Total expected score = 4 (kw) + 2 (source) + 3 (intersection) = 9
    score = score_relevance(item)
    assert score >= 9, f"Expected score >= 9 for RBI DPDPA intersection, got {score}"


def test_new_sources_scoring_bonuses():
    """Verify that new sources receive their designated custom score bonuses."""
    # 1. iapp.org/resources (score 1)
    iapp_res = NewsItem(
        title="Privacy regulations overview",
        url="https://iapp.org/resources/overview",
        summary="A summary of privacy frameworks.",
        published_date="2026-06-24",
        source="IAPP Resources",
    )
    # expect 1 (source bonus) + 1 (medium keyword 'privacy') = 2
    assert score_relevance(iapp_res) >= 2

    # 2. dataguidance.com (score 2)
    dataguidance = NewsItem(
        title="Privacy regulations overview",
        url="https://dataguidance.com/overview",
        summary="A summary of privacy frameworks.",
        published_date="2026-06-24",
        source="DataGuidance",
    )
    # expect 2 (source bonus) + 1 (medium keyword 'privacy') = 3
    assert score_relevance(dataguidance) >= 3

    # 3. Data Security Council of India (score 2 + India origin 2)
    dsci = NewsItem(
        title="Privacy regulations overview",
        url="https://dsci.in/overview",
        summary="A summary of privacy frameworks.",
        published_date="2026-06-24",
        source="Data Security Council of India",
    )
    # expect 2 (source bonus) + 2 (India-origin bonus) + 1 (medium keyword 'privacy') = 5
    assert score_relevance(dsci) >= 5


# -----------------------------------------------------------------------
# _score_item_sync — tests the full scored result object
# -----------------------------------------------------------------------

def test_score_item_sync_returns_scored_news_item(sample_news_item):
    """_score_item_sync must return a ScoredNewsItem with all required fields."""
    result = _score_item_sync(sample_news_item)
    assert isinstance(result, ScoredNewsItem)
    assert result.item == sample_news_item
    assert isinstance(result.relevance_score, int)
    assert result.why_relevant
    assert result.suggested_angle


def test_score_item_sync_why_relevant_not_empty(sample_news_item):
    """why_relevant must be a non-empty string."""
    result = _score_item_sync(sample_news_item)
    assert len(result.why_relevant) > 10


def test_score_item_sync_suggested_angle_not_empty(sample_news_item):
    """suggested_angle must be a non-empty string."""
    result = _score_item_sync(sample_news_item)
    assert len(result.suggested_angle) > 10


# -----------------------------------------------------------------------
# score_news_items — async pipeline tests
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_returns_empty_for_no_items():
    """score_news_items must return empty list for empty input."""
    result = await score_news_items([])
    assert result == []


@pytest.mark.asyncio
async def test_score_filters_below_threshold(sample_news_item):
    """Items scoring below the threshold (< 6) must be excluded."""
    low_relevance_item = NewsItem(
        title="Weather update",
        url="https://example.com/weather",
        summary="Rain expected in Mumbai. No tech or privacy news.",
        published_date="2026-06-08",
        source="Times of India",
    )
    result = await score_news_items([low_relevance_item])
    assert all(r.relevance_score >= 6 for r in result), (
        f"Expected all results to have score >= 6 but got: {[r.relevance_score for r in result]}"
    )


@pytest.mark.asyncio
async def test_score_top_3_limit(sample_news_item):
    """score_news_items must return at most 3 items."""
    # Use a high-relevance item repeated — all will score identically high
    items = [sample_news_item] * 10
    result = await score_news_items(items)
    assert len(result) <= 3, f"Expected <= 3 items but got {len(result)}"


@pytest.mark.asyncio
async def test_score_sorted_by_relevance_descending(sample_news_item):
    """Results must be sorted by relevance_score descending."""
    items = [
        sample_news_item,
        NewsItem(
            title="GDPR fine issued by ICO to UK company for data breach",
            url="https://ico.org.uk/example",
            summary="The ICO issued a fine related to data protection and personal data breach.",
            published_date="2026-06-08",
            source="ICO",
        ),
        NewsItem(
            title="DPDPA enforcement action — data protection board issues penalty notice",
            url="https://meity.gov.in/example",
            summary=(
                "MeitY and the Data Protection Board of India issued a penalty under DPDPA. "
                "The digital personal data protection act enforcement is accelerating. DSAR "
                "obligations were central to the notice. Breach notification compliance was key."
            ),
            published_date="2026-06-08",
            source="MeitY",
        ),
    ]
    result = await score_news_items(items)
    scores = [r.relevance_score for r in result]
    assert scores == sorted(scores, reverse=True), (
        f"Expected descending order but got {scores}"
    )


@pytest.mark.asyncio
async def test_score_returns_scored_news_item_objects(sample_news_item):
    """All returned items must be ScoredNewsItem instances."""
    result = await score_news_items([sample_news_item])
    for item in result:
        assert isinstance(item, ScoredNewsItem)


@pytest.mark.asyncio
async def test_score_high_relevance_item_included(sample_news_item):
    """A clearly high-relevance item must appear in results."""
    result = await score_news_items([sample_news_item])
    # sample_news_item is a DPDPA enforcement story — must score >= 6 and be included
    assert len(result) >= 1, (
        f"Expected sample_news_item (DPDPA enforcement) to be included but got empty result. "
        f"Score was: {score_relevance(sample_news_item)}"
    )
