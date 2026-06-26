from typing import Dict, List, Optional, Any
from sentence_transformers import CrossEncoder

from src.rag.chroma_client import get_or_create_collection
from src.rag.bm25_utils import load_bm25_index, tokenize_for_bm25

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Singleton reranker to avoid reloading model into memory
_reranker = None

def get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANKER_MODEL)
    return _reranker


def hybrid_search(
    query: str,
    collection_key: str,
    metadata_filter: Optional[Dict] = None,
    n_results: int = 50,
    rrf_k: int = 60
) -> List[Dict]:
    """
    Execute hybrid search: BM25 + vector search -> RRF fusion.
    Returns list of {id, document, metadata, rrf_score} dicts,
    sorted by RRF score descending.
    """
    collection = get_or_create_collection(collection_key)
    
    # --- VECTOR SEARCH ---
    vector_results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=metadata_filter,
        include=["documents", "metadatas", "distances"]
    )
    
    if not vector_results["ids"] or not vector_results["ids"][0]:
        vector_ids = []
        vector_docs = []
        vector_meta = []
    else:
        vector_ids = vector_results["ids"][0]
        vector_docs = vector_results["documents"][0]
        vector_meta = vector_results["metadatas"][0]

    # --- BM25 SEARCH ---
    index_data = load_bm25_index(collection_key)
    bm25_index = index_data.get("bm25")
    all_ids = index_data.get("ids", [])
    all_docs = index_data.get("documents", [])
    
    if not all_ids or not bm25_index:
        bm25_ids = []
        bm25_docs_map = {}
    else:
        tokenized_query = tokenize_for_bm25(query)
        bm25_scores = bm25_index.get_scores(tokenized_query)

        # If metadata_filter is set, we need to filter BM25 results too
        if metadata_filter:
            allowed = collection.get(where=metadata_filter)
            allowed_ids = set(allowed["ids"])
        else:
            allowed_ids = set(all_ids)

        id_score_pairs = [
            (all_ids[i], bm25_scores[i], all_docs[i])
            for i in range(len(all_ids))
            if all_ids[i] in allowed_ids
        ]
        id_score_pairs.sort(key=lambda x: x[1], reverse=True)
        bm25_top = id_score_pairs[:n_results]
        bm25_ids = [x[0] for x in bm25_top]
        bm25_docs_map = {x[0]: x[2] for x in bm25_top}

    # --- RRF FUSION ---
    vector_rank = {doc_id: rank + 1 for rank, doc_id in enumerate(vector_ids)}
    bm25_rank = {doc_id: rank + 1 for rank, doc_id in enumerate(bm25_ids)}

    fusion_ids = set(vector_ids) | set(bm25_ids)
    
    rrf_scores = {}
    for doc_id in fusion_ids:
        v_rank = vector_rank.get(doc_id, n_results + 1)
        b_rank = bm25_rank.get(doc_id, n_results + 1)
        rrf_scores[doc_id] = (1 / (rrf_k + v_rank)) + (1 / (rrf_k + b_rank))

    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

    # Retrieve full document objects for top results
    extra_ids = [i for i in sorted_ids[:n_results] if i not in set(vector_ids)]
    extra_docs = {}
    extra_meta = {}
    if extra_ids:
        extra_data = collection.get(ids=extra_ids, include=["documents", "metadatas"])
        if extra_data and extra_data.get("ids"):
            for idx, doc_id in enumerate(extra_data["ids"]):
                extra_docs[doc_id] = extra_data["documents"][idx]
                extra_meta[doc_id] = extra_data["metadatas"][idx]

    v_doc_map = {vid: vdoc for vid, vdoc in zip(vector_ids, vector_docs)}
    v_meta_map = {vid: vm for vid, vm in zip(vector_ids, vector_meta)}

    results = []
    for doc_id in sorted_ids[:n_results]:
        results.append({
            "id": doc_id,
            "document": v_doc_map.get(doc_id) or extra_docs.get(doc_id, ""),
            "metadata": v_meta_map.get(doc_id) or extra_meta.get(doc_id, {}),
            "rrf_score": rrf_scores[doc_id]
        })

    return results


def rerank(query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    Rerank hybrid search candidates using cross-encoder.
    candidates: output from hybrid_search()
    top_k: how many to return after reranking
    """
    if not candidates:
        return []
        
    reranker = get_reranker()
    pairs = [(query, c["document"]) for c in candidates]
    scores = reranker.predict(pairs)

    for i, candidate in enumerate(candidates):
        candidate["rerank_score"] = float(scores[i])

    reranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    return reranked[:top_k]


def retrieve(
    query: str,
    collection_key: str,
    metadata_filter: Optional[Dict] = None,
    top_k: int = 5,
    n_hybrid: int = 50
) -> List[Dict]:
    """
    Full retrieval pipeline: hybrid search -> rerank -> top_k.
    This is the ONLY function other modules should call.
    """
    candidates = hybrid_search(query, collection_key, metadata_filter, n_hybrid)
    
    # Note: Even if len(candidates) <= top_k, we MUST rerank to get the 'rerank_score' 
    # which is used by downstream thresholding tasks (like Duplicate Check).
    if not candidates:
        return []
        
    return rerank(query, candidates, top_k)
