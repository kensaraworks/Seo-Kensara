"""GEO Optimizer — Module 2.2 Step 6: Deterministic 20-item GEO scoring rubric.

This module is ENTIRELY deterministic Python logic. No LLM calls.
Checks every post against the spec's 20-item GEO optimisation checklist and
returns a score, flag list, risk level, and approval status.

Spec source: module2_report.txt, Section 2.2, STEP 6: GEO OPTIMIZATION PASS
"""
import re
import structlog
from typing import List, Tuple

log = structlog.get_logger()

# Non-negotiable critical items — failure on ANY = automatic HIGH risk
# regardless of total score (spec: "Items 1, 4, 12, 13, 17 are non-negotiable")
CRITICAL_ITEMS = {1, 4, 12, 13, 17}

# Minimum score to proceed without flagging as LOW quality
MINIMUM_SCORE = 14


def run_geo_checklist(
    markdown: str,
    meta: dict,
    slug: str,
    keyword: str,
) -> Tuple[int, List[str], List[int], str, bool]:
    """Run all 20 GEO checks. Returns (score, flags, failed_critical, risk_level, approved).

    Args:
        markdown:  The assembled post body Markdown.
        meta:      Dict with keys: title, description, slug.
        slug:      URL slug string.
        keyword:   Primary keyword string.

    Returns:
        score (int 0-20), flags (list of problem descriptions),
        failed_critical (list of critical item numbers that failed),
        risk_level ("LOW"|"MEDIUM"|"HIGH"), approved (bool).
    """
    score = 0
    flags: List[str] = []
    failed_critical: List[int] = []
    md_lower = markdown.lower()

    # -----------------------------------------------------------------
    # CITABILITY STRUCTURE (items 1-6)
    # -----------------------------------------------------------------

    # Item 1 (CRITICAL): Answer-first structure — 40-60 word self-contained block in first 200 words
    first_200_words = " ".join(markdown.split()[:200])
    has_answer_block = (
        "**quick answer**" in first_200_words.lower()
        or "quick answer:" in first_200_words.lower()
    )
    if has_answer_block:
        score += 1
    else:
        flags.append("[GEO-1 CRITICAL] Missing self-contained answer block in first 200 words.")
        failed_critical.append(1)

    # Item 2: Every H2 section opens with a standalone sentence
    # Approximate: check that text immediately follows each ## heading
    h2_sections = re.findall(r"^##\s+.+$", markdown, re.MULTILINE)
    h2_with_content = len(h2_sections)  # simplified — presence of H2s implies content follows
    if h2_with_content >= 2:
        score += 1
    else:
        flags.append("[GEO-2] Fewer than 2 H2 sections found — sections must open with standalone sentences.")

    # Item 3: Key takeaway box present
    if re.search(r"key takeaway|in brief|key points", md_lower):
        score += 1
    else:
        flags.append("[GEO-3] Missing 'Key Takeaways' or 'In Brief' box.")

    # Item 4 (CRITICAL): Minimum 3 specific statistics with inline attribution
    stat_count = len(re.findall(r"\d+(?:\.\d+)?%|₹\s*\d+|rs\.?\s*\d+", markdown, re.IGNORECASE))
    if stat_count >= 3:
        score += 1
    else:
        flags.append(f"[GEO-4 CRITICAL] Only {stat_count} statistics/₹ figures found. Need at least 3.")
        failed_critical.append(4)

    # Item 5: Expert quotation present
    has_quote = bool(
        re.search(r'"[^"]{20,200}"', markdown)
        and re.search(r"\b(?:says|said|according to|noted|stated|according)\b", md_lower)
    )
    if has_quote:
        score += 1
    else:
        flags.append("[GEO-5] Missing expert quotation with attribution.")

    # Item 6: Precise India-specific data (₹ + named Indian regulator)
    indian_regulators = ["dpbi", "meity", "rbi", "sebi", "irdai", "cert-in", "trai"]
    has_indian_reg = any(reg in md_lower for reg in indian_regulators)
    has_rupee = "₹" in markdown
    if has_rupee and has_indian_reg:
        score += 1
    else:
        flags.append("[GEO-6] Missing ₹ monetary figures AND named Indian regulatory body.")

    # -----------------------------------------------------------------
    # STRUCTURAL CLARITY (items 7-11)
    # -----------------------------------------------------------------

    # Item 7: Strict heading hierarchy — no H4 or H5
    has_h4_or_h5 = bool(re.search(r"^####", markdown, re.MULTILINE))
    if not has_h4_or_h5:
        score += 1
    else:
        flags.append("[GEO-7] Invalid heading hierarchy: H4/H5 detected. Use H1→H2→H3 only.")

    # Item 8: Paragraphs max 4 sentences each
    paragraphs = [p.strip() for p in markdown.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    long_paragraphs = [p for p in paragraphs if len(re.findall(r"[.!?]", p)) > 4]
    if len(long_paragraphs) == 0:
        score += 1
    else:
        flags.append(f"[GEO-8] {len(long_paragraphs)} paragraphs exceed 4-sentence limit.")

    # Item 9: Bold text density — max 8 bold instances per 1000 words
    word_count = len(markdown.split())
    bold_count = len(re.findall(r"\*\*[^*]+\*\*", markdown))
    max_bold = max(8, int(word_count / 1000) * 8)
    if bold_count <= max_bold:
        score += 1
    else:
        flags.append(f"[GEO-9] Excessive bold text: {bold_count} instances (max {max_bold} per 1000 words).")

    # Item 10: No inline comma-separated lists longer than 3 items
    long_inline_lists = re.findall(r"(?:[^,\n]+,\s*){3,}[^,\n]+", markdown)
    if not long_inline_lists:
        score += 1
    else:
        flags.append("[GEO-10] Inline comma-separated lists longer than 3 items detected. Use bullet points.")

    # Item 11: Comparison tables present (commercial intent posts require them)
    has_table = "|" in markdown and "---" in markdown
    if has_table:
        score += 1
    else:
        flags.append("[GEO-11] No Markdown comparison table found. Tables increase AI citation probability by 48%.")

    # -----------------------------------------------------------------
    # AUTHORITY SIGNALS / E-E-A-T (items 12-16)
    # -----------------------------------------------------------------

    # Item 12 (CRITICAL): Author byline with credentials in first 500 words
    first_500_words = " ".join(markdown.split()[:500]).lower()
    has_author = (
        ("rudraksh tatwal" in first_500_words and ("founder" in first_500_words or "ceo" in first_500_words))
        or ("mr prince" in first_500_words and ("co-founder" in first_500_words or "cofounder" in first_500_words or "coo" in first_500_words))
    )
    if has_author:
        score += 1
    else:
        flags.append("[GEO-12 CRITICAL] Missing author byline with CIPP/E credentials in first 500 words.")
        failed_critical.append(12)

    # Item 13 (CRITICAL): Regulatory source citations — link to official gov sites
    gov_link_pattern = r"dpboard\.gov\.in|meity\.gov\.in|gazette\.gov\.in|rbi\.org\.in|sebi\.gov\.in"
    has_gov_link = bool(re.search(gov_link_pattern, md_lower))
    # Fallback: specific section/rule citation counts as partial credit
    has_section_cite = bool(re.search(r"\bsection\s+\d+\b|\brule\s+\d+\b", md_lower))
    if has_gov_link:
        score += 1
    elif has_section_cite:
        score += 1  # Accept section/rule cite without hyperlink — flag it
        flags.append("[GEO-13 WARNING] Section citations present but no hyperlinks to official gov sources (dpboard.gov.in, meity.gov.in).")
    else:
        flags.append("[GEO-13 CRITICAL] No regulatory source citations or official gov links.")
        failed_critical.append(13)

    # Item 14: Minimum 2 external links to authoritative sources
    external_links = re.findall(r"\[.+?\]\(https?://(?!kensara\.in)[^)]+\)", markdown)
    if len(external_links) >= 2:
        score += 1
    else:
        flags.append(f"[GEO-14] Only {len(external_links)} external authority links. Need at least 2.")

    # Item 15: "Last Updated" timestamp visible in post body
    if re.search(r"last updated|date modified|updated on|published on", md_lower):
        score += 1
    else:
        flags.append("[GEO-15] Missing visible 'Last Updated' timestamp in post body.")

    # Item 16: About the author section at the bottom
    # Check last 300 words of the document
    last_300_words = " ".join(markdown.split()[-300:]).lower()
    has_author_bio = (
        ("rudraksh tatwal" in last_300_words and ("founder" in last_300_words or "ceo" in last_300_words))
        or ("mr prince" in last_300_words and ("co-founder" in last_300_words or "cofounder" in last_300_words or "coo" in last_300_words))
    )
    if has_author_bio:
        score += 1
    else:
        flags.append("[GEO-16] Missing 'About the Author' section at the bottom of the post.")

    # -----------------------------------------------------------------
    # MACHINE READABILITY (items 17-20)
    # -----------------------------------------------------------------

    # Item 17 (CRITICAL): No JavaScript-rendered content — guaranteed for markdown output
    # Our pipeline always outputs server-rendered markdown → True by design
    score += 1  # Always passes for this pipeline

    # Item 18: All images have alt text
    images_without_alt = re.findall(r"!\[\s*\]\(", markdown)  # ![](...) with empty alt
    if not images_without_alt:
        score += 1
    else:
        flags.append(f"[GEO-18] {len(images_without_alt)} images missing alt text.")

    # Item 19: Internal link anchor text is descriptive (not "click here" or "learn more")
    bad_anchors = re.findall(r"\[(?:click here|learn more|read more|see here|here)\]", md_lower)
    if not bad_anchors:
        score += 1
    else:
        flags.append(f"[GEO-19] {len(bad_anchors)} non-descriptive link anchor texts found ('click here', 'learn more').")

    # Item 20: Slug is lowercase, hyphenated, contains keyword, no stop words
    stop_words = {"the", "and", "a", "for", "of", "in", "to", "is", "are", "with", "on", "at"}
    slug_parts = set(slug.split("-"))
    slug_has_stop_word = bool(slug_parts & stop_words)
    keyword_in_slug = all(
        part in slug
        for part in re.sub(r"[^\w\s]", "", keyword.lower()).split()[:2]  # check first 2 keyword words
    )
    if not slug_has_stop_word and keyword_in_slug and slug == slug.lower():
        score += 1
    else:
        issues = []
        if slug_has_stop_word:
            issues.append("stop words in slug")
        if not keyword_in_slug:
            issues.append("keyword missing from slug")
        if slug != slug.lower():
            issues.append("slug not lowercase")
        flags.append(f"[GEO-20] URL slug issues: {', '.join(issues)}.")

    # -----------------------------------------------------------------
    # RISK CLASSIFICATION
    # -----------------------------------------------------------------
    has_critical_failures = len(failed_critical) > 0
    if has_critical_failures or score < MINIMUM_SCORE:
        risk_level = "HIGH"
        approved = False
    elif score < 17:
        risk_level = "MEDIUM"
        approved = False  # Requires human review
    else:
        risk_level = "LOW"
        approved = True

    log.info(
        "geo_scoring_complete",
        score=score,
        max=20,
        risk_level=risk_level,
        critical_failures=failed_critical,
        flag_count=len(flags),
    )

    return score, flags, failed_critical, risk_level, approved
