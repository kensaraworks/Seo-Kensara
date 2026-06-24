"""Pure Python TF-IDF and Cosine Similarity engine for deduplicating scraped news stories."""
import math
import re
import json
from typing import Dict, List, Set, Tuple

STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for",
    "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's",
    "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm",
    "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours",
    "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't",
    "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't",
    "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why",
    "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves"
}

def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric words, filtering out stopwords."""
    text = text.lower()
    # Replace non-alphanumeric with spaces
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    words = text.split()
    return [w for w in words if w not in STOPWORDS and len(w) > 1]

def get_word_frequencies(text: str) -> Dict[str, int]:
    """Get term frequency count map for a given text."""
    words = tokenize(text)
    freq: Dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return freq

def calculate_cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
    """Calculate cosine similarity between two sparse TF-IDF vectors represented as dicts."""
    intersection = set(vec1.keys()) & set(vec2.keys())
    if not intersection:
        return 0.0

    dot_product = sum(vec1[w] * vec2[w] for w in intersection)
    
    sum1 = sum(v ** 2 for v in vec1.values())
    sum2 = sum(v ** 2 for v in vec2.values())
    
    if sum1 == 0 or sum2 == 0:
        return 0.0
        
    return dot_product / (math.sqrt(sum1) * math.sqrt(sum2))

def build_tfidf_vectors(documents: List[Dict[str, int]]) -> List[Dict[str, float]]:
    """Convert raw term frequency documents into normalized TF-IDF vectors (dicts)."""
    if not documents:
        return []

    # 1. Compute Document Frequencies (DF) for each word
    df: Dict[str, int] = {}
    for doc in documents:
        for term in doc.keys():
            df[term] = df.get(term, 0) + 1

    # 2. Compute IDF for each word
    num_docs = len(documents)
    idf: Dict[str, float] = {}
    for term, count in df.items():
        idf[term] = math.log((1 + num_docs) / (1 + count)) + 1.0

    # 3. Compute TF-IDF vectors
    tfidf_vectors: List[Dict[str, float]] = []
    for doc in documents:
        vector: Dict[str, float] = {}
        total_words = sum(doc.values())
        for term, tf in doc.items():
            normalized_tf = tf / total_words if total_words > 0 else 0.0
            vector[term] = normalized_tf * idf[term]
        tfidf_vectors.append(vector)

    return tfidf_vectors

def is_duplicate_story(
    new_text: str,
    recent_fingerprints_json: List[str],
    threshold: float = 0.85,
) -> Tuple[bool, float]:
    """Check if new_text is a duplicate of any recent stories.
    
    Args:
        new_text: The title + summary of the candidate story.
        recent_fingerprints_json: List of serialized word-frequency dict JSON strings.
        threshold: Cosine similarity threshold for duplication.
        
    Returns:
        (is_duplicate, max_similarity)
    """
    if not recent_fingerprints_json:
        return False, 0.0

    new_freq = get_word_frequencies(new_text)
    if not new_freq:
        return False, 0.0

    # Parse historical documents
    docs: List[Dict[str, int]] = [new_freq]
    for fp_str in recent_fingerprints_json:
        try:
            docs.append(json.loads(fp_str))
        except (json.JSONDecodeError, TypeError):
            continue

    if len(docs) < 2:
        return False, 0.0

    # Compute TF-IDF vectors
    vectors = build_tfidf_vectors(docs)
    new_vector = vectors[0]
    historical_vectors = vectors[1:]

    max_sim = 0.0
    for hist_vector in historical_vectors:
        sim = calculate_cosine_similarity(new_vector, hist_vector)
        if sim > max_sim:
            max_sim = sim
            if max_sim > threshold:
                return True, max_sim

    return False, max_sim
