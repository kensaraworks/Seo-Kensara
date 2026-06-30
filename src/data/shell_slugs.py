"""Pre-registered shell slug catalog from blog_slug_reference.md.

These are the 33 URL paths on kensara.in that currently show a static
placeholder CTA. Once an article with the exact slug is inserted into
public.blogs, the site swaps the placeholder for the dynamic article body.

The SEO agent uses this catalog on startup to auto-enqueue any unfilled slugs
so they are prioritised for content generation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 8-pillar slug → blog_slug_reference.md mapping
# ---------------------------------------------------------------------------
#
# Structure per entry:
#   slug          — exact slug to use in public.blogs.slug
#   title         — article title (used as keyword for generation)
#   pillar        — exact Supabase pillar slug (must match router)
#   category      — badge tag displayed on the blog card
#   tier          — content generation tier (1 = deep, 2 = standard)
# ---------------------------------------------------------------------------

SHELL_SLUGS: list[dict] = [
    # ── 1. DPDPA Fundamentals ──────────────────────────────────────────────
    {
        "slug": "what-is-dpdpa",
        "title": "What is DPDPA? India's Digital Personal Data Protection Act Explained",
        "pillar": "fundamentals",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "dpdp-act-2023-explained",
        "title": "DPDP Act 2023 Explained: Section-by-Section Breakdown",
        "pillar": "fundamentals",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "data-fiduciary-vs-data-principal",
        "title": "Data Fiduciary vs. Data Principal: Key Definitions Under DPDPA",
        "pillar": "fundamentals",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "dpdpa-vs-gdpr-comparison",
        "title": "DPDPA vs. GDPR: How India's Data Law Compares to Europe's",
        "pillar": "fundamentals",
        "category": "Deep dive",
        "tier": 1,
    },
    {
        "slug": "dpdp-rules-2025-explained",
        "title": "DPDP Rules 2025 Explained: What Changed and What It Means for Compliance",
        "pillar": "fundamentals",
        "category": "Guide",
        "tier": 1,
    },

    # ── 2. DPDPA by Industry ───────────────────────────────────────────────
    {
        "slug": "dpdpa-compliance-fintech-india",
        "title": "DPDPA Compliance for Fintech & Payment Platforms in India",
        "pillar": "industry",
        "category": "Fintech",
        "tier": 1,
    },
    {
        "slug": "dpdpa-compliance-healthcare-india",
        "title": "DPDPA Compliance for Healthcare & Hospital Chains in India",
        "pillar": "industry",
        "category": "Healthcare",
        "tier": 1,
    },
    {
        "slug": "dpdpa-compliance-saas-companies",
        "title": "DPDPA Compliance for SaaS Companies in India",
        "pillar": "industry",
        "category": "SaaS",
        "tier": 1,
    },
    {
        "slug": "dpdpa-compliance-edtech-platforms",
        "title": "DPDPA Compliance for Edtech Platforms in India",
        "pillar": "industry",
        "category": "Edtech",
        "tier": 2,
    },
    {
        "slug": "dpdpa-compliance-ecommerce-businesses",
        "title": "DPDPA Compliance for E-commerce Businesses in India",
        "pillar": "industry",
        "category": "E-commerce",
        "tier": 2,
    },

    # ── 3. Technical Compliance Operations ────────────────────────────────
    {
        "slug": "consent-management-platform-india",
        "title": "Consent Management Platforms in India: What DPDPA Requires",
        "pillar": "operations",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "dsar-automation-india",
        "title": "DSAR Automation in India: Building a Scalable Request Workflow",
        "pillar": "operations",
        "category": "Deep dive",
        "tier": 1,
    },
    {
        "slug": "dpia-template-india",
        "title": "DPIA Template for Indian Businesses: A Step-by-Step Guide",
        "pillar": "operations",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "consent-manager-registration-dpdpa",
        "title": "Consent Manager Registration Under DPDPA: The MeitY Filing Process",
        "pillar": "operations",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "data-retention-policy-india-dpdpa",
        "title": "Building a Data Retention Policy for DPDPA Compliance",
        "pillar": "operations",
        "category": "Guide",
        "tier": 2,
    },

    # ── 4. SDF Obligations ────────────────────────────────────────────────
    {
        "slug": "who-is-significant-data-fiduciary-dpdpa",
        "title": "Who is a Significant Data Fiduciary Under DPDPA?",
        "pillar": "sdf-obligations",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "sdf-obligations-india",
        "title": "SDF Compliance Obligations in India: The Complete Checklist",
        "pillar": "sdf-obligations",
        "category": "Deep dive",
        "tier": 1,
    },
    {
        "slug": "data-protection-impact-assessment-india",
        "title": "Data Protection Impact Assessment (DPIA) for Indian Businesses",
        "pillar": "sdf-obligations",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "algorithmic-transparency-dpdpa",
        "title": "Algorithmic Transparency Requirements Under DPDPA for SDFs",
        "pillar": "sdf-obligations",
        "category": "Deep dive",
        "tier": 2,
    },

    # ── 5. Data Principal Rights ──────────────────────────────────────────
    {
        "slug": "right-to-erasure-dpdpa",
        "title": "Right to Erasure Under DPDPA: How Indian Users Can Delete Their Data",
        "pillar": "data-principal-rights",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "right-to-access-personal-data-india",
        "title": "Right to Access Personal Data in India Under DPDPA",
        "pillar": "data-principal-rights",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "how-to-file-grievance-dpdpa",
        "title": "How to File a Grievance Under DPDPA: Step-by-Step Workflow",
        "pillar": "data-principal-rights",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "parental-consent-dpdpa-children-data",
        "title": "Parental Consent & Children's Data Under DPDPA: What Platforms Must Do",
        "pillar": "data-principal-rights",
        "category": "Guide",
        "tier": 1,
    },

    # ── 6. Enforcement & Adjudication ─────────────────────────────────────
    {
        "slug": "dpdpa-penalty-amount",
        "title": "DPDPA Penalty Amounts: The Complete Fine Schedule for 2025–2026",
        "pillar": "enforcement",
        "category": "Deep dive",
        "tier": 1,
    },
    {
        "slug": "data-protection-board-adjudication",
        "title": "Data Protection Board of India: How Adjudication Proceedings Work",
        "pillar": "enforcement",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "dpdpa-non-compliance-consequences",
        "title": "The Real Consequences of DPDPA Non-Compliance in India",
        "pillar": "enforcement",
        "category": "Deep dive",
        "tier": 2,
    },

    # ── 7. DPO & Compliance Services ──────────────────────────────────────
    {
        "slug": "dpo-as-a-service-india",
        "title": "DPO-as-a-Service for Indian Companies: How Fractional DPO Works",
        "pillar": "dpo-services",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "dpdpa-compliance-consultant-india",
        "title": "What to Look for in a DPDPA Compliance Consultant in India",
        "pillar": "dpo-services",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "dpdpa-audit-service-india",
        "title": "DPDPA Audit Service: What a Compliance Audit Actually Covers",
        "pillar": "dpo-services",
        "category": "Deep dive",
        "tier": 2,
    },

    # ── 8. Comparing Compliance Software ──────────────────────────────────
    {
        "slug": "onetrust-alternative-india",
        "title": "Looking for a OneTrust Alternative in India? Here's What to Compare",
        "pillar": "compare",
        "category": "Guide",
        "tier": 1,
    },
    {
        "slug": "kensara-vs-onetrust",
        "title": "Kensara vs. OneTrust: A Feature-by-Feature Comparison for Indian Companies",
        "pillar": "compare",
        "category": "Deep dive",
        "tier": 1,
    },
    {
        "slug": "affordable-dpdpa-compliance-solution",
        "title": "The Most Affordable DPDPA Compliance Solution for Indian SMEs",
        "pillar": "compare",
        "category": "Guide",
        "tier": 2,
    },
    {
        "slug": "best-dpdpa-compliance-software",
        "title": "Best DPDPA Compliance Software in 2026: A Comprehensive Buyer's Guide",
        "pillar": "compare",
        "category": "Deep dive",
        "tier": 1,
    },
]

# Quick lookup: slug → entry
SHELL_SLUG_MAP: dict[str, dict] = {entry["slug"]: entry for entry in SHELL_SLUGS}

# ---------------------------------------------------------------------------
# Pillar → cluster mapping (used by the publisher to resolve pillar from cluster)
# ---------------------------------------------------------------------------
CLUSTER_TO_PILLAR: dict[str, str] = {
    "fundamentals": "fundamentals",
    "industry": "industry",
    "operations": "operations",
    "sdf-obligations": "sdf-obligations",
    "data-principal-rights": "data-principal-rights",
    "enforcement": "enforcement",
    "dpo-services": "dpo-services",
    "compare": "compare",
    # Fallback for legacy / unspecified clusters
    "general": "fundamentals",
}

# Cluster → human-readable category badge
CLUSTER_TO_CATEGORY: dict[str, str] = {
    "fundamentals": "Guide",
    "industry": "Industry",
    "operations": "Deep dive",
    "sdf-obligations": "Deep dive",
    "data-principal-rights": "Guide",
    "enforcement": "Enforcement",
    "dpo-services": "Guide",
    "compare": "Guide",
    "general": "Guide",
}
