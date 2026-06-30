"""Queue router — content approval queue."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

log = structlog.get_logger()

router = APIRouter(prefix="/queue", tags=["queue"])
templates = Jinja2Templates(directory="src/ui/templates")

from src.config import settings
from src.engines.content_calendar import sort_pending_review_items
DRAFTS_ROOT = Path(settings.content_output_dir)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fm: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.lower() == "true":
            val = True  # type: ignore[assignment]
        elif val.lower() == "false":
            val = False  # type: ignore[assignment]
        else:
            try:
                val = int(val)  # type: ignore[assignment]
            except ValueError:
                pass
        fm[key] = val
    return fm


def _replace_frontmatter_field(text: str, field: str, value: str) -> str:
    """Replace a single YAML frontmatter field value in the file text."""
    pattern = re.compile(rf"^({re.escape(field)}:\s*).*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(rf"\g<1>{value}", text)
    # Field absent — insert before closing ---
    return text.replace("\n---\n", f"\n{field}: {value}\n---\n", 1)


def _resolve_path(folder: str, filename: str) -> Path | None:
    """Return validated path inside drafts/. Returns None on traversal attempt."""
    safe_folder = folder.strip("/").split("/")[0]
    safe_filename = Path(filename).name  # strip any path components
    candidate = DRAFTS_ROOT / safe_folder / safe_filename
    try:
        candidate.resolve().relative_to(DRAFTS_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _collect_all_items(status_filter: list[str] | None = None) -> list[dict]:
    type_map = {
        "blogs": ("blog", "📄"),
        "linkedin": ("linkedin", "📱"),
        "newsletters": ("newsletter", "📧"),
    }
    items: list[dict] = []
    for folder, (content_type, icon) in type_map.items():
        folder_path = DRAFTS_ROOT / folder
        if not folder_path.exists():
            continue
        for md_file in sorted(folder_path.glob("*.md"), reverse=True):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("queue_read_error", path=str(md_file), error=str(exc))
                continue
            fm = _parse_frontmatter(text)
            status = fm.get("status", "draft")
            if status_filter and status not in status_filter:
                continue
            items.append(
                {
                    "filename": md_file.name,
                    "folder": folder,
                    "type": content_type,
                    "icon": icon,
                    "title": fm.get("title", md_file.stem),
                    "status": status,
                    "approved": fm.get("approved", False),
                    "date": fm.get("date", ""),
                    "primary_keyword": fm.get("primary_keyword", ""),
                    "post_type": fm.get("post_type", ""),
                    "content_type": fm.get("content_type", fm.get("post_type", "")),
                    "tier": fm.get("tier", 0),
                    "rank_position": fm.get("rank_position", ""),
                    "zero_coverage": fm.get("zero_coverage", False),
                    "word_count": fm.get("word_count", 0),
                    "model": fm.get("model", ""),
                    "meta_description": fm.get("meta_description", ""),
                    "image_url": fm.get("image_url", ""),
                    "content": text,
                    "body": _FRONTMATTER_RE.sub("", text).strip(),
                }
            )
    return sort_pending_review_items(items)


def _append_activity(action: str, title: str, content_type: str) -> None:
    """Append an entry to activity_log.json."""
    log_path = DRAFTS_ROOT / ".cache" / "activity_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing: list = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    except (OSError, json.JSONDecodeError):
        existing = []
    existing.append(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "action": action,
            "title": title[:80],
            "type": content_type,
        }
    )
    # Keep last 50 entries
    log_path.write_text(json.dumps(existing[-50:], indent=2), encoding="utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def queue_list(request: Request, filter: str = "pending") -> HTMLResponse:
    if filter == "all":
        items = _collect_all_items()
    elif filter == "approved":
        items = _collect_all_items(status_filter=["approved"])
    elif filter == "rejected":
        items = _collect_all_items(status_filter=["rejected"])
    else:
        items = _collect_all_items(status_filter=["draft", "pending_review"])

    return templates.TemplateResponse(
        "queue.html",
        {
            "request": request,
            "active_page": "queue",
            "items": items,
            "filter": filter,
            "total_count": len(items),
        },
    )


@router.post("/approve/{folder}/{filename}", response_class=JSONResponse)
async def approve_item(folder: str, filename: str) -> JSONResponse:
    path = _resolve_path(folder, filename)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        text = path.read_text(encoding="utf-8")
        text = _replace_frontmatter_field(text, "approved", "true")
        text = _replace_frontmatter_field(text, "status", "approved")
        text = _replace_frontmatter_field(text, "approved_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        path.write_text(text, encoding="utf-8")
        updated_frontmatter = _parse_frontmatter(text)

        # Ping Google Indexing API if a canonical URL is available.
        wp_post_url = updated_frontmatter.get("wp_post_url") or updated_frontmatter.get("canonical_url")
        if isinstance(wp_post_url, str) and wp_post_url.startswith("https://"):
            try:
                from src.analytics.indexing_ping import ping_indexing_api

                ping_result = ping_indexing_api(wp_post_url)
                if ping_result.get("success"):
                    log.info("indexing_ping_sent", url=wp_post_url)
                else:
                    log.info(
                        "indexing_ping_skipped_or_failed",
                        url=wp_post_url,
                        error=ping_result.get("error"),
                        note="post approval unaffected",
                    )
            except Exception as exc:
                log.warning("indexing_ping_exception_non_blocking", error=str(exc))

        # ── Supabase publish (blog posts only) ──────────────────────────────
        supabase_result: dict = {}
        if folder == "blogs":
            try:
                from src.agents.blog_writer import BlogPost
                from src.publishers.supabase_publisher import publish_to_supabase

                # Re-read body for full content
                full_text = path.read_text(encoding="utf-8")
                fm = _parse_frontmatter(full_text)

                # Build a minimal BlogPost from frontmatter to pass to publisher.
                # content_markdown holds the full file (frontmatter + body).
                post_for_publish = BlogPost(
                    title=str(fm.get("title", filename)),
                    meta_description=str(fm.get("meta_description", "")),
                    slug=str(fm.get("slug", path.stem)),
                    primary_keyword=str(fm.get("primary_keyword", "")),
                    content_markdown=full_text,
                    word_count=int(fm.get("word_count", len(full_text.split()))),
                    cluster=str(fm.get("cluster", "general")),
                    intent=str(fm.get("intent", "informational")),
                    tier=int(fm.get("tier", 2)),
                    geo_score=int(fm.get("geo_score", 0)),
                    qa_score=float(fm.get("qa_score", 0.0)),
                    risk_level=str(fm.get("risk_level", "HIGH")),
                    approved=True,
                    author=str(fm.get("author", "Mr Rudraksh Tatwal")),
                    author_credentials=str(fm.get("author_credentials", "Founder & CEO, KensaraAI")),
                    date_created=str(fm.get("date_created", "")),
                    schema_json=str(fm.get("schema_json", "{}")),
                    image_url=fm.get("image_url") or None,
                    pillar=str(fm.get("pillar", "")),
                    category=str(fm.get("category", "")),
                )
                supabase_result = await publish_to_supabase(post_for_publish)
                log.info(
                    "supabase_auto_publish_result",
                    slug=post_for_publish.slug,
                    status=supabase_result.get("status"),
                )
            except Exception as exc:
                log.warning("supabase_auto_publish_exception_non_blocking", error=str(exc))
                supabase_result = {"status": "error", "error": str(exc)}

        _append_activity("approved", updated_frontmatter.get("title", filename), folder)
        log.info("content_approved", path=str(path))
        return JSONResponse({
            "ok": True,
            "status": "approved",
            "supabase": supabase_result,
        })
    except OSError as exc:
        log.error("approve_write_error", path=str(path), error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/reject/{folder}/{filename}", response_class=JSONResponse)
async def reject_item(folder: str, filename: str) -> JSONResponse:
    path = _resolve_path(folder, filename)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        text = path.read_text(encoding="utf-8")
        text = _replace_frontmatter_field(text, "approved", "false")
        text = _replace_frontmatter_field(text, "status", "rejected")
        text = _replace_frontmatter_field(text, "rejected_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        path.write_text(text, encoding="utf-8")
        fm = _parse_frontmatter(text)
        _append_activity("rejected", fm.get("title", filename), folder)
        log.info("content_rejected", path=str(path))
        return JSONResponse({"ok": True, "status": "rejected"})
    except OSError as exc:
        log.error("reject_write_error", path=str(path), error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/edit/{folder}/{filename}", response_class=JSONResponse)
async def edit_item(folder: str, filename: str, content: str = Form(...)) -> JSONResponse:
    path = _resolve_path(folder, filename)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        existing = path.read_text(encoding="utf-8")
        # Preserve frontmatter, replace body
        fm_match = _FRONTMATTER_RE.match(existing)
        if fm_match:
            new_text = existing[: fm_match.end()] + "\n\n" + content.strip() + "\n"
        else:
            new_text = content
        path.write_text(new_text, encoding="utf-8")
        fm = _parse_frontmatter(existing)
        _append_activity("edited", fm.get("title", filename), folder)
        log.info("content_edited", path=str(path))
        return JSONResponse({"ok": True})
    except OSError as exc:
        log.error("edit_write_error", path=str(path), error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/upload-banner/{folder}/{filename}", response_class=JSONResponse)
async def upload_banner(folder: str, filename: str, file: UploadFile = File(...)) -> JSONResponse:
    """Upload a banner image file for a blog draft."""
    path = _resolve_path(folder, filename)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        if folder != "blogs":
            return JSONResponse({"ok": False, "error": "Only blogs support banner upload"}, status_code=400)

        # Create static/uploads directory if not exists
        static_uploads = Path("static") / "uploads"
        static_uploads.mkdir(parents=True, exist_ok=True)

        # Keep original extension or fallback to .jpg
        orig_suffix = Path(file.filename or "").suffix or ".jpg"
        import uuid
        unique_name = f"{uuid.uuid4()}{orig_suffix}"
        dest_path = static_uploads / unique_name

        # Save uploaded file
        with open(dest_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        relative_url = f"/static/uploads/{unique_name}"

        # Update the frontmatter of the Markdown file
        text = path.read_text(encoding="utf-8")
        text = _replace_frontmatter_field(text, "image_url", relative_url)
        path.write_text(text, encoding="utf-8")

        log.info("banner_uploaded", path=str(path), url=relative_url)
        return JSONResponse({"ok": True, "image_url": relative_url})
    except Exception as exc:
        log.error("upload_banner_error", path=str(path), error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/update-banner-url/{folder}/{filename}", response_class=JSONResponse)
async def update_banner_url(folder: str, filename: str, image_url: str = Form(...)) -> JSONResponse:
    """Manually update or clear the banner image URL for a blog draft."""
    path = _resolve_path(folder, filename)
    if path is None or not path.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        if folder != "blogs":
            return JSONResponse({"ok": False, "error": "Only blogs support banner URLs"}, status_code=400)

        text = path.read_text(encoding="utf-8")
        text = _replace_frontmatter_field(text, "image_url", image_url.strip())
        path.write_text(text, encoding="utf-8")

        log.info("banner_url_updated", path=str(path), url=image_url)
        return JSONResponse({"ok": True})
    except Exception as exc:
        log.error("update_banner_url_error", path=str(path), error=str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
