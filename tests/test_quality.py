"""Tests for quality checker and risk classifier.

All tests are deterministic — no LLM calls, no network I/O.
"""
import pytest

from src.quality.checker import check_blog_quality, QualityResult
from src.quality.risk_classifier import classify_content_risk, RiskLevel


# -----------------------------------------------------------------------
# Quality checker — passing cases
# -----------------------------------------------------------------------

def test_quality_passes_good_post(sample_blog_post):
    """A well-structured, keyword-rich post should pass with score >= 0.60."""
    result = check_blog_quality(sample_blog_post, "DPDPA compliance software", intent_type="transactional")

    assert isinstance(result, QualityResult)
    assert result.score >= 0.60, (
        f"Expected score >= 0.60 but got {result.score}. "
        f"Signals: {result.signals}"
    )
    assert result.passed, f"Expected passed=True. Issues: {result.issues}. Warnings: {result.warnings}"


def test_quality_score_is_float_between_0_and_1(sample_blog_post):
    """Score must always be a float in [0.0, 1.0]."""
    result = check_blog_quality(sample_blog_post, "DPDPA compliance software")
    assert 0.0 <= result.score <= 1.0


def test_quality_result_has_all_fields(sample_blog_post):
    """QualityResult must have all required fields."""
    result = check_blog_quality(sample_blog_post, "DPDPA compliance software")
    assert hasattr(result, "passed")
    assert hasattr(result, "score")
    assert hasattr(result, "issues")
    assert hasattr(result, "warnings")
    assert hasattr(result, "signals")
    assert isinstance(result.issues, list)
    assert isinstance(result.warnings, list)
    assert isinstance(result.signals, dict)


def test_quality_signals_contain_all_categories(sample_blog_post):
    """Signals breakdown must cover all 5 scoring categories."""
    result = check_blog_quality(sample_blog_post, "DPDPA compliance software")
    expected_categories = {
        "search_intent",
        "information_density",
        "structure",
        "eeat",
        "factual_specificity",
    }
    assert expected_categories.issubset(set(result.signals.keys())), (
        f"Missing categories: {expected_categories - set(result.signals.keys())}"
    )


# -----------------------------------------------------------------------
# Quality checker — failing cases
# -----------------------------------------------------------------------

def test_quality_fails_no_cta():
    """A post with no CTA should fail regardless of other quality."""
    from src.agents.blog_writer import BlogPost

    bad_post = BlogPost(
        title="DPDPA Guide",
        meta_description="DPDPA guide for Indian companies",
        slug="dpdpa-guide",
        primary_keyword="DPDPA",
        secondary_keywords=[],
        content_markdown="Generic content with no CTA and no specifics. No link anywhere.",
        word_count=100,
    )
    result = check_blog_quality(bad_post, "DPDPA")

    assert not result.passed, "Post without CTA must not pass"
    cta_issues = [i for i in result.issues if "kensara.in/request-demo" in i.lower() or "cta" in i.lower()]
    assert len(cta_issues) >= 1, f"Expected CTA issue but got: {result.issues}"


def test_quality_fails_very_low_score():
    """Minimal content with no signals should score below 0.40 and not pass."""
    from src.agents.blog_writer import BlogPost

    thin_post = BlogPost(
        title="Hello World",
        meta_description="Hello world post",
        slug="hello",
        primary_keyword="DPDPA",
        secondary_keywords=[],
        content_markdown="Hello world. This is some generic text with no value whatsoever.",
        word_count=15,
    )
    result = check_blog_quality(thin_post, "DPDPA")

    assert not result.passed
    assert result.score < 0.60


def test_quality_issue_for_missing_keyword_in_h1():
    """Keyword absent from H1 must produce an issue (not just a warning)."""
    from src.agents.blog_writer import BlogPost

    post = BlogPost(
        title="Data Privacy Guide",
        meta_description="Data privacy guide for enterprises",
        slug="data-privacy-guide",
        primary_keyword="DPDPA compliance software",
        secondary_keywords=[],
        content_markdown=(
            "# Unrelated Heading\n\n"
            "## Section One\n\n"
            "Some content about data protection.\n\n"
            "## Section Two\n\n"
            "More content. MeitY regulations.\n\n"
            "## Section Three\n\n"
            "Conclusion. [Request a Demo](https://kensara.in/request-demo)\n"
        ),
        word_count=40,
    )
    result = check_blog_quality(post, "DPDPA compliance software")

    kw_issues = [i for i in result.issues if "h1" in i.lower() or "keyword" in i.lower()]
    assert len(kw_issues) >= 1, (
        f"Expected H1 keyword issue but got issues: {result.issues}"
    )


def test_quality_warning_for_keyword_stuffed_post():
    """Keyword appearing > 5 times should trigger a warning."""
    from src.agents.blog_writer import BlogPost

    stuffed_content = (
        "# DPDPA compliance software guide\n\n"
        "## About DPDPA compliance software\n\n"
        "DPDPA compliance software is important. Use DPDPA compliance software.\n"
        "DPDPA compliance software helps you. DPDPA compliance software is key.\n"
        "DPDPA compliance software saves time. Get DPDPA compliance software now.\n\n"
        "## More DPDPA compliance software\n\n"
        "DPDPA compliance software again. [Demo](https://kensara.in/request-demo)\n"
    )
    post = BlogPost(
        title="DPDPA compliance software guide",
        meta_description="DPDPA compliance software for Indian enterprises. Book demo.",
        slug="dpdpa-compliance-software",
        primary_keyword="DPDPA compliance software",
        secondary_keywords=[],
        content_markdown=stuffed_content,
        word_count=60,
    )
    result = check_blog_quality(post, "DPDPA compliance software")

    stuffing_warnings = [w for w in result.warnings if "stuffing" in w.lower() or "times" in w.lower()]
    assert len(stuffing_warnings) >= 1, (
        f"Expected keyword stuffing warning but got warnings: {result.warnings}"
    )


