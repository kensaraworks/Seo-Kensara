"""Content risk classifier — determines approval workflow for generated content.

Risk levels:
    HIGH   — Full CEO approval required before any action.
    MEDIUM — 24-hour CEO review window; auto-publishes if no response.
    LOW    — Auto-publish after QA pass.

Classifier is intentionally conservative: when in doubt, escalate to HIGH.
"""
import re
import structlog
from enum import Enum
from pydantic import BaseModel

log = structlog.get_logger()

# -----------------------------------------------------------------------
# Signal dictionaries
# -----------------------------------------------------------------------

COMPETITOR_NAMES = [
    "onetrust",
    "trustarc",
    "seqrite",
    "vishwaas",
    "informatica",
    "bigid",
    "privacera",
    "cyberark",
]

# Pricing-related patterns
PRICING_PATTERNS = [
    r"₹\s*\d",                     # ₹15L, ₹40,000
    r"\$\s*\d",                     # $100/month
    r"\d+\s*(lakh|l)\s*(per|a)\s*(year|month|annum)",  # 15 lakh per year
    r"starting\s+at\s+[\$₹]",
    r"costs?\s+[\$₹]",
    r"priced?\s+at\s+[\$₹]",
    r"free\s+trial",
    r"pricing",
    r"per\s+user\s+per\s+month",
]

# Legal claim patterns (guarantee, certification assertions)
LEGAL_CLAIM_PATTERNS = [
    r"\bcompliant\b",               # "we are GDPR compliant" — dangerous claim
    r"\bguaranteed?\b",
    r"\bcertified\b",
    r"\bfully\s+compliant\b",
    r"\b100\s*%\s+compliant\b",
    r"\blegally\s+(required|mandated|binding)\b",
    r"\bmust\s+(by law|legally)\b",
    r"\bwe\s+guarantee\b",
    r"\bno\s+fines?\b",
    r"\bzero\s+(fine|penalty|risk)\b",
]

# Content types that are inherently HIGH risk
HIGH_RISK_CONTENT_TYPES = {
    "pillar",
    "pillar_page",
    "comparison",
    "product_comparison",
    "competitor_comparison",
    "legal_guide",
}

# Regulatory citation patterns that might be quoted incorrectly
REGULATORY_CITATION_PATTERNS = [
    r"section\s+\d+\s+(of|under)\s+(dpdpa|the act|digital personal)",
    r"article\s+\d+\s+gdpr",
    r"rule\s+\d+\s+of\s+(dpdpa|the|data)",
    r"schedule\s+[ivxIVX]+\s+of",
]

# KensaraAI feature / product mentions that elevate to MEDIUM
PRODUCT_MENTION_TERMS = [
    "kensarai",
    "kensara.in",
    "kensarai platform",
    "our platform",
    "our agents",
    "request-demo",
    "book a demo",
]

# Purely educational / news commentary indicators
EDUCATIONAL_TERMS = [
    "enforcement notice",
    "ico fined",
    "edpb ruling",
    "data protection board",
    "what is dpdpa",
    "how to",
    "checklist",
    "step-by-step",
    "guide for",
    "understanding",
    "explained",
]


class RiskLevel(str, Enum):
    LOW = "low"       # auto-publish after QA pass
    MEDIUM = "medium"  # 24h CEO window, then auto-publish
    HIGH = "high"     # full CEO approval required


class RiskAssessment(BaseModel):
    level: RiskLevel
    reasons: list[str]
    auto_publish_after_hours: int | None  # None = never auto-publish


