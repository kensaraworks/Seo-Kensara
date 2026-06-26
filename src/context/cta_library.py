"""CTA Library — approved call-to-action text by intent type.

Spec: Module 2.2, Step 2 (CTA_SECTION) and Module 2.3.
CTAs are NEVER LLM-generated. They are always pulled from this library.
"""

# Approved CTA library keyed by intent type (2.2 Step 2, CTA_SECTION spec)
# Evaluates URLs to append UTM Parameters: utm_source=kensara_hub, utm_medium=blog_cta, utm_campaign=dpdpa_compliance
CTA_LIBRARY: dict = {
    "informational": {
        "heading": "Ready to Get Compliant? Start with a Free DPDPA Checklist",
        "body": (
            "Download our free DPDPA Compliance Checklist — 47 actionable steps "
            "for Indian businesses. Updated for the latest DPDP Rules 2025."
        ),
        "cta_text": "Download Free DPDPA Checklist",
        "cta_url": "https://www.kensara.in/dpdpa?utm_source=kensara_hub&utm_medium=blog_cta&utm_campaign=dpdpa_compliance",
    },
    "commercial": {
        "heading": "See How Kensara Handles DPDPA Compliance at a Fraction of OneTrust's Cost",
        "body": (
            "Indian enterprises choose KensaraAI because it is built for DPDPA "
            "from day one — not retrofitted from a GDPR product. Compare features, "
            "pricing, and implementation timelines."
        ),
        "cta_text": "Compare KensaraAI vs OneTrust",
        "cta_url": "https://www.kensara.in/benefits?utm_source=kensara_hub&utm_medium=blog_cta&utm_campaign=dpdpa_compliance",
    },
    "transactional": {
        "heading": "Book a Free 30-Minute DPDPA Assessment",
        "body": (
            "Book a free 30-minute DPDPA assessment with our certified team. "
            "We assess your current compliance posture and show you exactly what "
            "needs to be done — no obligation, no sales pressure."
        ),
        "cta_text": "Book Your Free Assessment",
        "cta_url": "https://www.kensara.in/book-demo?utm_source=kensara_hub&utm_medium=blog_cta&utm_campaign=dpdpa_compliance",
    },
}

# Service page links by post topic keyword hints (Rule 2, Module 2.5.B)
# Evaluates URLs to append UTM Parameters: utm_source=kensara_hub&utm_medium=blog_service_link&utm_campaign=dpdpa_compliance
SERVICE_PAGE_LINKS: dict = {
    "consent": {
        "anchor": "Kensara Consent Management Platform",
        "url": "https://www.kensara.in/expertise?utm_source=kensara_hub&utm_medium=blog_service_link&utm_campaign=dpdpa_compliance",
    },
    "dsar": {
        "anchor": "Kensara DSAR Automation",
        "url": "https://www.kensara.in/expertise?utm_source=kensara_hub&utm_medium=blog_service_link&utm_campaign=dpdpa_compliance",
    },
    "audit": {
        "anchor": "Kensara Compliance Assessment",
        "url": "https://www.kensara.in/book-demo?utm_source=kensara_hub&utm_medium=blog_service_link&utm_campaign=dpdpa_compliance",
    },
    "default": {
        "anchor": "KensaraAI DPDPA Compliance Platform",
        "url": "https://www.kensara.in/expertise?utm_source=kensara_hub&utm_medium=blog_service_link&utm_campaign=dpdpa_compliance",
    },
}

def get_cta(intent_type: str, topic_keyword: str = "") -> dict:
    """Return approved CTA block for a given intent.
    Never returns an LLM-generated CTA.
    """
    return CTA_LIBRARY.get(intent_type, CTA_LIBRARY["informational"])

def get_service_link(topic_keyword: str) -> dict:
    """Return mandatory service page link based on topic."""
    kw_lower = topic_keyword.lower()
    for hint, link in SERVICE_PAGE_LINKS.items():
        if hint in kw_lower:
            return link
    return SERVICE_PAGE_LINKS["default"]
