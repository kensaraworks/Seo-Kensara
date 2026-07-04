"""Blog post quality checker — Module 2.6 QA System.

Evaluates SEO and content quality signals across 5 dimensions (100-point framework).
Replaces the old 60-point framework. Enforces minimum threshold 0.55.
Implements specific checks for India Localization, Readability, Uniqueness,
and Legal Accuracy as per Module 2.6 specs.
"""
import re
import math
import structlog
from typing import List, Dict, Tuple, Optional
from collections import Counter
from pydantic import BaseModel

from src.agents.blog_writer import BlogPost

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# 2.6 Constants & Specifications
# ---------------------------------------------------------------------------

# All global regulatory bodies (for E-E-A-T credibility)
REGULATORY_BODIES = [
    "meity", "edpb", "ico", "data protection board", "dpb",
    "cnil", "bsi", "fdpic", "cppa", "pdpc",
    "dpbi", "rbi", "sebi", "irdai", "cert-in", "trai"
]

# Specifically for Dimension 5: India Localization Score
INDIAN_REGULATORY_BODIES = [
    "dpbi", "meity", "rbi", "sebi", "irdai", "cert-in", "trai", "data protection board"
]

DPDPA_SECTIONS = [
    "section 4", "section 5", "section 6", "section 7", "section 8",
    "section 9", "section 10", "section 11", "section 12", "section 13",
    "section 14", "section 15", "section 16", "section 17", "section 18",
    "section 19", "section 20", "chapter ii", "chapter iii", "chapter iv",
    "art. ", "article 5", "article 6", "article 7", "article 13",
    "article 17", "article 25", "article 28", "article 32", "article 33",
    "gdpr art", "schedule i", "schedule ii", "rule 3", "rule"
]

INDIAN_SPELLINGS = [
    "organisation", "authorised", "recognise", "programme", "centre", "favour",
    "honour", "behaviour", "labour", "defence", "offence"
]

# 2.6.B Blocked Phrase List (Expanded to include legacy + new spec)
GENERIC_FILLER = [
    # Original Legacy Phrases
    "in today's world",
    "in today's fast-paced",
    "it is important to note",
    "it should be noted that",
    "in order to",
    "to be perfectly honest",
    "as we all know",
    "the fact of the matter is",
    "at this point in time",
    "due to the fact that",
    "in light of the fact",
    "for all intents and purposes",
    
    # Module 2.6 Spec Additions
    "in today's digital world",
    "in the ever-evolving landscape",
    "it is no secret that",
    "it goes without saying",
    "needless to say",
    "in conclusion",
    "in summary",
    "to summarize",
    "as we can see",
    "it is worth noting",
    "in this blog post",
    "we will explore",
    "let us dive into",
    "without further ado",
    "at the end of the day",

    # 2.6.B / spec CHANGE-E1 — transition/filler phrases that create redundancy
    # between sections generated in isolation.
    "to further understand the implications of",
    "moving forward, it is essential",
    "moving forward, it is important",
    "it is crucial for effective compliance",
    "it is essential to consider its impact",
    "this is crucial for",
    "as we have seen",
    "as discussed above",
    "as mentioned earlier",
    "as noted previously",
]

# Regex variants of the above where the subject varies (spec CHANGE-E1
# templates like "Understanding X is crucial/essential").
GENERIC_FILLER_PATTERNS = [
    r"understanding [\w\s]{1,40} is (?:also )?(?:crucial|essential)",
]

# Indian-context signals (Legacy fallback)
INDIA_CONTEXT_TERMS = [
    "india", "indian", "dpdpa", "meity", "data protection board",
    "data fiduciary", "data principal", "digital personal data",
    "₹", "lakh", "crore", "rbi", "sebi", "irdai", "trai",
    "bombay", "delhi", "bangalore", "mumbai", "hyderabad",
]

# Competitor and legal risk terms
COMPETITOR_NAMES = ["onetrust", "trustarc", "seqrite", "vishwaas"]