def classify_content_risk(
    content_type: str,  # "blog" | "linkedin" | "newsletter" | "pillar" | "comparison"
    content: str,
    keyword: str = "",
) -> RiskAssessment:
    """Classify risk level of generated content.

    HIGH (full CEO approval required):
    - Contains competitor names
    - Contains specific pricing claims
    - Contains legal guarantee claims ("compliant", "guaranteed", "certified")
    - Contains regulatory citations that might be wrong
    - Content type is a pillar page or product comparison

    MEDIUM (24h auto-publish window):
    - LinkedIn posts
    - Newsletter content
    - Blog posts mentioning KensaraAI features
    - Contains specific product CTAs

    LOW (auto-publish after QA pass):
    - News commentary without product or competitor mentions
    - Educational DPDPA/GDPR content with no competitor/product claims
    """
    content_lower = content.lower()
    reasons: list[str] = []
    risk_level = RiskLevel.LOW

    # ------------------------------------------------------------------ #
    #  HIGH risk signals — check all, accumulate reasons                   #
    # ------------------------------------------------------------------ #
    high_signals: list[str] = []

    # Competitor names
    for name in COMPETITOR_NAMES:
        if name in content_lower:
            high_signals.append(f"Contains competitor name: '{name}'")

    # Pricing claims
    for pattern in PRICING_PATTERNS:
        if re.search(pattern, content_lower):
            high_signals.append(f"Contains pricing reference (pattern: {pattern})")
            break  # one pricing flag is enough

    # Legal/guarantee claims
    for pattern in LEGAL_CLAIM_PATTERNS:
        match = re.search(pattern, content_lower)
        if match:
            high_signals.append(
                f"Contains legal claim ('{match.group()}') — requires legal review"
            )
            break  # one legal flag is enough

    # Regulatory citations (might be mis-cited)
    citation_count = 0
    for pattern in REGULATORY_CITATION_PATTERNS:
        if re.search(pattern, content_lower):
            citation_count += 1
    if citation_count >= 3:
        high_signals.append(
            f"Contains {citation_count} specific regulatory citations — verify accuracy before publish"
        )

    # High-risk content type
    if content_type.lower().replace(" ", "_") in HIGH_RISK_CONTENT_TYPES:
        high_signals.append(
            f"Content type '{content_type}' is always HIGH risk (pillar/comparison)"
        )

    if high_signals:
        reasons.extend(high_signals)
        risk_level = RiskLevel.HIGH
        log.info(
            "risk_classified",
            level="high",
            reason_count=len(high_signals),
            content_type=content_type,
        )
        return RiskAssessment(
            level=RiskLevel.HIGH,
            reasons=reasons,
            auto_publish_after_hours=None,
        )

    # ------------------------------------------------------------------ #
    #  MEDIUM risk signals                                                  #
    # ------------------------------------------------------------------ #
    medium_signals: list[str] = []

    # LinkedIn posts are always MEDIUM (reach + brand risk)
    if content_type.lower() in ("linkedin", "linkedin_post"):
        medium_signals.append("LinkedIn posts always require CEO review window (brand risk)")

    # Newsletter content
    if content_type.lower() in ("newsletter", "email", "digest"):
        medium_signals.append("Newsletter content reaches subscribers directly — CEO review required")

    # Blog posts mentioning KensaraAI features
    for term in PRODUCT_MENTION_TERMS:
        if term in content_lower:
            medium_signals.append(
                f"Contains product reference ('{term}') — elevates to MEDIUM for accuracy check"
            )
            break

    if medium_signals:
        reasons.extend(medium_signals)
        risk_level = RiskLevel.MEDIUM
        log.info(
            "risk_classified",
            level="medium",
            reason_count=len(medium_signals),
            content_type=content_type,
        )
        return RiskAssessment(
            level=RiskLevel.MEDIUM,
            reasons=reasons,
            auto_publish_after_hours=24,
        )

    # ------------------------------------------------------------------ #
    #  LOW — pure educational / news commentary                            #
    # ------------------------------------------------------------------ #
    reasons.append(
        "No competitor names, pricing claims, legal guarantees, or product mentions detected"
    )
    log.info("risk_classified", level="low", content_type=content_type)
    return RiskAssessment(
        level=RiskLevel.LOW,
        reasons=reasons,
        auto_publish_after_hours=0,
    )
