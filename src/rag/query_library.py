from typing import List, Dict, Any, Optional
from src.rag.retrieval import retrieve

def check_duplicate_post(primary_keyword: str, cluster_name: str) -> Dict[str, Any]:
    """
    TASK 1: Duplicate Check (Module 2.1.C)
    Checks if a post is a duplicate or related before generating.
    """
    query = f"blog post about {primary_keyword} for {cluster_name}"
    results = retrieve(query, "published_posts", metadata_filter=None, top_k=3)
    
    # Assess top result
    if results:
        top_score = results[0].get("rerank_score", 0.0)
        if top_score > 0.85:
            status = "REJECT"
        elif top_score >= 0.65:
            status = "FLAG_RELATED"
        else:
            status = "PROCEED"
    else:
        status = "PROCEED"
        
    return {
        "status": status,
        "results": results
    }

def get_competitor_gaps(primary_keyword: str) -> List[Dict]:
    """
    TASK 2: Competitor Gap Retrieval (Outline Step 1)
    """
    query = f"competitor content about {primary_keyword} topics covered angles"
    filter_dict = {"gap_flag": True}
    return retrieve(query, "competitor_intel", metadata_filter=filter_dict, top_k=5)

def get_dpdpa_grounding(section_heading: str, primary_keyword: str, industry: Optional[str] = None) -> List[Dict]:
    """
    TASK 3: DPDPA Regulatory Grounding (Body Generation, Regulatory Sections)
    """
    query = f"{section_heading} {primary_keyword} obligations requirements India"
    
    filter_dict = None
    if industry:
        # Note: ChromaDB uses $contains for array membership
        filter_dict = {"applicable_sectors": {"$contains": industry}}
        
    return retrieve(query, "dpdpa_source", metadata_filter=filter_dict, top_k=3)

def get_brand_facts(primary_keyword: str, relevant_modules: str, cluster_id: str) -> List[Dict]:
    """
    TASK 4: Brand Fact Retrieval (Context Assembly for Every Post)
    """
    query = f"KensaraAI {primary_keyword} {relevant_modules}"
    filter_dict = {
        "$and": [
            {"relevant_clusters": {"$contains": cluster_id}},
            {"superseded": False}
        ]
    }
    return retrieve(query, "brand_context", metadata_filter=filter_dict, top_k=5)

def get_paa_questions(primary_keyword: str, cluster_id: str) -> List[Dict]:
    """
    TASK 5: PAA Question Retrieval (FAQ Section Generation)
    """
    query = f"questions about {primary_keyword} asked by Indian businesses"
    filter_dict = {
        "$and": [
            {"cluster_id": cluster_id},
            # Using $eq for None/null or checking answered_in_post_url doesn't exist.
            # Assuming None translates to checking for empty or None in Chroma.
            # Let's use a standard format for "not answered".
            {"answered_in_post_url": ""} # Workaround for null/None if needed
        ]
    }
    return retrieve(query, "paa_queries", metadata_filter=filter_dict, top_k=5)

def get_high_performer_templates(cluster_id: str, intent_type: str) -> List[Dict]:
    """
    TASK 6: High-Performer Template Retrieval (Outline Quality Signal)
    """
    query = f"high performing post {cluster_id} {intent_type} structure"
    filter_dict = {
        "$and": [
            {"performance_class": "high"},
            {"cluster_id": cluster_id},
            {"intent_type": intent_type}
        ]
    }
    return retrieve(query, "performance_intel", metadata_filter=filter_dict, top_k=2)

def discover_internal_links(context_sentence: str, cluster_id: str, current_post_url: str) -> List[Dict]:
    """
    TASK 7: Internal Link Discovery (Internal Linking Engine)
    """
    query = f"{context_sentence}"
    filter_dict = {
        "$and": [
            {"cluster_id": cluster_id},
            {"post_url": {"$ne": current_post_url}}
        ]
    }
    return retrieve(query, "published_posts", metadata_filter=filter_dict, top_k=5)

def run_refresh_audit(topic_in_section: str, post_original_publish_date: str) -> List[Dict]:
    """
    TASK 8: Refresh Audit (Content Decay Module 2.7)
    """
    query = f"current requirements for {topic_in_section} 2026"
    filter_dict = {"date_issued": {"$gt": post_original_publish_date}}
    return retrieve(query, "dpdpa_source", metadata_filter=filter_dict, top_k=3)
