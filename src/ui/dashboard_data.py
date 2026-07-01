"""Dashboard data helpers — all backend data-fetching for the main dashboard.

Every function in this module is synchronous (no async) so the dashboard route
can call them directly without awaiting. Each function degrades gracefully when
the underlying data source (DB, JSON file, etc.) is not yet populated.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import settings, settings_database_path, settings_enforcement_tracker_path

log = structlog.get_logger()

_DB_PATH = Path(settings_database_path)
_REPORTS_DIR = Path(settings.content_output_dir) / "reports"
_RANKINGS_DIR = _REPORTS_DIR / "rankings"
_ENFORCEMENT_TRACKER = Path(settings_enforcement_tracker_path)
_MONDAY_BRIEF = _REPORTS_DIR / "monday-brief.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _db_connect() -> sqlite3.Connection | None:
    """Return a read-only connection to jobs.db, or None if it doesn't exist."""
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> Any:
    """Read a JSON file, return None on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Section 1 — Content pipeline counters (already in app.py, kept here too)
# ---------------------------------------------------------------------------

def get_gsc_stat_cards(gsc_summary: dict) -> list[dict]:
    """Return 7-day GSC metrics as stat cards if data is available."""
    if not gsc_summary or not gsc_summary.get("data_available"):
        return []
    return [
        {
            "value": gsc_summary.get("total_clicks_7d", 0),
            "label": "Clicks (7d)",
            "sub": "Google Search Console",
            "color": "#34d399",
        },
        {
            "value": gsc_summary.get("total_impressions_7d", 0),
            "label": "Impressions (7d)",
            "sub": "Google Search Console",
            "color": "#60a5fa",
        },
        {
            "value": f"{gsc_summary.get('avg_ctr_7d', 0)}%",
            "label": "Avg CTR (7d)",
            "sub": "Google Search Console",
            "color": "#a78bfa",
        },
        {
            "value": round(gsc_summary.get("avg_position_7d", 0), 1) if gsc_summary.get("avg_position_7d") else "—",
            "label": "Avg Position (7d)",
            "sub": "Google Search Console",
            "color": "#f472b6",
        },
    ]


# ---------------------------------------------------------------------------
# Section 2 — Intelligence feed: scored news with blog angles
# ---------------------------------------------------------------------------

def get_scored_news_feed(job_history: dict, limit: int = 6) -> list[dict]:
    """Return the latest scored news items from job_history, with blog angles."""
    raw = job_history.get("latest_news", [])
    results = []
    for item in raw[:limit]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "score": item.get("score", item.get("relevance_score", 0)),
                "angle": item.get("suggested_angle", ""),
                "is_india": item.get("is_india_source", False),
            }
        )
    return results


def get_recent_relevant_news(limit: int = 200, days: int = 7) -> list[dict]:
    """Return all relevant scanned stories from stories_processed for tracker view."""
    conn = _db_connect()
    if conn is None:
        return []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "stories_processed" not in tables:
            return []

        rows = conn.execute(
            """
            SELECT headline, url, source, score, intent_tag, processed_at
            FROM stories_processed
            WHERE action_taken = 'scanned'
              AND score >= 6
                            AND datetime(replace(substr(processed_at, 1, 19), 'T', ' ')) >= datetime('now', ?)
            ORDER BY processed_at DESC, score DESC
            LIMIT ?
            """,
            (f"-{days} days", limit),
        ).fetchall()

        results = []
        for r in rows:
            source = r["source"] or ""
            source_l = source.lower()
            is_india = any(
                k in source_l for k in (
                    "meity", "dpbi", "cert-in", "rbi", "sebi", "irdai",
                    "yourstory", "inc42", "entrackr", "indiankanoon", "livemint",
                )
            )
            results.append(
                {
                    "title": r["headline"] or "",
                    "url": r["url"] or "",
                    "source": source,
                    "score": int(r["score"] or 0),
                    "angle": r["intent_tag"] or "",
                    "is_india": is_india,
                    "processed_at": r["processed_at"] or "",
                }
            )
        return results
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 3 — Trending keywords (from keyword_clusters table, C_TRENDS cluster)
# ---------------------------------------------------------------------------

def get_trending_keywords(limit: int = 8) -> list[dict]:
    """Return recently upserted trending keywords from the keyword_clusters table."""
    conn = _db_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT keyword, cluster_name, intent_type, last_updated
            FROM keyword_clusters
            WHERE cluster_id IN ('C_TRENDS', 'C_AUTOCOMPLETE', 'C_REDDIT', 'C_LINKEDIN')
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 4 — Enforcement tracker: new / recent actions
# ---------------------------------------------------------------------------

def get_recent_enforcement_actions(limit: int = 5) -> list[dict]:
    """Return the most recent enforcement actions from the tracker JSON."""
    data = _read_json(_ENFORCEMENT_TRACKER)
    if not data:
        return []

    all_actions: list[dict] = []
    for section in ("enforcement_actions", "cert_in_enforcement", "pre_dpdpa_actions"):
        for action in data.get(section, []):
            all_actions.append(
                {
                    "id": action.get("id", ""),
                    "date": action.get("date", ""),
                    "authority": action.get("authority", ""),
                    "company": action.get("company", ""),
                    "violation_type": action.get("violation_type", ""),
                    "penalty_amount": action.get("penalty_amount", ""),
                    "summary": action.get("summary", "")[:160],
                    "source_url": action.get("source_url", ""),
                    "outcome": action.get("outcome", ""),
                    "section": section,
                }
            )

    # Sort by date descending, skip legislative/N/A entries
    actionable = [a for a in all_actions if a["company"] not in ("N/A — Legislative", "N/A — Draft Rules", "Industry-wide mandate")]
    actionable.sort(key=lambda x: x["date"], reverse=True)
    return actionable[:limit]


def get_enforcement_tracker_meta() -> dict:
    """Return tracker metadata (last updated, total count)."""
    data = _read_json(_ENFORCEMENT_TRACKER)
    if not data:
        return {"last_updated": "Never", "total_count": 0}
    meta = data.get("metadata", {})
    total = sum(
        len(data.get(s, []))
        for s in ("enforcement_actions", "cert_in_enforcement", "pre_dpdpa_actions")
    )
    return {
        "last_updated": meta.get("last_updated", "Unknown"),
        "total_count": total,
    }


# ---------------------------------------------------------------------------
# Section 5 — Monday Intelligence Brief (content gaps + competitor updates)
# ---------------------------------------------------------------------------

def get_monday_brief() -> dict:
    """Return the latest Monday Intelligence Brief data."""
    data = _read_json(_MONDAY_BRIEF)
    if not data:
        return {
            "generated_at": None,
            "top_content_gaps": [],
            "competitor_updates": [],
            "ranking_threats": [],
        }
    return {
        "generated_at": data.get("generated_at"),
        "top_content_gaps": data.get("top_content_gaps", [])[:5],
        "competitor_updates": data.get("competitor_updates", [])[:5],
        "ranking_threats": data.get("ranking_threats", [])[:5],
    }


def get_competitor_gaps_from_db(limit: int = 5) -> list[dict]:
    """Return top open competitor gaps from the SQLite DB (live, not JSON)."""
    conn = _db_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT topic, competitor_count, kensara_coverage, priority_score, first_seen
            FROM competitor_gaps
            WHERE status = 'open'
            ORDER BY priority_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 6 — Keyword rank tracker
# ---------------------------------------------------------------------------

def get_latest_rankings() -> list[dict]:
    """Return the most recent ranking snapshot from the rankings directory."""
    if not _RANKINGS_DIR.exists():
        return []
    files = sorted(_RANKINGS_DIR.glob("*-rankings.json"), reverse=True)
    if not files:
        return []
    data = _read_json(files[0])
    if not isinstance(data, list):
        return []
    return data  # list of {keyword, position, url, change_from_last_week, date_checked}


def get_ranking_summary(rankings: list[dict]) -> dict:
    """Derive summary stats from the latest rankings list."""
    if not rankings:
        return {"ranked": 0, "not_ranked": 0, "improved": 0, "dropped": 0, "date": None}
    ranked = [r for r in rankings if r.get("position") is not None]
    improved = [r for r in ranked if (r.get("change_from_last_week") or 0) > 0]
    dropped = [r for r in ranked if (r.get("change_from_last_week") or 0) < 0]
    return {
        "ranked": len(ranked),
        "not_ranked": len(rankings) - len(ranked),
        "improved": len(improved),
        "dropped": len(dropped),
        "date": rankings[0].get("date_checked") if rankings else None,
    }


# ---------------------------------------------------------------------------
# Section 7 — Content refresh queue
# ---------------------------------------------------------------------------

def get_pending_refresh_items(limit: int = 5) -> list[dict]:
    """Return pending items from the refresh_queue table."""
    conn = _db_connect()
    if conn is None:
        return []
    try:
        # Check if the table exists first
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "refresh_queue" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT post_url, trigger_reason, priority, queued_date
            FROM refresh_queue
            WHERE refresh_status = 'pending'
            ORDER BY priority ASC, queued_date ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def get_refresh_queue_count() -> int:
    """Return total pending refresh count."""
    conn = _db_connect()
    if conn is None:
        return 0
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "refresh_queue" not in tables:
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM refresh_queue WHERE refresh_status = 'pending'"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 8 — Seasonal compliance calendar
# ---------------------------------------------------------------------------

def get_upcoming_seasonal_windows(days_ahead: int = 120) -> list[dict]:
    """Return upcoming seasonal content windows within the horizon."""
    conn = _db_connect()
    if conn is None:
        return _get_hardcoded_windows(days_ahead)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "seasonal_calendar" not in tables:
            return _get_hardcoded_windows(days_ahead)
        rows = conn.execute(
            """
            SELECT window_name, window_start, window_end, triggered, burst_count
            FROM seasonal_calendar
            WHERE window_start >= date('now')
              AND window_start <= date('now', ?)
            ORDER BY window_start ASC
            """,
            (f"+{days_ahead} days",),
        ).fetchall()
        result = [dict(r) for r in rows]
        return result if result else _get_hardcoded_windows(days_ahead)
    except sqlite3.Error:
        return _get_hardcoded_windows(days_ahead)
    finally:
        conn.close()


def _get_hardcoded_windows(days_ahead: int) -> list[dict]:
    """Fallback: return hardcoded seasonal windows within the horizon."""
    from src.analytics.feedback_loop import SEASONAL_WINDOWS
    today = date.today()
    horizon = today + timedelta(days=days_ahead)
    result = []
    for w in SEASONAL_WINDOWS:
        try:
            ws = date.fromisoformat(w["window_start"])
            we = date.fromisoformat(w["window_end"])
            if ws >= today and ws <= horizon:
                days_until = (ws - today).days
                result.append(
                    {
                        "window_name": w["window_name"].replace("_", " "),
                        "window_start": w["window_start"],
                        "window_end": w["window_end"],
                        "burst_count": w.get("burst_count", 6),
                        "triggered": 0,
                        "days_until": days_until,
                    }
                )
        except ValueError:
            pass
    result.sort(key=lambda x: x["window_start"])
    return result


# ---------------------------------------------------------------------------
# Section 9 — Content queue depth (keyword cluster queue)
# ---------------------------------------------------------------------------

def get_content_queue_depth() -> dict:
    """Return queue depth broken down by tier / content type."""
    conn = _db_connect()
    if conn is None:
        return {"total": 0, "by_type": {}}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "content_queue" not in tables:
            return {"total": 0, "by_type": {}}
        rows = conn.execute(
            """
            SELECT content_type, COUNT(*) as cnt
            FROM content_queue
            WHERE status = 'queued'
              AND (scheduled_for IS NULL OR scheduled_for <= date('now'))
            GROUP BY content_type
            ORDER BY cnt DESC
            """
        ).fetchall()
        by_type = {r["content_type"]: r["cnt"] for r in rows}
        return {"total": sum(by_type.values()), "by_type": by_type}
    except sqlite3.Error:
        return {"total": 0, "by_type": {}}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 10 — Source health (feed rejection stats)
# ---------------------------------------------------------------------------

def get_source_health(limit: int = 5) -> list[dict]:
    """Return sources sorted by recent rejection rate."""
    conn = _db_connect()
    if conn is None:
        return []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "source_rejection_stats" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT source, COUNT(*) as rejections,
                   MAX(rejected_at) as last_rejection
            FROM source_rejection_stats
            WHERE rejected_at >= datetime('now', '-30 days')
            GROUP BY source
            ORDER BY rejections DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 11 — Pipeline health (extended)
# ---------------------------------------------------------------------------

def get_pipeline_health(news_scan_status: str, job_history: dict) -> list[dict]:
    """Return extended pipeline health rows."""
    conn = _db_connect()
    db_ok = _DB_PATH.exists()

    # Check scheduler jobs via recent job history
    rank_check = job_history.get("rank_check", {})
    gap_check = job_history.get("content_gap_check", {})
    trending = job_history.get("trending_monitor", {})

    def _age_label(last_run_str: str) -> str:
        if not last_run_str or last_run_str == "Never":
            return "Never"
        try:
            dt = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
            age = datetime.now().replace(tzinfo=None) - dt.replace(tzinfo=None)
            if age.days == 0:
                return f"{age.seconds // 3600}h ago"
            return f"{age.days}d ago"
        except ValueError:
            return last_run_str[:10]

    return [
        {
            "name": "News scanner",
            "ok": news_scan_status == "ok",
            "last_run": _age_label(job_history.get("news_scan", {}).get("last_run", "Never")),
        },
        {
            "name": "Blog writer",
            "ok": True,
            "last_run": _age_label(job_history.get("blog_generate", {}).get("last_run", "Never")),
        },
        {
            "name": "Rank tracker",
            "ok": bool(rank_check),
            "last_run": _age_label(rank_check.get("last_run", "Never")),
        },
        {
            "name": "Competitor intel",
            "ok": bool(gap_check),
            "last_run": _age_label(gap_check.get("last_run", "Never")),
        },
        {
            "name": "Trending monitor",
            "ok": bool(trending),
            "last_run": _age_label(trending.get("last_run", "Never")),
        },
        {
            "name": "Job database",
            "ok": db_ok,
            "last_run": "Persistent" if db_ok else "—",
        },
    ]


# ---------------------------------------------------------------------------
# Section 12 — API billing / token costs
# ---------------------------------------------------------------------------

def get_api_costs() -> dict:
    """Return this-month and this-week LLM cost/token usage from token_cost_log."""
    conn = _db_connect()
    if conn is None:
        return {"available": False}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "token_cost_log" not in tables:
            return {"available": False}

        month_prefix = date.today().strftime("%Y-%m")
        week_ago = (date.today() - timedelta(days=7)).isoformat()

        # This month totals
        row = conn.execute(
            "SELECT SUM(input_tokens+output_tokens) as tokens, SUM(cost_usd) as cost "
            "FROM token_cost_log WHERE timestamp LIKE ?",
            (f"{month_prefix}%",),
        ).fetchone()
        month_tokens = int(row["tokens"] or 0)
        month_cost = round(float(row["cost"] or 0), 4)

        # This week totals
        row7 = conn.execute(
            "SELECT SUM(input_tokens+output_tokens) as tokens, SUM(cost_usd) as cost "
            "FROM token_cost_log WHERE timestamp >= ?",
            (week_ago,),
        ).fetchone()
        week_tokens = int(row7["tokens"] or 0)
        week_cost = round(float(row7["cost"] or 0), 4)

        # Breakdown by model (this month, top 5)
        model_rows = conn.execute(
            """
            SELECT model_used, SUM(input_tokens+output_tokens) as tokens,
                   SUM(cost_usd) as cost, COUNT(*) as calls
            FROM token_cost_log WHERE timestamp LIKE ?
            GROUP BY model_used ORDER BY cost DESC LIMIT 5
            """,
            (f"{month_prefix}%",),
        ).fetchall()
        by_model = [dict(r) for r in model_rows]

        # Breakdown by task (this month, top 5)
        task_rows = conn.execute(
            """
            SELECT task, SUM(cost_usd) as cost, COUNT(*) as calls
            FROM token_cost_log WHERE timestamp LIKE ?
            GROUP BY task ORDER BY cost DESC LIMIT 5
            """,
            (f"{month_prefix}%",),
        ).fetchall()
        by_task = [dict(r) for r in task_rows]

        return {
            "available": True,
            "month_label": date.today().strftime("%B %Y"),
            "month_tokens": month_tokens,
            "month_cost_usd": month_cost,
            "week_tokens": week_tokens,
            "week_cost_usd": week_cost,
            "by_model": by_model,
            "by_task": by_task,
        }
    except sqlite3.Error:
        return {"available": False}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 13 — This week's drafts (for strategy page)
# ---------------------------------------------------------------------------

def get_this_week_drafts() -> tuple[list[dict], int]:
    """Return (this_week_items, count) by reading drafts/ folder directly."""
    import re as _re
    from pathlib import Path as _Path
    from src.config import settings as _settings

    DRAFTS_ROOT = _Path(_settings.content_output_dir)
    _FM_RE = _re.compile(r"^---\s*\n(.*?)\n---", _re.DOTALL)
    type_map = {
        "blogs": ("blog", "📄"),
        "linkedin": ("linkedin", "📱"),
        "newsletters": ("newsletter", "📧"),
    }

    def _parse_fm(text: str) -> dict:
        m = _FM_RE.match(text)
        if not m:
            return {}
        fm: dict = {}
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v.lower() == "true":
                v = True  # type: ignore[assignment]
            elif v.lower() == "false":
                v = False  # type: ignore[assignment]
            else:
                try:
                    v = int(v)  # type: ignore[assignment]
                except ValueError:
                    pass
            fm[k] = v
        return fm

    items: list[dict] = []
    for folder, (content_type, icon) in type_map.items():
        folder_path = DRAFTS_ROOT / folder
        if not folder_path.exists():
            continue
        for md_file in sorted(folder_path.glob("*.md"), reverse=True):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = _parse_fm(text)
            items.append({
                "filename": md_file.name,
                "folder": folder,
                "type": content_type,
                "icon": icon,
                "title": fm.get("title", md_file.stem),
                "status": fm.get("status", "draft"),
                "approved": fm.get("approved", False),
                "date": fm.get("date", ""),
                "word_count": fm.get("word_count", 0),
            })

    week_ago = str(date.today() - timedelta(days=7))
    this_week = [i for i in items if str(i.get("date", "")) >= week_ago]
    return this_week[:10], len(this_week)


# ---------------------------------------------------------------------------
# Section 14 — GEO monitor (AI visibility)
# ---------------------------------------------------------------------------

def get_geo_monitor_summary(days: int = 30, engine: str | None = None, mentioned_only: bool = False) -> dict:
    """Return dashboard-friendly GEO summary from ai_visibility table."""
    conn = _db_connect()
    if conn is None:
        return {
            "available": False,
            "total_checks": 0,
            "mention_rate": 0.0,
            "avg_position_score": None,
            "engines_count": 0,
            "positive_count": 0,
            "last_checked": "Never",
            "engine_breakdown": [],
        }
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "ai_visibility" not in tables:
            return {
                "available": False,
                "total_checks": 0,
                "mention_rate": 0.0,
                "avg_position_score": None,
                "engines_count": 0,
                "positive_count": 0,
                "last_checked": "Never",
                "engine_breakdown": [],
            }

        where_clauses = ["checked_at >= datetime('now', ?)"]
        params: list[Any] = [f"-{days} days"]
        if engine:
            where_clauses.append("engine = ?")
            params.append(engine)
        if mentioned_only:
            where_clauses.append("kensara_mentioned = 1")

        where_sql = " AND ".join(where_clauses)

        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_checks,
                SUM(CASE WHEN kensara_mentioned = 1 THEN 1 ELSE 0 END) as mentions,
                AVG(CASE WHEN kensara_mentioned = 1 THEN position_score END) as avg_position_score,
                COUNT(DISTINCT engine) as engines_count,
                SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive_count,
                MAX(checked_at) as last_checked
            FROM ai_visibility
            WHERE {where_sql}
            """,
            tuple(params),
        ).fetchone()

        total_checks = int((row["total_checks"] or 0) if row else 0)
        mentions = int((row["mentions"] or 0) if row else 0)
        mention_rate = round((mentions / total_checks) * 100.0, 1) if total_checks else 0.0
        avg_position = float(row["avg_position_score"]) if row and row["avg_position_score"] is not None else None
        if avg_position is not None:
            avg_position = round(avg_position, 2)

        engine_rows = conn.execute(
            f"""
            SELECT
                engine,
                COUNT(*) as checks,
                SUM(CASE WHEN kensara_mentioned = 1 THEN 1 ELSE 0 END) as mentions,
                AVG(CASE WHEN kensara_mentioned = 1 THEN position_score END) as avg_position_score,
                MAX(checked_at) as last_checked
            FROM ai_visibility
            WHERE {where_sql}
            GROUP BY engine
            ORDER BY checks DESC
            """,
            tuple(params),
        ).fetchall()

        engine_breakdown = []
        for r in engine_rows:
            checks = int(r["checks"] or 0)
            eng_mentions = int(r["mentions"] or 0)
            engine_breakdown.append(
                {
                    "engine": r["engine"],
                    "checks": checks,
                    "mentions": eng_mentions,
                    "mention_rate": round((eng_mentions / checks) * 100.0, 1) if checks else 0.0,
                    "avg_position_score": round(float(r["avg_position_score"]), 2) if r["avg_position_score"] is not None else None,
                    "last_checked": r["last_checked"] or "Never",
                }
            )

        return {
            "available": total_checks > 0,
            "total_checks": total_checks,
            "mention_rate": mention_rate,
            "avg_position_score": avg_position,
            "engines_count": int((row["engines_count"] or 0) if row else 0),
            "positive_count": int((row["positive_count"] or 0) if row else 0),
            "last_checked": (row["last_checked"] or "Never") if row else "Never",
            "engine_breakdown": engine_breakdown,
        }
    except sqlite3.Error:
        return {
            "available": False,
            "total_checks": 0,
            "mention_rate": 0.0,
            "avg_position_score": None,
            "engines_count": 0,
            "positive_count": 0,
            "last_checked": "Never",
            "engine_breakdown": [],
        }
    finally:
        conn.close()


