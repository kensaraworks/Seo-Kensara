"""Module 2.5 — Internal Linking Engine

Deterministic Python engine — NO LLM calls.
All linking decisions are made against the SQLite `internal_link_map` database.

Spec: module2_report.txt, Section 2.5
"""
import re
import sqlite3
import uuid
import datetime
import os
from typing import Optional
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = os.path.join("drafts", "internal_link_map.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS internal_link_map (
    post_id            TEXT PRIMARY KEY,
    post_url           TEXT NOT NULL UNIQUE,
    post_title         TEXT NOT NULL,
    primary_keyword    TEXT NOT NULL,
    cluster_id         TEXT NOT NULL DEFAULT 'general',
    intent_type        TEXT NOT NULL DEFAULT 'informational',
    tier               INTEGER NOT NULL DEFAULT 1,
    date_published     TEXT,
    date_updated       TEXT,
    is_pillar          INTEGER NOT NULL DEFAULT 0,
    incoming_link_count INTEGER NOT NULL DEFAULT 0,
    outgoing_link_count INTEGER NOT NULL DEFAULT 0
);
"""

# ---------------------------------------------------------------------------
# Service page map (spec 2.5.B Rule 2)
# ---------------------------------------------------------------------------

_SERVICE_PAGE_MAP = {
    "consent": "https://www.kensara.in/expertise",
    "dsar":    "https://www.kensara.in/expertise",
    "audit":   "https://www.kensara.in/book-demo",
    "assessment": "https://www.kensara.in/book-demo",
    # fallback
    "default": "https://www.kensara.in/expertise",
}

_SERVICE_ANCHOR_MAP = {
    "consent":    "KensaraAI consent management platform",
    "dsar":       "KensaraAI DSAR automation",
    "audit":      "book a free DPDPA assessment",
    "assessment": "book a free DPDPA assessment",
    "default":    "KensaraAI DPDPA compliance platform",
}

def _pick_service_page(keyword: str) -> tuple[str, str]:
    """Return (url, anchor_text) based on primary keyword topic."""
    kw_lower = keyword.lower()
    for key in ("consent", "dsar", "audit", "assessment"):
        if key in kw_lower:
            return _SERVICE_PAGE_MAP[key], _SERVICE_ANCHOR_MAP[key]
    return _SERVICE_PAGE_MAP["default"], _SERVICE_ANCHOR_MAP["default"]


# ---------------------------------------------------------------------------
# 2.5.A — SQLite Link Map Database CRUD
# ---------------------------------------------------------------------------

def get_connection(db_path: str = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a connection to the SQLite link map database, creating it if needed."""
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()
    return conn


def register_post(
    post_url: str,
    post_title: str,
    primary_keyword: str,
    cluster_id: str,
    intent_type: str,
    tier: int,
    is_pillar: bool = False,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    """Register a newly published or updated post in the link map.
    
    Returns the post_id.
    Auto-updated on every publish/refresh (spec 2.5.A).
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection(db_path)
    with conn:
        # Check if post already exists
        existing = conn.execute(
            "SELECT post_id FROM internal_link_map WHERE post_url = ?", (post_url,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE internal_link_map
                   SET post_title=?, primary_keyword=?, cluster_id=?,
                       intent_type=?, tier=?, is_pillar=?, date_updated=?
                   WHERE post_url=?""",
                (post_title, primary_keyword, cluster_id, intent_type,
                 tier, int(is_pillar), now, post_url)
            )
            post_id = existing["post_id"]
            log.info("link_map_post_updated", post_url=post_url)
        else:
            post_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO internal_link_map
                   (post_id, post_url, post_title, primary_keyword,
                    cluster_id, intent_type, tier, date_published,
                    date_updated, is_pillar)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (post_id, post_url, post_title, primary_keyword,
                 cluster_id, intent_type, tier, now, now, int(is_pillar))
            )
            log.info("link_map_post_registered", post_url=post_url, post_id=post_id)
    conn.close()
    return post_id


def increment_link_counts(
    from_url: str, to_url: str, db_path: str = _DEFAULT_DB_PATH
) -> None:
    """Increment outgoing count on from_url and incoming count on to_url."""
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            "UPDATE internal_link_map SET outgoing_link_count = outgoing_link_count + 1 WHERE post_url = ?",
            (from_url,)
        )
        conn.execute(
            "UPDATE internal_link_map SET incoming_link_count = incoming_link_count + 1 WHERE post_url = ?",
            (to_url,)
        )
    conn.close()


