"""Deterministic structure templates and generation parameters for the 3-Tier system.

Spec: Module 2.3 — Three-Tier Content Type System.
"""
from typing import Any

TIER_1_TEMPLATE = {
    "target_word_count": "1,800-2,500 words",
    "structure": [
        "H2: What [Regulation/Rule/Section] Actually Says (Plain English)",
        "H2: What This Means for Indian Businesses — Section by Section",
        "H2: Which Industries Are Most Affected? [table]",
        "H2: What You Must Do Before [Enforcement Date] [HowTo steps]",
        "H2: Common Compliance Mistakes to Avoid",
        "H2: How Kensara Helps You Implement [Regulation] — CTA section",
        "FAQ section: 5 PAA questions with 60-word answers each"
    ],
    "localization_rules": (
        "MAXIMUM LOCALIZATION: Every section MUST reference specific Indian regulatory bodies, "
        "Indian enforcement precedents (if any), Indian company size thresholds, and the "
        "Indian calendar (noting Diwali period compliance disruption, financial year end constraints, etc.)."
    )
}

TIER_2_TEMPLATE = {
    "target_word_count": "1,200-1,600 words",
    "structure": [
        "H2: What DPDPA Means Specifically for [Industry] Companies",
        "H2: Your [Industry]-Specific DPDPA Obligations — The Complete List [table]",
        "H2: The Data You Collect and Why Each Category Triggers Different Rules",
        "H2: [Industry] DPDPA Compliance: Step-by-Step Implementation [HowTo]",
        "H2: Real Consequences of Non-Compliance for [Industry] in India [case study]",
        "H2: How Kensara's relevant module Solves This for [Industry] — CTA",
        "FAQ section: 4 PAA questions"
    ],
    "localization_rules": (
        "HIGH LOCALIZATION: Must name at least 2 real Indian companies in the relevant industry "
        "as illustrative examples. Must reference the industry-specific Indian regulator "
        "(e.g., SEBI for BFSI, MCI for healthtech, UGC for education) in addition to DPBI/MeitY."
    )
}

TIER_3_TEMPLATE = {
    "target_word_count": "600-900 words",
    "structure": [
        "H2: What Happened — The Regulatory Development",
        "H2: What This Means for Indian Businesses Under DPDPA",
        "H2: The 3 Immediate Actions You Should Take",
        "H2: How Kensara Helps You Respond to This Update — CTA section"
    ],
    "localization_rules": (
        "MEDIUM LOCALIZATION: Reference the specific Indian entity or regulator that issued "
        "the update. Maintain Indian English spelling standards."
    )
}

def get_tier_config(tier: int, industry: str = None) -> dict[str, Any]:
    """Return the complete configuration and prompt template for a specific tier."""
    if tier == 1:
        return TIER_1_TEMPLATE
    elif tier == 2:
        config = TIER_2_TEMPLATE.copy()
        if industry:
            # Inject the industry directly into the structure template strings
            config["structure"] = [
                h2.replace("[Industry]", industry)
                for h2 in config["structure"]
            ]
        return config
    elif tier == 3:
        return TIER_3_TEMPLATE
    else:
        raise ValueError(f"Invalid tier: {tier}")

def generate_tier3_title(entity: str, action: str) -> str:
    """Deterministic title generation for Tier 3 newsjack posts.
    
    Spec: "[Indian Entity/Regulator] Issues [Guidance/Penalty/Rule]: 
           What This Means for Your DPDPA Compliance"
    """
    if not entity:
        entity = "DPBI"
    if not action:
        action = "New Rule"
        
    return f"{entity} Issues {action}: What This Means for Your DPDPA Compliance"
