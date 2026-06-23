"""
GEO Schema Generator — generates JSON-LD structured data for KensaraAI content.

JSON-LD schemas enable:
- Google Rich Results (Dataset, FAQPage, Organization)
- AI system knowledge graph ingestion
- Schema.org machine-readable authority signals

Usage:
    from src.geo.schema_generator import generate_dataset_schema, generate_organization_schema, generate_faq_schema
"""

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Organisation constants
# ---------------------------------------------------------------------------

KENSARAI_ORG = {
    "@type": "Organization",
    "@id": "https://kensara.in/#organization",
    "name": "KensaraAI",
    "legalName": "Tajmanor LLP",
    "url": "https://kensara.in",
    "logo": {
        "@type": "ImageObject",
        "url": "https://kensara.in/logo.png",
        "width": 512,
        "height": 512,
    },
    "description": (
        "India's first AI-native DPDPA + GDPR + CCPA compliance platform. "
        "KensaraAI deploys 12 AI agents that autonomously scan enterprise infrastructure "
        "and guide Data Protection Officers through regulatory requirements."
    ),
    "foundingDate": "2024",
    "foundingLocation": {
        "@type": "Place",
        "name": "India",
        "address": {"@type": "PostalAddress", "addressCountry": "IN"},
    },
    "areaServed": "IN",
    "knowsAbout": [
        "Digital Personal Data Protection Act 2023",
        "GDPR compliance",
        "CCPA compliance",
        "Data Subject Access Requests",
        "Consent Management",
        "Data Breach Notification",
        "Data Protection Impact Assessment",
        "GRC compliance automation",
    ],
    "memberOf": [
        {
            "@type": "Organization",
            "name": "MeitY GENESIS EIR 2.0",
            "url": "https://www.meity.gov.in/",
        },
        {
            "@type": "Organization",
            "name": "IIT Guwahati Technology Incubation Centre",
            "url": "https://iitg.ac.in/",
        },
    ],
    "sameAs": [
        "https://www.linkedin.com/company/kensarai",
    ],
    "contactPoint": {
        "@type": "ContactPoint",
        "contactType": "sales",
        "url": "https://kensara.in/request-demo",
        "availableLanguage": ["English", "Hindi"],
    },
}


# ---------------------------------------------------------------------------
# Dataset schema — for the DPDPA enforcement tracker
# ---------------------------------------------------------------------------


def generate_dataset_schema(tracker_data: dict[str, Any]) -> dict[str, Any]:
    """
    Generate JSON-LD Dataset schema for the DPDPA enforcement tracker.
    Use in the <head> of the enforcement tracker HTML page.

    Args:
        tracker_data: The parsed enforcement_tracker.json content.

    Returns:
        JSON-LD dict ready for json.dumps() into a <script type="application/ld+json"> tag.
    """
    stats = tracker_data.get("statistics", {})
    last_updated = tracker_data.get("metadata", {}).get(
        "last_updated", datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    total_actions = (
        stats.get("total_enforcement_actions", 0)
        + stats.get("total_pre_dpdpa_actions", 0)
        + stats.get("total_cert_in_actions", 0)
    )

    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "@id": "https://kensara.in/dpdpa-enforcement-tracker#dataset",
        "name": "DPDPA Enforcement Actions India — KensaraAI Tracker",
        "description": (
            f"Comprehensive database of {total_actions} Indian data privacy enforcement actions. "
            "Covers DPDPA 2023 proceedings, IT Act Section 43A/72A cases, CERT-In breach notification "
            "enforcement, RBI data localisation actions, and international GDPR fines affecting Indian companies. "
            "Maintained by KensaraAI — India's AI-native compliance platform."
        ),
        "url": "https://kensara.in/dpdpa-enforcement-tracker",
        "creator": KENSARAI_ORG,
        "publisher": KENSARAI_ORG,
        "dateModified": last_updated,
        "datePublished": "2024-01-01",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "isAccessibleForFree": True,
        "keywords": [
            "DPDPA",
            "Digital Personal Data Protection Act",
            "data protection enforcement India",
            "CERT-In enforcement",
            "IT Act 43A",
            "data breach India",
            "MeitY enforcement",
            "data privacy penalty India",
            "GDPR India",
            "KensaraAI",
        ],
        "spatialCoverage": {
            "@type": "Place",
            "name": "India",
            "geo": {"@type": "GeoCoordinates", "latitude": 20.5937, "longitude": 78.9629},
        },
        "temporalCoverage": "2017/..",
        "measurementTechnique": "Manual research from official regulatory sources, verified news reports, and court orders",
        "variableMeasured": [
            {
                "@type": "PropertyValue",
                "name": "Enforcement actions",
                "value": total_actions,
            },
            {
                "@type": "PropertyValue",
                "name": "Regulatory authorities covered",
                "value": "MeitY, CERT-In, CCI, RBI, SEBI, IRDAI, UIDAI, Data Protection Board of India",
            },
        ],
        "distribution": {
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": "https://kensara.in/dpdpa-enforcement-tracker/data.json",
        },
        "about": [
            {"@type": "Thing", "name": "Digital Personal Data Protection Act 2023"},
            {"@type": "Thing", "name": "Data Protection Board of India"},
            {"@type": "Thing", "name": "CERT-In cybersecurity enforcement"},
            {"@type": "Thing", "name": "IT Act Section 43A"},
        ],
        "citation": "KensaraAI (2024). DPDPA Enforcement Tracker. https://kensara.in/dpdpa-enforcement-tracker",
    }


