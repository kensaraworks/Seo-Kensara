"""Keyword Cluster Intelligence Engine.

Manages the 8 primary keyword clusters, calculates coverage scores, assesses
difficulty as unknown for Phase 1, extracts PAA questions, and populates the
content generation queue.
"""

import asyncio
from datetime import datetime, date
import structlog

from src.queue.job_queue import job_queue
from src.agents.intent_classifier import classify_intent

log = structlog.get_logger()

# -----------------------------------------------------------------------
# Cluster Definitions
# -----------------------------------------------------------------------

CLUSTERS = {
    "C1": {
        "name": "DPDPA Fundamentals",
        "keywords": [
            "what is DPDPA",
            "DPDP Act 2023 explained",
            "what is a data fiduciary under DPDPA",
            "what is a data principal DPDPA",
            "data processor vs data fiduciary India",
            "DPDPA vs GDPR comparison",
            "DPDPA enforcement timeline 2025 2026 2027",
            "DPDPA penalties India",
            "Data Protection Board of India explained",
            "DPDP Rules 2025 explained"
        ]
    },
    "C2": {
        "name": "DPDPA by Industry",
        "keywords": [
            "DPDPA Compliance for Indian Fintech Companies",
            "DPDPA Compliance for Indian Healthcare Organizations",
            "DPDPA Compliance for EdTech Platforms",
            "DPDPA Compliance for Indian SaaS Companies",
            "DPDPA Compliance for E-commerce Businesses"
        ]
    },
    "C3": {
        "name": "Technical Compliance Operations",
        "keywords": [
            "consent management platform India",
            "DSAR automation India",
            "DPIA template India",
            "data mapping DPDPA",
            "consent manager registration DPDPA",
            "privacy notice requirements DPDPA",
            "breach notification procedure India",
            "data retention policy India DPDPA"
        ]
    },
    "C4": {
        "name": "Significant Data Fiduciary",
        "keywords": [
            "who is significant data fiduciary DPDPA",
            "SDF obligations India",
            "data protection impact assessment India",
            "DPO requirement India",
            "algorithmic transparency DPDPA",
            "data audit requirement significant data fiduciary"
        ]
    },
    "C5": {
        "name": "Data Principal Rights",
        "keywords": [
            "right to access personal data India",
            "right to erasure DPDPA",
            "right to correction India data law",
            "how to file grievance DPDPA",
            "DSAR India deadline",
            "parental consent DPDPA children data"
        ]
    },
    "C6": {
        "name": "Enforcement & Penalties",
        "keywords": [
            "DPDPA penalty amount",
            "maximum fine DPDPA",
            "Data Protection Board adjudication",
            "DPDPA non-compliance consequences",
            "how does DPDPA enforcement work",
            "DPBI penalty orders",
            "DPDPA compliance deadline 2027"
        ]
    },
    "C7": {
        "name": "DPO & Compliance Services",
        "keywords": [
            "DPO as a service India",
            "outsourced data protection officer India",
            "DPDPA compliance consultant India",
            "DPDPA audit service India",
            "privacy program implementation India",
            "CIPP certified privacy India"
        ]
    },
    "C8": {
        "name": "Competitor Displacement",
        "keywords": [
            "OneTrust alternative India",
            "DPDPA compliance software India",
            "best privacy management tool India",
            "affordable DPDPA compliance solution",
            "DPDPA compliance platform comparison",
            "Kensara vs OneTrust",
            "cheap DPDPA compliance tool India"
        ]
    }
}


# -----------------------------------------------------------------------
# Initialization
# -----------------------------------------------------------------------

async def initialize_clusters() -> None:
    """Populate the database with the initial cluster seeds."""
    for cluster_id, data in CLUSTERS.items():
        for kw in data["keywords"]:
            intent = (await classify_intent(kw)).value
            job_queue.upsert_keyword_cluster(
                cluster_id=cluster_id,
                cluster_name=data["name"],
                keyword=kw,
                intent_type=intent
            )
    log.info("keyword_clusters_initialized")


