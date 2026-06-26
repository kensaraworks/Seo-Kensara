"""Context builder — assembles structured Keyword Brief for LLM-injection.

Usage:
    from src.context.builder import assemble_keyword_brief
    brief = assemble_keyword_brief(keyword="DPDPA compliance software", intent_type="commercial")
"""

import json
import structlog
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from src.context.kensarai_facts import KENSARAI_FACTS
from src.context.platform_stats import get_platform_stats
from src.engines.serp_formatter import analyze_serp_format, get_target_word_count

log = structlog.get_logger()

# --- Pydantic Models for Keyword Brief (Module 2.1.A) ---

class PrimarySignals(BaseModel):
    primary_keyword: str
    cluster_id: str
    intent_type: str
    tier: int
    target_word_count: str
    target_reading_grade: str = "10-12"
    schema_types_required: List[str]
    cta_type: str

class SerpIntelligence(BaseModel):
    top_5_competitor_urls: List[str] = []
    top_5_competitor_h2_structures: List[Dict[str, Any]] = []
    top_5_avg_word_count: int = 1500
    featured_snippet_exists: bool = False
    featured_snippet_format: Optional[str] = None
    ai_overview_exists: bool = False
    ai_overview_competitor: Optional[str] = None
    paa_questions: List[str] = []

class ContentGap(BaseModel):
    gap_topics: List[str] = []
    thin_coverage_topics: List[str] = []
    unique_angles: List[str] = []

class IndiaLocalization(BaseModel):
    relevant_regulators: List[str] = []
    relevant_dpdpa_sections: List[str] = []
    relevant_dpdpa_rules: List[str] = []
    indian_company_examples: List[str] = []
    indian_penalty_refs: List[str] = []
    compliance_deadlines: List[str] = []
    india_english: bool = True

class BrandContext(BaseModel):
    kensara_relevant_features: List[str] = []
    kensara_relevant_stats: List[str] = []
    competitor_comparison_facts: List[str] = []
    founder_credentials: str = "Mr Rudraksh Tatwal, Founder & CEO; Mr Prince, Co-founder & COO"
    cta_url: str
    cta_text: str

class InternalLinkTargets(BaseModel):
    mandatory_internal_links: List[Dict[str, str]] = []
    optional_internal_links: List[Dict[str, str]] = []
    links_to_avoid: List[str] = []

class FreshnessRequirements(BaseModel):
    requires_current_date: bool = True
    stat_freshness_year: int = 2024
    regulatory_reference_cutoff: Optional[str] = None

class KeywordBrief(BaseModel):
    primary_signals: PrimarySignals
    serp_intelligence: SerpIntelligence
    content_gap: ContentGap
    india_localization: IndiaLocalization
    brand_context: BrandContext
    internal_link_targets: InternalLinkTargets
    freshness_requirements: FreshnessRequirements
    serp_format_strategy: str = ""


def duplicate_content_precheck(keyword: str) -> str:
    """2.1.C Duplicate Content Pre-Check.
    Queries RAG vector database to prevent keyword cannibalization.
    Returns: 'proceed', 'refresh', or 'related'
    """
    try:
        from src.rag.retrieval import retrieve
        
        # Query the published posts collection
        results = retrieve(query=keyword, collection_key="published_posts", top_k=1)
        
        if not results:
            return "proceed"
            
        top_match = results[0]
        score = top_match.get("rerank_score", 0.0)
        
        # Map cross-encoder scores (roughly) to the 0-1 similarity scale requested in spec
        # Cross encoders usually range from -10 to +10. >3 is very similar, >0 is somewhat similar.
        if score > 4.0:
            # Equivalent to > 0.75 similarity
            return "refresh"
        elif score > 0.0:
            # Equivalent to 0.50-0.75 similarity
            return "related"
        else:
            return "proceed"
            
    except Exception as e:
        log.warning("duplicate_check_failed", error=str(e))
        return "proceed"

