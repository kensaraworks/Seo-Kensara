"""Tests for the TF-IDF cosine similarity deduplicator."""
import json
from src.scrapers.deduplicator import tokenize, get_word_frequencies, is_duplicate_story

def test_tokenize():
    text = "The quick brown Fox, jumped over the lazy DPDPA Act!"
    tokens = tokenize(text)
    # Stopwords like the, over, quick, brown, fox, jumped, lazy, dpdpa, act
    # "the" is stopword, "quick" is not, "brown" is not, etc.
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens
    assert "dpdpa" in tokens
    assert "the" not in tokens
    assert "over" not in tokens

def test_get_word_frequencies():
    text = "DPDPA compliance and DPDPA rules for India."
    freq = get_word_frequencies(text)
    assert freq.get("dpdpa") == 2
    assert freq.get("compliance") == 1
    assert freq.get("rules") == 1
    assert freq.get("india") == 1
    assert "and" not in freq

def test_is_duplicate_story():
    # 1. Exactly same story
    new_story = "Data Protection Board of India penalizes company for breach of consent."
    history = [
        json.dumps(get_word_frequencies("Data Protection Board of India penalizes company for breach of consent."))
    ]
    is_dup, score = is_duplicate_story(new_story, history)
    assert is_dup is True
    assert score > 0.99

    # 2. Highly similar story
    similar_story = "Data Protection Board of India fines firm for violating consent framework."
    history_similar = [
        json.dumps(get_word_frequencies("Data Protection Board of India fines firm for consent violations."))
    ]
    is_dup, score = is_duplicate_story(similar_story, history_similar, threshold=0.5)
    assert is_dup is True
    assert score >= 0.5

    # 3. Completely different story
    different_story = "Microsoft Azure India servers launch in new region."
    is_dup, score = is_duplicate_story(different_story, history)
    assert is_dup is False
    assert score < 0.3
