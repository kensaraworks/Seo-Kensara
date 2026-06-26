"""Shared pytest fixtures for the KensaraAI SEO Agent test suite."""
import pytest

from src.scrapers.rss_scraper import NewsItem
from src.agents.news_scout import ScoredNewsItem


@pytest.fixture
def sample_news_item() -> NewsItem:
    """A high-relevance DPDPA enforcement news item."""
    return NewsItem(
        title="DPDPA Data Protection Board issues first enforcement notice to fintech company",
        url="https://example.com/dpdpa-enforcement",
        summary=(
            "The Data Protection Board of India has issued its first enforcement notice "
            "under the Digital Personal Data Protection Act (DPDPA) to a leading fintech "
            "company for failing to obtain valid consent before processing personal data "
            "of over 2 million users. The company faces a penalty of ₹50 lakh under Section "
            "25 of the Act. MeitY confirmed the notice was issued after a complaint by a "
            "data principal."
        ),
        published_date="2026-06-08",
        source="MeitY",
    )


@pytest.fixture
def sample_scored_item(sample_news_item: NewsItem) -> ScoredNewsItem:
    """A pre-scored news item with high relevance."""
    return ScoredNewsItem(
        item=sample_news_item,
        relevance_score=9,
        why_relevant=(
            "First DPDPA enforcement action — directly impacts Indian enterprise DPOs "
            "who must now treat DPDPA compliance as non-negotiable."
        ),
        suggested_angle=(
            "DPDPA enforcement is here: What Indian companies must do now to avoid ₹50 lakh fines"
        ),
    )


@pytest.fixture
def sample_blog_post():
    """A well-structured blog post that should pass quality checks."""
    from src.agents.blog_writer import BlogPost

    return BlogPost(
        title="DPDPA Compliance Software: Complete Guide for Indian Enterprises",
        meta_description=(
            "Complete guide to DPDPA compliance software for Indian companies. Learn "
            "requirements, automation, and how KensaraAI helps achieve compliance. "
            "Book a demo today."
        ),
        slug="dpdpa-compliance-software",
        primary_keyword="DPDPA compliance software",
        secondary_keywords=["DPDPA tool India", "data protection compliance India"],
        content_markdown="""# DPDPA Compliance Software: Complete Guide for Indian Enterprises

India's Digital Personal Data Protection Act (DPDPA) came into force in 2023, giving \
MeitY and the Data Protection Board of India sweeping powers to fine non-compliant \
organisations up to ₹250 crore. If your company processes personal data of Indian \
citizens, DPDPA compliance software is no longer optional — it is a legal requirement.

This guide covers what Section 5 through Section 10 of the DPDPA requires, the most \
common mistakes Indian companies make, and how purpose-built tooling can cut your \
compliance workload by 80%.

## What is DPDPA and Why Does It Matter for Indian Enterprises?

The Digital Personal Data Protection Act 2023 (DPDPA) is India's first comprehensive \
data privacy law. Unlike GDPR in Europe, it was designed with Indian business realities \
in mind — but its enforcement teeth are very real. The Data Protection Board of India \
(DPBI) issued its first enforcement notice in June 2026 to a fintech company, levying \
a ₹50 lakh penalty under Section 25.

Key obligations under DPDPA:
1. Obtain valid, informed consent before processing personal data (Section 6)
2. Respond to Data Subject Access Requests (DSARs) within 30 days
3. Notify the Data Protection Board and affected individuals within 72 hours of a breach
4. Appoint a Consent Manager if you are a Data Fiduciary processing at scale
5. Maintain Records of Processing Activities aligned with Schedule I

## DPDPA Compliance Checklist for Indian Companies

Use this checklist to identify your compliance gaps before the DPBI finds them:

- [ ] Consent notices reviewed and updated for Section 6 requirements
- [ ] DSAR intake form live on website
- [ ] Breach notification SOP documented (72-hour clock starts at detection)
- [ ] Data retention schedules defined per Schedule II
- [ ] Third-party Data Processors mapped and DPAs signed
- [ ] Data Protection Officer (DPO) appointed and trained

Running this checklist manually across multiple business units is where most companies \
fail. A DPDPA compliance software platform automates the tracking and alerts.

## Common Mistakes Indian Companies Make with DPDPA Compliance

**1. Treating consent as a checkbox, not a process**
Most companies copy-paste a GDPR cookie banner and assume they are DPDPA-compliant. \
They are not. Section 6 of the DPDPA requires consent to be specific, informed, and \
freely given — with a clear withdrawal mechanism.

**2. Missing the 72-hour breach clock**
Under GDPR Article 33 and DPDPA's equivalent provisions, breach notification must \
happen within 72 hours of detection — not discovery by your CISO on Monday morning. \
The clock starts the moment any employee becomes aware.

**3. No audit trail for DSARs**
When the DPBI asks "prove you responded to this DSAR within 30 days," you need an \
automated audit trail — not a spreadsheet.

## How KensaraAI Solves DPDPA Compliance for Indian Enterprises

KensaraAI is India's first AI-native DPDPA compliance software, built specifically for \
the Indian regulatory environment. Unlike US-centric tools like OneTrust (priced at \
₹75L+/year), KensaraAI starts at ₹15L/year and covers DPDPA, GDPR, and CCPA in a \
single platform.

Key capabilities:
- **Automated DSAR handling** — AI agents process requests end-to-end in 72 hours
- **72-hour breach clock** — starts automatically on detection, not manual trigger
- **Consent management** — Section 6 compliant consent notices with audit trail
- **MeitY-credentialed** — GENESIS EIR 2.0 incubatee + IITG TIC incubated

All data stays in India (Azure India region), making KensaraAI the only \
enterprise-grade DPDPA compliance software that meets data residency requirements \
without expensive customisation.

## Conclusion

DPDPA enforcement is no longer hypothetical — the Data Protection Board of India has \
started issuing fines. Every Indian enterprise that processes personal data needs a \
documented compliance programme backed by software that provides audit trails, \
automated DSAR handling, and real-time breach detection.

Ready to see how KensaraAI can get your company DPDPA-ready in 30 days?

[Request a Demo →](https://www.kensara.in/book-demo)
""",
        word_count=650,
    )
