"""Tests for Module 2.8 — India Localization Engine.

Covers:
  2.8.A Level 1: Monetary framing (₹ first, USD in parentheses)
  2.8.A Level 2: Regulatory body specificity (vague reference detection)
  2.8.A Level 3: Business scale references (Indian MSME/startup terminology)
  2.8.A Level 4: Industry sector data library (fintech, healthtech, edtech, etc.)
  2.8.A Level 5: Compliance calendar grounding with hedged language

  2.8.B Style Guide: apply_india_style() spelling enforcement
                     audit_india_style() violation detection
                     practise/practice and licence/license context-sensitivity
"""

import pytest
from src.engines.india_localization import (
    format_monetary_figure,
    enforce_monetary_framing,
    validate_regulatory_specificity,
    get_correct_regulator,
    inject_business_scale_context,
    get_business_scale_context_snippet,
    get_sector_context,
    get_sector_statistic,
    get_hedged_deadline_phrase,
    get_compliance_calendar_context,
    build_india_context_block,
)
from src.context.india_style_guide import (
    apply_india_style,
    audit_india_style,
)


# ---------------------------------------------------------------------------
# Level 1: Monetary Framing
# ---------------------------------------------------------------------------

class TestMonetaryFraming:
    def test_format_monetary_figure_with_usd(self):
        result = format_monetary_figure(250)
        assert "₹250 crore" in result
        assert "million USD" in result
        assert "approximately" in result

    def test_format_monetary_figure_without_usd(self):
        result = format_monetary_figure(50, include_usd=False)
        assert result == "₹50 crore"
        assert "USD" not in result

    def test_enforce_bare_usd_converted_to_inr_first(self):
        text = "The maximum penalty is $30 million."
        result = enforce_monetary_framing(text)
        assert "₹" in result
        assert result.index("₹") < result.index("$30")

    def test_enforce_inr_without_usd_gets_parenthetical(self):
        text = "Penalties under DPDPA can reach ₹250 crore for significant violations."
        result = enforce_monetary_framing(text)
        assert "(approximately $" in result
        assert "million USD)" in result

    def test_enforce_inr_already_with_usd_untouched(self):
        text = "Penalties of ₹250 crore (approximately $29.8 million USD) apply."
        result = enforce_monetary_framing(text)
        # Should NOT double-add the parenthetical
        assert result.count("million USD") == 1


# ---------------------------------------------------------------------------
# Level 2: Regulatory Specificity
# ---------------------------------------------------------------------------

class TestRegulatorySpecificity:
    def test_vague_regulator_flagged(self):
        text = "The regulator has notified companies that personal data must be protected."
        issues = validate_regulatory_specificity(text)
        assert len(issues) >= 1
        assert any("Vague regulator" in i for i in issues)

    def test_specific_regulator_not_flagged(self):
        text = "The Data Protection Board of India (DPBI) has notified companies."
        issues = validate_regulatory_specificity(text)
        assert len(issues) == 0

    def test_correct_regulator_for_banking(self):
        body = get_correct_regulator("banking and payment data")
        assert "RBI" in body

    def test_correct_regulator_for_insurance(self):
        body = get_correct_regulator("insurance company data")
        assert "IRDAI" in body

    def test_correct_regulator_for_cybersecurity(self):
        body = get_correct_regulator("cybersecurity breach")
        assert "CERT-In" in body

    def test_correct_regulator_default_to_dpbi(self):
        body = get_correct_regulator("unknown domain")
        assert "DPBI" in body


# ---------------------------------------------------------------------------
# Level 3: Business Scale
# ---------------------------------------------------------------------------

class TestBusinessScale:
    def test_sme_replaced_with_indian_msme(self):
        text = "SMEs must comply with the DPDPA."
        result = inject_business_scale_context(text)
        assert "Indian MSMEs" in result

    def test_startup_gets_dpiit_context(self):
        text = "startups in India are struggling with DPDPA compliance."
        result = inject_business_scale_context(text)
        assert "Indian startups" in result

    def test_context_snippet_for_msme(self):
        snippet = get_business_scale_context_snippet("MSME")
        assert snippet is not None
        assert "63 million" in snippet

    def test_context_snippet_for_startup(self):
        snippet = get_business_scale_context_snippet("startup")
        assert snippet is not None
        assert "DPIIT" in snippet

    def test_context_snippet_for_unknown_returns_none(self):
        snippet = get_business_scale_context_snippet("cryptocurrency exchange")
        assert snippet is None


# ---------------------------------------------------------------------------
# Level 4: Industry Sector Data
# ---------------------------------------------------------------------------

