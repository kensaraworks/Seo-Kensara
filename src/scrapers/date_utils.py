"""Shared date-parsing and recency utilities for the news-scraping pipeline.

A single module drives both the hard age filter in rss_scraper._fetch_single_rss
and the soft recency scoring signal in news_scout.score_relevance, so the two
layers stay consistent without duplicating parsing logic.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Optional


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")

# Formats emitted by court judgment text extraction (_extract_date_from_text)
_HUMAN_FORMATS = (
    "%d %b %Y",   # 15 Jan 2025
    "%d %B %Y",   # 15 January 2025
    "%B %d, %Y",  # January 15, 2025
    "%b %d, %Y",  # Jan 15, 2025
    "%d/%m/%Y",   # 15/01/2025
    "%m/%d/%Y",   # 01/15/2025
    "%Y/%m/%d",   # 2025/01/15
    "%d-%m-%Y",   # 15-01-2025
    "%d-%b-%Y",   # 15-Jan-2025
)


def parse_published_date(date_str: str) -> Optional[date]:
    """Parse a published_date string into a date object. Returns None on failure.

    Handles every format that enters the pipeline:
    - ISO date          "2025-01-15"
    - ISO datetime      "2025-01-15T10:00:00Z"
    - RFC 2822          "Mon, 27 Jun 2026 10:00:00 +0000"  (feedparser output)
    - Human-readable    "15 Jan 2025", "January 15, 2025"  (court judgment text)
    """
    if not date_str:
        return None
    s = date_str.strip()

    if _ISO_DATE_RE.match(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass

    if _ISO_DATETIME_RE.match(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        pass

    for fmt in _HUMAN_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    return None


def days_since(date_str: str) -> Optional[int]:
    """Return how many days ago the article was published. None if unparseable.

    Future-dated articles (e.g. scheduled releases) return 0.
    """
    parsed = parse_published_date(date_str)
    if parsed is None:
        return None
    return max(0, (date.today() - parsed).days)


def is_recent_enough(date_str: str, max_days: int) -> bool:
    """Return True if the article falls within max_days of today.

    Unparseable dates are treated as a pass-through (True) because many
    scrapers legitimately assign str(date.today()) as a proxy for
    "scraped from the current listing page", and an unrecognised format
    must never silently discard a valid item.
    """
    age = days_since(date_str)
    return age is None or age <= max_days


def recency_score_delta(date_str: str, *, court_source: bool = False) -> int:
    """Return a score adjustment (positive or negative) based on article age.

    Regular news sources:
      +3   0–7 days    this week — hot story
      +1   8–30 days   this month
       0  31–90 days   recent quarter — still actionable
      -3  91–180 days  going stale
      -6  >180 days    stale — heavily penalised

    Court / judgment sources get a more lenient curve because legal precedents
    have a longer editorial shelf life than breaking news:
      +3   0–30 days
       0  31–180 days
      -2  181–730 days  up to two years old
      -4  >730 days
    """
    age = days_since(date_str)
    if age is None:
        return 0  # unknown date — neutral, don't penalise

    if court_source:
        if age <= 30:
            return 3
        if age <= 180:
            return 0
        if age <= 730:
            return -2
        return -4

    if age <= 7:
        return 3
    if age <= 30:
        return 1
    if age <= 90:
        return 0
    if age <= 180:
        return -3
    return -6
