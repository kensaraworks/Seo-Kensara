"""Blog post quality checker — evaluates SEO and content quality signals.

Replaces naive word count / H2 count checks with real, weighted signals
covering search intent, information density, structure, E-E-A-T, and
factual specificity.
"""
import re
import structlog
from pydantic import BaseModel

from src.agents.blog_writer import BlogPost

log = structlog.get_logger()

# Regulatory bodies that add E-E-A-T credibility
REGULATORY_BODIES = [
    "meity", "edpb", "ico", "data protection board", "dpb",
    "cnil", "bsi", "fdpic", "cppa", "pdpc",
]

# DPDPA-specific section references
DPDPA_SECTIONS = [
    "section 4", "section 5", "section 6", "section 7", "section 8",
    "section 9", "section 10", "section 11", "section 12", "section 13",
    "section 14", "section 15", "section 16", "section 17", "section 18",
    "section 19", "section 20", "chapter ii", "chapter iii", "chapter iv",
    "art. ", "article 5", "article 6", "article 7", "article 13",
    "article 17", "article 25", "article 28", "article 32", "article 33",
    "gdpr art", "schedule i", "schedule ii",
]

# Generic filler phrases that reduce quality
FILLER_PHRASES = [
    "in today's world",
    "in today's digital world",
    "in today's fast-paced",
    "it is important to note",
    "it should be noted that",
    "needless to say",
    "at the end of the day",
    "in order to",
    "it goes without saying",
    "to be perfectly honest",
    "as we all know",
    "the fact of the matter is",
    "at this point in time",
    "due to the fact that",
    "in light of the fact",
    "for all intents and purposes",
]

# Indian-context signals
INDIA_CONTEXT_TERMS = [
    "india", "indian", "dpdpa", "meity", "data protection board",
    "data fiduciary", "data principal", "digital personal data",
    "₹", "lakh", "crore", "rbi", "sebi", "irdai", "trai",
    "bombay", "delhi", "bangalore", "mumbai", "hyderabad",
]

# Competitor and legal risk terms handled by risk_classifier
# (kept here for reference only — not used in quality scoring)
COMPETITOR_NAMES = ["onetrust", "trustarc", "seqrite", "vishwaas"]


class QualityResult(BaseModel):
    passed: bool
    score: float  # 0.0 to 1.0
    issues: list[str]      # block publish — must fix
    warnings: list[str]    # log but allow
    signals: dict          # detailed breakdown by category


