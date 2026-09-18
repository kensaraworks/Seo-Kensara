"""Enforcement tracker repository.

The DPDPA enforcement tracker is the highest-value public page on the site, so
reads are designed to *never* raise: every source is tried in turn and the last
one is a skeleton that still renders. Writes go to Supabase when it is
configured and to a local JSON cache otherwise, so the pipeline behaves the same
on a laptop, on Vercel and on a long-running box.

Read order
    1. Supabase ``public.enforcement_actions`` (source of truth)
    2. Supabase ``public.platform_stats`` blob (legacy layout, pre-migration)
    3. Writable JSON cache under ``DATA_DIR``
    4. The JSON bundled in the repository at ``data/enforcement_tracker.json``
    5. An empty skeleton

Statistics are always recomputed from the rows that were actually loaded, so the
counters on the public page can never drift away from the table contents.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import structlog

log = structlog.get_logger()

# ── Layout ───────────────────────────────────────────────────────────────────

TABLE = "enforcement_actions"
META_TABLE = "platform_stats"
META_KEY = "enforcement_tracker_meta"
LEGACY_BLOB_KEY = "enforcement_tracker"

#: Ordered so the public page always renders sections in the same sequence.
SECTIONS: tuple[str, ...] = (
    "enforcement_actions",
    "cert_in_enforcement",
    "pre_dpdpa_actions",
    "international_gdpr_fines_india_relevant",
)

#: Sections that count towards the "cases tracked" headline number.
CORE_SECTIONS: tuple[str, ...] = (
    "enforcement_actions",
    "cert_in_enforcement",
    "pre_dpdpa_actions",
)

ID_PREFIXES = {
    "enforcement_actions": "IND-DPDPA",
    "cert_in_enforcement": "CERT",
    "pre_dpdpa_actions": "IND-IT43A",
    "international_gdpr_fines_india_relevant": "INTL-GDPR",
}

TEXT_FIELDS = (
    "date",
    "authority",
    "company",
    "sector",
    "violation_type",
    "dpdpa_section",
    "summary",
    "penalty_amount",
    "outcome",
    "source_url",
    "notes",
)

DEFAULT_METADATA = {
    "title": "DPDPA Enforcement Tracker — India Data Privacy Enforcement Actions",
    "description": (
        "Database of Indian data privacy enforcement actions, penalties and regulatory "
        "decisions. Covers DPDPA 2023 proceedings, IT Act Section 43A/72A enforcement, "
        "CERT-In breach notifications and MeitY actions. Maintained by KensaraAI."
    ),
    "maintained_by": "KensaraAI",
    "source": "https://www.kensara.in/dpdpa",
    "last_updated": "",
}

#: Repository-bundled seed data. Resolved from this file, never from the process
#: working directory — on Vercel the CWD is not the project root.
BUNDLED_PATH = Path(__file__).resolve().parents[2] / "data" / "enforcement_tracker.json"

# Cache the rendered snapshot briefly so a warm lambda serving the public page
# does not issue a PostgREST round-trip per request.
_CACHE_TTL_SECONDS = 120
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"snapshot": None, "expires_at": 0.0}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path() -> Path:
    """Writable JSON cache location.

    With ``DATA_DIR="."`` the configured path resolves to the repository seed
    file itself; writing there would commit scraped placeholder rows into git,
    so the cache is redirected into the drafts cache directory instead.
    """
    from src.config import settings, settings_enforcement_tracker_path

    path = Path(settings_enforcement_tracker_path)
    try:
        if path.resolve() == BUNDLED_PATH.resolve():
            return Path(settings.content_output_dir) / ".cache" / "enforcement_tracker.json"
    except OSError:
        pass
    return path


def _supabase():
    """Return ``(SupabaseDB, configured)`` without importing at module scope."""
    try:
        from src.db.supabase_client import SupabaseDB, is_supabase_configured

        return SupabaseDB, is_supabase_configured()
    except Exception as exc:  # pragma: no cover - import guard
        log.warning("supabase_client_unavailable", error=str(exc))
        return None, False


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def normalize_action(raw: dict[str, Any], section: str | None = None) -> dict[str, Any]:
    """Coerce any stored shape into the dict the template and API expect.

    Every text field is guaranteed to be a string so Jinja expressions such as
    ``{% if 'Fine' in action.outcome %}`` cannot raise on a NULL column.
    """
    row = dict(raw or {})
    out: dict[str, Any] = {
        "id": str(row.get("id") or "").strip(),
        "section": (section or row.get("section") or "pre_dpdpa_actions"),
    }
    for field in TEXT_FIELDS:
        value = row.get(field)
        out[field] = "" if value is None else str(value).strip()

    # Legacy underscore-prefixed flags from the old JSON file are still honoured.
    out["auto_detected"] = _as_bool(row.get("auto_detected", row.get("_auto_detected", False)))
    out["needs_review"] = _as_bool(row.get("needs_review", row.get("_needs_review", False)))
    out["confidence"] = str(row.get("confidence") or row.get("_confidence") or "high")
    detected = row.get("detected_at") or row.get("_detected_at") or ""
    out["detected_at"] = str(detected) if detected else ""
    return out


def to_db_row(action: dict[str, Any], section: str | None = None) -> dict[str, Any]:
    """Map a normalized action onto the ``enforcement_actions`` table columns."""
    item = normalize_action(action, section)
    row: dict[str, Any] = {
        "id": item["id"],
        "section": item["section"],
        "auto_detected": item["auto_detected"],
        "needs_review": item["needs_review"],
        "confidence": item["confidence"],
        "updated_at": _now_iso(),
    }
    for field in TEXT_FIELDS:
        # A unique index guards source_url, so empty must be NULL rather than ''.
        if field == "source_url":
            row[field] = item[field] or None
        else:
            row[field] = item[field]
    row["detected_at"] = item["detected_at"] or None
    return row


def empty_snapshot(source: str = "empty") -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "metadata": dict(DEFAULT_METADATA),
        "source": source,
        "loaded_at": _now_iso(),
    }
    for section in SECTIONS:
        snapshot[section] = []
    snapshot["statistics"] = compute_statistics(snapshot)
    return snapshot


def _sort_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first; entries without a date sink to the bottom."""
    return sorted(actions, key=lambda a: (a.get("date") or "", a.get("id") or ""), reverse=True)