# ---------------------------------------------------------------------------
# Organisation schema — for site-wide use
# ---------------------------------------------------------------------------


def generate_organization_schema() -> dict[str, Any]:
    """
    Generate JSON-LD Organization schema for KensaraAI.
    Use in the <head> of every page on kensara.in.

    Returns:
        JSON-LD dict for KensaraAI's organizational identity.
    """
    return {
        "@context": "https://schema.org",
        **KENSARAI_ORG,
        "offers": [
            {
                "@type": "Offer",
                "name": "KensaraAI DPDPA Compliance Platform",
                "description": (
                    "AI-native DPDPA + GDPR + CCPA compliance platform for Indian enterprises. "
                    "Includes DSAR automation, consent management, breach notification, and GRC/DPIA modules."
                ),
                "url": "https://kensara.in/request-demo",
                "priceCurrency": "INR",
                "priceSpecification": {
                    "@type": "PriceSpecification",
                    "minPrice": 1500000,
                    "maxPrice": 4000000,
                    "priceCurrency": "INR",
                    "description": "₹15 lakh to ₹40 lakh per year depending on company size",
                },
                "eligibleRegion": {"@type": "Country", "name": "India"},
            }
        ],
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "KensaraAI Compliance Modules",
            "itemListElement": [
                {
                    "@type": "Offer",
                    "itemOffered": {
                        "@type": "SoftwareApplication",
                        "name": "M2 — DSAR Automation",
                        "description": "Automates Data Subject Access Request intake, processing, and fulfilment within DPDPA's 30-day deadline.",
                        "applicationCategory": "BusinessApplication",
                    },
                },
                {
                    "@type": "Offer",
                    "itemOffered": {
                        "@type": "SoftwareApplication",
                        "name": "M3 — Consent Management",
                        "description": "Captures, stores, and manages data principal consent under DPDPA 2023. Includes consent withdrawal workflow.",
                        "applicationCategory": "BusinessApplication",
                    },
                },
                {
                    "@type": "Offer",
                    "itemOffered": {
                        "@type": "SoftwareApplication",
                        "name": "M5 — GRC/DPIA",
                        "description": "Governance, Risk and Compliance module with AI-assisted Data Protection Impact Assessment for DPDPA.",
                        "applicationCategory": "BusinessApplication",
                    },
                },
            ],
        },
        "slogan": "India's first AI-native DPDPA compliance platform",
        "numberOfEmployees": {"@type": "QuantitativeValue", "minValue": 10, "maxValue": 50},
        "award": [
            "MeitY GENESIS EIR 2.0 Incubatee",
            "IIT Guwahati Technology Incubation Centre — Selected Company",
        ],
    }


