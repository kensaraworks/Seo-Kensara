"""KensaraAI brand facts — single source of truth for all LLM prompts.

Update this file when facts change. Never hardcode brand claims anywhere else.
"""

KENSARAI_FACTS: dict = {
    "company": {
        "name": "KensaraAI",
        "legal_entity": "Tajmanor LLP",
        "tagline": "India's AI-Native Compliance Platform",
        "website": "https://kensara.in",
        "demo_url": "https://kensara.in/request-demo",
    },

    "what_it_is": (
        "India's first AI-native DPDPA + GDPR + CCPA compliance platform. "
        "12 AI agents autonomously scan client infrastructure (read-only) to detect "
        "privacy gaps, generate findings, and surface actionable compliance reports. "
        "Human-gate architecture ensures AI proposes — DPO decides. "
        "Nothing executes without explicit human approval."
    ),

    "ai_agents": {
        "count": 12,
        "description": (
            "12 AI agents that scan client infrastructure in read-only mode. "
            "Agents detect compliance gaps across databases, cloud storage, SaaS tools, "
            "and identity systems. All agent findings flow into the KensaraAI platform — "
            "never written back to client systems."
        ),
    },

    "modules": {
        "M2": {
            "name": "DSAR Automation",
            "description": (
                "Automates Data Subject Access Requests end-to-end. "
                "30-day compliance clock auto-starts on submission. "
                "AI compiles data report — DPO reviews and approves delivery."
            ),
        },
        "M3": {
            "name": "Consent Management",
            "description": (
                "Consent collection, storage, and audit trail for DPDPA and GDPR. "
                "Granular purpose-based consent. Withdrawal handled automatically. "
                "Full consent lifecycle with immutable audit log."
            ),
        },
        "M5": {
            "name": "GRC + DPIA",
            "description": (
                "Governance, Risk and Compliance module with Data Protection Impact "
                "Assessment automation. AI-assisted DPIA questionnaire. "
                "Gap analysis against DPDPA Schedule I obligations."
            ),
        },
    },

    "credentials": {
        "meity": "MeitY GENESIS EIR 2.0 incubatee (Government of India — Ministry of Electronics and IT)",
        "iitg": "IITG TIC incubated (IIT Guwahati Technology Innovation Centre)",
    },

    "key_features": {
        "breach_clock": (
            "72-hour breach notification clock auto-starts the moment a breach is detected. "
            "Compliant with GDPR Article 33. DPO receives immediate alert with next-step guidance."
        ),
        "human_gate": (
            "Human-gate architecture: AI proposes every action, DPO approves before execution. "
            "No autonomous decisions with legal or regulatory consequences. "
            "Every approval recorded in immutable audit trail."
        ),
        "data_residency": (
            "All data stays in India — hosted on Microsoft Azure India (Central + South) region. "
            "No data leaves Indian jurisdiction. Client infrastructure data never stored on KensaraAI servers."
        ),
        "read_only_agents": (
            "All 12 AI agents operate in strict read-only mode on client infrastructure. "
            "SELECT queries only. No INSERT, UPDATE, or DELETE on client systems."
        ),
        "multi_law": (
            "Single platform covers DPDPA (India), GDPR (EU/UK), and CCPA (California). "
            "Not bolted-on adapters — each law's obligations are natively modelled."
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
        "GDPR + DPDPA + CCPA in one platform — not bolted-on modules",
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
        "Always end blog posts with CTA to https://kensara.in/request-demo",
        "Tone: expert, India-focused, practical, DPO-friendly — not American, not generic",
        "Competitor comparisons: factual only, never disparaging",
    ],
}