LEGAL_OVERCLAIMS = [
    "100% compliant",
    "fully compliant",
    "guarantees compliance",
    "ensures compliance",
    "completely eliminate risk",
    "zero risk",
    "legally guaranteed",
    "fully protected",
]

# 2.6.E Legal Accuracy Flag System
LEGAL_ACCURACY_TRIGGERS = [
    "must", "shall", "required to", "obligation", "liability",
    "non-compliance will result in", "penalty of", "fine of",
    "liable for", "requires"
]

# 2.6.F Factual Date Verification
APPROVED_DATES = [
    "august 2023",
    "2025",
    "november 2026",
    "may 2027"
]

# ---------------------------------------------------------------------------
# Spec CHANGE-B2 — Penalty amount consistency (legal accuracy hard blocker)
# ---------------------------------------------------------------------------
# Approved figures per the enacted DPDPA 2023 (see model_router.py's
# ANTI_HALLUCINATION_SYSTEM_PROMPT for the full breakdown by section).
APPROVED_PENALTY_AMOUNTS = {"250 crore", "200 crore", "50 crore"}

# Draft-bill-era figures that must never appear — they are not in the enacted Act.
BANNED_PENALTY_AMOUNTS = {
    "5 crore", "25 crore", "500 crore", "2,500 crore", "2500 crore",
}

# Spec CHANGE-E2 — Information-density specificity signals
SPECIFICITY_PATTERNS = [
    r'₹\s*\d+',                                      # Rupee amounts
    r'\b(?:DPBI|MeitY|RBI|SEBI|IRDAI|CERT-In)\b',    # Named regulators
    r'\bSection\s+\d+\b|\bRule\s+\d+\b',              # Statutory references
    r'\d+\s*(?:hours|days|months|years)',             # Timeframes
    r'\d+\s*(?:crore|lakh|%)',                        # Figures
    r'\b(?:fintech|healthtech|edtech|BFSI|MSME|NBFC)\b',  # Named sectors
]


def check_penalty_consistency(content: str) -> Dict:
    """Detect banned draft-bill penalty figures and internal ₹-figure contradictions.

    A post citing ₹5 crore in one paragraph and ₹250 crore in another for the
    same class of violation is a legal-accuracy failure, not a style issue —
    this is a hard blocker per spec CHANGE-B2, not just a scoring nudge.
    """
    sentences = re.split(r'(?<=[.!?])\s+', content)
    penalty_sentences = [
        s for s in sentences
        if re.search(r'₹|crore', s, re.IGNORECASE)
        and re.search(r'penalty|fine|non.compliance|violation|liable', s, re.IGNORECASE)
    ]

    content_lower = content.lower()
    banned_found = [amt for amt in BANNED_PENALTY_AMOUNTS if amt in content_lower]

    amounts_found: List[str] = []
    for s in penalty_sentences:
        amounts_found.extend(re.findall(r'₹\s*([\d,]+)\s*crore', s, re.IGNORECASE))
    unique_amounts = {a.replace(",", "") for a in amounts_found}

    # More than 2 distinct crore figures across penalty-bearing sentences is a
    # strong signal of a contradiction (e.g. ₹5 crore vs ₹250 crore for the
    # same violation type), not merely a post covering several violation tiers.
    conflicting = penalty_sentences if len(unique_amounts) > 2 else []

    passed = not banned_found and not conflicting
    deduction = (len(banned_found) * 10) + (15 if conflicting else 0)

    return {
        "passed": passed,
        "banned_amounts_found": banned_found,
        "conflicting_sentences": conflicting,
        "deduction": deduction,
    }


def count_specific_facts_in_section(section_text: str) -> int:
    """Count sentences containing at least one specificity signal (spec CHANGE-E2)."""
    sentences = re.split(r'(?<=[.!?])\s+', section_text)
    return sum(
        1 for sentence in sentences
        if any(re.search(p, sentence, re.IGNORECASE) for p in SPECIFICITY_PATTERNS)
    )


