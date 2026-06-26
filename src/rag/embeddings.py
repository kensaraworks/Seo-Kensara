from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

EMBEDDING_MODEL_NAME = "all-mpnet-base-v2"

# We use a singleton-like pattern to ensure the model is loaded only once
_embedding_fn = None

def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    """Returns the shared embedding function. Singleton pattern."""
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL_NAME
        )
    return _embedding_fn