def check_blog_quality(post: BlogPost, keyword: str) -> QualityResult:
    """Evaluate blog post quality across 5 weighted categories (100 pts total).

    Categories:
        1. Search intent match     (0–20 pts)
        2. Information density     (0–20 pts)
        3. Structure completeness  (0–20 pts)
        4. E-E-A-T signals         (0–20 pts)
        5. Factual specificity     (0–20 pts)

    Pass threshold: score >= 0.60 (60/100 pts)
    Issues (block publish): score < 0.40, no CTA, no keyword in H1
    Warnings (allow with flag): score 0.40–0.60, keyword stuffed (> 5 occurrences)
    """
    content = post.content_markdown
    content_lower = content.lower()
    kw_lower = keyword.lower()
    issues: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------ #
    #  1. Search intent match (0-20 pts)                                   #
    # ------------------------------------------------------------------ #
    intent_pts = 0
    intent_signals: dict = {}

    # Keyword in H1 (any line starting with single #)
    h1_lines = [line for line in content.split("\n") if re.match(r"^#\s+", line)]
    kw_in_h1 = any(kw_lower in line.lower() for line in h1_lines)
    if kw_in_h1:
        intent_pts += 5
    intent_signals["keyword_in_h1"] = kw_in_h1

    # Keyword in first 150 words
    first_150 = " ".join(content.split()[:150]).lower()
    kw_in_intro = kw_lower in first_150
    if kw_in_intro:
        intent_pts += 5
    intent_signals["keyword_in_first_150_words"] = kw_in_intro

    # Keyword in meta description
    kw_in_meta = kw_lower in post.meta_description.lower()
    if kw_in_meta:
        intent_pts += 5
    intent_signals["keyword_in_meta"] = kw_in_meta

    # Keyword density: appears 2-4 times in body (not stuffed)
    kw_count = len(re.findall(re.escape(kw_lower), content_lower))
    kw_density_ok = 2 <= kw_count <= 4
    if kw_density_ok:
        intent_pts += 5
    intent_signals["keyword_occurrences"] = kw_count
    intent_signals["keyword_density_ok"] = kw_density_ok

    if kw_count > 5:
        warnings.append(
            f"Keyword '{keyword}' appears {kw_count} times — possible keyword stuffing (max 4 recommended)"
        )

    # ------------------------------------------------------------------ #
    #  2. Information density (0-20 pts)                                   #
    # ------------------------------------------------------------------ #
    density_pts = 0
    density_signals: dict = {}

    # Specific numbers or statistics (digits in context)
    has_numbers = bool(re.search(r"\b\d[\d,\.]*\s*(%|lakh|crore|million|billion|₹|rs\.?|days?|hours?|weeks?|months?)\b", content_lower))
    if not has_numbers:
        # Fallback: any standalone number >= 2 digits near compliance words
        has_numbers = bool(re.search(r"\b\d{2,}\b", content))
    if has_numbers:
        density_pts += 5
    density_signals["has_numbers_or_stats"] = has_numbers

    # Section references (Section X, Art. Y, Article N)
    has_section_refs = any(term in content_lower for term in DPDPA_SECTIONS)
    if has_section_refs:
        density_pts += 5
    density_signals["has_section_references"] = has_section_refs

    # Named entities: regulator names, company names
    has_named_entities = any(body in content_lower for body in REGULATORY_BODIES)
    if not has_named_entities:
        # Check for capitalized proper nouns (rough heuristic)
        has_named_entities = bool(re.search(r"\b[A-Z][a-z]+ (Ltd|Pvt|Inc|Corp|LLP|PLC)\b", content))
    if has_named_entities:
        density_pts += 5
    density_signals["has_named_entities"] = has_named_entities

    # Dates or deadlines mentioned
    has_dates = bool(re.search(
        r"\b(202[4-9]|203\d|january|february|march|april|may|june|july|august"
        r"|september|october|november|december|q[1-4]\s+20\d\d|fy\s*20\d\d"
        r"|deadline|by\s+\w+\s+\d{4}|within\s+\d+\s+(days?|hours?))\b",
        content_lower
    ))
    if has_dates:
        density_pts += 5
    density_signals["has_dates_or_deadlines"] = has_dates

    # ------------------------------------------------------------------ #
    #  3. Structure completeness (0-20 pts)                                #
    # ------------------------------------------------------------------ #
    structure_pts = 0
    structure_signals: dict = {}

    # Has H1 (# heading)
    has_h1 = len(h1_lines) >= 1
    if has_h1:
        structure_pts += 5
    structure_signals["has_h1"] = has_h1

    # Has 3+ H2 headings (## heading)
    h2_lines = [line for line in content.split("\n") if re.match(r"^##\s+", line)]
    has_enough_h2 = len(h2_lines) >= 3
    if has_enough_h2:
        structure_pts += 10
    structure_signals["h2_count"] = len(h2_lines)
    structure_signals["has_3_or_more_h2"] = has_enough_h2

    # Has conclusion paragraph (last 200 words contain conclusion signals)
    last_200 = " ".join(content.split()[-200:]).lower()
    has_conclusion = any(term in last_200 for term in [
        "conclusion", "summary", "in summary", "to summarise", "to summarize",
        "key takeaway", "final", "request-demo", "kensara.in", "book a demo",
        "get started", "contact us", "request a demo",
    ])
    if has_conclusion:
        structure_pts += 5
    structure_signals["has_conclusion"] = has_conclusion

    # ------------------------------------------------------------------ #
    #  4. E-E-A-T signals (0-20 pts)                                       #
    # ------------------------------------------------------------------ #
    eeat_pts = 0
    eeat_signals: dict = {}

    # References specific DPDPA sections
    has_dpdpa_refs = any(term in content_lower for term in [
        "section", "chapter", "schedule", "clause", "art.", "article",
        "dpdpa", "digital personal data protection act",
    ])
    if has_dpdpa_refs:
        eeat_pts += 10
    eeat_signals["has_regulatory_references"] = has_dpdpa_refs

    # Mentions real regulatory bodies
    has_regulators = any(body in content_lower for body in REGULATORY_BODIES)
    if has_regulators:
        eeat_pts += 5
    eeat_signals["mentions_regulatory_bodies"] = has_regulators

    # Has CTA to kensara.in/request-demo
    has_cta = "kensara.in/request-demo" in content_lower
    if has_cta:
        eeat_pts += 5
    eeat_signals["has_kensarai_cta"] = has_cta

    # ------------------------------------------------------------------ #
    #  5. Factual specificity (0-20 pts)                                   #
    # ------------------------------------------------------------------ #
    specificity_pts = 20  # start at max, subtract for filler
    specificity_signals: dict = {}

    # Detect filler phrases (-5 each, floor at 0)
    filler_found = []
    for phrase in FILLER_PHRASES:
        if phrase in content_lower:
            filler_found.append(phrase)
            specificity_pts = max(0, specificity_pts - 5)
    specificity_signals["filler_phrases_found"] = filler_found

    # Has India-specific context (+10, but only add if not already deducted away)
    has_india_context = any(term in content_lower for term in INDIA_CONTEXT_TERMS)
    if has_india_context:
        specificity_pts = min(20, specificity_pts + 10)
    specificity_signals["has_india_context"] = has_india_context

    # Has actionable steps (numbered list or checklist pattern)
    has_actionable = bool(re.search(
        r"(^\s*\d+\.\s+.+$|^\s*[-*]\s+.+$|^\s*-\s*\[.?\]\s+.+$)",
        content,
        re.MULTILINE
    ))
    if has_actionable:
        specificity_pts = min(20, specificity_pts + 10)
    specificity_signals["has_actionable_steps"] = has_actionable

    # Cap at 20
    specificity_pts = min(20, specificity_pts)

    # ------------------------------------------------------------------ #
    #  Final scoring                                                        #
    # ------------------------------------------------------------------ #
    total_pts = intent_pts + density_pts + structure_pts + eeat_pts + specificity_pts
    score = round(total_pts / 100.0, 3)

    # Determine issues (hard blockers)
    if score < 0.40:
        issues.append(
            f"Quality score {score:.2f} is below minimum threshold 0.40 — content must be rewritten"
        )
    if not has_cta:
        issues.append("Missing CTA link to https://kensara.in/request-demo — required in every post")
    if not kw_in_h1:
        issues.append(f"Primary keyword '{keyword}' not found in H1 — critical for SEO ranking")

    # Warnings (score between floor and pass threshold)
    if 0.40 <= score < 0.60:
        warnings.append(
            f"Quality score {score:.2f} is in warning range (0.40–0.60) — review before publishing"
        )

    passed = score >= 0.60 and len(issues) == 0

    log.info(
        "quality_check_done",
        title=post.title[:60],
        score=score,
        passed=passed,
        issues=len(issues),
        warnings=len(warnings),
    )

    return QualityResult(
        passed=passed,
        score=score,
        issues=issues,
        warnings=warnings,
        signals={
            "search_intent": {"pts": intent_pts, "max": 20, **intent_signals},
            "information_density": {"pts": density_pts, "max": 20, **density_signals},
            "structure": {"pts": structure_pts, "max": 20, **structure_signals},
            "eeat": {"pts": eeat_pts, "max": 20, **eeat_signals},
            "factual_specificity": {"pts": specificity_pts, "max": 20, **specificity_signals},
        },
    )