def query_cluster_posts(
    cluster_id: str, exclude_keyword: str = "", db_path: str = _DEFAULT_DB_PATH
) -> list[dict]:
    """Return all posts in a cluster, excluding the current post's keyword."""
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT post_url, post_title, primary_keyword, is_pillar, tier
           FROM internal_link_map
           WHERE cluster_id = ? AND primary_keyword != ?
           ORDER BY is_pillar DESC, incoming_link_count DESC""",
        (cluster_id, exclude_keyword)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_pillar_for_cluster(cluster_id: str, db_path: str = _DEFAULT_DB_PATH) -> Optional[dict]:
    """Return the pillar page record for a given cluster, or None if not yet generated."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM internal_link_map WHERE cluster_id = ? AND is_pillar = 1 LIMIT 1",
        (cluster_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def query_relevant_tier1_or_2_post(
    keyword: str, cluster_id: str, db_path: str = _DEFAULT_DB_PATH
) -> Optional[dict]:
    """Find the most relevant Tier 1 or 2 post for a Tier 3 newsjack.
    
    Prefers same cluster. Falls back to highest incoming_link_count globally.
    """
    conn = get_connection(db_path)
    # Same cluster first
    row = conn.execute(
        """SELECT * FROM internal_link_map
           WHERE cluster_id = ? AND tier IN (1, 2)
           ORDER BY incoming_link_count DESC LIMIT 1""",
        (cluster_id,)
    ).fetchone()
    if not row:
        # Fall back to any T1/T2 post
        row = conn.execute(
            """SELECT * FROM internal_link_map
               WHERE tier IN (1, 2) AND primary_keyword != ?
               ORDER BY incoming_link_count DESC LIMIT 1""",
            (keyword,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def validate_optional_link(suggested_url: str, current_keyword: str, db_path: str = _DEFAULT_DB_PATH) -> Optional[dict]:
    """Validate an LLM-suggested link against the link map.
    
    Returns the post record if the URL exists and does NOT target the same keyword.
    Returns None if URL is not in the DB or would cannibalize keyword.
    """
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM internal_link_map WHERE post_url = ?", (suggested_url,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if row["primary_keyword"].lower() == current_keyword.lower():
        log.warning("cannibalization_blocked", url=suggested_url, keyword=current_keyword)
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# 2.5.B — Mandatory Link Injection (Rules 1-4)
# ---------------------------------------------------------------------------

def _url_already_linked(markdown: str, url: str) -> bool:
    """Return True if `url` already appears as a Markdown link target in `markdown`."""
    return f"]({url})" in markdown


def inject_mandatory_links(
    markdown: str,
    keyword: str,
    cluster_id: str,
    intent_type: str,
    tier: int,
    db_path: str = _DEFAULT_DB_PATH,
) -> tuple[str, list[str]]:
    """Deterministically inject all mandatory links into a markdown post.

    Returns (updated_markdown, list_of_injected_urls).
    All 4 mandatory rules from spec 2.5.B are applied in order.
    Idempotent: if a target URL is already present in the markdown the rule
    is skipped, so calling this function twice produces the same result.
    """
    injected_urls: list[str] = []
    used_anchors: set[str] = set()

    # Rule 1: Link to cluster pillar page
    pillar = query_pillar_for_cluster(cluster_id, db_path)
    if pillar:
        if _url_already_linked(markdown, pillar["post_url"]):
            log.debug("rule1_pillar_already_linked", cluster=cluster_id)
        else:
            anchor = f"complete DPDPA {cluster_id} compliance guide"
            link_text = f"[{anchor}]({pillar['post_url']})"
            markdown, injected = _inject_after_nth_h2(markdown, link_text, n=1)
            if injected:
                injected_urls.append(pillar["post_url"])
                used_anchors.add(anchor)
                increment_link_counts("", pillar["post_url"], db_path)
                log.info("rule1_pillar_link_injected", cluster=cluster_id)
    else:
        log.warning("rule1_no_pillar_found", cluster=cluster_id)

    # Rule 2: Link to service page (deterministic topic detection)
    service_url, service_anchor = _pick_service_page(keyword)
    if _url_already_linked(markdown, service_url):
        log.debug("rule2_service_already_linked", url=service_url)
    elif service_anchor not in used_anchors:
        link_text = f"[{service_anchor}]({service_url})"
        markdown, injected = _inject_after_nth_h2(markdown, link_text, n=2)
        if injected:
            injected_urls.append(service_url)
            used_anchors.add(service_anchor)
            log.info("rule2_service_link_injected", url=service_url)

    # Rule 3: Commercial/transactional posts must link to /compare or /pricing
    if intent_type in ("commercial", "transactional"):
        compare_url = "https://www.kensara.in/benefits"
        if _url_already_linked(markdown, compare_url):
            log.debug("rule3_compare_already_linked")
        else:
            compare_anchor = "compare KensaraAI vs OneTrust pricing"
            if compare_anchor not in used_anchors:
                compare_link = f"[{compare_anchor}]({compare_url})"
                markdown = _inject_into_cta_section(markdown, compare_link)
                injected_urls.append(compare_url)
                used_anchors.add(compare_anchor)
                log.info("rule3_compare_link_injected")

    # Rule 4: Tier 3 must link to a relevant Tier 1/2 post
    if tier == 3:
        relevant = query_relevant_tier1_or_2_post(keyword, cluster_id, db_path)
        if relevant:
            if _url_already_linked(markdown, relevant["post_url"]):
                log.debug("rule4_tier3_link_already_present", url=relevant["post_url"])
            else:
                anchor = f"read our in-depth guide to {relevant['primary_keyword']}"
                if anchor not in used_anchors:
                    link_text = f"[{anchor}]({relevant['post_url']})"
                    markdown = _inject_at_end_of_first_h2(markdown, link_text)
                    injected_urls.append(relevant["post_url"])
                    used_anchors.add(anchor)
                    increment_link_counts("", relevant["post_url"], db_path)
                    log.info("rule4_tier3_link_injected", url=relevant["post_url"])
        else:
            log.warning("rule4_no_tier1_or_2_post_found", keyword=keyword)

    return markdown, injected_urls


def validate_and_inject_optional_links(
    markdown: str,
    suggested_links: list[dict],
    current_keyword: str,
    injected_urls: list[str],
    db_path: str = _DEFAULT_DB_PATH,
) -> tuple[str, list[str]]:
    """Validate LLM-suggested optional links and inject them if safe.
    
    Spec 2.5.B Optional Links: URL must exist in DB, must not target same keyword.
    Link equity caps enforced: max 8 optional outgoing links, no duplicate anchors.
    """
    optional_count = len(injected_urls)  # includes mandatory ones for equity cap
    used_anchors: set[str] = _extract_all_anchors(markdown)

    for suggestion in suggested_links:
        # Link equity cap: max 8 optional links (not counting mandatory)
        if optional_count >= 8:
            log.info("optional_link_cap_reached", cap=8)
            break

        url = suggestion.get("url", "")
        anchor = suggestion.get("anchor", "")
        context_phrase = suggestion.get("context_phrase", "")

        # No duplicate anchor text
        if anchor.lower() in {a.lower() for a in used_anchors}:
            log.warning("duplicate_anchor_blocked", anchor=anchor)
            continue

        # Validate against DB (existence + no cannibalization)
        post = validate_optional_link(url, current_keyword, db_path)
        if not post:
            continue

        # Inject: replace the context phrase with the linked version
        if context_phrase and context_phrase in markdown:
            linked_phrase = f"[{context_phrase}]({url})"
            markdown = markdown.replace(context_phrase, linked_phrase, 1)
            injected_urls.append(url)
            used_anchors.add(anchor)
            optional_count += 1
            log.info("optional_link_injected", url=url, anchor=anchor)

    return markdown, injected_urls


# ---------------------------------------------------------------------------
# 2.5.C — Orphan Post Detection
# ---------------------------------------------------------------------------

def detect_orphans(db_path: str = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all posts with zero incoming links plus 3 recommended linkers.
    
    Spec 2.5.C: Target is zero orphan posts at any time.
    """
    conn = get_connection(db_path)
    orphans = conn.execute(
        """SELECT * FROM internal_link_map WHERE incoming_link_count = 0 AND is_pillar = 0"""
    ).fetchall()

    result = []
    for orphan in orphans:
        orphan_dict = dict(orphan)
        # Find 3 posts in same cluster that could naturally link to this orphan
        candidates = conn.execute(
            """SELECT post_url, post_title, primary_keyword
               FROM internal_link_map
               WHERE cluster_id = ?
                 AND post_url != ?
                 AND primary_keyword != ?
               ORDER BY incoming_link_count DESC
               LIMIT 3""",
            (orphan["cluster_id"], orphan["post_url"], orphan["primary_keyword"])
        ).fetchall()
        orphan_dict["recommended_linkers"] = [dict(c) for c in candidates]
        result.append(orphan_dict)

    conn.close()
    log.info("orphan_detection_complete", orphan_count=len(result))
    return result


# ---------------------------------------------------------------------------
# 2.5.D — Anchor Text Diversity Monitor
# ---------------------------------------------------------------------------

def audit_anchor_diversity(db_path: str = _DEFAULT_DB_PATH) -> list[dict]:
    """Monthly audit: flag any target URL where >60% of incoming anchors are identical.
    
    Spec 2.5.D: Over-optimized anchor text is a Google penalty risk.
    Returns a list of flagged URLs with anchor breakdown.
    """
    conn = get_connection(db_path)
    all_posts = conn.execute("SELECT post_url FROM internal_link_map").fetchall()
    conn.close()

    # We need to scan the actual markdown files to get real anchor text.
    # This reads from drafts/blogs/ and drafts/pillars/
    anchor_map: dict[str, list[str]] = {}  # target_url -> list of anchors pointing at it
    _scan_drafts_for_anchors(anchor_map)

    flagged = []
    for target_url, anchors in anchor_map.items():
        if not anchors:
            continue
        anchor_counts: dict[str, int] = {}
        for a in anchors:
            anchor_counts[a] = anchor_counts.get(a, 0) + 1
        max_anchor = max(anchor_counts, key=anchor_counts.get)
        max_pct = anchor_counts[max_anchor] / len(anchors)
        if max_pct > 0.60:
            flagged.append({
                "target_url": target_url,
                "total_incoming_anchors": len(anchors),
                "dominant_anchor": max_anchor,
                "dominant_anchor_pct": round(max_pct * 100, 1),
                "anchor_breakdown": anchor_counts,
            })

    log.info("anchor_audit_complete", flagged_count=len(flagged))
    return flagged


def _scan_drafts_for_anchors(anchor_map: dict) -> None:
    """Scan all draft markdown files and collect anchor text per target URL."""
    link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
    for directory in ("drafts/blogs", "drafts/pillars"):
        if not os.path.isdir(directory):
            continue
        for fname in os.listdir(directory):
            if not fname.endswith(".md"):
                continue
            with open(os.path.join(directory, fname), encoding="utf-8") as f:
                content = f.read()
            for anchor, url in link_pattern.findall(content):
                if url not in anchor_map:
                    anchor_map[url] = []
                anchor_map[url].append(anchor.strip())


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _extract_all_anchors(markdown: str) -> set[str]:
    """Return all anchor texts already present in the markdown."""
    return set(re.findall(r'\[([^\]]+)\]\(', markdown))


def _inject_after_nth_h2(markdown: str, link_text: str, n: int) -> tuple[str, bool]:
    """Insert link_text as a new paragraph after the closing of the nth H2 section.
    
    Finds the nth ## heading, then looks for the end of the next paragraph.
    Returns (updated_markdown, was_injected).
    """
    h2_positions = [m.start() for m in re.finditer(r'^## .+', markdown, re.MULTILINE)]
    if len(h2_positions) < n:
        return markdown, False

    target_pos = h2_positions[n - 1]
    # Find the next blank line after the heading
    after_heading = markdown[target_pos:]
    # Find end of first paragraph after the H2
    para_end = re.search(r'\n\n', after_heading)
    if para_end:
        insert_pos = target_pos + para_end.end()
        updated = markdown[:insert_pos] + f"\n{link_text}\n\n" + markdown[insert_pos:]
        return updated, True

    return markdown, False


def _inject_at_end_of_first_h2(markdown: str, link_text: str) -> str:
    """Inject a link at the very end of the first H2 section content."""
    h2_positions = [m.start() for m in re.finditer(r'^## .+', markdown, re.MULTILINE)]
    if len(h2_positions) < 2:
        # Only one section — append before end
        return markdown.rstrip() + f"\n\n{link_text}\n"

    # Find the content between the first and second H2
    first_h2_end = h2_positions[1]
    block = markdown[:first_h2_end].rstrip()
    return block + f"\n\n{link_text}\n\n" + markdown[first_h2_end:]


def _inject_into_cta_section(markdown: str, link_text: str) -> str:
    """Inject a link at the beginning of the CTA section."""
    # Find the CTA section — it always contains the kensara.in CTA URL
    cta_match = re.search(r'(## .+\n\n)', markdown)
    # More specifically, find heading above a kensara.in/book-assessment or /compare link
    cta_section_match = re.search(
        r'(## How Kensara[^\n]*\n)',
        markdown,
        re.IGNORECASE
    )
    if cta_section_match:
        pos = cta_section_match.end()
        return markdown[:pos] + f"\n{link_text}\n" + markdown[pos:]
    # Fallback: append before last line
    return markdown.rstrip() + f"\n\n{link_text}\n"