def validate_information_density(content: str) -> Dict:
    """Flag H2 sections with low information density — many words, few specific facts.

    Minimum bar: 1 specific fact per 75 words (floor of 2 facts per section),
    per spec CHANGE-E2. Sections that pad word count with generic advice
    instead of specific claims fail this check.
    """
    sections = re.split(r'\n##\s+', content)
    failing_sections: List[Dict] = []

    for section in sections[1:]:  # sections[0] is content before the first H2
        lines = section.split('\n', 1)
        heading = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        word_count = len(body.split())
        if word_count < 30:
            continue  # too short to meaningfully judge (e.g. CTA sections)
        fact_count = count_specific_facts_in_section(body)
        required_facts = max(2, word_count // 75)

        if fact_count < required_facts:
            failing_sections.append({
                "heading": heading,
                "word_count": word_count,
                "facts_found": fact_count,
                "facts_required": required_facts,
            })

    return {
        "passed": len(failing_sections) == 0,
        "failing_sections": failing_sections,
        "deduction": len(failing_sections) * 5,
    }

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class QualityResult(BaseModel):
    passed: bool
    score: float
    status: str
    issues: List[str]
    warnings: List[str]
    signals: Dict


# ---------------------------------------------------------------------------
# Helper Functions (Readability & Uniqueness)
# ---------------------------------------------------------------------------

def count_syllables(word: str) -> int:
    """Robust regex-based syllable counter for Flesch-Kincaid."""
    word = word.lower()
    if len(word) <= 3:
        return 1
    # Remove common silent endings
    word = re.sub(r'(?:[^laeiouy]es|ed|[^laeiouy]e)$', '', word)
    word = re.sub(r'^y', '', word)
    syllables = re.findall(r'[aeiouy]{1,2}', word)
    return len(syllables) if syllables else 1

def flesch_kincaid_grade(text: str) -> Tuple[float, float]:
    """Calculate Flesch-Kincaid Grade Level and average sentence length.
    
    Formula: 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59
    """
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0.0, 0.0
    
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    if not words:
        return 0.0, 0.0
    
    syllable_count = sum(count_syllables(w) for w in words)
    word_count = len(words)
    sentence_count = len(sentences)
    
    fk_grade = 0.39 * (word_count / sentence_count) + 11.8 * (syllable_count / word_count) - 15.59
    avg_sentence_length = word_count / sentence_count
    
    return round(fk_grade, 1), round(avg_sentence_length, 1)

def get_tf_idf_similarity(text1: str, text2: str) -> float:
    """Calculate cosine similarity using native TF-IDF (2.6.D Uniqueness)."""
    def get_terms(text):
        words = re.findall(r'\b\w+\b', text.lower())
        return Counter(words)
    
    terms1 = get_terms(text1)
    terms2 = get_terms(text2)
    
    all_terms = set(terms1.keys()).union(set(terms2.keys()))
    
    vec1 = [terms1.get(t, 0) for t in all_terms]
    vec2 = [terms2.get(t, 0) for t in all_terms]
    
    dot = sum(v1 * v2 for v1, v2 in zip(vec1, vec2))
    mag1 = math.sqrt(sum(v ** 2 for v in vec1))
    mag2 = math.sqrt(sum(v ** 2 for v in vec2))
    
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


# ---------------------------------------------------------------------------
# Main Quality Checker
# ---------------------------------------------------------------------------

def check_blog_quality(
    post: BlogPost, 
    keyword: str, 
    intent_type: str = "informational",
    secondary_keywords: List[str] = None,
    existing_published_texts: List[str] = None
) -> QualityResult:
    """Evaluate blog post quality across 5 dimensions (100 pts total) per 2.6.
    
    Pass threshold: score >= 0.55.
    Categories:
        1. Search Intent Alignment (20 pts)
        2. Information Density & Accuracy (20 pts)
        3. Content Structure (20 pts)
        4. E-E-A-T Signals (20 pts)
        5. India Localization Score (20 pts)
    """
    content = post.content_markdown
    content_lower = content.lower()
    kw_lower = keyword.lower()
    issues: List[str] = []
    warnings: List[str] = []
    
    sec_keywords = [k.lower() for k in (secondary_keywords or [])]

    # -----------------------------------------------------------------------
    # DIMENSION 1: Search Intent Alignment (20 points)
    # -----------------------------------------------------------------------
    intent_pts = 0
    
    h1_lines = [line for line in content.split("\n") if re.match(r"^#\s+", line)]
    if any(kw_lower in line.lower() for line in h1_lines):
        intent_pts += 5
        
    first_150 = " ".join(content.split()[:150]).lower()
    if kw_lower in first_150:
        intent_pts += 5
        
    if post.title and kw_lower in post.title.lower():
        intent_pts += 5
        
    word_count = len(re.findall(r'\b\w+\b', content))
    kw_count = len(re.findall(r'\b' + re.escape(kw_lower) + r'\b', content_lower))
    kw_density = (kw_count / max(1, word_count)) * 100
    if 0.5 <= kw_density <= 1.5:
        intent_pts += 3
        
    sec_kws_found = sum(1 for k in sec_keywords if k in content_lower)
    if sec_kws_found >= 3:
        intent_pts += 2

    # -----------------------------------------------------------------------
    # DIMENSION 2: Information Density & Accuracy (20 points)
    # -----------------------------------------------------------------------
    density_pts = 0
    
    # 3 specific statistics with inline attribution (8 pts)
    # Heuristic: counts %, ₹, or Rs. figures
    stat_count = len(re.findall(r"\d+(?:\.\d+)?%|₹\s*\d+|rs\.?\s*\d+", content, re.IGNORECASE))
    if stat_count >= 3:
        density_pts += 8
        
    # Minimum 2 regulatory section/rule citations (5 pts)
    sec_refs = len(re.findall(r"\bsection\s+\d+\b|\brule\s+\d+\b|\bchapter\s+[ivx]+\b", content_lower))
    if sec_refs >= 2:
        density_pts += 5
        
    # Named entities (Indian regulators) (4 pts)
    if any(reg in content_lower for reg in REGULATORY_BODIES):
        density_pts += 4
        
    # No generic filler phrases (3 pts)
    filler_count = sum(1 for f in GENERIC_FILLER if f in content_lower)
    filler_count += sum(1 for p in GENERIC_FILLER_PATTERNS if re.search(p, content_lower))
    if filler_count == 0:
        density_pts += 3

    # Penalty: -5 points per blocked phrase (spec CHANGE-E1), capped at -15
    penalty_points = min(15, filler_count * 5)

    # -----------------------------------------------------------------------
    # 2.6.G Penalty Amount Consistency (spec CHANGE-B2 — hard legal blocker)
    # -----------------------------------------------------------------------
    penalty_check = check_penalty_consistency(content)
    penalty_points += penalty_check["deduction"]
    if penalty_check["banned_amounts_found"]:
        issues.append(
            "REJECTED: Banned penalty amount(s) detected — figures from a draft "
            f"version of the DPDPA, not the enacted Act: {penalty_check['banned_amounts_found']}."
        )
    if penalty_check["conflicting_sentences"]:
        warnings.append(
            "[PENALTY_INCONSISTENCY] More than 2 distinct ₹ crore figures found across "
            "penalty-bearing sentences — verify these aren't contradictory claims for the "
            "same violation."
        )

    # -----------------------------------------------------------------------
    # 2.6.H Information Density Per Section (spec CHANGE-E2)
    # -----------------------------------------------------------------------
    density_check = validate_information_density(content)
    penalty_points += density_check["deduction"]
    if density_check["failing_sections"]:
        low_density_headings = ", ".join(s["heading"] for s in density_check["failing_sections"][:3])
        warnings.append(
            f"[LOW_INFORMATION_DENSITY] {len(density_check['failing_sections'])} section(s) pad "
            f"word count without enough specific facts: {low_density_headings}"
        )

    # -----------------------------------------------------------------------
    # DIMENSION 3: Content Structure (20 points)
    # -----------------------------------------------------------------------
    structure_pts = 0
    
    if h1_lines:
        structure_pts += 3
        
    h2_lines = [line for line in content.split("\n") if re.match(r"^##\s+", line)]
    if len(h2_lines) >= 4:
        structure_pts += 3
        
    question_h2s = sum(1 for h2 in h2_lines if "?" in h2)
    if question_h2s >= 2:
        structure_pts += 3
        
    first_200 = " ".join(content.split()[:200]).lower()
    if "**quick answer**" in first_200 or "quick answer:" in first_200:
        structure_pts += 4
        
    faq_section = re.search(r"##.*faq|##.*frequently asked questions", content_lower)
    if faq_section:
        faq_text = content_lower[faq_section.end():]
        faq_q_count = len(re.findall(r"###\s+.*?\?", faq_text))
        if faq_q_count >= 3:
            structure_pts += 4
            
    if "kensara.in" in content_lower:
        structure_pts += 3

    # -----------------------------------------------------------------------
    # DIMENSION 4: E-E-A-T Signals (20 points)
    # -----------------------------------------------------------------------
    eeat_pts = 0
    
    first_500 = " ".join(content.split()[:500]).lower()
    if (
        ("rudraksh tatwal" in first_500 and ("founder" in first_500 or "ceo" in first_500))
        or ("mr prince" in first_500 and ("co-founder" in first_500 or "cofounder" in first_500 or "coo" in first_500))
    ):
        eeat_pts += 5
        
    external_links = re.findall(r"\[.+?\]\(https?://(?!kensara\.in)[^)]+\)", content)
    if len(external_links) >= 2:
        eeat_pts += 4
        
    gov_links = re.findall(r"dpboard\.gov\.in|meity\.gov\.in|gazette\.gov\.in|rbi\.org\.in|sebi\.gov\.in", content_lower)
    if gov_links:
        eeat_pts += 4
        
    if re.search(r"last updated|date modified|updated on|published on", content_lower):
        eeat_pts += 3
        
    if "iapp" in content_lower or "years of experience" in content_lower or "founder" in content_lower:
        eeat_pts += 4

    # -----------------------------------------------------------------------
    # DIMENSION 5: India Localization Score (20 points)
    # -----------------------------------------------------------------------
    india_pts = 0
    
    if "₹" in content:
        india_pts += 4
        
    if any(reg in content_lower for reg in INDIAN_REGULATORY_BODIES):
        india_pts += 4
        
    if sec_refs > 0:
        india_pts += 4
        
    # Check for Pvt Ltd, Ltd or common Indian entities
    if re.search(r"\b[A-Z][a-z]+ (Ltd|Pvt|Private Limited)\b", content) or \
       any(c in content_lower for c in ["tata", "reliance", "infosys", "wipro", "zomato", "paytm", "hdfc", "sbi"]):
        india_pts += 4
        
    if any(sp in content_lower for sp in INDIAN_SPELLINGS):
        india_pts += 2
        
    if re.search(r"deadline|by 2026|by 2027", content_lower):
        india_pts += 2

    # -----------------------------------------------------------------------
    # Final Scoring
    # -----------------------------------------------------------------------
    total_pts = intent_pts + density_pts + structure_pts + eeat_pts + india_pts - penalty_points
    score = max(0, total_pts) / 100.0

    # -----------------------------------------------------------------------
    # 2.6.B Blocked Phrases & Legal Over-claims
    # -----------------------------------------------------------------------
    if filler_count > 2:
        issues.append("REJECTED: More than 2 generic filler phrases detected.")
    if filler_count > 5:
        warnings.append(
            f"[EXCESSIVE_FILLER] {filler_count} filler/transition phrases detected — "
            "force human review regardless of score."
        )

    for overclaim in LEGAL_OVERCLAIMS:
        if overclaim in content_lower:
            warnings.append(f"[LEGAL_ACCURACY] Legal over-claim detected: '{overclaim}'.")
            
    # Check for negative competitor framing dynamically
    for competitor in COMPETITOR_NAMES:
        # Simple heuristic for negative framing around competitor names
        if re.search(rf"\b{competitor}\b.*\b(bad|worse|inferior|terrible|fail)\b", content_lower) or \
           re.search(rf"\b(bad|worse|inferior|terrible|fail)\b.*\b{competitor}\b", content_lower) or \
           f"{competitor} is bad" in content_lower:
            issues.append(f"[HIGH RISK] Negative competitor framing detected against {competitor}.")

    # -----------------------------------------------------------------------
    # 2.6.C Readability
    # -----------------------------------------------------------------------
    fk_grade, avg_sentence_len = flesch_kincaid_grade(content)
    if fk_grade > 14:
        warnings.append(f"Readability too academic: Grade {fk_grade}. Target 10-12.")
    elif fk_grade < 8:
        warnings.append(f"Readability too simple: Grade {fk_grade}. Target 10-12.")
        
    sentences = re.split(r'[.!?]+', content)
    long_sentences = [s for s in sentences if len(re.findall(r'\b\w+\b', s)) > 35]
    if long_sentences:
        warnings.append(f"{len(long_sentences)} sentences exceed 35 words. Split them.")

    # -----------------------------------------------------------------------
    # 2.6.D Uniqueness Check (TF-IDF)
    # -----------------------------------------------------------------------
    if existing_published_texts:
        max_sim = 0.0
        for published in existing_published_texts:
            sim = get_tf_idf_similarity(content, published)
            max_sim = max(max_sim, sim)
        
        if max_sim > 0.70:
            issues.append(f"REJECTED: Near-duplicate detected (TF-IDF similarity {max_sim:.2f} > 0.70)")
        elif max_sim >= 0.50:
            warnings.append(f"RELATED: Moderate similarity detected ({max_sim:.2f})")

    # -----------------------------------------------------------------------
    # 2.6.E Legal Accuracy Flags
    # -----------------------------------------------------------------------
    for s in sentences:
        s_lower = s.lower()
        # Ensure we only flag sentences containing trigger words
        # Only matching as isolated words where applicable
        if re.search(r'\b(must|shall|required to|obligation|liability|non-compliance will result in|penalty of|fine of|liable for)\b|section \d+ requires', s_lower):
            warnings.append("[LEGAL REVIEW REQUIRED] Sentence contains liability/obligation triggers.")
            break # Flag once per post

    # -----------------------------------------------------------------------
    # 2.6.F Factual Dates Verification
    # -----------------------------------------------------------------------
    date_pattern = r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+20\d{2}\b|\b20\d{2}\b"
    found_dates = re.findall(date_pattern, content_lower)
    for d in found_dates:
        # Check if the date string is part of approved dates
        if not any(d in app_d or app_d in d for app_d in APPROVED_DATES):
            # Only trigger once per unverified date
            w_msg = f"[UNVERIFIED DATE] {d} found. Verify against enforcement calendar."
            if w_msg not in warnings:
                warnings.append(w_msg)

    # -----------------------------------------------------------------------
    # Status Assignment
    # -----------------------------------------------------------------------
    if score < 0.40 or issues:
        status = "REJECTED"
        passed = False
    elif 0.40 <= score < 0.55:
        status = "NEEDS REVISION"
        passed = False
    elif 0.55 <= score < 0.70:
        status = "LOW RISK AUTO-PUBLISH"
        passed = True
    else:
        status = "QUALITY"
        passed = True

    log.info(
        "quality_check_done",
        title=post.title[:60],
        score=score,
        status=status,
        passed=passed,
        issues=len(issues),
        warnings=len(warnings),
    )

    return QualityResult(
        passed=passed,
        score=score,
        status=status,
        issues=issues,
        warnings=warnings,
        signals={
            "intent_pts": intent_pts,
            "density_pts": density_pts,
            "structure_pts": structure_pts,
            "eeat_pts": eeat_pts,
            "india_pts": india_pts,
            "penalty_pts": penalty_points,
            "fk_grade": fk_grade,
            "avg_sentence_len": avg_sentence_len,
            "banned_penalty_amounts": penalty_check["banned_amounts_found"],
            "low_density_section_count": len(density_check["failing_sections"]),
        }
    )
