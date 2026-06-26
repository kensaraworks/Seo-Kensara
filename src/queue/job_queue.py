"""SQLite-backed job queue for reliable background task tracking.

Provides production-grade job lifecycle management without requiring Redis or
any external broker. All state is persisted in a local SQLite database so
jobs survive process restarts.

Lifecycle:
    PENDING  → RUNNING → SUCCESS
                       → FAILED (if retry_count >= 3)
                       → RETRYING (if retry_count < 3)

The singleton `job_queue` at module level is the recommended entry point.
"""
import asyncio
import json
import re
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT    NOT NULL,
    job_name     TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    payload      TEXT             DEFAULT '{}',
    result       TEXT             DEFAULT '{}',
    error        TEXT,
    retry_count  INTEGER          DEFAULT 0,
    created_at   TEXT    NOT NULL,
    started_at   TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_job_id ON jobs(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);

CREATE TABLE IF NOT EXISTS feeds_catalog (
    feed_id          TEXT PRIMARY KEY,
    url              TEXT,
    type             TEXT,
    last_polled      TEXT,
    error_count      INTEGER DEFAULT 0,
    score_multiplier REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS stories_processed (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id           TEXT UNIQUE,
    source             TEXT,
    headline           TEXT,
    url                TEXT,
    score              INTEGER,
    intent_tag         TEXT,
    fingerprint_vector TEXT,
    processed_at       TEXT,
    action_taken       TEXT
);

CREATE INDEX IF NOT EXISTS idx_stories_processed_story_id ON stories_processed(story_id);
CREATE INDEX IF NOT EXISTS idx_stories_processed_url ON stories_processed(url);

CREATE TABLE IF NOT EXISTS keyword_clusters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id       TEXT NOT NULL,
    cluster_name     TEXT NOT NULL,
    keyword          TEXT NOT NULL UNIQUE,
    intent_type      TEXT,
    coverage_status  TEXT DEFAULT 'uncovered',
    current_rank     INTEGER,
    monthly_volume   INTEGER DEFAULT 0,
    difficulty_score TEXT,
    last_updated     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kc_cluster_id ON keyword_clusters(cluster_id);
CREATE INDEX IF NOT EXISTS idx_kc_keyword ON keyword_clusters(keyword);

CREATE TABLE IF NOT EXISTS content_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword          TEXT NOT NULL UNIQUE,
    intent_type      TEXT,
    cluster_id       TEXT,
    priority_score   REAL DEFAULT 0.0,
    paa_questions    TEXT,
    tier             INTEGER DEFAULT 2,
    content_type     TEXT DEFAULT 'tier2',
    source           TEXT DEFAULT 'cluster_gap',
    scheduled_for    TEXT,
    rank_position    INTEGER,
    zero_coverage    INTEGER DEFAULT 0,
    reason           TEXT DEFAULT '',
    status           TEXT DEFAULT 'queued',
    queued_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cq_status ON content_queue(status);

CREATE TABLE IF NOT EXISTS content_calendar_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    payload     TEXT DEFAULT '{}',
    status      TEXT DEFAULT 'open',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_cca_status ON content_calendar_alerts(status);

CREATE TABLE IF NOT EXISTS ai_visibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    engine TEXT NOT NULL,
    kensara_mentioned BOOLEAN,
    position_score INTEGER,
    sentiment TEXT,
    competitors_mentioned TEXT,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_vis_engine ON ai_visibility(engine);
CREATE INDEX IF NOT EXISTS idx_ai_vis_query ON ai_visibility(query);

CREATE TABLE IF NOT EXISTS linkedin_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    metrics TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    listing_status TEXT NOT NULL,
    profile_url TEXT,
    last_audited TEXT NOT NULL,
    completeness_score INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS unlinked_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    context_url TEXT NOT NULL,
    brand_term TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    outreach_status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS founder_brand_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    context TEXT NOT NULL,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_performance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword          TEXT NOT NULL UNIQUE,
    cluster_id       TEXT,
    intent_type      TEXT,
    word_count       INTEGER,
    h2_structure     TEXT,
    impressions_30d  INTEGER DEFAULT 0,
    clicks_30d       INTEGER DEFAULT 0,
    ranked_keywords  INTEGER DEFAULT 0,
    backlinks_found  INTEGER DEFAULT 0,
    performance_tag  TEXT DEFAULT 'pending',
    recorded_at      TEXT NOT NULL,
    evaluated_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_cp_performance_tag ON content_performance(performance_tag);
CREATE INDEX IF NOT EXISTS idx_cp_keyword ON content_performance(keyword);

CREATE TABLE IF NOT EXISTS source_rejection_stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT NOT NULL,
    story_id         TEXT,
    story_headline   TEXT,
    rejection_reason TEXT,
    rejected_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_srs_source ON source_rejection_stats(source);

CREATE TABLE IF NOT EXISTS seasonal_calendar (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    window_name     TEXT NOT NULL UNIQUE,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    theme_keywords  TEXT NOT NULL,
    preload_weeks   INTEGER DEFAULT 8,
    burst_count     INTEGER DEFAULT 6,
    triggered       INTEGER DEFAULT 0,
    triggered_at    TEXT
);

CREATE TABLE IF NOT EXISTS competitor_intel (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_domain TEXT NOT NULL,
    url               TEXT NOT NULL UNIQUE,
    title             TEXT,
    summary           TEXT,
    published_date    TEXT,
    primary_keyword   TEXT,
    word_count        INTEGER,
    crawl_date        TEXT NOT NULL,
    gap_flag          INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_competitor_intel_domain ON competitor_intel(competitor_domain);
CREATE INDEX IF NOT EXISTS idx_competitor_intel_crawl_date ON competitor_intel(crawl_date);

CREATE TABLE IF NOT EXISTS competitor_gaps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    topic             TEXT NOT NULL UNIQUE,
    competitor_count  INTEGER DEFAULT 0,
    first_seen        TEXT NOT NULL,
    kensara_coverage  TEXT DEFAULT 'none',
    priority_score    REAL DEFAULT 0.0,
    status            TEXT DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_competitor_gaps_priority ON competitor_gaps(priority_score);

CREATE TABLE IF NOT EXISTS competitor_rankings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword           TEXT NOT NULL,
    competitor_domain TEXT NOT NULL,
    position          INTEGER NOT NULL,
    date_checked      TEXT NOT NULL,
    week_change       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_competitor_rankings_lookup
    ON competitor_rankings(keyword, competitor_domain, date_checked);

CREATE TABLE IF NOT EXISTS competitor_backlink_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_domain TEXT NOT NULL,
    mention_count     INTEGER NOT NULL,
    date_checked      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_competitor_backlink_log_lookup
    ON competitor_backlink_log(competitor_domain, date_checked);

"""

_MAX_RETRIES = 3


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class JobQueue:
    """SQLite-backed persistent job queue."""

    def __init__(self, db_path: str = "drafts/.cache/jobs.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row_factory for dict-like access."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # WAL mode: allows concurrent readers while writer is active
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self) -> None:
        """Create the jobs table and indexes if they don't already exist."""
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                self._migrate_content_calendar_schema(conn)
                self._migrate_competitor_intel_schema(conn)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cq_scheduled_for ON content_queue(scheduled_for)"
                )
            log.debug("job_queue_db_ready", path=str(self.db_path))
        except sqlite3.Error as exc:
            log.error("job_queue_init_failed", path=str(self.db_path), error=str(exc))
            raise

    def _migrate_content_calendar_schema(self, conn: sqlite3.Connection) -> None:
        """Add Module 2.10 columns for existing SQLite databases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(content_queue)").fetchall()
        }
        additions = {
            "tier": "ALTER TABLE content_queue ADD COLUMN tier INTEGER DEFAULT 2",
            "content_type": "ALTER TABLE content_queue ADD COLUMN content_type TEXT DEFAULT 'tier2'",
            "source": "ALTER TABLE content_queue ADD COLUMN source TEXT DEFAULT 'cluster_gap'",
            "scheduled_for": "ALTER TABLE content_queue ADD COLUMN scheduled_for TEXT",
            "rank_position": "ALTER TABLE content_queue ADD COLUMN rank_position INTEGER",
            "zero_coverage": "ALTER TABLE content_queue ADD COLUMN zero_coverage INTEGER DEFAULT 0",
            "reason": "ALTER TABLE content_queue ADD COLUMN reason TEXT DEFAULT ''",
        }
        for column, statement in additions.items():
            if column not in columns:
                conn.execute(statement)

    def _migrate_competitor_intel_schema(self, conn: sqlite3.Connection) -> None:
        """Add newer competitor intelligence columns/tables to existing databases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(competitor_intel)").fetchall()
        }
        additions = {
            "summary": "ALTER TABLE competitor_intel ADD COLUMN summary TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                conn.execute(statement)

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def enqueue(self, job_id: str, job_name: str, payload: dict[str, Any] | None = None) -> int:
        """Insert a new job in PENDING state. Returns the autoincrement row id."""
        if payload is None:
            payload = {}
        now = self._now()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (job_id, job_name, status, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, job_name, JobStatus.PENDING, json.dumps(payload), now),
                )
                row_id = cursor.lastrowid
            log.info("job_enqueued", job_id=job_id, job_name=job_name, row_id=row_id)
            return row_id
        except sqlite3.Error as exc:
            log.error("job_enqueue_failed", job_id=job_id, error=str(exc))
            raise

    def start_job(self, job_id: str) -> None:
        """Transition job to RUNNING state, recording start time."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status=?, started_at=? WHERE job_id=?",
                    (JobStatus.RUNNING, now, job_id),
                )
            log.info("job_started", job_id=job_id)
        except sqlite3.Error as exc:
            log.error("job_start_failed", job_id=job_id, error=str(exc))
            raise

    def complete_job(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        """Transition job to SUCCESS state with an optional result payload."""
        if result is None:
            result = {}
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status=?, result=?, completed_at=? WHERE job_id=?",
                    (JobStatus.SUCCESS, json.dumps(result), now, job_id),
                )
            log.info("job_completed", job_id=job_id)
        except sqlite3.Error as exc:
            log.error("job_complete_failed", job_id=job_id, error=str(exc))
            raise

    def fail_job(self, job_id: str, error: str, retry_count: int = 0) -> None:
        """Record job failure. If retry_count < _MAX_RETRIES, set to RETRYING; else FAILED."""
        now = self._now()
        new_status = JobStatus.RETRYING if retry_count < _MAX_RETRIES else JobStatus.FAILED
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status=?, error=?, retry_count=?, completed_at=?
                    WHERE job_id=?
                    """,
                    (new_status, error, retry_count, now, job_id),
                )
            log.warning(
                "job_failed",
                job_id=job_id,
                new_status=new_status,
                retry_count=retry_count,
                error=error[:200],
            )
        except sqlite3.Error as exc:
            log.error("job_fail_update_failed", job_id=job_id, error=str(exc))
            raise

    def record_linkedin_metric(self, entity: str, metrics: str | dict, recorded_at: str | None = None) -> None:
        """Record LinkedIn metrics for an entity.
        `metrics` can be a JSON string or a dict which will be serialized.
        """
        ts = recorded_at or self._now()
        if isinstance(metrics, dict):
            metrics_json = json.dumps(metrics)
        else:
            metrics_json = metrics
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO linkedin_metrics (entity, metrics, recorded_at) VALUES (?, ?, ?)",
                    (entity, metrics_json, ts),
                )
            log.info("linkedin_metric_recorded", entity=entity)
        except sqlite3.Error as exc:
            log.error("linkedin_metric_record_failed", entity=entity, error=str(exc))
            raise

    # ------------------------------------------------------------------ #
    #  Competitor Intelligence Methods                                   #
    # ------------------------------------------------------------------ #

    def record_competitor_intel(
        self,
        domain: str,
        url: str,
        title: str,
        pub_date: str,
        keyword: str,
        word_count: int,
        summary: str = "",
        gap_flag: bool = False,
    ) -> None:
        """Upsert one competitor content record captured during crawl."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO competitor_intel
                    (competitor_domain, url, title, summary, published_date, primary_keyword,
                     word_count, crawl_date, gap_flag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        competitor_domain=excluded.competitor_domain,
                        title=excluded.title,
                        summary=excluded.summary,
                        published_date=excluded.published_date,
                        primary_keyword=excluded.primary_keyword,
                        word_count=excluded.word_count,
                        crawl_date=excluded.crawl_date,
                        gap_flag=excluded.gap_flag
                    """,
                    (
                        domain,
                        url,
                        title,
                        summary,
                        pub_date,
                        keyword,
                        word_count,
                        self._now(),
                        int(gap_flag),
                    ),
                )
            log.debug("competitor_intel_recorded", domain=domain, url=url)
        except sqlite3.Error as exc:
            log.error("competitor_intel_record_failed", domain=domain, url=url, error=str(exc))
            raise

    def get_recent_competitor_intel(self, days: int = 14) -> list[dict[str, Any]]:
        """Return competitor intel rows crawled in the last N days."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM competitor_intel
                    WHERE crawl_date >= datetime('now', ?)
                    ORDER BY crawl_date DESC
                    """,
                    (f"-{max(1, days)} days",),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("competitor_intel_recent_fetch_failed", error=str(exc))
            return []

    def upsert_competitor_gap(
        self,
        topic: str,
        competitor_count: int,
        kensara_coverage: str,
        priority_score: float,
    ) -> None:
        """Persist one competitor gap topic with latest priority values."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO competitor_gaps
                    (topic, competitor_count, first_seen, kensara_coverage, priority_score, status)
                    VALUES (?, ?, ?, ?, ?, 'open')
                    ON CONFLICT(topic) DO UPDATE SET
                        competitor_count=excluded.competitor_count,
                        kensara_coverage=excluded.kensara_coverage,
                        priority_score=excluded.priority_score,
                        status='open'
                    """,
                    (topic, competitor_count, now, kensara_coverage, priority_score),
                )
        except sqlite3.Error as exc:
            log.error("competitor_gap_upsert_failed", topic=topic, error=str(exc))
            raise

    def get_top_competitor_gaps(self, limit: int = 3) -> list[dict[str, Any]]:
        """Return highest priority open competitor gaps."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM competitor_gaps
                    WHERE status = 'open'
                    ORDER BY priority_score DESC, id ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("competitor_gap_top_fetch_failed", error=str(exc))
            return []

    def record_competitor_ranking(
        self,
        keyword: str,
        competitor_domain: str,
        position: int,
    ) -> int:
        """Record competitor rank and return position change.

        Positive change means competitor improved (moved up).

        Baseline preference:
        1) Most recent row at or before 7 days ago (week-over-week)
        2) If unavailable, most recent prior row (run-over-run)
        """
        now = self._now()
        week_change = 0
        try:
            with self._connect() as conn:
                previous = conn.execute(
                    """
                    SELECT position
                    FROM competitor_rankings
                    WHERE keyword=? AND competitor_domain=? AND date_checked <= datetime('now', '-7 days')
                    ORDER BY date_checked DESC
                    LIMIT 1
                    """,
                    (keyword, competitor_domain),
                ).fetchone()
                if not previous:
                    previous = conn.execute(
                        """
                        SELECT position
                        FROM competitor_rankings
                        WHERE keyword=? AND competitor_domain=?
                        ORDER BY date_checked DESC
                        LIMIT 1
                        """,
                        (keyword, competitor_domain),
                    ).fetchone()
                if previous and previous["position"]:
                    week_change = int(previous["position"]) - int(position)

                conn.execute(
                    """
                    INSERT INTO competitor_rankings
                    (keyword, competitor_domain, position, date_checked, week_change)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (keyword, competitor_domain, int(position), now, int(week_change)),
                )
            return int(week_change)
        except sqlite3.Error as exc:
            log.error(
                "competitor_ranking_record_failed",
                keyword=keyword,
                competitor_domain=competitor_domain,
                error=str(exc),
            )
            return 0

    def get_previous_backlink_count(self, competitor_domain: str, days_ago: int = 7) -> int:
        """Return most recent backlink mention count at or before N days ago."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT mention_count
                    FROM competitor_backlink_log
                    WHERE competitor_domain=? AND date_checked <= datetime('now', ?)
                    ORDER BY date_checked DESC
                    LIMIT 1
                    """,
                    (competitor_domain, f"-{max(1, days_ago)} days"),
                ).fetchone()
                return int(row["mention_count"]) if row else 0
        except sqlite3.Error as exc:
            log.error("competitor_backlink_previous_fetch_failed", domain=competitor_domain, error=str(exc))
            return 0

    def record_competitor_backlink_count(self, competitor_domain: str, mention_count: int) -> None:
        """Persist weekly backlink mention proxy count for a competitor domain."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO competitor_backlink_log
                    (competitor_domain, mention_count, date_checked)
                    VALUES (?, ?, ?)
                    """,
                    (competitor_domain, int(mention_count), self._now()),
                )
        except sqlite3.Error as exc:
            log.error("competitor_backlink_record_failed", domain=competitor_domain, error=str(exc))
            raise

    def is_story_processed(self, story_id: str) -> bool:
        """Check if a story has already been processed by its ID."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM stories_processed WHERE story_id = ?",
                    (story_id,),
                ).fetchone()
                return row is not None
        except sqlite3.Error as exc:
            log.error("db_check_story_processed_failed", story_id=story_id, error=str(exc))
            return False

    def is_url_processed(self, url: str) -> bool:
        """Check if a story has already been processed by its URL."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM stories_processed WHERE url = ?",
                    (url.strip().lower(),),
                ).fetchone()
                return row is not None
        except sqlite3.Error as exc:
            log.error("db_check_url_processed_failed", url=url, error=str(exc))
            return False

    def record_processed_story(
        self,
        story_id: str,
        source: str,
        headline: str,
        url: str,
        score: int,
        intent_tag: str = "",
        fingerprint_vector: str = "",
        action_taken: str = "scanned",
    ) -> None:
        """Record a newly processed story to prevent reprocessing."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stories_processed
                    (story_id, source, headline, url, score, intent_tag, fingerprint_vector, processed_at, action_taken)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        story_id,
                        source,
                        headline,
                        url.strip().lower(),
                        score,
                        intent_tag,
                        fingerprint_vector,
                        now,
                        action_taken,
                    ),
                )
            log.debug("db_story_recorded", story_id=story_id, headline=headline[:50])
        except sqlite3.Error as exc:
            log.error("db_record_story_failed", story_id=story_id, error=str(exc))
            raise

    def get_recent_processed_fingerprints(self, limit: int = 200) -> list[tuple[str, str]]:
        """Retrieve recent processed story IDs and their fingerprint vectors."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT story_id, fingerprint_vector FROM stories_processed ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [(r["story_id"], r["fingerprint_vector"]) for r in rows if r["fingerprint_vector"]]
        except sqlite3.Error as exc:
            log.error("db_get_fingerprints_failed", error=str(exc))
            return []

    def get_feed_catalog(self) -> list[dict[str, Any]]:
        """Retrieve all feeds in the catalog."""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM feeds_catalog").fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("db_get_feeds_failed", error=str(exc))
            return []

    def update_feed_poll_time(self, feed_id: str, error_occurred: bool = False) -> None:
        """Update last polled time and error counts for a feed in the catalog."""
        now = self._now()
        try:
            with self._connect() as conn:
                if error_occurred:
                    conn.execute(
                        """
                        UPDATE feeds_catalog
                        SET last_polled = ?, error_count = error_count + 1
                        WHERE feed_id = ?
                        """,
                        (now, feed_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE feeds_catalog
                        SET last_polled = ?, error_count = 0
                        WHERE feed_id = ?
                        """,
                        (now, feed_id),
                    )
        except sqlite3.Error as exc:
            log.error("db_update_feed_failed", feed_id=feed_id, error=str(exc))

    def get_recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent jobs, newest first. Used by the UI dashboard."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.error("job_get_recent_failed", error=str(exc))
            return []

    def get_failed_jobs(self) -> list[dict[str, Any]]:
        """Return all permanently-failed jobs for alert display."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY id DESC",
                    (JobStatus.FAILED,),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.error("job_get_failed_failed", error=str(exc))
            return []

    async def retry_failed_jobs(self) -> int:
        """Re-queue all jobs with status=retrying. Returns the count of jobs retried."""
        # Run the blocking SQLite call in a thread pool to avoid blocking the event loop
        return await asyncio.to_thread(self._retry_failed_jobs_sync)

    def _retry_failed_jobs_sync(self) -> int:
        """Synchronous implementation of retry logic — called via asyncio.to_thread."""
        retried = 0
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=?",
                    (JobStatus.RETRYING,),
                ).fetchall()

                for row in rows:
                    job_id = row["job_id"]
                    retry_count = row["retry_count"]
                    if retry_count >= _MAX_RETRIES:
                        # Exceeded retries — mark permanently failed
                        conn.execute(
                            "UPDATE jobs SET status=? WHERE job_id=?",
                            (JobStatus.FAILED, job_id),
                        )
                        log.warning("job_retry_exhausted", job_id=job_id, retry_count=retry_count)
                    else:
                        # Reset to PENDING so the scheduler picks it up again
                        conn.execute(
                            "UPDATE jobs SET status=?, started_at=NULL WHERE job_id=?",
                            (JobStatus.PENDING, job_id),
                        )
                        retried += 1
                        log.info("job_requeued", job_id=job_id, retry_count=retry_count)

        except sqlite3.Error as exc:
            log.error("job_retry_failed", error=str(exc))

        return retried

    # ------------------------------------------------------------------ #
    #  Keyword Cluster Engine Methods                                    #
    # ------------------------------------------------------------------ #

    def upsert_keyword_cluster(self, cluster_id: str, cluster_name: str, keyword: str, intent_type: str = "") -> None:
        """Insert or update a keyword in the cluster engine."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO keyword_clusters
                    (cluster_id, cluster_name, keyword, intent_type, difficulty_score, last_updated)
                    VALUES (?, ?, ?, ?, 'unknown', ?)
                    ON CONFLICT(keyword) DO UPDATE SET
                    cluster_id=excluded.cluster_id,
                    cluster_name=excluded.cluster_name,
                    intent_type=excluded.intent_type,
                    difficulty_score='unknown',
                    last_updated=excluded.last_updated
                    """,
                    (cluster_id, cluster_name, keyword, intent_type, now),
                )
        except sqlite3.Error as exc:
            log.error("db_upsert_keyword_cluster_failed", keyword=keyword, error=str(exc))

    def update_keyword_coverage(self, keyword: str, coverage_status: str) -> None:
        """Update the coverage status (e.g. 'uncovered', 'draft', 'published') for a keyword."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE keyword_clusters SET coverage_status=?, last_updated=? WHERE keyword=?",
                    (coverage_status, now, keyword),
                )
        except sqlite3.Error as exc:
            log.error("db_update_keyword_coverage_failed", keyword=keyword, error=str(exc))

    def get_keyword_coverage(self, keyword: str) -> str:
        """Get the coverage status of a keyword ('uncovered', 'draft', 'published', or 'none')."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT coverage_status FROM keyword_clusters WHERE keyword=?",
                    (keyword,)
                ).fetchone()
                return row["coverage_status"] if row else "none"
        except sqlite3.Error as exc:
            log.error("db_get_keyword_coverage_failed", keyword=keyword, error=str(exc))
            return "none"

    def get_cluster_stats(self) -> dict[str, dict[str, int]]:
        """Returns stats for each cluster: total keywords, published, ranking."""
        stats: dict[str, dict[str, int]] = {}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT cluster_id, coverage_status, current_rank 
                    FROM keyword_clusters
                    """
                ).fetchall()
                for row in rows:
                    cid = row["cluster_id"]
                    if cid not in stats:
                        stats[cid] = {"total": 0, "published": 0, "ranking": 0}
                    stats[cid]["total"] += 1
                    if row["coverage_status"] == "published":
                        stats[cid]["published"] += 1
                    if row["current_rank"] is not None and row["current_rank"] <= 10:
                        stats[cid]["ranking"] += 1
            return stats
        except sqlite3.Error as exc:
            log.error("db_get_cluster_stats_failed", error=str(exc))
            return {}

    def get_underserved_keywords(self, cluster_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """Get top uncovered keywords for a cluster."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM keyword_clusters 
                    WHERE cluster_id = ? AND coverage_status = 'uncovered'
                    LIMIT ?
                    """,
                    (cluster_id, limit),
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.error("db_get_underserved_keywords_failed", cluster_id=cluster_id, error=str(exc))
            return []

    def enqueue_content(
        self,
        keyword: str,
        intent_type: str,
        cluster_id: str,
        priority_score: float,
        paa_questions: list[str],
        *,
        tier: int = 2,
        content_type: str = "tier2",
        source: str = "cluster_gap",
        scheduled_for: str | None = None,
        rank_position: int | None = None,
        zero_coverage: bool = False,
        reason: str = "",
    ) -> None:
        """Enqueue a keyword for generation, carrying Module 2.10 metadata."""
        now = self._now()
        paa_json = json.dumps(paa_questions)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO content_queue
                    (keyword, intent_type, cluster_id, priority_score, paa_questions,
                     tier, content_type, source, scheduled_for, rank_position,
                     zero_coverage, reason, status, queued_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    ON CONFLICT(keyword) DO UPDATE SET
                    priority_score=excluded.priority_score,
                    paa_questions=excluded.paa_questions,
                    tier=excluded.tier,
                    content_type=excluded.content_type,
                    source=excluded.source,
                    scheduled_for=excluded.scheduled_for,
                    rank_position=excluded.rank_position,
                    zero_coverage=excluded.zero_coverage,
                    reason=excluded.reason,
                    status='queued',
                    queued_at=excluded.queued_at
                    """,
                    (
                        keyword,
                        intent_type,
                        cluster_id,
                        priority_score,
                        paa_json,
                        tier,
                        content_type,
                        source,
                        scheduled_for,
                        rank_position,
                        int(zero_coverage),
                        reason,
                        now,
                    ),
                )
        except sqlite3.Error as exc:
            log.error("db_enqueue_content_failed", keyword=keyword, error=str(exc))

    def pop_content_queue(self) -> dict[str, Any] | None:
        """Pop the highest priority due keyword from the content queue."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM content_queue 
                    WHERE status = 'queued'
                      AND (scheduled_for IS NULL OR scheduled_for <= date('now'))
                    ORDER BY
                        CASE
                            WHEN content_type IN ('tier3', 'newsjack', 'tier3_newsjack') THEN 1
                            WHEN rank_position BETWEEN 8 AND 20 THEN 2
                            WHEN zero_coverage = 1 THEN 3
                            WHEN content_type LIKE '%pillar%' THEN 4
                            WHEN content_type IN ('tier2', 'supporting_cluster') THEN 5
                            WHEN content_type LIKE '%refresh%' THEN 6
                            ELSE 7
                        END ASC,
                        priority_score DESC,
                        id ASC
                    LIMIT 1
                    """
                ).fetchone()
                
                if row:
                    job = _row_to_dict(row)
                    conn.execute(
                        "UPDATE content_queue SET status='processing' WHERE id=?",
                        (job["id"],),
                    )
                    return job
                return None
        except sqlite3.Error as exc:
            log.error("db_pop_content_queue_failed", error=str(exc))
            return None

    def mark_content_completed(self, keyword: str) -> None:
        """Mark a keyword as processed in the content queue and update cluster coverage."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE content_queue SET status='completed' WHERE keyword=?",
                    (keyword,)
                )
                conn.execute(
                    "UPDATE keyword_clusters SET coverage_status='published' WHERE keyword=?",
                    (keyword,)
                )
        except sqlite3.Error as exc:
            log.error("db_mark_content_completed_failed", keyword=keyword, error=str(exc))

    def update_link_map(self, post_id: str, updated_content: str) -> None:
        """Refresh outgoing link counts for an updated post.

        `post_id` may be either the internal link-map id or the post URL. The
        refreshed count is derived from Markdown links in the updated content.
        """
        from src.engines.internal_linker import get_connection

        links = set(re_match.group(1) for re_match in re.finditer(r"\]\((https?://[^)]+)\)", updated_content))
        try:
            with get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT post_id, post_url
                    FROM internal_link_map
                    WHERE post_id = ? OR post_url = ?
                    LIMIT 1
                    """,
                    (post_id, post_id),
                ).fetchone()
                if not row:
                    return

                conn.execute(
                    """
                    UPDATE internal_link_map
                    SET outgoing_link_count = ?, date_updated = ?
                    WHERE post_id = ?
                    """,
                    (len(links), self._now(), row["post_id"]),
                )
            log.info("link_map_refreshed", post_id=post_id, outgoing_links=len(links))
        except sqlite3.Error as exc:
            log.error("link_map_refresh_failed", post_id=post_id, error=str(exc))

    def count_content_queue(self, statuses: tuple[str, ...] = ("queued", "processing")) -> int:
        """Count generation queue items in the provided statuses."""
        placeholders = ",".join("?" for _ in statuses)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM content_queue WHERE status IN ({placeholders})",
                    statuses,
                ).fetchone()
                return int(row["cnt"] if row else 0)
        except sqlite3.Error as exc:
            log.error("db_count_content_queue_failed", error=str(exc))
            return 0

    def get_content_queue_items(self, statuses: tuple[str, ...] = ("queued", "processing")) -> list[dict[str, Any]]:
        """Return generation queue rows ordered by Module 2.10 priority."""
        placeholders = ",".join("?" for _ in statuses)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT * FROM content_queue
                    WHERE status IN ({placeholders})
                    ORDER BY
                        CASE
                            WHEN content_type IN ('tier3', 'newsjack', 'tier3_newsjack') THEN 1
                            WHEN rank_position BETWEEN 8 AND 20 THEN 2
                            WHEN zero_coverage = 1 THEN 3
                            WHEN content_type LIKE '%pillar%' THEN 4
                            WHEN content_type IN ('tier2', 'supporting_cluster') THEN 5
                            WHEN content_type LIKE '%refresh%' THEN 6
                            ELSE 7
                        END ASC,
                        priority_score DESC,
                        id ASC
                    """,
                    statuses,
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.error("db_get_content_queue_items_failed", error=str(exc))
            return []

    def record_content_calendar_alert(self, alert_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        """Persist a Module 2.10 CEO alert for dashboard/API display."""
        payload = payload or {}
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO content_calendar_alerts
                    (alert_type, message, payload, status, created_at)
                    VALUES (?, ?, ?, 'open', ?)
                    """,
                    (alert_type, message, json.dumps(payload), self._now()),
                )
            log.warning("content_calendar_alert_recorded", alert_type=alert_type, message=message)
        except sqlite3.Error as exc:
            log.error("db_record_content_calendar_alert_failed", alert_type=alert_type, error=str(exc))

    def get_open_content_calendar_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return open Module 2.10 alerts, newest first."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM content_calendar_alerts
                    WHERE status = 'open'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.error("db_get_content_calendar_alerts_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------ #
    #  Module-level helpers (class utility methods)                       #
    # ------------------------------------------------------------------ #

    def record_ai_citation(self, query: str, engine: str, kensara_mentioned: bool, position_score: int, sentiment: str, competitors_mentioned: list[str]) -> None:

        """Record AI citation metrics for week-over-week tracking."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility 
                    (query, engine, kensara_mentioned, position_score, sentiment, competitors_mentioned, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        query,
                        engine,
                        kensara_mentioned,
                        position_score,
                        sentiment,
                        json.dumps(competitors_mentioned),
                        self._now()
                    )
                )
                conn.commit()
            log.info("ai_citation_recorded", query=query, engine=engine)
        except sqlite3.Error as exc:
            log.error("ai_citation_record_failed", query=query, engine=engine, error=str(exc))

    def record_entity_status(self, platform: str, listing_status: str, profile_url: str = "", completeness_score: int = 0) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO entity_status (platform, listing_status, profile_url, completeness_score, last_audited)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (platform, listing_status, profile_url, completeness_score, self._now())
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.error("entity_status_record_failed", platform=platform, error=str(exc))

    def record_unlinked_mention(self, domain: str, context_url: str, brand_term: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO unlinked_mentions (domain, context_url, brand_term, discovered_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (domain, context_url, brand_term, self._now())
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.error("unlinked_mention_record_failed", url=context_url, error=str(exc))

    def record_founder_mention(self, source_url: str, context: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO founder_brand_mentions (source_url, context, discovered_at)
                    VALUES (?, ?, ?)
                    """,
                    (source_url, context, self._now())
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.error("founder_mention_record_failed", url=source_url, error=str(exc))

    # ------------------------------------------------------------------ #
    #  Module 1.8 — Intelligence Feedback Loop Methods                   #
    # ------------------------------------------------------------------ #

    def record_content_performance(
        self,
        keyword: str,
        cluster_id: str = "",
        intent_type: str = "",
        word_count: int = 0,
        h2_structure: list[str] | None = None,
        impressions_30d: int = 0,
        clicks_30d: int = 0,
        ranked_keywords: int = 0,
        backlinks_found: int = 0,
        performance_tag: str = "pending",
    ) -> None:
        """Record 30-day performance data for a published post (1.8.A)."""
        now = self._now()
        h2_json = json.dumps(h2_structure or [])
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO content_performance
                    (keyword, cluster_id, intent_type, word_count, h2_structure,
                     impressions_30d, clicks_30d, ranked_keywords, backlinks_found,
                     performance_tag, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(keyword) DO UPDATE SET
                        impressions_30d=excluded.impressions_30d,
                        clicks_30d=excluded.clicks_30d,
                        ranked_keywords=excluded.ranked_keywords,
                        backlinks_found=excluded.backlinks_found,
                        performance_tag=excluded.performance_tag,
                        recorded_at=excluded.recorded_at
                    """,
                    (
                        keyword, cluster_id, intent_type, word_count, h2_json,
                        impressions_30d, clicks_30d, ranked_keywords, backlinks_found,
                        performance_tag, now,
                    ),
                )
            log.info("content_performance_recorded", keyword=keyword[:50], tag=performance_tag)
        except sqlite3.Error as exc:
            log.error("content_performance_record_failed", keyword=keyword, error=str(exc))
            raise

    def update_performance_tag(self, keyword: str, tag: str) -> None:
        """Update the performance classification tag for a keyword (1.8.A)."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE content_performance SET performance_tag=?, evaluated_at=? WHERE keyword=?",
                    (tag, now, keyword),
                )
            log.info("performance_tag_updated", keyword=keyword[:50], tag=tag)
        except sqlite3.Error as exc:
            log.error("performance_tag_update_failed", keyword=keyword, error=str(exc))

    def get_pending_performance_reviews(self) -> list[dict[str, Any]]:
        """Return content_performance rows with tag='pending' for evaluation (1.8.A)."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM content_performance WHERE performance_tag = 'pending'"
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("get_pending_performance_failed", error=str(exc))
            return []

    def get_winning_templates(self, cluster_id: str | None = None) -> list[dict[str, Any]]:
        """Return winner posts, optionally filtered by cluster, for template extraction (1.8.A)."""
        try:
            with self._connect() as conn:
                if cluster_id:
                    rows = conn.execute(
                        "SELECT * FROM content_performance WHERE performance_tag='winner' AND cluster_id=?",
                        (cluster_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM content_performance WHERE performance_tag='winner'"
                    ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("get_winning_templates_failed", error=str(exc))
            return []

    def record_story_rejection(
        self, source: str, story_id: str, headline: str, rejection_reason: str = ""
    ) -> None:
        """Record a human-rejected story for source health tracking (1.8.B)."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO source_rejection_stats (source, story_id, story_headline, rejection_reason, rejected_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source, story_id, headline, rejection_reason, now),
                )
            log.info("story_rejection_recorded", source=source, story_id=story_id)
        except sqlite3.Error as exc:
            log.error("story_rejection_record_failed", source=source, error=str(exc))

    def get_source_rejection_rate(self, source: str) -> dict[str, Any]:
        """Return total stories processed + rejections + rate for a source (1.8.B)."""
        try:
            with self._connect() as conn:
                # Total processed from stories_processed
                total_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM stories_processed WHERE source=?", (source,)
                ).fetchone()
                total = total_row["cnt"] if total_row else 0

                # Total rejections
                rejected_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM source_rejection_stats WHERE source=?", (source,)
                ).fetchone()
                rejected = rejected_row["cnt"] if rejected_row else 0

                rate = (rejected / total) if total > 0 else 0.0
                return {"source": source, "total": total, "rejected": rejected, "rate": rate}
        except sqlite3.Error as exc:
            log.error("get_source_rejection_rate_failed", source=source, error=str(exc))
            return {"source": source, "total": 0, "rejected": 0, "rate": 0.0}

    def get_all_sources_with_rejections(self) -> list[str]:
        """Return distinct sources that appear in rejection stats (1.8.B)."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT source FROM source_rejection_stats"
                ).fetchall()
                return [r["source"] for r in rows]
        except sqlite3.Error as exc:
            log.error("get_all_sources_with_rejections_failed", error=str(exc))
            return []

    def downgrade_feed_score_multiplier(
        self, feed_id: str, amount: float = 1.0, min_value: float = 0.5
    ) -> None:
        """Reduce the score_multiplier of a feed in feeds_catalog (1.8.B)."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE feeds_catalog
                    SET score_multiplier = MAX(?, score_multiplier - ?)
                    WHERE feed_id = ?
                    """,
                    (min_value, amount, feed_id),
                )
            log.info("feed_score_multiplier_downgraded", feed_id=feed_id, amount=amount)
        except sqlite3.Error as exc:
            log.error("feed_downgrade_failed", feed_id=feed_id, error=str(exc))

    def seed_seasonal_calendar(self, windows: list[dict]) -> None:
        """Seed the seasonal calendar with enforcement window definitions (1.8.C).
        Uses INSERT OR IGNORE so it's safe to call on every startup.
        """
        try:
            with self._connect() as conn:
                for w in windows:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO seasonal_calendar
                        (window_name, window_start, window_end, theme_keywords, preload_weeks, burst_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            w["window_name"],
                            w["window_start"],
                            w["window_end"],
                            json.dumps(w["theme_keywords"]),
                            w.get("preload_weeks", 8),
                            w.get("burst_count", 6),
                        ),
                    )
            log.info("seasonal_calendar_seeded", count=len(windows))
        except sqlite3.Error as exc:
            log.error("seasonal_calendar_seed_failed", error=str(exc))

    def get_untriggered_seasonal_windows(self, days_ahead: int = 56) -> list[dict[str, Any]]:
        """Return windows whose preload date has arrived but haven't been triggered (1.8.C).
        Preload date = window_start - preload_weeks weeks.
        """
        from datetime import date, timedelta
        today = date.today().isoformat()
        cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM seasonal_calendar
                    WHERE triggered = 0
                      AND window_start <= ?
                    """,
                    (cutoff,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.error("get_untriggered_windows_failed", error=str(exc))
            return []

    def mark_seasonal_window_triggered(self, window_id: int) -> None:
        """Mark a seasonal window as triggered so it won't fire again (1.8.C)."""
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE seasonal_calendar SET triggered=1, triggered_at=? WHERE id=?",
                    (now, window_id),
                )
            log.info("seasonal_window_triggered", window_id=window_id)
        except sqlite3.Error as exc:
            log.error("mark_seasonal_window_failed", window_id=window_id, error=str(exc))

job_queue = JobQueue()


# -----------------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict, deserialising JSON columns."""
    d = dict(row)
    for col in ("payload", "result", "paa_questions"):
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (json.JSONDecodeError, TypeError):
                pass  # leave as raw string if not valid JSON
    return d