# ---------------------------------------------------------------------------
# FAQ schema — for blog posts and landing pages
# ---------------------------------------------------------------------------


def generate_faq_schema(faqs: list[dict[str, str]]) -> dict[str, Any]:
    """
    Generate JSON-LD FAQPage schema.
    Use on blog posts and landing pages to enable Google rich result FAQ snippets.

    Args:
        faqs: List of dicts with "question" and "answer" keys.
              Answers should be plain text (max 300 chars for rich result eligibility).

    Returns:
        JSON-LD FAQPage schema dict.

    Example:
        faqs = [
            {"question": "What is DPDPA?", "answer": "DPDPA is India's Digital Personal Data Protection Act 2023..."},
            {"question": "What is the penalty under DPDPA?", "answer": "Up to ₹250 crore per violation..."},
        ]
        schema = generate_faq_schema(faqs)
    """
    if not faqs:
        raise ValueError("faqs list must not be empty")

    for i, faq in enumerate(faqs):
        if "question" not in faq or "answer" not in faq:
            raise ValueError(f"FAQ at index {i} must have 'question' and 'answer' keys. Got: {list(faq.keys())}")

    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": faq["answer"],
                },
            }
            for faq in faqs
        ],
    }


# ---------------------------------------------------------------------------
# Article/BlogPosting schema — for individual blog posts
# ---------------------------------------------------------------------------


def generate_article_schema(
    title: str,
    description: str,
    slug: str,
    published_date: str,
    modified_date: str | None = None,
    keywords: list[str] | None = None,
    word_count: int | None = None,
) -> dict[str, Any]:
    """
    Generate JSON-LD Article schema for a KensaraAI blog post.

    Args:
        title: The blog post title.
        description: Meta description (150-160 chars).
        slug: URL slug, e.g. "dpdpa-compliance-checklist".
        published_date: ISO date string, e.g. "2025-01-15".
        modified_date: ISO date of last update (defaults to published_date).
        keywords: List of target keywords for the post.
        word_count: Approximate word count.

    Returns:
        JSON-LD Article schema dict.
    """
    url = f"https://kensara.in/blog/{slug}"
    if modified_date is None:
        modified_date = published_date

    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "@id": f"{url}#article",
        "headline": title,
        "description": description,
        "url": url,
        "datePublished": published_date,
        "dateModified": modified_date,
        "author": KENSARAI_ORG,
        "publisher": KENSARAI_ORG,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "inLanguage": "en-IN",
        "about": {"@type": "Thing", "name": "DPDPA compliance"},
        "mentions": [
            {"@type": "Legislation", "name": "Digital Personal Data Protection Act 2023"},
        ],
    }
    if keywords:
        schema["keywords"] = ", ".join(keywords)
    if word_count:
        schema["wordCount"] = word_count
    return schema


# ---------------------------------------------------------------------------
# BreadcrumbList schema — for navigation
# ---------------------------------------------------------------------------


def generate_breadcrumb_schema(breadcrumbs: list[dict[str, str]]) -> dict[str, Any]:
    """
    Generate JSON-LD BreadcrumbList schema.

    Args:
        breadcrumbs: List of {"name": str, "url": str} dicts in order from root to current page.

    Returns:
        JSON-LD BreadcrumbList schema dict.

    Example:
        breadcrumbs = [
            {"name": "Home", "url": "https://kensara.in"},
            {"name": "DPDPA Resources", "url": "https://kensara.in/dpdpa"},
            {"name": "DPDPA Enforcement Tracker", "url": "https://kensara.in/dpdpa-enforcement-tracker"},
        ]
    """
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": crumb["name"],
                "item": crumb["url"],
            }
            for i, crumb in enumerate(breadcrumbs)
        ],
    }
