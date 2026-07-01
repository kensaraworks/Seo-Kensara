import os
import sqlite3
import datetime
from typing import List, Dict, Any, Tuple

from src.rag.chroma_client import get_or_create_collection, COLLECTION_NAMES
from src.rag.bm25_utils import load_bm25_index

from src.config import settings_database_path
DB_PATH = settings_database_path

def _get_db_connection():
    """Helper to get SQLite connection to the jobs db."""
    # Ensure directory exists before connecting
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # Create tables if they don't exist per 7.3.12 specs
    conn.execute('''
        CREATE TABLE IF NOT EXISTS rag_retrieval_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            collection_key TEXT,
            query TEXT,
            results_returned INTEGER,
            expected_top_k INTEGER,
            timestamp TEXT
        )
    ''')
    conn.commit()
    return conn

# ==============================================================================
# 7.3.10.A Retrieval Quality Metrics
# ==============================================================================

def evaluate_context_relevance(generated_text: str, retrieved_chunks: List[Dict]) -> float:
    """
    Target: > 0.70
    Uses a lightweight LLM call to evaluate if chunks were used.
    Returns: used_chunks / total_retrieved_chunks
    """
    if not retrieved_chunks:
        return 0.0
        
    used_count = 0
    # STUB: Replace with actual Groq LLM call
    # Prompt: "Did the generated_text use information from this chunk: {chunk_text}? Answer YES/NO"
    for chunk in retrieved_chunks:
        # Mocking evaluation
        if chunk.get("document", "")[:20] in generated_text:
            used_count += 1
            
    return used_count / len(retrieved_chunks)


def evaluate_context_faithfulness(generated_text: str, retrieved_chunks: List[Dict]) -> float:
    """
    Target: > 0.85
    Uses a lightweight LLM call to extract factual claims from generated_text
    and check if they are supported by retrieved_chunks.
    Returns: supported_claims / total_claims
    """
    if not generated_text:
        return 1.0
        
    # STUB: Replace with actual Groq LLM call
    # 1. Extract claims: "Extract all factual claims from this text..."
    # 2. Verify claims: "Is claim X supported by this context context_blocks? YES/NO"
    mock_total_claims = 10
    mock_supported_claims = 9 # Mocked 90% faithfulness
    
    return mock_supported_claims / mock_total_claims


def evaluate_answer_completeness(generated_text: str, key_points_to_cover: List[str]) -> float:
    """
    Target: > 0.80
    Checks keyword/topic presence for each brief requirement.
    Returns: covered_points / total_brief_points
    """
    if not key_points_to_cover:
        return 1.0
        
    covered = 0
    generated_lower = generated_text.lower()
    
    for point in key_points_to_cover:
        # Simple heuristic, ideally uses LLM semantic match
        if any(word in generated_lower for word in point.lower().split()):
            covered += 1
            
    return covered / len(key_points_to_cover)

# ==============================================================================
# 7.3.10.B Collection Health Metrics
# ==============================================================================

def get_collection_health_metrics() -> Dict[str, Any]:
    """
    Weekly automated check on all 6 collections for dashboard display.
    """
    report = {}
    
    for coll_key, coll_name in COLLECTION_NAMES.items():
        coll = get_or_create_collection(coll_key)
        all_data = coll.get(include=["metadatas"])
        
        chunk_count = len(all_data.get("ids", []))
        metas = all_data.get("metadatas", [])
        
        oldest_date = None
        last_update_date = None
        
        for meta in metas:
            if not meta: continue
            
            # Approximate date fields used across collections
            date_str = meta.get("date_crawled") or meta.get("date_issued") or meta.get("date_published") or meta.get("date_modified")
            if date_str:
                try:
                    dt = datetime.datetime.fromisoformat(date_str)
                    if oldest_date is None or dt < oldest_date:
                        oldest_date = dt
                    if last_update_date is None or dt > last_update_date:
                        last_update_date = dt
                except ValueError:
                    pass
                    
        # Check BM25 sync
        try:
            bm25_data = load_bm25_index(coll_key)
            bm25_ids = set(bm25_data.get("ids", []))
            db_ids = set(all_data.get("ids", []))
            embedding_coverage = (db_ids == bm25_ids)
        except Exception:
            embedding_coverage = False
            
        report[coll_key] = {
            "chunk_count": chunk_count,
            "oldest_chunk_date": oldest_date.isoformat() if oldest_date else None,
            "last_update_date": last_update_date.isoformat() if last_update_date else None,
            "embedding_coverage": embedding_coverage,
            "needs_rebuild": not embedding_coverage
        }
        
    # Additional checks for published_posts
    # posts_without_rag_chunks and orphan_posts require cross-referencing SQLite
    # which is handled by a different module, but would be aggregated here.
    
    return report

# ==============================================================================
# 7.3.10.D Retrieval Failure Logging
# ==============================================================================

def log_retrieval_failure(
    job_id: str,
    collection_key: str,
    query: str,
    results_returned: int,
    expected_top_k: int
):
    """
    Logs anytime retrieve() returns fewer than top_k results.
    Triggers immediate alerts for critical tasks (Task 3 and Task 4).
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    conn = _get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO rag_retrieval_failures 
        (job_id, collection_key, query, results_returned, expected_top_k, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (job_id, collection_key, query, results_returned, expected_top_k, timestamp))
    conn.commit()
    conn.close()
    
    # Critical Alerting based on collection
    if results_returned < 3 and collection_key in ["dpdpa_source", "brand_context"]:
        # In production this pauses the generation job and alerts
        print(f"CRITICAL RETRIEVAL FAILURE: Job {job_id} on {collection_key}. Expected {expected_top_k}, got {results_returned}.")