class TestSectorContextLibrary:
    def test_fintech_sector_context(self):
        ctx = get_sector_context("fintech")
        assert ctx is not None
        assert "UPI" in ctx["statistic"]
        assert "RBI" in ctx["regulator"]
        assert ctx["risk_level"] == "HIGH"

    def test_healthtech_sector_context(self):
        ctx = get_sector_context("healthtech")
        assert "ABDM" in ctx["statistic"]
        assert "Section 9" in ctx["dpdpa_section"]

    def test_edtech_sector_context(self):
        ctx = get_sector_context("edtech")
        assert "parental consent" in ctx["statistic"]

    def test_ecommerce_sector_context(self):
        ctx = get_sector_context("ecommerce")
        assert "400 million" in ctx["statistic"]

    def test_banking_sector_context(self):
        ctx = get_sector_context("banking")
        assert "RBI" in ctx["regulator"]

    def test_sector_statistic_shortcut(self):
        stat = get_sector_statistic("saas")
        assert stat is not None
        assert "GDPR" in stat

    def test_unknown_sector_returns_none(self):
        ctx = get_sector_context("agriculture")
        assert ctx is None


# ---------------------------------------------------------------------------
# Level 5: Compliance Calendar
# ---------------------------------------------------------------------------

class TestComplianceCalendar:
    def test_calendar_returns_full_list_if_no_keyword(self):
        calendar = get_compliance_calendar_context()
        assert len(calendar) >= 4

    def test_hedged_phrase_for_consent_manager(self):
        phrase = get_hedged_deadline_phrase("Consent Manager")
        assert phrase is not None
        assert "expected" in phrase.lower() or "projected" in phrase.lower()
        assert "Indian businesses" in phrase

    def test_hedged_phrase_for_sdf_classification(self):
        phrase = get_hedged_deadline_phrase("Significant Data Fiduciary")
        assert phrase is not None
        assert "expected" in phrase.lower() or "projected" in phrase.lower()

    def test_hedged_phrase_for_unknown_milestone_returns_none(self):
        phrase = get_hedged_deadline_phrase("something completely unknown")
        assert phrase is None

    def test_all_projected_milestones_have_hedge_language(self):
        calendar = get_compliance_calendar_context()
        for item in calendar:
            if item["status"] == "PROJECTED":
                assert item["hedge"] in ["expected", "projected for", "expected to come into effect by"], \
                    f"Milestone '{item['milestone']}' has non-approved hedge: '{item['hedge']}'"


# ---------------------------------------------------------------------------
# Master Context Builder
# ---------------------------------------------------------------------------

class TestBuildIndiaContextBlock:
    def test_full_context_block_fintech(self):
        ctx = build_india_context_block(sector="fintech", business_type="MSME")
        assert ctx["level_1_monetary_note"] is not None
        assert ctx["level_4_sector_data"]["regulator"] == "Reserve Bank of India (RBI)"
        assert "63 million" in ctx["level_3_business_scale"]
        assert len(ctx["level_5_calendar"]) >= 4

    def test_context_block_with_deadline(self):
        ctx = build_india_context_block(milestone_keyword="Consent Manager")
        assert "level_5_deadline_phrase" in ctx
        assert "expected" in ctx["level_5_deadline_phrase"].lower()


# ---------------------------------------------------------------------------
# 2.8.B Style Guide
# ---------------------------------------------------------------------------

class TestIndiaStyleGuide:
    def test_organization_to_organisation(self):
        result = apply_india_style("The organization must comply.")
        assert "organisation" in result
        assert "organization" not in result

    def test_authorized_to_authorised(self):
        result = apply_india_style("This is an authorized agent.")
        assert "authorised" in result

    def test_behavior_to_behaviour(self):
        result = apply_india_style("Organizational behavior is key.")
        assert "behaviour" in result

    def test_capitalize_preserved(self):
        result = apply_india_style("Organization must comply.")
        assert "Organisation" in result

    def test_leverage_verb_banned(self):
        result = apply_india_style("Companies can leverage this technology.")
        assert "leverage" not in result.lower()

    def test_audit_style_detects_violations(self):
        text = "The organization must authorize the data processor."
        violations = audit_india_style(text)
        assert len(violations) >= 2

    def test_practise_verb_context(self):
        # "practice to" (verb usage) should become "practise to"
        result = apply_india_style("Companies should practice to comply with DPDPA.")
        assert "practise" in result

    def test_practice_noun_unchanged(self):
        # "practice is" (noun usage) should stay as "practice"
        result = apply_india_style("The practice is well established.")
        # Noun usage — should remain "practice"
        assert "practice" in result

    def test_license_noun_becomes_licence(self):
        # "license agreement" (noun) should become "licence agreement"
        result = apply_india_style("The license agreement must be reviewed.")
        assert "licence agreement" in result

    def test_math_to_maths(self):
        result = apply_india_style("The math is straightforward.")
        assert "maths" in result

    def test_gotten_to_got(self):
        result = apply_india_style("They have gotten approval.")
        assert "got" in result

    def test_centre_replaces_center(self):
        result = apply_india_style("The data center is located in Mumbai.")
        assert "centre" in result
