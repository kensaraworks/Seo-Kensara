"""Performance Report — compiles weekly SEO metrics into a report for the CEO dashboard.

Reads from the drafts/ directory structure to count generated/approved/published content,
pulls ranking data from the latest rankings snapshot, and formats an HTML summary for the UI.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import structlog
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()


class WeeklyReport(BaseModel):
    week_ending: str
    blogs_generated: int
    blogs_approved: int
    blogs_published: int
    linkedin_posts: int
    top_ranking_keywords: list[dict]  # [{"keyword": str, "position": int}]
    ranking_improvements: list[dict]  # [{"keyword": str, "change": int, "new_position": int}]
    content_gaps_found: int
    pr_pitches_drafted: int


def _count_files_in_dir(directory: Path, suffix: str = ".md") -> int:
    """Count files with the given suffix in a directory. Returns 0 if directory doesn't exist."""
    if not directory.exists():
        return 0
    return sum(1 for f in directory.iterdir() if f.suffix == suffix and f.is_file())


def _count_files_created_this_week(directory: Path, suffix: str = ".md") -> int:
    """Count files created in the current week (Mon–Sun) in a directory."""
    if not directory.exists():
        return 0
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    count = 0
    for f in directory.iterdir():
        if not f.is_file() or f.suffix != suffix:
            continue
        # Parse date from filename prefix (YYYY-MM-DD-...)
        try:
            file_date = date.fromisoformat(f.name[:10])
            if file_date >= week_start:
                count += 1
        except ValueError:
            continue
    return count


def _count_json_files_this_week(directory: Path) -> int:
    """Count .json files created this week in a directory."""
    if not directory.exists():
        return 0
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    count = 0
    for f in directory.iterdir():
        if not f.is_file() or f.suffix != ".json":
            continue
        try:
            file_date = date.fromisoformat(f.name[:10])
            if file_date >= week_start:
                count += 1
        except ValueError:
            continue
    return count