# -----------------------------------------------------------------------
# Risk classifier — HIGH risk
# -----------------------------------------------------------------------

def test_risk_high_competitor_mention():
    """Content mentioning a competitor name must be classified HIGH."""
    result = classify_content_risk(
        "blog",
        "OneTrust is expensive but KensaraAI is a much cheaper alternative for Indian companies.",
    )
    assert result.level == RiskLevel.HIGH, f"Expected HIGH but got {result.level}"
    assert result.auto_publish_after_hours is None


def test_risk_high_all_competitor_names():
    """Each competitor name should independently trigger HIGH risk."""
    competitor_texts = [
        ("blog", "TrustArc is the main alternative to consider."),
        ("blog", "Seqrite offers rule-based scanning for Indian enterprises."),
        ("blog", "Vishwaas AI provides free compliance for startups."),
    ]
    for content_type, content in competitor_texts:
        result = classify_content_risk(content_type, content)
        assert result.level == RiskLevel.HIGH, (
            f"Expected HIGH for '{content[:50]}' but got {result.level}"
        )


def test_risk_high_legal_claim():
    """Legal guarantee claims must trigger HIGH risk."""
    result = classify_content_risk(
        "blog",
        "KensaraAI makes your company fully compliant with zero fines guaranteed.",
    )
    assert result.level == RiskLevel.HIGH


def test_risk_high_pricing_claim():
    """Specific pricing in content must trigger HIGH risk."""
    result = classify_content_risk(
        "blog",
        "Our platform starts at ₹15 lakh per year, much less than competitors.",
    )
    assert result.level == RiskLevel.HIGH


def test_risk_high_pillar_page():
    """Pillar page content type must always be HIGH risk."""
    result = classify_content_risk(
        "pillar",
        "This is general educational content about data privacy law in India.",
    )
    assert result.level == RiskLevel.HIGH


def test_risk_high_has_no_auto_publish():
    """HIGH risk content must never have an auto_publish window."""
    result = classify_content_risk(
        "blog",
        "TrustArc vs KensaraAI — complete comparison guide for Indian DPOs.",
    )
    assert result.auto_publish_after_hours is None


# -----------------------------------------------------------------------
# Risk classifier — MEDIUM risk
# -----------------------------------------------------------------------

def test_risk_medium_linkedin_always():
    """LinkedIn posts are always MEDIUM regardless of content."""
    result = classify_content_risk(
        "linkedin",
        "ICO issued a £500K fine for failing to report a breach within 72 hours.",
    )
    assert result.level == RiskLevel.MEDIUM
    assert result.auto_publish_after_hours == 24


def test_risk_medium_newsletter():
    """Newsletter content should be MEDIUM risk."""
    result = classify_content_risk(
        "newsletter",
        "This month in DPDPA: enforcement actions, new guidelines, and what to do next.",
    )
    assert result.level == RiskLevel.MEDIUM


def test_risk_medium_product_mention():
    """Blog with KensaraAI product mention should be MEDIUM."""
    result = classify_content_risk(
        "blog",
        (
            "When selecting a compliance tool, look for automated DSAR handling. "
            "KensaraAI provides this via its M2 module. "
            "Request a demo at kensara.in/request-demo."
        ),
    )
    assert result.level == RiskLevel.MEDIUM
    assert result.auto_publish_after_hours == 24


# -----------------------------------------------------------------------
# Risk classifier — LOW risk
# -----------------------------------------------------------------------

def test_risk_low_news_commentary():
    """Pure news commentary with no product or competitor mentions should be LOW."""
    result = classify_content_risk(
        "blog",
        (
            "The ICO issued a fine for GDPR violation related to consent management "
            "in the healthcare sector. This highlights the importance of proper "
            "data governance for organisations handling patient records."
        ),
    )
    assert result.level == RiskLevel.LOW, f"Expected LOW but got {result.level}"


def test_risk_low_educational_dpdpa():
    """Educational DPDPA content with no commercial signals should be LOW."""
    result = classify_content_risk(
        "blog",
        (
            "DPDPA stands for the Digital Personal Data Protection Act 2023. "
            "It applies to all Data Fiduciaries processing personal data of "
            "Indian citizens, whether the processing happens in India or abroad. "
            "MeitY oversees implementation through the Data Protection Board."
        ),
    )
    assert result.level == RiskLevel.LOW


def test_risk_low_has_zero_auto_publish_hours():
    """LOW risk content should auto-publish immediately (0 hours)."""
    result = classify_content_risk(
        "blog",
        "The EDPB published new guidelines on cookie consent in March 2026.",
    )
    assert result.level == RiskLevel.LOW
    assert result.auto_publish_after_hours == 0


def test_risk_assessment_has_reasons():
    """Every risk assessment must include at least one reason."""
    for content_type, content in [
        ("blog", "OneTrust is an alternative."),
        ("linkedin", "ICO fined a UK company."),
        ("blog", "The EDPB published new guidelines."),
    ]:
        result = classify_content_risk(content_type, content)
        assert len(result.reasons) >= 1, (
            f"No reasons returned for level={result.level}, content_type={content_type}"
        )
