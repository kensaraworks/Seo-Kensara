"""APScheduler wiring for long-running (non-serverless) deployments.

This is the single owner of scheduled work. ``src/main.py`` still has a
``main()`` that builds its own scheduler for standalone use, but the web process
must not run both — duplicate schedulers meant every job fired twice, so
``start.sh`` now launches uvicorn alone.

Every import in here is deliberately function-local: the module is only touched
when a real server process starts, so a serverless cold start never pays for the
scraping and LLM dependency tree.
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()

#: (job_id, "module:function", trigger kwargs, human name)
JOB_SPECS: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    ("news_scan", "src.main:run_news_scan", {"hour": 8, "minute": 0}, "Daily news scan"),
    ("regulatory_poll", "src.main:run_regulatory_poll", {"hour": "*/10", "minute": 0}, "Regulatory feed poll"),
    ("content_gap_check", "src.main:run_content_gap_check", {"hour": 7, "minute": 45}, "Daily content gap check"),
    (
        "competitor_intelligence",
        "src.agents.content_gap_analyzer:run_competitor_intelligence",
        {"day_of_week": "mon", "hour": 6, "minute": 0},
        "Weekly competitor intelligence",
    ),
    ("trending_monitor", "src.main:run_trending_monitors", {"hour": 6, "minute": 30}, "Daily trending monitor"),
    (
        "trending_monitor_weekly",
        "src.main:run_trending_monitor_weekly",
        {"day_of_week": "mon", "hour": 7, "minute": 0},
        "Weekly trending monitor",
    ),
    (
        "cluster_auto_queue",
        "src.agents.keyword_cluster_engine:run_cluster_gap_auto_queue",
        {"day_of_week": "mon", "hour": 6, "minute": 0},
        "Weekly keyword cluster auto-queue",
    ),
    (
        "geo_monitor_weekly",
        "src.main:run_geo_monitor_weekly",
        {"day_of_week": "tue", "hour": 7, "minute": 30},
        "Weekly GEO visibility monitor",
    ),
    (
        "entity_monitor_weekly",
        "src.main:run_entity_monitor_weekly",
        {"day_of_week": "wed", "hour": 7, "minute": 0},
        "Weekly entity visibility monitor",
    ),
    (
        "third_party_audit",
        "src.main:run_third_party_audit_monthly",
        {"day": 2, "hour": 5, "minute": 0},
        "Monthly third-party directory audit",
    ),
    (
        "llms_txt_update",
        "src.main:run_llms_txt_update",
        {"day": 1, "hour": 5, "minute": 0},
        "Monthly llms.txt generation",
    ),
    (
        "source_health_weekly",
        "src.main:run_source_health_weekly",
        {"day_of_week": "mon", "hour": 5, "minute": 30},
        "Weekly source rejection health analysis",
    ),
    (
        "seasonal_preload_daily",
        "src.main:run_seasonal_preload_daily",
        {"hour": 7, "minute": 15},
        "Daily seasonal enforcement window preload check",
    ),
    (
        "content_refresh",
        "src.agents.content_refresher:process_pending_refreshes",
        {"day_of_week": "sun", "hour": 8, "minute": 0},
        "Weekly content refresh queue drain",
    ),
    (
        "feedback_loop_monthly",
        "src.main:run_feedback_loop_monthly",
        {"day": 1, "hour": 4, "minute": 0},
        "Monthly content performance feedback loop",
    ),
    ("gsc_sync", "src.ui.scheduler:run_gsc_sync", {"day_of_week": "sun", "hour": 7, "minute": 0}, "Weekly GSC sync"),
    (
        "enforcement_tracker_update",
        "src.agents.enforcement_tracker:update_enforcement_tracker",
        {"day_of_week": "thu", "hour": 6, "minute": 0},
        "Weekly DPDPA enforcement tracker update",
    ),
)

TIMEZONE = "Asia/Kolkata"


def _sync_page_summaries_to_content_performance(page_summaries: list) -> int:
    """Write page-level GSC summaries into content_performance (schema-safe)."""
    import sqlite3
    from pathlib import Path

    from src.config import settings_database_path

    db_path = Path(settings_database_path)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
    except Exception as exc:
        log.warning("gsc_sync_db_unavailable", error=str(exc))
        return 0

    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(content_performance)").fetchall()}
        for statement in (
            "ALTER TABLE content_performance ADD COLUMN post_url TEXT",
            "ALTER TABLE content_performance ADD COLUMN avg_position_30d REAL DEFAULT 0.0",
            "ALTER TABLE content_performance ADD COLUMN avg_ctr_30d REAL DEFAULT 0.0",
            "ALTER TABLE content_performance ADD COLUMN top_query TEXT DEFAULT ''",
            "ALTER TABLE content_performance ADD COLUMN last_checked TEXT",
        ):
            column = statement.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if column in columns:
                continue
            try:
                conn.execute(statement)
                columns.add(column)
            except sqlite3.OperationalError:
                pass

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_post_url_unique ON content_performance(post_url)"
        )

        updated = 0
        for summary in page_summaries:
            page_url = getattr(summary, "page_url", "")
            if not page_url:
                continue
            conn.execute(
                """
                INSERT INTO content_performance
                    (keyword, post_url, impressions_30d, clicks_30d, avg_position_30d,
                     avg_ctr_30d, top_query, last_checked, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, date('now'), datetime('now'))
                ON CONFLICT(post_url) DO UPDATE SET
                    impressions_30d  = excluded.impressions_30d,
                    clicks_30d       = excluded.clicks_30d,
                    avg_position_30d = excluded.avg_position_30d,
                    avg_ctr_30d      = excluded.avg_ctr_30d,
                    top_query        = excluded.top_query,
                    last_checked     = date('now')
                """,
                (
                    page_url,
                    page_url,
                    getattr(summary, "impressions_30d", 0),
                    getattr(summary, "clicks_30d", 0),
                    getattr(summary, "avg_position_30d", 0.0),
                    getattr(summary, "avg_ctr_30d", 0.0),
                    getattr(summary, "top_query", ""),
                ),
            )
            updated += 1
        conn.commit()
        return updated
    except Exception as exc:
        log.warning("gsc_sync_page_summaries_failed", error=str(exc))
        return 0
    finally:
        conn.close()


async def run_gsc_sync() -> dict[str, Any]:
    """Pull 30-day Search Console performance into the local analytics tables."""
    from src.analytics.gsc_widgets import sync_gsc_query_data_to_db
    from src.analytics.search_console import gsc_client

    if not gsc_client.is_configured():
        log.warning("gsc_sync_skipped_not_configured")
        return {"status": "skipped", "reason": "gsc_not_configured"}

    log.info("gsc_sync_started")
    page_summaries = gsc_client.get_blog_performance_30d()
    updated_pages = _sync_page_summaries_to_content_performance(page_summaries)
    synced_queries = sync_gsc_query_data_to_db(gsc_client.get_query_performance_30d(max_queries=500))

    result = {
        "status": "ok",
        "pages": len(page_summaries),
        "pages_updated": updated_pages,
        "query_rows": synced_queries,
    }
    log.info("gsc_sync_completed", **result)
    return result


def _resolve(target: str):
    """Import ``module:function`` on demand."""
    import importlib

    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def _attach_listeners(scheduler) -> None:
    """Record every background run in job history, without breaking the job."""
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

    try:
        from src.ui.routers.schedule import record_job_execution
    except Exception as exc:
        log.warning("scheduler_history_listener_unavailable", error=str(exc))
        return

    def on_executed(event) -> None:
        payload = event.retval if isinstance(event.retval, dict) else {}
        try:
            record_job_execution(
                job_id=event.job_id,
                status="ok",
                item_count=payload.get("count", 0),
                duration_ms=payload.get("duration_ms", 0),
                triggered_by="auto",
                latest_news=payload.get("latest_news"),
            )
        except Exception as exc:
            log.warning("scheduler_history_write_failed", job_id=event.job_id, error=str(exc))

    def on_error(event) -> None:
        try:
            record_job_execution(
                job_id=event.job_id,
                status="error",
                error=str(event.exception) if event.exception else "Execution error",
                triggered_by="auto",
            )
        except Exception as exc:
            log.warning("scheduler_history_write_failed", job_id=event.job_id, error=str(exc))

    scheduler.add_listener(on_executed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(on_error, EVENT_JOB_ERROR)


def build_scheduler():
    """Create and start the scheduler. Returns None if it cannot be started.

    A job whose module fails to import (an optional dependency is missing) is
    skipped and logged; the remaining jobs still run.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        log.warning("scheduler_unavailable", error=str(exc))
        return None

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    registered, skipped = [], []

    for job_id, target, trigger_kwargs, name in JOB_SPECS:
        try:
            func = _resolve(target)
        except Exception as exc:
            skipped.append(job_id)
            log.warning("scheduler_job_skipped", job_id=job_id, target=target, error=str(exc))
            continue
        scheduler.add_job(
            func,
            CronTrigger(timezone=TIMEZONE, **trigger_kwargs),
            id=job_id,
            name=name,
            replace_existing=True,
        )
        registered.append(job_id)

    _attach_listeners(scheduler)

    try:
        scheduler.start()
    except Exception as exc:
        log.error("scheduler_start_failed", error=str(exc))
        return None

    log.info("scheduler_started", registered=registered, skipped=skipped)
    return scheduler
