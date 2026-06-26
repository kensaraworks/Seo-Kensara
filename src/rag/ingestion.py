import json
import re
from typing import List, Dict, Any
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

def chunk_published_posts(markdown_content: str, metadata_prefix: str = "") -> List[str]:
    """
    7.3.4.A - COLLECTION 1: published_posts CHUNKING
    Markdown header-based chunking with small-to-large fallback.
    """
    headers_to_split_on = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False
    )
    
    header_chunks = splitter.split_text(markdown_content)
    
    # Post-splitting size control (max 512, overlap 64)
    # Note: Using RecursiveCharacterTextSplitter with roughly 4 chars per token estimation
    # 512 tokens ~ 2048 chars, 64 tokens ~ 256 chars
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2048,
        chunk_overlap=256
    )
    
    final_chunks = []
    for chunk in header_chunks:
        sub_chunks = size_splitter.split_text(chunk.page_content)
        for sc in sub_chunks:
            # Add contextual enrichment
            final_chunks.append(f"{metadata_prefix} {sc}".strip())
            
    return final_chunks

def chunk_dpdpa_source(text: str, doc_type: str, metadata_prefix: str = "") -> List[str]:
    """
    7.3.4.B - COLLECTION 2: dpdpa_source_texts CHUNKING
    Statute-aware structural chunking.
    """
    final_chunks = []
    
    if doc_type in ["act", "rules"]:
        # Split at Section level (rough approximation, looking for "Section X" at start of line)
        sections = re.split(r'\n(?=Section\s+\d+)', text)
        for section in sections:
            section = section.strip()
            if section:
                # Truncate if extremely long, but typically keep intact
                final_chunks.append(f"{metadata_prefix} {section}".strip())
                
    elif doc_type in ["dpbi_order", "circular"]:
        # Paragraph level with overlap
        # 100 token overlap ~ 400 chars. Paragraphs can just be separated by double newline
        paragraphs = re.split(r'\n\s*\n', text)
        # simplistic overlap
        for i in range(len(paragraphs)):
            para = paragraphs[i].strip()
            if not para: continue
            
            overlap_text = ""
            if i > 0:
                prev_para = paragraphs[i-1].strip()
                # take last 400 chars of prev paragraph
                overlap_text = prev_para[-400:] + " " if len(prev_para) > 400 else prev_para + " "
                
            chunk_text = overlap_text + para
            final_chunks.append(f"{metadata_prefix} {chunk_text}".strip())
            
    elif doc_type == "court_judgment":
        paragraphs = re.split(r'\n\s*\n', text)
        for para in paragraphs:
            para = para.strip()
            if not para: continue
            # is_holding metadata should be added in caller by checking "HELD" or "ORDERED"
            final_chunks.append(f"{metadata_prefix} {para}".strip())
            
    return final_chunks

def chunk_competitor_intelligence(text: str, metadata_prefix: str = "") -> List[str]:
    """
    7.3.4.C - COLLECTION 3: competitor_intelligence CHUNKING
    Fixed-size chunking with large overlap.
    400 tokens ~ 1600 chars, 80 tokens ~ 320 chars overlap
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1600,
        chunk_overlap=320
    )
    chunks = splitter.split_text(text)
    
    return [f"{metadata_prefix} {c}".strip() for c in chunks]

def chunk_brand_context(fact: str, metadata_prefix: str = "") -> List[str]:
    """
    7.3.4.D - COLLECTION 4: kensara_brand_context CHUNKING
    Atomic fact chunking — one fact per chunk. No limit, no overlap.
    """
    return [f"{metadata_prefix} {fact}".strip()]

# 7.3.4.E - paa_and_queries doesn't need a chunker since it's 1 doc per question.

def chunk_performance_intelligence(report_data: Dict[str, Any]) -> List[str]:
    """
    7.3.4.F - COLLECTION 6: performance_intelligence CHUNKING
    Structured JSON serialized as a natural language summary.
    """
    title = report_data.get("post_title", "Unknown Post")
    cluster = report_data.get("cluster_id", "Unknown Cluster")
    tier = report_data.get("tier", "Unknown Tier")
    word_count = report_data.get("word_count", 0)
    avg_pos = report_data.get("avg_position_30d", 0.0)
    sessions = report_data.get("organic_sessions_30d", 0)
    ctr = report_data.get("ctr_30d", 0.0)
    conversions = report_data.get("demo_conversions_30d", 0)
    perf_class = report_data.get("performance_class", "unknown")
    structure_summary = report_data.get("structure_summary", "")

    summary = (
        f"The post '{title}' (Cluster: {cluster}, Tier {tier}, {word_count} words) "
        f"ranked at average position {avg_pos} for its target keyword after 30 days, "
        f"earned {sessions} organic sessions, achieved {ctr}% CTR, and generated "
        f"{conversions} demo booking conversions. Classified as {perf_class.upper()}. "
        f"Structure: {structure_summary}"
    )
    return [summary]