def get_geo_monitor_details(
    days: int = 30,
    limit: int = 120,
    engine: str | None = None,
    mentioned_only: bool = False,
) -> dict:
    """Return detailed GEO table rows, top queries, and competitor frequency."""
    conn = _db_connect()
    if conn is None:
        return {
            "rows": [],
            "top_queries": [],
            "top_competitors": [],
        }
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "ai_visibility" not in tables:
            return {
                "rows": [],
                "top_queries": [],
                "top_competitors": [],
            }

        where_clauses = ["checked_at >= datetime('now', ?)"]
        params: list[Any] = [f"-{days} days"]
        if engine:
            where_clauses.append("engine = ?")
            params.append(engine)
        if mentioned_only:
            where_clauses.append("kensara_mentioned = 1")
        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(
            f"""
            SELECT id, query, engine, kensara_mentioned, position_score,
                   sentiment, competitors_mentioned, checked_at
            FROM ai_visibility
            WHERE {where_sql}
            ORDER BY checked_at DESC
            LIMIT ?
            """,
            tuple(params + [limit]),
        ).fetchall()

        result_rows = []
        competitor_counts: dict[str, int] = {}
        for r in rows:
            comps_raw = r["competitors_mentioned"] or "[]"
            try:
                comps = json.loads(comps_raw)
                if not isinstance(comps, list):
                    comps = []
            except (json.JSONDecodeError, TypeError):
                comps = []

            for c in comps:
                c_key = str(c).strip().lower()
                if not c_key:
                    continue
                competitor_counts[c_key] = competitor_counts.get(c_key, 0) + 1

            result_rows.append(
                {
                    "id": r["id"],
                    "query": r["query"],
                    "engine": r["engine"],
                    "kensara_mentioned": bool(r["kensara_mentioned"]),
                    "position_score": r["position_score"],
                    "sentiment": r["sentiment"] or "neutral",
                    "competitors": comps,
                    "checked_at": r["checked_at"],
                }
            )

        top_query_rows = conn.execute(
            f"""
            SELECT
                query,
                COUNT(*) as checks,
                SUM(CASE WHEN kensara_mentioned = 1 THEN 1 ELSE 0 END) as mentions,
                AVG(CASE WHEN kensara_mentioned = 1 THEN position_score END) as avg_position_score
            FROM ai_visibility
            WHERE {where_sql}
            GROUP BY query
            ORDER BY checks DESC, mentions DESC
            LIMIT 20
            """,
            tuple(params),
        ).fetchall()

        top_queries = []
        for r in top_query_rows:
            checks = int(r["checks"] or 0)
            mentions = int(r["mentions"] or 0)
            top_queries.append(
                {
                    "query": r["query"],
                    "checks": checks,
                    "mentions": mentions,
                    "mention_rate": round((mentions / checks) * 100.0, 1) if checks else 0.0,
                    "avg_position_score": round(float(r["avg_position_score"]), 2) if r["avg_position_score"] is not None else None,
                }
            )

        top_competitors = [
            {"name": k, "count": v}
            for k, v in sorted(competitor_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        return {
            "rows": result_rows,
            "top_queries": top_queries,
            "top_competitors": top_competitors,
        }
    except sqlite3.Error:
        return {
            "rows": [],
            "top_queries": [],
            "top_competitors": [],
        }
    finally:
        conn.close()
