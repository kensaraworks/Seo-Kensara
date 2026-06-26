"""SERP Format Analyzer — Module 2.1.B.

Analyses a populated SerpIntelligence object to determine:
  - Which content format Google is rewarding for the query (paragraph answer,
    list/HowTo, comparison table, video, mixed)
  - Calibrated target word count (15% above SERP average, capped at tier max)
  - Strategy instruction string injected into the keyword brief

Deterministic Python — no LLM call.

Usage:
    from src.engines.serp_formatter import analyze_serp_format, get_target_word_count
    fmt = analyze_serp_format(serp_intel, tier=2)
    strategy_str = fmt.strategy_instruction
    word_range  = fmt.calibrated_word_count_range
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tier word-count bounds (spec 2.3)
# ---------------------------------------------------------------------------
_TIER_BOUNDS: dict[int, tuple[int, int]] = {
    1: (1800, 2500),
    2: (1200, 1600),
    3: (600, 900),
    0: (3000, 5000),  # pillar pages
}

# ---------------------------------------------------------------------------
# SerpFormat result object
# ---------------------------------------------------------------------------

@dataclass
class SerpFormat:
    """All SERP-derived signals needed before the first LLM call."""

    # Raw feature detection
    featured_snippet_type: Optional[str]    # "paragraph" | "list" | "table" | "video" | None
    has_featured_snippet: bool
    has_ai_overview: bool
    has_paa: bool
    paa_count: int
    has_video_carousel: bool
    has_news_box: bool
    has_knowledge_panel: bool
    has_shopping_results: bool

    # Competitor word-count signal
    avg_competitor_word_count: int
    calibrated_word_count: int              # single integer used internally
    calibrated_word_count_range: str        # e.g. "1200-1450" passed to the brief

    # Recommended content format for outline generation
    recommended_content_format: str         # see _FORMATS below

    # Plain-English strategy instruction injected into every prompt
    strategy_instruction: str

    # PAA questions surfaced by the SERP
    paa_questions: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal format labels
# ---------------------------------------------------------------------------
_FMT_ANSWER_GUIDE = "answer_block_plus_guide"
_FMT_HOW_TO_LIST  = "how_to_numbered_list"
_FMT_COMPARISON   = "comparison_table"
_FMT_VIDEO_HYBRID = "video_summary_plus_text"
_FMT_MIXED        = "mixed_answer_and_list"

_STRATEGY_INSTRUCTIONS: dict[str, str] = {
    _FMT_ANSWER_GUIDE: (
        "Google is showing a paragraph featured snippet for this keyword. "
        "Open the post with a standalone 40-60 word answer block immediately after the H1 "
        "(spec GEO item 1). Then develop the full guide below it."
    ),
    _FMT_HOW_TO_LIST: (
        "Google is rewarding a list/How-To format for this keyword. "
        "Include a numbered How-To section in the first three H2s. "
        "Each step must be 10-20 words. Use HowTo schema."
    ),
    _FMT_COMPARISON: (
        "Google is surfacing a table featured snippet for this keyword. "
        "Open with or prioritise a Markdown comparison table as the primary content element. "
        "Use a header row with clear column labels. Competitor comparison facts must come "
        "from kensarai_facts.py only."
    ),
    _FMT_VIDEO_HYBRID: (
        "Google shows a video carousel for this keyword — text content competes against video. "
        "Lead with a concise 40-60 word answer block, then structure the post as a 'text transcript' "
        "style guide with short paragraphs (max 3 sentences) to maximise extractability."
    ),
    _FMT_MIXED: (
        "No dominant featured snippet format detected. "
        "Generate both a paragraph answer block (40-60 words after H1) AND a structured "
        "numbered list section to compete for the answer box on multiple formats."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_serp_format(
    serp_intel,           # SerpIntelligence from src.context.builder
    tier: int = 2,
) -> SerpFormat:
    """Derive content format strategy and calibrated word count from SERP signals.

    Args:
        serp_intel: A SerpIntelligence object populated by get_full_serp_intelligence().
        tier: Content tier (1=regulatory deep-dive, 2=industry playbook, 3=newsjack,
              0=pillar page).

    Returns:
        SerpFormat dataclass with strategy_instruction and calibrated_word_count_range
        ready for injection into the keyword brief.
    """
    fs_type: Optional[str] = getattr(serp_intel, "featured_snippet_format", None)
    has_fs: bool = getattr(serp_intel, "featured_snippet_exists", False) or bool(fs_type)
    has_ai: bool = getattr(serp_intel, "ai_overview_exists", False)
    paa_qs: List[str] = getattr(serp_intel, "paa_questions", []) or []
    avg_wc: int = getattr(serp_intel, "top_5_avg_word_count", 0) or 0

    # ---- Feature flags (Serper.dev JSON fields, graceful if absent) ----
    raw_data: dict = getattr(serp_intel, "_raw_serp_data", {}) or {}
    has_video    = bool(raw_data.get("videos"))
    has_news     = bool(raw_data.get("news") or raw_data.get("topStories"))
    has_kg       = bool(raw_data.get("knowledgeGraph"))
    has_shopping = bool(raw_data.get("shopping"))

    # ---- Word-count calibration (spec 2.1.B) ----
    calibrated_wc, wc_range = get_target_word_count(tier, avg_wc)

    # ---- Content format decision tree ----
    if has_fs and fs_type == "paragraph":
        fmt = _FMT_ANSWER_GUIDE
    elif has_fs and fs_type == "list":
        fmt = _FMT_HOW_TO_LIST
    elif has_fs and fs_type == "table":
        fmt = _FMT_COMPARISON
    elif has_video:
        fmt = _FMT_VIDEO_HYBRID
    else:
        fmt = _FMT_MIXED

    # Supplement strategy with PAA signal when questions dominate the SERP
    strategy = _STRATEGY_INSTRUCTIONS[fmt]
    if len(paa_qs) >= 4:
        strategy += (
            f" The SERP shows {len(paa_qs)} People Also Ask questions — include a dedicated "
            "FAQ section (spec 2.2 Step 1) answering at least 3 of them verbatim."
        )
    if has_ai:
        strategy += (
            " Google AI Overview is present. Ensure citability signals (GEO items 1, 3, 4, 5) "
            "are all satisfied to compete for AI citation."
        )

    result = SerpFormat(
        featured_snippet_type=fs_type,
        has_featured_snippet=has_fs,
        has_ai_overview=has_ai,
        has_paa=bool(paa_qs),
        paa_count=len(paa_qs),
        has_video_carousel=has_video,
        has_news_box=has_news,
        has_knowledge_panel=has_kg,
        has_shopping_results=has_shopping,
        avg_competitor_word_count=avg_wc,
        calibrated_word_count=calibrated_wc,
        calibrated_word_count_range=wc_range,
        recommended_content_format=fmt,
        strategy_instruction=strategy,
        paa_questions=paa_qs,
    )

    log.info(
        "serp_format_analyzed",
        fmt=fmt,
        has_fs=has_fs,
        has_ai=has_ai,
        paa_count=len(paa_qs),
        wc_range=wc_range,
    )
    return result


def get_target_word_count(tier: int, avg_competitor_words: int) -> tuple[int, str]:
    """Return (calibrated_integer, range_string) for the keyword brief.

    Spec 2.1.B: Target 15% above the SERP average, capped at the tier's upper
    bound and floored at the tier's lower bound.

    Args:
        tier: 1 | 2 | 3 | 0 (pillar)
        avg_competitor_words: Average word count of top-5 competitors (0 = unknown)

    Returns:
        (calibrated_int, "floor-calibrated") e.g. (1450, "1200-1450")
    """
    lo, hi = _TIER_BOUNDS.get(tier, (1200, 1600))

    if avg_competitor_words > 0:
        calibrated = max(lo, min(int(avg_competitor_words * 1.15), hi))
    else:
        # No SERP data — default to midpoint
        calibrated = int((lo + hi) / 2)

    return calibrated, f"{lo}-{calibrated}"
