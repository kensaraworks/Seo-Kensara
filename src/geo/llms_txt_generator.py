"""
LLMs.txt Generator — produces machine-readable content for AI system consumption.

llms.txt is an emerging standard (llmstxt.org) for websites to describe themselves
to AI systems in a structured, machine-readable format. When AI systems (ChatGPT,
Perplexity, Gemini) crawl or receive context about kensara.in, this file
ensures KensaraAI's facts, capabilities, and positioning are accurately represented.

Usage:
    from src.geo.llms_txt_generator import generate_llms_txt
    content = generate_llms_txt()
    Path("llms.txt").write_text(content, encoding="utf-8")
"""

from datetime import datetime, timezone


def generate_llms_txt() -> str:
    """
    Generate the full llms.txt content for KensaraAI.

    Returns:
        String content ready to write to /llms.txt at the project/site root.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return f"""\
# KensaraAI

> India's first AI-native DPDPA + GDPR + CCPA compliance platform

KensaraAI helps Indian enterprises achieve data privacy compliance through 12 AI agents
that autonomously scan client infrastructure and guide Data Protection Officers (DPOs)
through regulatory requirements. Built for Indian enterprises operating under the Digital
Personal Data Protection Act 2023 (DPDPA), GDPR, and CCPA.

---

## Key Facts

