"""Context builder — assembles full LLM-injection string from KensaraAI facts + stats.

Usage:
    from src.context.builder import build_context
    ctx = build_context(keyword="DPDPA compliance software", news_angle="ICO fines X for breach")
    # Inject ctx into any prompt as the authoritative brand/product context block.
"""

import structlog

from src.context.kensarai_facts import KENSARAI_FACTS
from src.context.platform_stats import PLATFORM_STATS

log = structlog.get_logger()


def build_context(keyword: str = "", news_angle: str = "") -> str:
    """Assemble full context string for LLM injection.

    Args:
        keyword: Primary SEO keyword being targeted (e.g. "DPDPA compliance software").
        news_angle: News hook to weave into the content (e.g. "ICO fines company ₹X").

    Returns:
        Structured string ready for injection into any LLM prompt.
    """
    facts = KENSARAI_FACTS
    stats = PLATFORM_STATS

    # --- Company identity ---
    company_block = (
        f"COMPANY: {facts['company']['name']} ({facts['company']['legal_entity']})\n"
        f"TAGLINE: {facts['company']['tagline']}\n"
        f"WEBSITE: {facts['company']['website']}\n"
        f"DEMO URL: {facts['company']['demo_url']}\n"
    )

    # --- What it is ---
    what_block = f"WHAT IT IS:\n{facts['what_it_is']}\n"

    # --- Credentials ---
    creds = facts["credentials"]
    creds_block = (
        f"CREDENTIALS:\n"
        f"- {creds['meity']}\n"
        f"- {creds['iitg']}\n"
    )

    # --- Modules ---
    modules_lines = ["MODULES:"]
    for code, mod in facts["modules"].items():
        modules_lines.append(f"- {code} {mod['name']}: {mod['description']}")
    modules_block = "\n".join(modules_lines) + "\n"

    # --- Key features ---
    features = facts["key_features"]
    features_lines = ["KEY FEATURES:"]
    for name, desc in features.items():
        features_lines.append(f"- {name.upper()}: {desc}")
    features_block = "\n".join(features_lines) + "\n"

    # --- Differentiators ---
    diff_lines = ["DIFFERENTIATORS (use these — verified facts only):"]
    for d in facts["differentiators"]:
        diff_lines.append(f"- {d}")
    diff_block = "\n".join(diff_lines) + "\n"

    # --- Pricing ---
    pricing = facts["pricing"]
    pricing_block = (
        f"PRICING:\n"
        f"- KensaraAI: {pricing['kensarai_range']} ({pricing['kensarai_note']})\n"
        f"- OneTrust benchmark: {pricing['onetrust_benchmark']}\n"
        f"- {pricing['rationale']}\n"
    )

    # --- Competitors ---
    comp_lines = ["COMPETITORS (factual comparisons only — never disparage):"]
    for name, comp in facts["competitors"].items():
        comp_lines.append(
            f"- {name}: {comp['description']}. Weakness: {comp['weakness']}"
        )
    comp_block = "\n".join(comp_lines) + "\n"

    # --- Platform stats (only include if non-zero) ---
    stats_lines = []
    if stats["dsars_processed"] > 0:
        stats_lines.append(f"- DSARs processed: {stats['dsars_processed']:,}")
    if stats["consents_recorded"] > 0:
        stats_lines.append(f"- Consents recorded: {stats['consents_recorded']:,}")
    if stats["breach_clocks_started"] > 0:
        stats_lines.append(f"- Breach clocks started: {stats['breach_clocks_started']:,}")
    if stats["clients_onboarded"] > 0:
        stats_lines.append(f"- Clients onboarded: {stats['clients_onboarded']:,}")

    if stats_lines:
        stats_block = "PLATFORM TRACTION (real numbers — use in content):\n" + "\n".join(stats_lines) + "\n"
    else:
        stats_block = ""

    # --- Content rules ---
    rules_lines = ["CONTENT RULES (mandatory — do not violate):"]
    for rule in facts["content_rules"]:
        rules_lines.append(f"- {rule}")
    rules_block = "\n".join(rules_lines) + "\n"

    # --- Keyword + news angle (session-specific) ---
    session_lines = []
    if keyword:
        session_lines.append(f"TARGET KEYWORD: {keyword}")
    if news_angle:
        session_lines.append(f"NEWS ANGLE: {news_angle}")
    session_block = ("\n".join(session_lines) + "\n") if session_lines else ""

    # --- Assemble ---
    sections = [
        "=== KENSARAI BRAND CONTEXT (authoritative — use only these facts) ===",
        company_block,
        what_block,
        creds_block,
        modules_block,
        features_block,
        diff_block,
        pricing_block,
        comp_block,
    ]
    if stats_block:
        sections.append(stats_block)
    sections.append(rules_block)
    if session_block:
        sections.append(session_block)
    sections.append("=== END KENSARAI BRAND CONTEXT ===")

    result = "\n".join(sections)

    log.debug(
        "context_built",
        keyword=keyword,
        has_news_angle=bool(news_angle),
        has_stats=bool(stats_block),
        char_count=len(result),
    )

    return result
