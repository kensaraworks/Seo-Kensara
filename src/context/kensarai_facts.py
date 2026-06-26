"""KensaraAI brand facts — single source of truth for all LLM prompts.

Update this file when facts change. Never hardcode brand claims anywhere else.
"""

KENSARAI_FACTS: dict = {
    "company": {
        "name": "KensaraAI",
        "legal_entity": "KensaraAI Private Limited",
        "tagline": "AI-Powered Compliance & Data Governance for Enterprises",
        "website": "https://kensara.in",
        "demo_url": "https://www.kensara.in/book-demo",
        "founders": "Mr Rudraksh Tatwal (Founder & CEO), Mr Prince (Co-founder & COO)",
    },

    "sitelinks": {
        "home": "https://kensara.in/",
        "dpdpa": "https://kensara.in/dpdpa",
        "what_we_do": "https://kensara.in/benefits",
        "expertise": "https://kensara.in/expertise",
        "blogs": "https://kensara.in/blogs",
        "credibility": "https://kensara.in/credibility",
        "book_demo": "https://kensara.in/book-demo",
        "contact": "https://www.kensara.in/book-demo",
        "privacy": "https://kensara.in/privacy",
        "terms": "https://kensara.in/terms",
    },

    "what_it_is": (
        "India's first AI-native DPDPA + GDPR + GRC compliance platform. "
        "18 AI agents autonomously scan client infrastructure (read-only) to detect "
        "privacy gaps, generate findings, and surface actionable compliance reports. "
        "Human-gate architecture ensures AI proposes — DPO decides. "
        "Nothing executes without explicit human approval."
    ),

    "ai_agents": {
        "count": 18,
        "description": (
            "18 AI agents orchestrated by a Central Coordinator. "
            "They scan client infrastructure across databases, cloud storage, SaaS, and identity systems "
            "in strict read-only mode (Max 100-row sampling, SELECT only). "
            "All agent findings flow into the KensaraAI platform — never written back to client systems."
        ),
    },

    "modules": {
        "M1": {
            "name": "Data Intelligence & Discovery",
            "description": (
                "The foundational data layer. Contains 7 AI agents including Infrastructure Discovery, "
                "Data Classification (PII detection), Data Lineage Mapping, Assessment AI (contract extraction), "
                "RoPA Generator, Compliance Risk Detection, and Evidence Collection."
            ),
        },
        "M2": {
            "name": "Data Subject Rights (DSR)",
            "description": (
                "Automates Data Subject Access Requests (DSARs) end-to-end. "
                "Includes identity verification, Breach Detection Agent, and Multi-jurisdiction notification logic."
            ),
        },
        "M3": {
            "name": "Consent Management",
            "description": (
                "Consent collection and storage with immutable receipts. "
                "Features a weekly Selenium-based Cookie Scanner Agent and real-time Consent Signal Tracking."
            ),
        },
        "M4": {
            "name": "Vendor Risk Management (TPRM)",
            "description": (
                "Vendor risk monitoring via API integrations. Includes a Procurement Integration Agent "
                "and Dark Web Credential Monitoring. Auto-tiers vendors by criticality."
            ),
        },
        "M5": {
            "name": "Continuous Monitoring & Security",
            "description": (
                "Anomaly Detection and Security Posture Monitoring. "
                "Tracks encryption coverage, MFA, patch currency, with real-time alerts."
            ),
        },
        "M6": {
            "name": "AI Governance",
            "description": (
                "AI model auditing, bias detection, and explainability reporting for enterprise AI systems."
            ),
        },
    },

    "target_market_and_gtm": {
        "primary_channel": "CA/CS Partnership Channel (Chartered Accountants and Company Secretaries refer clients)",
        "direct_sales": "Direct outreach to CFOs, Company Secretaries, and Compliance Managers",
        "target_audience": "Indian MSMEs and enterprises facing DPDPA 2023 compliance urgency",
    },

    "credentials": {
        "meity": "MeitY GENESIS EIR 2.0 incubatee (Government of India — Ministry of Electronics and IT)",
        "iitg": "IITG TIC incubated (IIT Guwahati Technology Innovation Centre)",
        "linkedin": {
            "api_url": "https://api.linkedin.com/v2",
            "access_token": "<YOUR_LINKEDIN_ACCESS_TOKEN>",
            "company_id": "<COMPANY_LINKEDIN_ID>",
            "founder_ids": ["<RUDRAKSH_LINKEDIN_ID>", "<PRINCE_LINKEDIN_ID>"]
        }
    },

    "key_features": {
        "breach_clock": (
            "72-hour breach notification clock auto-starts the moment a breach is detected. "
            "Compliant with GDPR Article 33. DPO receives immediate alert with next-step guidance."
        ),
        "human_gate": (
            "Human-gate architecture: AI proposes every action, DPO approves before execution. "
            "No autonomous decisions with legal or regulatory consequences. "
            "Every approval recorded in immutable 7-year audit trail."
        ),
        "data_residency": (
            "All data stays in India — hosted on Microsoft Azure India (Central + South) region. "
            "No data leaves Indian jurisdiction. Client infrastructure data never stored on KensaraAI servers."
        ),
        "security_mandate": (
            "All agents operate with AES-256 encryption, TLS 1.3, and strict read-only access. "
            "RAG system is powered by a pgvector database supporting 50+ data privacy regulations."
        ),
        "multi_law": (
            "Single platform natively covers DPDPA (India), GDPR (EU), and ISO 27001 / SOC2."
        ),
    },

    "pricing": {
        "kensarai_range": "₹15L–₹40L per year",
        "kensarai_note": "Fraction of enterprise-tier Western tools",
        "onetrust_benchmark": "₹75L+ per year (OneTrust enterprise tier)",
        "rationale": (
            "India-appropriate pricing. Same AI-native capability at 20–50% of OneTrust cost. "
            "No per-user licensing. Flat annual fee covers all modules."
        ),
    },

    "differentiators": [
        "Only India-native AND AI-native compliance platform — no competitor occupies this quadrant",
        "18-agent autonomous architecture drastically reduces manual RoPA and compliance time",
        "Unique CA/CS partnership distribution model embedding compliance directly into trusted financial advisory",
        "72-hour breach notification clock auto-starts on detection — zero manual trigger",
        "Human-gate architecture — AI proposes, DPO decides — no autonomous legal execution",
        "India-appropriate pricing: ₹15L–₹40L/year vs OneTrust ₹75L+",
        "Data stays in India — Azure India region — no cross-border data transfer",
        "Government credibility: MeitY GENESIS EIR 2.0 + IITG TIC incubation",
    ],

    "competitors": {
        "OneTrust": {
            "description": "Global market leader, US-centric, complex implementation",
            "weakness": "Expensive (₹75L+/year), not India-native, no DPDPA-first design",
            "tone": "factual",
        },
        "TrustArc": {
            "description": "US-based compliance platform",
            "weakness": "Manual-heavy workflows, limited AI automation, not India-native",
            "tone": "factual",
        },
        "Seqrite": {
            "description": "India-based security and compliance vendor (Quick Heal group)",
            "weakness": "Rule-based engine, strong India presence but not AI-native",
            "tone": "factual",
        },
        "Vishwaas AI": {
            "description": "India-based privacy startup, free tier available",
            "weakness": "Free pricing unsustainable for enterprise SLAs, limited enterprise credibility",
            "tone": "factual",
        },
    },

    "content_rules": [
        "Never claim anything not in this facts dict",
        "Never mention AI model providers (OpenAI, Anthropic, etc.)",
        "Never cite fake statistics or made-up case studies",
        "Always end blog posts with CTA to https://www.kensara.in/book-demo",
        "Tone: expert, India-focused, practical, DPO-friendly — not American, not generic",
        "Competitor comparisons: factual only, never disparaging",
    ],
}
# LinkedIn configuration constants
LINKEDIN_API_URL = KENSARAI_FACTS["credentials"]["linkedin"]["api_url"]
LINKEDIN_ACCESS_TOKEN = KENSARAI_FACTS["credentials"]["linkedin"]["access_token"]
LINKEDIN_ORGANIZATION_ID = KENSARAI_FACTS["credentials"]["linkedin"]["company_id"]
LINKEDIN_FOUNDERS = KENSARAI_FACTS["credentials"]["linkedin"]["founder_ids"]