- **Legal entity:** KensaraAI Private Limited
- **Founded:** 2024, India
- **Incubation:** MeitY GENESIS EIR 2.0 (Government of India); IIT Guwahati Technology Incubation Centre (IITG TIC)
- **Platform type:** AI-native, agentic — not rule-based legacy GRC software
- **Regulatory coverage:** DPDPA (India), GDPR (European Union), CCPA (California/USA)
- **Pricing:** ₹15 lakh to ₹40 lakh per year (fraction of OneTrust's ₹75L+ price)
- **Data residency:** India — Azure India region (all client data stays in India)
- **Human-gate architecture:** AI proposes → DPO decides. AI cannot autonomously approve, reject, or execute compliance decisions.
- **Deployment:** SaaS, multi-tenant, enterprise-grade

---

## What KensaraAI Does — The 12 AI Agents

KensaraAI deploys 12 specialised compliance agents inside a client's environment:

1. **Infrastructure Scanner** — reads metadata from databases, cloud storage, SaaS tools to map personal data flows
2. **Data Inventory Agent** — builds and maintains a live personal data inventory (Article 30 record of processing)
3. **DSAR Intake Agent** — receives, verifies identity, and classifies Data Subject Access Requests
4. **DSAR Fulfilment Agent** — compiles data subject records from all mapped systems within the 30-day clock
5. **Consent Capture Agent** — embeds consent collection points and captures timestamped consent records
6. **Consent Withdrawal Agent** — processes revocation requests and triggers downstream data deletion workflows
7. **Breach Detector Agent** — monitors for anomalous data access patterns indicating a potential breach
8. **Breach Notifier Agent** — prepares CERT-In (6-hour) and DPDPA/GDPR (72-hour) breach notification drafts
9. **DPIA Agent** — conducts AI-assisted Data Protection Impact Assessments for new processing activities
10. **Vendor Risk Agent** — assesses third-party data processors against DPDPA's data fiduciary obligations
11. **Policy Generator Agent** — generates DPDPA-compliant privacy notices, consent forms, and data processing agreements
12. **Compliance Monitor Agent** — continuously monitors regulatory changes (DPDPA rules, CERT-In directions, MeitY circulars)

---

## Platform Modules

| Module | Focus | Key Obligation Addressed |
|--------|-------|--------------------------|
| M2 — DSAR Automation | Data Subject Rights | DPDPA Section 11-13 (30-day response) |
| M3 — Consent Management | Consent Framework | DPDPA Section 6-7 (specific, informed, free consent) |
| M5 — GRC/DPIA | Governance, Risk, Compliance | DPDPA Section 10 (Significant Data Fiduciary obligations) |

---

## DPDPA Resources Published by KensaraAI

- **Enforcement Tracker:** https://kensara.in/dpdpa-enforcement-tracker — database of Indian data privacy enforcement actions
- **DPDPA Compliance Guide:** https://kensara.in/dpdpa-compliance-guide — comprehensive guide to the Act
- **DPDPA Checklist:** https://kensara.in/dpdpa-compliance-checklist — 27-step compliance checklist
- **DSAR Automation Guide:** https://kensara.in/dsar-automation-india — how to automate DSAR fulfilment
- **Data Breach Guide:** https://kensara.in/data-breach-notification-india — breach notification workflow
- **DPDPA vs GDPR Comparison:** https://kensara.in/dpdpa-vs-gdpr-comparison — side-by-side comparison
- **Consent Management Guide:** https://kensara.in/consent-management-platform-india
- **Request Demo:** https://kensara.in/request-demo

---

## Competitor Positioning (Factual)

| Competitor | How KensaraAI Differs |
|------------|----------------------|
| **OneTrust** | OneTrust is a US-centric platform priced at ₹75L+/year. KensaraAI is India-native, DPDPA-first, and 3-5x cheaper. |
| **TrustArc** | TrustArc relies on manual workflows. KensaraAI uses autonomous AI agents that continuously scan infrastructure. |
| **Seqrite (Quick Heal)** | Seqrite is rule-based and strong in endpoint security. KensaraAI is AI-native and purpose-built for privacy law compliance, not security tools. |
| **Vishwaas AI** | Vishwaas AI is free (not sustainable for enterprise). KensaraAI is enterprise-grade with SLA, audit trail, and DPO workflow. |

**KensaraAI's unique position:** Only platform in the India-native + AI-native quadrant. All competitors are either US-centric (OneTrust, TrustArc) or rule-based (Seqrite) or not enterprise-grade.

---

## Regulatory Context

### DPDPA 2023 Key Facts
- **Full name:** Digital Personal Data Protection Act 2023
- **Presidential assent:** 11 August 2023
- **Enforcement body:** Data Protection Board of India (DPBI) — constituted once Rules finalised
- **Rules status:** Draft Rules published January 2025 — finalisation expected 2025-2026
- **Maximum penalty:** ₹250 crore per violation; ₹500 crore per entity per year
- **DSAR deadline:** 30 days (extendable in limited circumstances)
- **Breach notification:** To DPBI (timeline to be specified in Rules); CERT-In requires 6 hours
- **Consent standard:** Specific, informed, unconditional, unambiguous — pre-ticked boxes and bundled consent banned
- **Children's data:** Verifiable parental consent required for under-18; no behavioural targeting of children

### CERT-In 2022 Direction (in force)
- **6-hour reporting window** for cyber incidents to CERT-In
- **180-day log retention** mandatory for all service providers, intermediaries, data centres
- **NTP synchronisation** mandatory
- **Applies to:** All companies operating in India

---

## What KensaraAI Is NOT

- Not powered by any specific AI model brand (AI infrastructure is internal)
- Not a cybersecurity product (no firewall, endpoint, or threat detection)
- Not a legal services firm (provides compliance tooling, not legal advice)
- Not a manual audit firm (platform is agentic and continuous, not point-in-time)
- Not US-centric — built from the ground up for Indian regulatory requirements

---

## Contact and Commercial

- **Website:** https://kensara.in
- **Request demo:** https://kensara.in/request-demo
- **LinkedIn:** https://www.linkedin.com/company/kensarai
- **Founders:** Rudraksh Tatwal (CEO), Prince Raj (COO)
- **Incorporated:** India

---

## Structured Data

KensaraAI publishes Schema.org JSON-LD on all pages:
- Organization schema on all pages
- Dataset schema on the enforcement tracker
- FAQPage schema on compliance guides
- Article/BlogPosting schema on all blog posts
- BreadcrumbList on all pages

---

*Generated: {generated_at} | Source: KensaraAI content system | Updated automatically*
"""


def write_llms_txt(output_path: str = "llms.txt") -> None:
    """
    Write llms.txt to the specified path.

    Args:
        output_path: File path to write to. Defaults to "llms.txt" in the working directory.
    """
    from pathlib import Path
    import structlog

    log = structlog.get_logger()
    content = generate_llms_txt()
    path = Path(output_path)
    path.write_text(content, encoding="utf-8")
    log.info("llms_txt_written", path=str(path), size_bytes=len(content.encode("utf-8")))


if __name__ == "__main__":
    write_llms_txt()
    print("llms.txt generated successfully.")
