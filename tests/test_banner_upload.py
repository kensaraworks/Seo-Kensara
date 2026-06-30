import re
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ui.routers.queue import router, _parse_frontmatter


def test_banner_upload_and_update_url(monkeypatch, tmp_path):
    # Setup temporary draft file structure
    blogs_dir = tmp_path / "blogs"
    blogs_dir.mkdir(parents=True, exist_ok=True)
    draft_file = blogs_dir / "2026-06-30-test-blog.md"
    draft_file.write_text(
        "---\ntitle: \"Test Blog\"\nslug: \"test-blog\"\napproved: false\nstatus: \"pending\"\n---\n\n## Content Heading\nThis is content.",
        encoding="utf-8",
    )

    # Monkeypatch settings/directory configurations inside queue router
    import src.ui.routers.queue as queue_router
    monkeypatch.setattr(queue_router, "DRAFTS_ROOT", tmp_path)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Test update_banner_url endpoint
    resp = client.post(
        "/queue/update-banner-url/blogs/2026-06-30-test-blog.md",
        data={"image_url": "https://example.com/custom-banner.jpg"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify update in frontmatter
    updated_text = draft_file.read_text(encoding="utf-8")
    fm = _parse_frontmatter(updated_text)
    assert fm.get("image_url") == "https://example.com/custom-banner.jpg"

    # 2. Test upload_banner endpoint with a mock image file
    mock_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR..."
    files = {"file": ("banner.png", mock_image, "image/png")}
    
    # Path for static uploads mock
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    
    # Mock file writing to save to filesystem
    written_data = []
    def mock_open(filepath, mode):
        class MockFile:
            def write(self, data):
                written_data.append(data)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return MockFile()
        
    import builtins
    monkeypatch.setattr(builtins, "open", mock_open)

    resp = client.post(
        "/queue/upload-banner/blogs/2026-06-30-test-blog.md",
        files=files,
    )
    assert resp.status_code == 200
    assert "image_url" in resp.json()
    assert resp.json()["ok"] is True
    assert resp.json()["image_url"].startswith("/static/uploads/")
    assert len(written_data) > 0
