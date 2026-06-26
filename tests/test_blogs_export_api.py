import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ui.routers.api import router


def test_blogs_export_returns_zip_with_only_approved_posts(monkeypatch, tmp_path):
    approved_md = tmp_path / "approved.md"
    approved_md.write_text("# Approved\n", encoding="utf-8")

    pending_md = tmp_path / "pending.md"
    pending_md.write_text("# Pending\n", encoding="utf-8")

    import src.ui.app as ui_app

    monkeypatch.setattr(
        ui_app,
        "_collect_drafts",
        lambda: [
            {"approved": True, "folder": "blogs", "path": str(approved_md)},
            {"approved": False, "folder": "blogs", "path": str(pending_md)},
        ],
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/v1/blogs/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")

    with zipfile.ZipFile(io.BytesIO(response.content), "r") as zf:
        assert zf.namelist() == ["blogs/approved.md"]
        assert zf.read("blogs/approved.md").decode("utf-8") == "# Approved\n"