# ── Statistics ───────────────────────────────────────────────────────────────

def compute_statistics(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recompute the counters shown on the public page from the loaded rows."""
    core: list[dict[str, Any]] = []
    for section in CORE_SECTIONS:
        core.extend(snapshot.get(section) or [])

    by_sector: dict[str, int] = {}
    by_violation: dict[str, int] = {}
    by_outcome: dict[str, int] = {}

    for action in core:
        sector = action.get("sector") or "Unknown"
        by_sector[sector] = by_sector.get(sector, 0) + 1

        violation = action.get("violation_type") or "Unknown"
        by_violation[violation] = by_violation.get(violation, 0) + 1

        outcome = (action.get("outcome") or "Unknown").lower()
        if "ban" in outcome:
            bucket = "Business ban"
        elif "fine" in outcome or "penalty" in outcome:
            bucket = "Fine imposed"
        elif "ongoing" in outcome or "investigation" in outcome:
            bucket = "Investigation ongoing"
        elif "compliance" in outcome:
            bucket = "Compliance achieved"
        elif "enacted" in outcome or "drafted" in outcome or "rule" in outcome:
            bucket = "Law enacted / Rule drafted"
        else:
            bucket = (action.get("outcome") or "Unknown")[:50]
        by_outcome[bucket] = by_outcome.get(bucket, 0) + 1

    return {
        "total_enforcement_actions": len(snapshot.get("enforcement_actions") or []),
        "total_cert_in_actions": len(snapshot.get("cert_in_enforcement") or []),
        "total_pre_dpdpa_actions": len(snapshot.get("pre_dpdpa_actions") or []),
        "total_international_actions_india_relevant": len(
            snapshot.get("international_gdpr_fines_india_relevant") or []
        ),
        "total_all_sections": len(core),
        "by_sector": by_sector,
        "by_violation_type": by_violation,
        "by_outcome": by_outcome,
        "last_recalculated": _now_iso(),
    }


#: Ordered substring rules — the first bucket whose keywords match a sector
#: label wins. Matching on substrings keeps labels such as "Social Media /
#: Messaging" and "Technology / Search" in the right bucket instead of "Other".
_SECTOR_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gov", ("government", "public sector", "municipal", "regulatory")),
    ("healthcare", ("health", "insurance", "hospital", "pharma", "medical")),
    ("fintech", ("fintech", "payment", "banking", "lending", "financial", "capital markets")),
    ("social_tech", ("social media", "tech", "messaging", "app", "search", "software", "internet")),
)


def bucket_for_sector(sector: str) -> str:
    """Return the display bucket a raw sector label belongs to."""
    label = (sector or "").lower()
    for bucket, keywords in _SECTOR_BUCKETS:
        if any(keyword in label for keyword in keywords):
            return bucket
    return "other"


def sector_breakdown(statistics: dict[str, Any]) -> dict[str, int]:
    """Collapse raw sector labels into the five buckets the page displays."""
    by_sector = statistics.get("by_sector") or {}
    grouped = {"social_tech": 0, "healthcare": 0, "fintech": 0, "gov": 0, "other": 0}
    for label, count in by_sector.items():
        grouped[bucket_for_sector(label)] += count
    return grouped


# ── Reads ────────────────────────────────────────────────────────────────────

def _snapshot_from_sections(
    sections: dict[str, list[dict[str, Any]]],
    metadata: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "metadata": {**DEFAULT_METADATA, **(metadata or {})},
        "source": source,
        "loaded_at": _now_iso(),
    }
    for section in SECTIONS:
        snapshot[section] = _sort_actions(sections.get(section) or [])
    snapshot["statistics"] = compute_statistics(snapshot)
    return snapshot


def _load_from_table() -> dict[str, Any] | None:
    db, configured = _supabase()
    if not db or not configured:
        return None
    try:
        rows = db.select_sync(TABLE, order="date.desc", limit=2000)
    except Exception as exc:
        log.warning("enforcement_table_read_failed", error=str(exc))
        return None
    if not rows:
        return None

    sections: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
    for row in rows:
        action = normalize_action(row)
        section = action.get("section") or "pre_dpdpa_actions"
        sections.setdefault(section, []).append(action)

    metadata = _load_metadata_from_supabase() or {}
    return _snapshot_from_sections(sections, metadata, source="supabase:table")


def _load_metadata_from_supabase() -> dict[str, Any] | None:
    db, configured = _supabase()
    if not db or not configured:
        return None
    try:
        rows = db.select_sync(META_TABLE, filters={"key": f"eq.{META_KEY}"}, limit=1)
    except Exception as exc:
        log.warning("enforcement_metadata_read_failed", error=str(exc))
        return None
    if rows and isinstance(rows[0].get("value"), dict):
        return rows[0]["value"]
    return None


def _load_from_legacy_blob() -> dict[str, Any] | None:
    """Read the pre-migration ``platform_stats`` JSON blob, if one is still there."""
    db, configured = _supabase()
    if not db or not configured:
        return None
    try:
        rows = db.select_sync(META_TABLE, filters={"key": f"eq.{LEGACY_BLOB_KEY}"}, limit=1)
    except Exception as exc:
        log.warning("enforcement_legacy_blob_read_failed", error=str(exc))
        return None
    if not rows or not isinstance(rows[0].get("value"), dict):
        return None
    return _snapshot_from_json(rows[0]["value"], source="supabase:legacy_blob")


def _snapshot_from_json(data: dict[str, Any], source: str) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {}
    for section in SECTIONS:
        sections[section] = [normalize_action(a, section) for a in (data.get(section) or [])]
    return _snapshot_from_sections(sections, data.get("metadata") or {}, source=source)


def _load_from_path(path: Path, source: str) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("enforcement_json_read_failed", path=str(path), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    if not any(data.get(section) for section in SECTIONS):
        return None
    return _snapshot_from_json(data, source=source)


def load_snapshot(use_cache: bool = True) -> dict[str, Any]:
    """Return the full tracker snapshot. Never raises."""
    if use_cache:
        with _cache_lock:
            if _cache["snapshot"] is not None and _cache["expires_at"] > time.monotonic():
                return _cache["snapshot"]

    snapshot: dict[str, Any] | None = None
    for loader in (
        _load_from_table,
        _load_from_legacy_blob,
        lambda: _load_from_path(_cache_path(), "file:cache"),
        lambda: _load_from_path(BUNDLED_PATH, "file:bundled"),
    ):
        try:
            snapshot = loader()
        except Exception as exc:  # defensive: a read must never break the page
            log.warning("enforcement_loader_failed", error=str(exc))
            snapshot = None
        if snapshot:
            break

    if not snapshot:
        snapshot = empty_snapshot()

    log.info(
        "enforcement_snapshot_loaded",
        source=snapshot.get("source"),
        total=snapshot.get("statistics", {}).get("total_all_sections", 0),
    )

    with _cache_lock:
        _cache["snapshot"] = snapshot
        _cache["expires_at"] = time.monotonic() + _CACHE_TTL_SECONDS
    return snapshot


def invalidate_cache() -> None:
    """Drop the in-process snapshot cache (called after every write)."""
    with _cache_lock:
        _cache["snapshot"] = None
        _cache["expires_at"] = 0.0


def all_actions(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every action across the core sections, newest first."""
    snap = snapshot or load_snapshot()
    merged: list[dict[str, Any]] = []
    for section in CORE_SECTIONS:
        merged.extend(snap.get(section) or [])
    return _sort_actions(merged)


def existing_source_urls(snapshot: dict[str, Any] | None = None) -> set[str]:
    """Lower-cased source URLs already tracked, for de-duplication."""
    snap = snapshot or load_snapshot(use_cache=False)
    urls: set[str] = set()
    for section in SECTIONS:
        for action in snap.get(section) or []:
            url = (action.get("source_url") or "").strip().lower()
            if url:
                urls.add(url)
    return urls


def next_action_id(snapshot: dict[str, Any], section: str) -> str:
    """Next sequential ID for a section, e.g. ``IND-DPDPA-019``."""
    prefix = ID_PREFIXES.get(section, "IND")
    highest = 0
    for action in snapshot.get(section) or []:
        action_id = str(action.get("id") or "")
        if not action_id.startswith(prefix):
            continue
        try:
            highest = max(highest, int(action_id.rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            continue
    return f"{prefix}-{highest + 1:03d}"


# ── Writes ───────────────────────────────────────────────────────────────────

def upsert_actions(actions: Iterable[dict[str, Any]], section: str | None = None) -> int:
    """Upsert actions into Supabase. Returns how many rows were written."""
    rows = [to_db_row(action, section) for action in actions]
    rows = [row for row in rows if row.get("id")]
    if not rows:
        return 0

    db, configured = _supabase()
    if not db or not configured:
        log.info("enforcement_upsert_skipped_no_supabase", rows=len(rows))
        return 0

    written = 0
    # PostgREST payloads stay small and well under statement limits in batches.
    for start in range(0, len(rows), 100):
        chunk = rows[start : start + 100]
        try:
            result = db.upsert_sync(TABLE, chunk, on_conflict="id")
            written += len(result) if result else len(chunk)
        except Exception as exc:
            log.error("enforcement_upsert_failed", error=str(exc), rows=len(chunk))
    invalidate_cache()
    return written


def save_metadata(metadata: dict[str, Any]) -> bool:
    """Persist tracker metadata into ``platform_stats``."""
    payload = {**DEFAULT_METADATA, **(metadata or {})}
    payload["last_updated"] = payload.get("last_updated") or _today()

    db, configured = _supabase()
    if not db or not configured:
        return False
    try:
        db.upsert_sync(
            META_TABLE,
            {"key": META_KEY, "value": payload, "updated_at": _now_iso()},
            on_conflict="key",
        )
        invalidate_cache()
        return True
    except Exception as exc:
        log.error("enforcement_metadata_write_failed", error=str(exc))
        return False


def save_snapshot_to_disk(snapshot: dict[str, Any]) -> bool:
    """Mirror the snapshot to the writable JSON cache (local + warm-lambda use)."""
    payload = {"metadata": snapshot.get("metadata", {}), "statistics": snapshot.get("statistics", {})}
    total = 0
    for section in SECTIONS:
        payload[section] = snapshot.get(section) or []
        total += len(payload[section])

    if total == 0:
        # The cache is read in preference to the bundled seed, so writing an
        # empty snapshot here would permanently blank the public page.
        log.warning("enforcement_disk_mirror_refused_empty_snapshot")
        return False

    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError as exc:
        # Read-only filesystems are expected on serverless; Supabase is the real store.
        log.info("enforcement_disk_mirror_skipped", error=str(exc))
        return False


def save_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Persist a whole snapshot (rows + metadata) and return a write report."""
    snapshot["statistics"] = compute_statistics(snapshot)
    metadata = {**DEFAULT_METADATA, **(snapshot.get("metadata") or {})}
    metadata["last_updated"] = _today()
    snapshot["metadata"] = metadata

    written = 0
    for section in SECTIONS:
        written += upsert_actions(snapshot.get(section) or [], section)

    report = {
        "rows_written": written,
        "metadata_saved": save_metadata(metadata),
        "disk_mirror": save_snapshot_to_disk(snapshot),
        "supabase_configured": _supabase()[1],
    }
    invalidate_cache()
    log.info("enforcement_snapshot_saved", **report)
    return report


def load_bundled_snapshot() -> dict[str, Any]:
    """The repository seed data, independent of Supabase. Used by the seeder."""
    return _load_from_path(BUNDLED_PATH, "file:bundled") or empty_snapshot("file:bundled-missing")


def seed_from_bundle(force: bool = False) -> dict[str, Any]:
    """Push the bundled JSON into Supabase.

    By default this is a no-op when the table already holds rows, so it is safe
    to call on every deploy. Pass ``force=True`` to re-apply the seed over the
    existing rows (matching IDs are updated in place, nothing is deleted).
    """
    db, configured = _supabase()
    if not db or not configured:
        return {"status": "skipped", "reason": "supabase_not_configured"}

    if not force:
        try:
            existing = db.select_sync(TABLE, select="id", limit=1)
        except Exception as exc:
            return {"status": "error", "reason": f"table_probe_failed: {exc}"}
        if existing:
            return {"status": "skipped", "reason": "table_already_populated"}

    snapshot = load_bundled_snapshot()
    report = save_snapshot(snapshot)
    return {"status": "seeded", **report}


# ── Verification gate ────────────────────────────────────────────────────────

_UNVERIFIED_MARKERS = ("[unconfirmed", "needs verification", "auto-detected")


def is_verified(action: dict[str, Any]) -> bool:
    """True when a row has been confirmed by a human and is safe to publish.

    Rows discovered by the Tavily sweep land in the tracker as placeholders with
    ``[Unconfirmed — ...]`` fields. They are genuine leads but they are not facts,
    so they stay off the public page until someone fills them in. The flags are
    checked first; the text markers are a fallback for rows written before the
    flags existed.
    """
    if action.get("needs_review") or action.get("auto_detected"):
        return False
    for field in ("company", "authority", "violation_type", "summary", "outcome"):
        value = (action.get(field) or "").lower()
        if any(marker in value for marker in _UNVERIFIED_MARKERS):
            return False
    return True


def public_snapshot(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """The snapshot as shown to the public: verified rows only.

    The enforcement tracker is the site's main backlink asset, so publishing
    scraped placeholders would be both an accuracy problem and an SEO problem
    (dozens of near-identical thin rows). Statistics are recomputed from the
    filtered rows so the headline counts match what a visitor can actually see.
    """
    snap = snapshot or load_snapshot()
    public: dict[str, Any] = {
        "metadata": dict(snap.get("metadata") or {}),
        "source": snap.get("source", "unknown"),
        "loaded_at": snap.get("loaded_at", _now_iso()),
    }
    for section in SECTIONS:
        public[section] = [a for a in (snap.get(section) or []) if is_verified(a)]
    public["statistics"] = compute_statistics(public)
    public["statistics"]["pending_review"] = sum(
        1 for section in SECTIONS for a in (snap.get(section) or []) if not is_verified(a)
    )
    return public


def review_queue(snapshot: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Unverified rows awaiting human confirmation, newest first (dashboard only)."""
    snap = snapshot or load_snapshot()
    pending: list[dict[str, Any]] = []
    for section in SECTIONS:
        pending.extend(a for a in (snap.get(section) or []) if not is_verified(a))
    return _sort_actions(pending)[:limit]


def mark_verified(action_id: str, updates: dict[str, Any] | None = None) -> bool:
    """Promote a reviewed row onto the public page.

    ``updates`` carries the corrected field values a reviewer supplied; the row
    is only cleared for publication once nothing unverified is left in it.
    """
    snapshot = load_snapshot(use_cache=False)
    for section in SECTIONS:
        for action in snapshot.get(section) or []:
            if action.get("id") != action_id:
                continue
            merged = {**action, **(updates or {})}
            merged["needs_review"] = False
            merged["auto_detected"] = False
            merged = normalize_action(merged, section)
            if not is_verified(merged):
                log.warning("enforcement_verify_rejected_placeholder_fields", id=action_id)
                return False
            upsert_actions([merged], section)
            save_snapshot_to_disk(load_snapshot(use_cache=False))
            return True
    log.warning("enforcement_verify_id_not_found", id=action_id)
    return False
