import os
import chromadb
from pathlib import Path
from typing import Dict, List, Optional
from src.rag.embeddings import get_embedding_function

# We store the chroma db alongside the sqlite cache in drafts/.cache/
CHROMA_PATH = str(Path(__file__).resolve().parent.parent.parent / "drafts" / ".cache" / "chroma_db")

COLLECTION_NAMES = {
    "published_posts":         "kensara_published_posts",
    "dpdpa_source":            "kensara_dpdpa_source_texts",
    "competitor_intel":        "kensara_competitor_intelligence",
    "brand_context":           "kensara_brand_context",
    "paa_queries":             "kensara_paa_and_queries",
    "performance_intel":       "kensara_performance_intelligence"
}

_client: Optional[chromadb.PersistentClient] = None

def get_client() -> chromadb.PersistentClient:
    """Returns a persistent ChromaDB client. Creates path if not exists."""
    global _client
    if _client is None:
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client

def get_or_create_collection(collection_key: str) -> chromadb.Collection:
    """Gets or creates a collection by its internal key."""
    client = get_client()
    embedding_fn = get_embedding_function()
    
    if collection_key not in COLLECTION_NAMES:
        raise ValueError(f"Unknown collection key: {collection_key}")
        
    name = COLLECTION_NAMES[collection_key]
    return client.get_or_create_collection(
        name=name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"}  # Critical: cosine similarity for text
    )

def upsert_chunks(collection_key: str, documents: List[str], ids: List[str], metadatas: List[Dict]) -> None:
    """
    Upsert document chunks. Upsert = insert if new, update if ID exists.
    ALWAYS use upsert, never add — adding fails if ID already exists.
    """
    if not documents or not ids or len(documents) != len(ids) or len(documents) != len(metadatas):
        raise ValueError("documents, ids, and metadatas lists must be of the same length and non-empty.")
        
    collection = get_or_create_collection(collection_key)
    collection.upsert(
        documents=documents,
        ids=ids,
        metadatas=metadatas
    )

def delete_by_ids(collection_key: str, ids: List[str]) -> None:
    """Delete specific chunks by their IDs. Used during content refresh."""
    if not ids:
        return
    collection = get_or_create_collection(collection_key)
    collection.delete(ids=ids)

def delete_by_metadata(collection_key: str, where: Dict) -> None:
    """
    Delete all chunks matching a metadata filter.
    Example: delete all chunks for a specific post URL
    """
    collection = get_or_create_collection(collection_key)
    # Get IDs first (ChromaDB delete needs IDs, not metadata filter directly)
    results = collection.get(where=where)
    if results and "ids" in results and results["ids"]:
        collection.delete(ids=results["ids"])

def count_chunks(collection_key: str) -> int:
    """Returns total chunk count for a collection. For monitoring."""
    collection = get_or_create_collection(collection_key)
    return collection.count()