# -----------------------------------------------------------------------
# Mock External API (Serper.dev)
# -----------------------------------------------------------------------

async def _mock_extract_paa_questions(keyword: str) -> list[str]:
    """Mock Serper.dev People Also Ask extraction."""
    await asyncio.sleep(0.5)
    # Generate contextual fake PAA questions based on keyword
    return [
        f"What is the latest update on {keyword}?",
        f"How much does {keyword} cost in India?",
        f"Who is the best provider for {keyword}?"
    ]

# -----------------------------------------------------------------------
# Core Engine Logic
# -----------------------------------------------------------------------

def _calculate_coverage_score(stats: dict) -> float:
    """
    Coverage Score = (published posts / total keywords in cluster) *
                     (ranking keywords / total published posts)
    """
    total = stats.get("total", 0)
    published = stats.get("published", 0)
    ranking = stats.get("ranking", 0)

    if total == 0 or published == 0:
        return 0.0

    return (published / total) * (ranking / published)


def _get_deadline_boost(keyword: str) -> float:
    """
    Boost deadline-proximate keywords 90 days before each enforcement date.
    Oct-Nov 2026, Mar-May 2027.
    """
    today = date.today()
    kw_lower = keyword.lower()
    boost = 0.0
    
    # Example Deadline 1: Nov 1, 2026
    deadline_1 = date(2026, 11, 1)
    days_to_deadline_1 = (deadline_1 - today).days
    if 0 <= days_to_deadline_1 <= 90 and ("2026" in kw_lower or "november" in kw_lower):
        boost += 50.0

    # Example Deadline 2: May 1, 2027
    deadline_2 = date(2027, 5, 1)
    days_to_deadline_2 = (deadline_2 - today).days
    if 0 <= days_to_deadline_2 <= 90 and ("2027" in kw_lower or "may" in kw_lower):
        boost += 50.0

    return boost


async def run_cluster_gap_auto_queue() -> None:
    """
    Every Monday morning, calculates coverage scores for all 8 clusters
    and automatically populates the content queue with top 3 underserved
    keyword targets from the lowest-scoring clusters.
    """
    log.info("run_cluster_gap_auto_queue_start")
    
    # 1. Ensure clusters are populated
    await initialize_clusters()
    
    # 2. Get cluster stats and calculate scores
    stats = job_queue.get_cluster_stats()
    scores = []
    for cid in CLUSTERS.keys():
        c_stat = stats.get(cid, {"total": len(CLUSTERS[cid]["keywords"]), "published": 0, "ranking": 0})
        score = _calculate_coverage_score(c_stat)
        scores.append((cid, score))
        
    # Sort clusters by lowest score
    scores.sort(key=lambda x: x[1])
    
    # 3. Take the 3 lowest scoring clusters and get 1 underserved keyword from each
    # Or top 3 from the lowest scoring cluster
    queued_count = 0
    for cid, score in scores:
        if queued_count >= 3:
            break
            
        underserved = job_queue.get_underserved_keywords(cid, limit=3)
        for kw_data in underserved:
            if queued_count >= 3:
                break
                
            keyword = kw_data["keyword"]
            intent = kw_data["intent_type"] or (await classify_intent(keyword)).value
            
            # difficulty_score: Phase 2 - Serper DA integration pending.
            # Phase 1 intentionally avoids synthetic difficulty signals.
            paa_questions = await _mock_extract_paa_questions(keyword)
            
            # Calculate priority
            priority = 100.0 - score  # lower score = higher priority
            priority += _get_deadline_boost(keyword)
            
            # Enqueue
            job_queue.enqueue_content(
                keyword=keyword,
                intent_type=intent,
                cluster_id=cid,
                priority_score=priority,
                paa_questions=paa_questions
            )
            log.info("keyword_queued", keyword=keyword, priority=priority, difficulty="unknown")
            queued_count += 1
            
    log.info("run_cluster_gap_auto_queue_done", queued_keywords=queued_count)
