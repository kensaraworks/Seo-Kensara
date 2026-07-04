import os
import logging
from sentence_transformers import SentenceTransformer, CrossEncoder

from src.rag.chroma_client import get_or_create_collection, COLLECTION_NAMES
from src.rag.bm25_utils import build_bm25_index
from src.rag.update_pipelines import update_dpdpa_source_texts, update_brand_context

try:
    from src.context.kensarai_facts import KENSARAI_FACTS
except ImportError:
    KENSARAI_FACTS = {}

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def main():
    logger.info("Starting RAG initialization...")

    # 1. Create all 6 ChromaDB collections
    logger.info("Initializing ChromaDB collections...")
    for key in COLLECTION_NAMES.keys():
        get_or_create_collection(key)
        logger.info(f"Created collection: {key}")

    # 2. Download and cache the embedding model
    logger.info("Downloading embedding model (all-mpnet-base-v2)...")
    _ = SentenceTransformer("all-mpnet-base-v2")

    # 3. Download and cache the cross-encoder reranker model
    logger.info("Downloading cross-encoder model (ms-marco-MiniLM-L-6-v2)...")
    _ = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

    # 4. Ingest DPDP Rules 2025 text
    # NOTE: despite the filename, assets/dpdpa_act_2023.txt contains the
    # Digital Personal Data Protection RULES, 2025 (RULE 1 ... RULE 23 +
    # Schedules) — not the enacted Act's Sections. Metadata below reflects
    # what the document actually is so retrieval/citations aren't mislabeled.
    dpdpa_path = os.path.join("assets", "dpdpa_act_2023.txt")
    if os.path.exists(dpdpa_path):
        logger.info(f"Ingesting DPDPA source text from {dpdpa_path}...")
        with open(dpdpa_path, "r", encoding="utf-8") as f:
            text = f.read()

        doc_metadata = {
            "doc_title": "Digital Personal Data Protection Rules, 2025",
            "issuing_body": "Ministry of Electronics and Information Technology",
            "date_issued": "2025-11-13",
            "doc_type": "rules",
        }
        update_dpdpa_source_texts("dpdp_rules_2025", text, doc_metadata, is_manual_amendment=True)
    else:
        logger.warning(f"File not found: {dpdpa_path}. Skipping DPDPA ingestion.")

    # 5. Ingest brand facts
    if KENSARAI_FACTS:
        logger.info("Ingesting brand facts into brand_context...")
        formatted_facts = []
        
        # Flatten dictionary into chunkable facts
        for category, value in KENSARAI_FACTS.items():
            if isinstance(value, str):
                fact_text = f"{category.replace('_', ' ').title()}: {value}"
            elif isinstance(value, dict):
                fact_text = f"{category.replace('_', ' ').title()}: " + ", ".join([f"{k}={v}" for k,v in value.items()])
            elif isinstance(value, list):
                fact_text = f"{category.replace('_', ' ').title()}: " + "; ".join(value)
            else:
                fact_text = f"{category.replace('_', ' ').title()}: {str(value)}"
                
            formatted_facts.append({
                "fact_text": fact_text,
                "metadata": {
                    "fact_category": category,
                    "relevant_modules": ["general"]
                }
            })
            
        update_brand_context(formatted_facts)
    else:
        logger.warning("No brand facts found to ingest.")

    # 6. Build BM25 indexes for all collections
    logger.info("Building BM25 indexes...")
    for key in COLLECTION_NAMES.keys():
        build_bm25_index(key)

    logger.info("RAG database initialized. 6 collections created. Ready for production.")


if __name__ == "__main__":
    main()
