import pytest
from src.agents.blog_writer import BlogPost
from src.quality.checker import (
    check_blog_quality,
    flesch_kincaid_grade,
    get_tf_idf_similarity,
    count_syllables
)

def test_count_syllables():
    assert count_syllables("compliance") >= 2
    assert count_syllables("the") == 1
    assert count_syllables("organization") >= 4

def test_flesch_kincaid_grade():
    text = "The quick brown fox jumps over the lazy dog. It was a good day."
    fk, avg_len = flesch_kincaid_grade(text)
    assert avg_len == 7.0
    assert isinstance(fk, float)

def test_tf_idf_similarity():
    text1 = "digital personal data protection act compliance"
    text2 = "digital personal data protection act guide"
    sim = get_tf_idf_similarity(text1, text2)
    assert 0.7 < sim < 1.0  # highly similar
    
    text3 = "completely unrelated text about fishing"
    sim_low = get_tf_idf_similarity(text1, text3)
    assert sim_low == 0.0

def create_mock_post(content: str, title: str = "Test Post") -> BlogPost:
    return BlogPost(
        title=title,
        slug="test-post",
        meta_description="A test post about DPDPA.",
        schema_markup={},
        content_markdown=content,
        word_count=len(content.split()),
        tier=1,
        cluster_id="dpdpa-core",
        intent_type="informational",
        primary_keyword="compliance"
    )

def test_check_blog_quality_perfect_post():
    content = """# Comprehensive Guide to DPDPA Compliance
    
    **quick answer:** The Digital Personal Data Protection Act requires companies to obtain verifiable consent. This applies to all entities.
    
    ## What is the DPDPA?
    It is the new privacy law in India. As per Section 8 and Section 11, obligations are strict.
    The penalty of non-compliance can reach ₹250 crore. MeitY and DPBI enforce this.
    
    ## How does it affect companies?
    Reliance and Tata must comply. According to Harjinder Singh, CIPP/E, founder of Kensara, it is critical.
    [External Source 1](https://example.com) and [External Source 2](https://example2.com).
    See the [official site](https://dpboard.gov.in).
    
    ## Frequently Asked Questions
    ### What is consent?
    Consent must be free and specific.
    ### Who is a Data Fiduciary?
    Any organisation deciding purpose of processing.
    ### What are the fines?
    Fines go up to ₹250 crore.
    
    ## Next Steps?
    Visit kensara.in to learn more.
    Last updated: May 2027.
    """
    
    post = create_mock_post(content, title="Comprehensive Guide to DPDPA Compliance")
    res = check_blog_quality(post, keyword="DPDPA compliance")
    
    assert res.score >= 0.70
    assert res.status == "QUALITY"
    assert res.passed is True
    assert not any("generic filler" in msg.lower() for msg in res.issues)

def test_check_blog_quality_rejected_post():
    content = """# Stuff
    In today's digital world, it is no secret that data is important.
    Needless to say, OneTrust is bad.
    100% compliant guarantee here.
    """
    
    post = create_mock_post(content, title="Stuff")
    res = check_blog_quality(post, keyword="DPDPA compliance")
    
    assert res.status == "REJECTED"
    assert res.passed is False
    assert any("filler" in issue.lower() for issue in res.issues)
    assert any("competitor framing" in issue.lower() for issue in res.issues)
    assert any("over-claim" in w.lower() for w in res.warnings)

def test_legal_accuracy_flags():
    content = "You must comply. Non-compliance will result in fines."
    post = create_mock_post(content)
    res = check_blog_quality(post, keyword="compliance")
    assert any("LEGAL REVIEW REQUIRED" in w for w in res.warnings)

def test_unverified_dates():
    content = "The law will be fully enforced by June 2026."
    post = create_mock_post(content)
    res = check_blog_quality(post, keyword="compliance")
    assert any("UNVERIFIED DATE" in w for w in res.warnings)

def test_uniqueness_rejection():
    content = "This is the exact same text as the published one."
    post = create_mock_post(content)
    res = check_blog_quality(post, keyword="compliance", existing_published_texts=[content])
    assert any("Near-duplicate" in i for i in res.issues)
    assert res.status == "REJECTED"