def assemble_keyword_brief(
    keyword: str,
    intent_type: str = "informational",
    tier: int = 1,
    cluster_id: str = "general",
    news_angle: str = "",
    paa_questions: List[str] = None,
    serp_intelligence: Optional[SerpIntelligence] = None
) -> KeywordBrief:
    """Assemble full Keyword Brief JSON for LLM injection."""
    
    # Pre-Check
    status = duplicate_content_precheck(keyword)
    if status != "proceed":
        log.warning("duplicate_content_flag", keyword=keyword, status=status)

    facts = KENSARAI_FACTS
    stats = get_platform_stats()  # reads from disk — picks up UI edits without restart
    
    if serp_intelligence:
        serp = serp_intelligence
    else:
        # Mock SERP intelligence until Module 1.3 is connected
        serp = SerpIntelligence(
            top_5_avg_word_count=1500,
            featured_snippet_exists=True,
            featured_snippet_format="paragraph",
            paa_questions=paa_questions or [],
        )
    serp_fmt = analyze_serp_format(serp, tier=tier)
    avg_word_count = serp_fmt.avg_competitor_word_count or 1500
    
    # Primary Signals
    schema_reqs = ["Article", "BreadcrumbList"]
    cta_type = "lead_gen"
    cta_url = "https://www.kensara.in/dpdpa"
    cta_text = "Download our free DPDPA Compliance Checklist"
    
    if intent_type == "commercial":
        cta_type = "compare"
        cta_url = "https://www.kensara.in/benefits"
        cta_text = f"See how Kensara handles {keyword} at a fraction of OneTrust's cost."
        schema_reqs.append("FAQPage")
    elif intent_type == "transactional":
        cta_type = "book_demo"
        cta_url = "https://www.kensara.in/book-demo"
        cta_text = "Book a free 30-minute DPDPA assessment with our certified team."

    primary = PrimarySignals(
        primary_keyword=keyword,
        cluster_id=cluster_id,
        intent_type=intent_type,
        tier=tier,
        target_word_count=serp_fmt.calibrated_word_count_range,
        schema_types_required=schema_reqs,
        cta_type=cta_type
    )
    
    gap = ContentGap(
        unique_angles=["AI-native compliance vs legacy workflows"]
    )
    
    localization = IndiaLocalization(
        relevant_regulators=["DPBI", "MeitY"],
        indian_company_examples=["Reliance", "Tata Motors"], # Mocks
        compliance_deadlines=["Ongoing"]
    )
    
    # Filter Brand Context based on intent
    rel_features = [desc for name, desc in facts["key_features"].items()]
    comp_facts = facts["differentiators"] if intent_type == "commercial" else []
    
    brand = BrandContext(
        kensara_relevant_features=rel_features[:3],
        kensara_relevant_stats=[f"{k}: {v}" for k, v in stats.items() if isinstance(v, (int, float)) and v > 0],
        competitor_comparison_facts=comp_facts,
        cta_url=cta_url,
        cta_text=cta_text
    )
    
    links = InternalLinkTargets(
        mandatory_internal_links=[{"anchor_text": "Kensara Homepage", "url": "https://kensara.in"}]
    )
    
    freshness = FreshnessRequirements()
    
    serp_strategy = serp_fmt.strategy_instruction
    
    brief = KeywordBrief(
        primary_signals=primary,
        serp_intelligence=serp,
        content_gap=gap,
        india_localization=localization,
        brand_context=brand,
        internal_link_targets=links,
        freshness_requirements=freshness,
        serp_format_strategy=serp_strategy
    )
    
    log.debug("keyword_brief_assembled", keyword=keyword, intent=intent_type)
    return brief

def build_context(keyword: str = "", news_angle: str = "") -> str:
    """Legacy wrapper for backward compatibility. 
    Use assemble_keyword_brief going forward."""
    brief = assemble_keyword_brief(keyword=keyword, news_angle=news_angle)
    return brief.model_dump_json(indent=2)
