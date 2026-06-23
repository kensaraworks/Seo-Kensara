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
            log.debug("job_queue_db_ready", path=str(self.db_path))
        except sqlite3.Error as exc:
            log.error("job_queue_init_failed", path=str(self.db_path), error=str(exc))
            raise

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


# -----------------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict, deserialising JSON columns."""
    d = dict(row)
    for col in ("payload", "result"):
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (json.JSONDecodeError, TypeError):
                pass  # leave as raw string if not valid JSON
    return d


# -----------------------------------------------------------------------
# Singleton — import and use directly
# -----------------------------------------------------------------------

job_queue = JobQueue()
