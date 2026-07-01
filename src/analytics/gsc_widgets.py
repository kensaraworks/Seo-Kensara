import logging
import sqlite3
from datetime import date, timedelta

logger = logging.getLogger(__name__)
from src.config import settings_database_path
DB_PATH = settings_database_path


def get_high_impression_low_ctr_queries(
    min_impressions: int = 50,
    max_ctr: float = 0.03,
    limit: int = 10,
) -> list[dict]:
    """
    Widget 1: Queries with significant impressions but very low CTR.

    Returns list of dicts with keys:
      query, page_url, impressions, clicks, ctr_pct, position, action
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT query, page_url, impressions, clicks, ctr, position
            FROM gsc_query_performance
            WHERE impressions >= ?
              AND ctr <= ?
              AND date_synced = (SELECT MAX(date_synced) FROM gsc_query_performance)
            ORDER BY impressions DESC
            LIMIT ?
            """,
            (min_impressions, max_ctr, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("GSC widget 1 query failed: %s", exc)
        rows = []
    finally:
        conn.close()

    return [
        {
            "query": row[0],
            "page_url": row[1],
            "impressions": row[2],
            "clicks": row[3],
            "ctr_pct": round(row[4] * 100, 1),
            "position": round(row[5], 1),
            "action": "Rewrite meta description - post is visible but not clicked",
        }
        for row in rows
    ]


def get_pages_near_page_one(
    min_position: float = 8.0,
    max_position: float = 20.0,
    min_impressions: int = 20,
    limit: int = 10,
) -> list[dict]:
    """
    Widget 2: Pages ranking average position 8-20.

    Returns list of dicts with keys:
      query, page_url, impressions, clicks, ctr_pct, position,
      position_gap, action
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT query, page_url, impressions, clicks, ctr, position
            FROM gsc_query_performance
            WHERE position >= ?
              AND position <= ?
              AND impressions >= ?
              AND date_synced = (SELECT MAX(date_synced) FROM gsc_query_performance)
            ORDER BY impressions DESC, position ASC
            LIMIT ?
            """,
            (min_position, max_position, min_impressions, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("GSC widget 2 query failed: %s", exc)
        rows = []
    finally:
        conn.close()

    return [
        {
            "query": row[0],
            "page_url": row[1],
            "impressions": row[2],
            "clicks": row[3],
            "ctr_pct": round(row[4] * 100, 1),
            "position": round(row[5], 1),
            "position_gap": round(row[5] - 1.0, 1),
            "action": (
                f"Refresh content - currently position {round(row[5], 1)}, "
                f"{round(row[5] - 1.0, 1)} spots from page 1"
            ),
        }
        for row in rows
    ]


def get_zero_impression_posts(days_since_publish: int = 30) -> list[dict]:
    """
    Widget 3: Published posts with zero impressions over the last 30 days.

    Returns list of dicts with keys:
      post_url, date_published, days_old, primary_keyword, action
    """
    conn = sqlite3.connect(DB_PATH)

    try:
        cp_columns = {
            col[1]
            for col in conn.execute("PRAGMA table_info(content_performance)").fetchall()
        }
        required = {"post_url", "date_published", "primary_keyword"}
        if not required.issubset(cp_columns):
            logger.info(
                "GSC widget 3 skipped: content_performance missing required columns: %s",
                sorted(required - cp_columns),
            )
            return []

        rows = conn.execute(
            """
            SELECT cp.post_url, cp.date_published, cp.primary_keyword
            FROM content_performance cp
            WHERE cp.date_published <= date('now', ?)
              AND cp.post_url NOT IN (
                  SELECT DISTINCT page_url
                  FROM gsc_query_performance
                  WHERE date_synced = (
                      SELECT MAX(date_synced) FROM gsc_query_performance
                  )
              )
            ORDER BY cp.date_published ASC
            LIMIT 15
            """,
            (f"-{days_since_publish} days",),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("GSC widget 3 query failed: %s", exc)
        rows = []
    finally:
        conn.close()

    results: list[dict] = []
    for row in rows:
        post_url, date_published, primary_keyword = row[0], row[1], row[2]
        try:
            pub_date = date.fromisoformat(str(date_published).split("T")[0])
            days_old = (date.today() - pub_date).days
        except Exception:
            days_old = 0

        results.append(
            {
                "post_url": post_url,
                "date_published": date_published,
                "days_old": days_old,
                "primary_keyword": primary_keyword or "unknown",
                "action": "Check indexing status in Search Console URL Inspection",
            }
        )

    return results


def sync_gsc_query_data_to_db(query_rows: list, db_path: str = DB_PATH) -> int:
    """
    Writes query performance rows to gsc_query_performance.

    Uses INSERT OR REPLACE to handle re-syncing on the same day.
    Returns number of rows written.
    """
    from src.analytics.search_console import GSCRow

    today = date.today().isoformat()
    end_date = (date.today() - timedelta(days=3)).isoformat()
    start_date = (date.today() - timedelta(days=33)).isoformat()

    conn = sqlite3.connect(db_path)
    count = 0

    try:
        for row in query_rows:
            if not isinstance(row, GSCRow):
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO gsc_query_performance
                (query, page_url, clicks, impressions, ctr, position,
                 date_synced, date_range_start, date_range_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.query,
                    row.page,
                    row.clicks,
                    row.impressions,
                    row.ctr,
                    row.position,
                    today,
                    start_date,
                    end_date,
                ),
            )
            count += 1
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning("GSC query sync failed: %s", exc)
    finally:
        conn.close()

    logger.info("GSC: Synced %d query rows to gsc_query_performance table", count)
    return count
