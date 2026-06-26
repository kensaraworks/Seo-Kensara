from typing import List, Dict, Any

CONTEXT_BLOCK_TEMPLATE = """
=== RETRIEVED KNOWLEDGE BASE CONTEXT ===
The following information has been retrieved from verified sources.
Use it to ground your response. Do not invent facts not present here.

{sections}

IMPORTANT: If the task requires a fact not found in the above context,
explicitly state that the fact is not available in the knowledge base
rather than generating a plausible-sounding value.
=== END OF RETRIEVED CONTEXT ===
"""

TASK_HEADERS = {
    "competitor_gap": "CONTENT GAPS NOT YET COVERED BY COMPETITORS:",
    "statutory_text": "VERIFIED DPDPA STATUTORY TEXT TO CITE ACCURATELY:",
    "brand_facts": "APPROVED KENSARAAI FACTS (USE AS-IS, DO NOT MODIFY):",
    "paa_questions": "QUESTIONS YOUR AUDIENCE IS ASKING — ANSWER THESE:",
    "high_performer": "STRUCTURAL REFERENCE FROM HIGH-PERFORMING POSTS:"
}

# Task types that CANNOT be dropped during token budget overflow
IMMUNE_TASKS = {"statutory_text", "brand_facts"}


def _estimate_tokens(text: str) -> int:
    """Fast heuristic: 1 token ~= 4 chars"""
    return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to roughly max_tokens (first max_tokens * 4 chars)"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + "..."


def _build_metadata_summary(collection_key: str, metadata: Dict[str, Any]) -> str:
    """
    7.3.9.D: Metadata Summary Format
    """
    if collection_key == "published_posts":
        return f"Kensara article '{metadata.get('post_title', 'Unknown')}', {metadata.get('date_published', '')}"
    elif collection_key == "dpdpa_source":
        return f"{metadata.get('doc_title', '')}, {metadata.get('issuing_body', '')}, {metadata.get('date_issued', '')}"
    elif collection_key == "competitor_intel":
        return f"{metadata.get('competitor_domain', '')}, URL: {metadata.get('competitor_url', '')}, {metadata.get('date_crawled', '')}"
    elif collection_key == "brand_context":
        return f"KensaraAI approved brand fact v{metadata.get('version', 1)}"
    elif collection_key == "paa_queries":
        return f"Google PAA for '{metadata.get('primary_keyword', '')}'"
    elif collection_key == "performance_intel":
        return f"Kensara 30-day performance report, {metadata.get('report_date', '')}"
    return "Verified Source"


def build_context_block(
    task_type: str,
    collection_key: str,
    retrieved_chunks: List[Dict[str, Any]],
    token_budget: int
) -> str:
    """
    7.3.9 Context Builder
    Assembles retrieved chunks into LLM prompt injection format.
    Enforces token budgets and truncation logic.
    """
    header = TASK_HEADERS.get(task_type, "RELEVANT CONTEXT:")
    
    # Base framework token cost (template text)
    base_cost = _estimate_tokens(CONTEXT_BLOCK_TEMPLATE.format(sections=""))
    available_budget = token_budget - base_cost
    
    chunks_data = []
    
    for c in retrieved_chunks:
        text = c.get("document", "")
        meta_str = _build_metadata_summary(collection_key, c.get("metadata", {}))
        # Keep original text for now
        chunks_data.append({"text": text, "source": meta_str})
        
    def render_sections(data) -> str:
        res = [header]
        for item in data:
            res.append(f"{item['text']}\n[Source: {item['source']}]")
        return "\n\n".join(res)
    
    current_text = render_sections(chunks_data)
    
    if _estimate_tokens(current_text) <= available_budget:
        return CONTEXT_BLOCK_TEMPLATE.format(sections=current_text).strip()
        
    # Budget exceeded. Step 1: Truncate each chunk to 150 tokens.
    for c in chunks_data:
        c["text"] = _truncate_to_tokens(c["text"], 150)
        
    current_text = render_sections(chunks_data)
    if _estimate_tokens(current_text) <= available_budget:
        return CONTEXT_BLOCK_TEMPLATE.format(sections=current_text).strip()
        
    # Budget exceeded. Step 2: Drop lowest ranked chunks iteratively.
    # Note: retrieved_chunks are assumed to be sorted by rank (best first).
    # We drop from the end of the list.
    if task_type not in IMMUNE_TASKS:
        while len(chunks_data) > 0 and _estimate_tokens(render_sections(chunks_data)) > available_budget:
            chunks_data.pop() # Remove lowest ranked
            
    # If it's an immune task and STILL over budget, we just have to return it 
    # despite the budget (since we can't drop them).
    final_text = render_sections(chunks_data)
    return CONTEXT_BLOCK_TEMPLATE.format(sections=final_text).strip()