def _load_latest_rankings(rankings_dir: Path) -> list[dict]:
    """Load the most recent rankings snapshot. Returns empty list if none found."""
    if not rankings_dir.exists():
        return []
    snapshots = sorted(rankings_dir.glob("*-rankings.json"), reverse=True)
    if not snapshots:
        return []
    try:
        return json.loads(snapshots[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("perf_report_rankings_load_failed", file=str(snapshots[0]), error=str(exc))
        return []


def _count_pr_pitches_this_week(pr_dir: Path) -> int:
    """Count PR pitch markdown files created this week."""
    return _count_files_created_this_week(pr_dir, suffix=".md")


def generate_weekly_report() -> WeeklyReport:
    """
    Compile a weekly performance report from the drafts/ directory tree.

    Data sources:
    - drafts/blogs/          → blogs generated (all .md), blogs approved (those containing 'approved')
    - drafts/linkedin/       → LinkedIn posts drafted this week
    - drafts/reports/rankings/ → latest keyword position snapshot
    - drafts/reports/content-gaps/ → gap analysis files created this week
    - drafts/pr/             → PR pitches drafted this week

    Note: "approved" is inferred from presence of an 'approved' marker in filename
    (e.g. blog_writer saves approved blogs with '-approved' suffix when implemented).
    "published" = WordPress-published blogs (future: check WP API or a published/ subfolder).

    Saves to drafts/reports/weekly/YYYY-MM-DD-weekly-report.json
    """
    output_root = Path(settings.content_output_dir)
    today = date.today()

    blogs_dir = output_root / "blogs"
    linkedin_dir = output_root / "linkedin"
    rankings_dir = output_root / "reports" / "rankings"
    gaps_dir = output_root / "reports" / "content-gaps"
    pr_dir = output_root / "pr"

    # Blog counts
    blogs_generated = _count_files_in_dir(blogs_dir, ".md")
    # Approved = files with 'approved' in their name (naming convention for future use)
    blogs_approved = sum(
        1 for f in blogs_dir.iterdir()
        if f.is_file() and f.suffix == ".md" and "approved" in f.name
    ) if blogs_dir.exists() else 0
    # Published = files with 'published' in their name or in a published/ subdir
    published_dir = blogs_dir / "published"
    blogs_published = _count_files_in_dir(published_dir, ".md") if published_dir.exists() else 0

    # LinkedIn posts drafted this week
    linkedin_posts = _count_files_created_this_week(linkedin_dir, ".md")

    # Rankings
    raw_rankings = _load_latest_rankings(rankings_dir)
    top_ranking_keywords = [
        {"keyword": r["keyword"], "position": r["position"]}
        for r in raw_rankings
        if r.get("position") is not None
    ]
    top_ranking_keywords.sort(key=lambda x: x["position"])

    ranking_improvements = [
        {
            "keyword": r["keyword"],
            "change": r["change_from_last_week"],
            "new_position": r["position"],
        }
        for r in raw_rankings
        if r.get("change_from_last_week") is not None
        and r["change_from_last_week"] > 0
        and r.get("position") is not None
    ]
    ranking_improvements.sort(key=lambda x: x["change"], reverse=True)

    # Content gaps found this week
    content_gaps_found = _count_json_files_this_week(gaps_dir)

    # PR pitches drafted this week
    pr_pitches_drafted = _count_pr_pitches_this_week(pr_dir)

    report = WeeklyReport(
        week_ending=today.isoformat(),
        blogs_generated=blogs_generated,
        blogs_approved=blogs_approved,
        blogs_published=blogs_published,
        linkedin_posts=linkedin_posts,
        top_ranking_keywords=top_ranking_keywords[:10],  # top 10 ranked keywords
        ranking_improvements=ranking_improvements[:5],   # top 5 improvements
        content_gaps_found=content_gaps_found,
        pr_pitches_drafted=pr_pitches_drafted,
    )

    # Persist report
    weekly_dir = output_root / "reports" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    report_path = weekly_dir / f"{today.isoformat()}-weekly-report.json"
    try:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        log.info(
            "weekly_report_saved",
            path=str(report_path),
            blogs_generated=blogs_generated,
            ranked_keywords=len(top_ranking_keywords),
            improvements=len(ranking_improvements),
        )
    except OSError as exc:
        log.error("weekly_report_save_failed", error=str(exc))

    return report


def format_report_for_ui(report: WeeklyReport) -> str:
    """
    Format WeeklyReport as an HTML snippet for the CEO dashboard.

    Returns a self-contained <div> that can be injected into the UI
    (src/ui/templates/*.html or served via the FastAPI /analytics endpoint).
    """
    improvements_html = ""
    if report.ranking_improvements:
        rows = "".join(
            f"<tr><td>{r['keyword']}</td><td>#{r['new_position']}</td>"
            f"<td class='improvement'>+{r['change']}</td></tr>"
            for r in report.ranking_improvements
        )
        improvements_html = f"""
        <h3>Ranking Improvements This Week</h3>
        <table class="rank-table">
          <thead><tr><th>Keyword</th><th>Position</th><th>Change</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    top_keywords_html = ""
    if report.top_ranking_keywords:
        rows = "".join(
            f"<tr><td>{r['keyword']}</td><td>#{r['position']}</td></tr>"
            for r in report.top_ranking_keywords
        )
        top_keywords_html = f"""
        <h3>Current Rankings (kensara.in)</h3>
        <table class="rank-table">
          <thead><tr><th>Keyword</th><th>Position</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""
    else:
        top_keywords_html = "<p class='muted'>No ranking data yet — configure SERPER_API_KEY.</p>"

    return f"""
<div class="weekly-report card">
  <div class="report-header">
    <h2>Weekly SEO Report</h2>
    <span class="report-date">Week ending {report.week_ending}</span>
  </div>

  <div class="metrics-grid">
    <div class="metric">
      <span class="metric-value">{report.blogs_generated}</span>
      <span class="metric-label">Blogs Generated</span>
    </div>
    <div class="metric">
      <span class="metric-value">{report.blogs_approved}</span>
      <span class="metric-label">Approved</span>
    </div>
    <div class="metric">
      <span class="metric-value">{report.blogs_published}</span>
      <span class="metric-label">Published</span>
    </div>
    <div class="metric">
      <span class="metric-value">{report.linkedin_posts}</span>
      <span class="metric-label">LinkedIn Posts</span>
    </div>
    <div class="metric">
      <span class="metric-value">{report.content_gaps_found}</span>
      <span class="metric-label">Gaps Found</span>
    </div>
    <div class="metric">
      <span class="metric-value">{report.pr_pitches_drafted}</span>
      <span class="metric-label">PR Pitches</span>
    </div>
  </div>

  {improvements_html}
  {top_keywords_html}
</div>
""".strip()
