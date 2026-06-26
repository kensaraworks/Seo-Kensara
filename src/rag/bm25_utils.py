import os
import pickle
import re
from rank_bm25 import BM25Okapi
from typing import Dict, Any
from src.rag.chroma_client import get_or_create_collection

BM25_INDEX_PATH = os.path.join("drafts", ".cache", "bm25_indexes")

def tokenize_for_bm25(text: str) -> list[str]:
    """
    Tokenize text for BM25. Custom tokenizer for legal/compliance text:
    - Lowercase
    - Split on whitespace and punctuation
    - Preserve DPDPA section numbers as single tokens (Section8 not [Section][8])
    - Preserve ₹ amounts as single tokens
    - Remove common stop words (English)
    """
    if not text:
        return []
    
    # Preserve legal identifiers before splitting
    text = re.sub(r'Section\s+(\d+)', r'section\1', text, flags=re.IGNORECASE)
    text = re.sub(r'Rule\s+(\d+)', r'rule\1', text, flags=re.IGNORECASE)
    text = re.sub(r'₹\s*(\d+)', r'₹\1', text)
    
    # Lowercase and split
    tokens = re.findall(r'\b\w+\b', text.lower())
    
    # Remove stop words
    stop_words = {"the", "a", "an", "in", "of", "for", "and", "or",
                  "to", "is", "are", "was", "were", "be", "been",
                  "have", "has", "that", "this", "with", "by", "at"}
    return [t for t in tokens if t not in stop_words and len(t) > 1]


def build_bm25_index(collection_key: str) -> BM25Okapi:
    """
    Build BM25 index from all chunks in a ChromaDB collection.
    Called on first startup and when collection is updated.
    """
    os.makedirs(BM25_INDEX_PATH, exist_ok=True)
    
    collection = get_or_create_collection(collection_key)
    # ChromaDB .get() with no filters returns ALL documents
    all_docs = collection.get(include=["documents"])
    
    ids = all_docs.get("ids", [])
    documents = all_docs.get("documents", [])
    
    if not ids or not documents:
        # Create an empty index if collection is empty
        tokenized_corpus = []
        bm25_index = BM25Okapi([[""]]) # Rank-bm25 needs at least one doc to not crash on empty
        ids = []
        documents = []
    else:
        tokenized_corpus = [tokenize_for_bm25(doc) for doc in documents]
        bm25_index = BM25Okapi(tokenized_corpus)

    # Save corpus mapping
    index_data = {
        "bm25": bm25_index,
        "ids": ids,
        "documents": documents
    }
    
    file_path = os.path.join(BM25_INDEX_PATH, f"{collection_key}.pkl")
    with open(file_path, "wb") as f:
        pickle.dump(index_data, f)

    return bm25_index


def load_bm25_index(collection_key: str) -> Dict[str, Any]:
    """
    Load BM25 index from disk.
    If it doesn't exist, build it first.
    """
    file_path = os.path.join(BM25_INDEX_PATH, f"{collection_key}.pkl")
    if not os.path.exists(file_path):
        build_bm25_index(collection_key)
        
    with open(file_path, "rb") as f:
        return pickle.load(f)
