"""Regressions for the ways this app has actually broken on Vercel.

Each test here corresponds to a real production failure, not a hypothetical:
a module-scope timezone lookup that killed the import, and paths resolved
against the process CWD when serverless runs from a read-only /var/task with
the writable tree under /tmp.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── Timezone ──────────────────────────────────────────────────────────────────

def test_ist_resolves_and_is_five_thirty():
    from src.runtime import IST, now_ist

    assert now_ist().utcoffset().total_seconds() == 5.5 * 3600
    assert IST is not None


def test_ist_falls_back_when_no_tz_database_exists(monkeypatch):
    """Serverless images ship without /usr/share/zoneinfo. This used to raise
    ZoneInfoNotFoundError at import and take down every route."""
    import src.runtime as runtime

    def no_zoneinfo(*args, **kwargs):
        raise ImportError("no tz database")

    monkeypatch.setattr(runtime, "_resolve_ist", runtime._resolve_ist)
    monkeypatch.setitem(sys.modules, "zoneinfo", None)  # import zoneinfo -> ImportError
    tz = runtime._resolve_ist()
    assert tz.utcoffset(None).total_seconds() == 5.5 * 3600


def test_no_module_scope_zoneinfo_on_the_web_path():
    """A ZoneInfo lookup at import time is an outage, not a bad timestamp."""
    offenders = []
    for path in sorted((ROOT / "src" / "ui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "ZoneInfo"
                ):
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"module-scope ZoneInfo() in: {offenders}"


def test_tzdata_is_pinned_in_the_runtime_requirements():
    assert "tzdata==" in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_app_imports_with_no_system_timezone_database():
    """The exact failure Vercel hit: reproduce it with an empty TZPATH."""
    result = subprocess.run(
        [sys.executable, "-c", "from src.ui.app import app; print(len(app.routes))"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONTZPATH": "/nonexistent"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


# ── Filesystem assumptions ────────────────────────────────────────────────────

def test_no_cwd_relative_paths_in_routers():
    """Vercel's CWD is /var/task, so Path("drafts") points at a read-only
    directory that does not contain the drafts."""
    offenders = []
    for path in sorted((ROOT / "src" / "ui" / "routers").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for bad in ('Path("drafts")', 'Path("static")', 'Path("data")', 'Path("config")'):
            if bad in source:
                offenders.append(f"{path.name}: {bad}")
    assert offenders == [], f"CWD-relative paths: {offenders}"


def test_drafts_roots_come_from_settings():
    from src.config import settings
    from src.ui.routers import context_editor, queue, schedule

    expected = Path(settings.content_output_dir)
    assert schedule.DRAFTS_ROOT == expected
    assert context_editor.DRAFTS_ROOT == expected
    assert queue.DRAFTS_ROOT == expected


def test_bundled_seed_resolves_independently_of_cwd():
    """The store must find data/enforcement_tracker.json from any directory."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(ROOT)!r});"
            "from src.store import enforcement_store as s;"
            "print(s.BUNDLED_PATH.exists(), len(s.load_bundled_snapshot()['pre_dpdpa_actions']))",
        ],
        cwd="/",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    # structlog writes to stdout too; the print is the last line.
    exists, count = result.stdout.strip().splitlines()[-1].split()
    assert exists == "True"
    assert int(count) > 0


def test_uploads_are_written_and_served_from_the_same_place():
    """They used to be written into the read-only static bundle and served
    from a URL that pointed somewhere else."""
    source = (ROOT / "src/ui/routers/queue.py").read_text(encoding="utf-8")
    assert '/static/uploads/' not in source
    assert 'f"/uploads/{unique_name}"' in source
    assert "/uploads" in (ROOT / "src/ui/app.py").read_text(encoding="utf-8")


# ── Entry points ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", ["app", "api.index"])
def test_both_entrypoints_expose_the_same_app(entry):
    """Vercel's zero-config picked the root app.py over api/index.py, so the
    two must not diverge."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {entry} as m; print(type(m.app).__name__, m.app.title)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "FastAPI" in result.stdout
    assert "KensaraAI Content Hub" in result.stdout


def test_recovery_app_serves_only_verified_rows():
    from src.asgi import _bundled_tracker

    payload = _bundled_tracker()
    assert payload, "recovery mode could not read the bundled dataset"
    for section in ("enforcement_actions", "cert_in_enforcement", "pre_dpdpa_actions"):
        for action in payload[section]:
            blob = f"{action.get('company','')} {action.get('summary','')}".lower()
            assert "[unconfirmed" not in blob
            assert "auto-detected" not in blob


def test_vercel_config_is_coherent():
    import json

    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["rewrites"][0]["destination"] == "/api/index"
    assert (ROOT / "api" / "index.py").exists()
    cron = config["crons"][0]
    assert cron["path"] == "/api/cron/enforcement-tracker"
    # Hobby plan allows at most a daily cron; weekly is within that.
    assert cron["schedule"].split()[2] == "*"
    assert config["functions"]["api/index.py"]["maxDuration"] <= 60


# ── Bootstrap resilience ──────────────────────────────────────────────────────

def test_entrypoints_never_raise_when_the_app_cannot_be_imported(tmp_path):
    """A raising entry point gives FUNCTION_INVOCATION_FAILED, which tells the
    operator nothing. Copy the entry points somewhere with no `src` package and
    check they still produce a usable ASGI app."""
    import shutil

    (tmp_path / "api").mkdir()
    shutil.copy(ROOT / "app.py", tmp_path / "app.py")
    shutil.copy(ROOT / "api" / "index.py", tmp_path / "api" / "index.py")

    script = (
        "import sys; sys.path.insert(0, '.');"
        "from fastapi.testclient import TestClient;"
        "import app as e;"
        "c = TestClient(e.app, raise_server_exceptions=False);"
        "r = c.get('/healthz');"
        "print(r.status_code, r.json()['status']);"
        "print('ModuleNotFoundError' in r.json().get('traceback', ''))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "DEBUG_STARTUP": "1"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    status_line, traceback_line = result.stdout.strip().splitlines()[-2:]
    assert status_line == "503 bootstrap_failed"
    assert traceback_line == "True", "the real cause must reach DEBUG_STARTUP output"


def test_bootstrap_traceback_is_hidden_without_debug_startup(tmp_path):
    import shutil

    (tmp_path / "api").mkdir()
    shutil.copy(ROOT / "app.py", tmp_path / "app.py")
    shutil.copy(ROOT / "api" / "index.py", tmp_path / "api" / "index.py")

    script = (
        "import sys; sys.path.insert(0, '.');"
        "from fastapi.testclient import TestClient;"
        "import app as e;"
        "c = TestClient(e.app, raise_server_exceptions=False);"
        "print('traceback' in c.get('/healthz').json())"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "DEBUG_STARTUP"}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, env=env, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_healthz_reports_the_running_deployment(monkeypatch):
    """Distinguishes a stale deployment from a fresh one when both misbehave."""
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "0123456789abcdef")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "main")
    monkeypatch.setenv("VERCEL_ENV", "production")

    from src.runtime import deployment_info

    info = deployment_info()
    assert info["commit"] == "0123456789ab"
    assert info["branch"] == "main"
    assert info["env"] == "production"


def test_vercel_config_bundles_the_application_files():
    """Import tracing does not pick up Jinja templates or the seed JSON."""
    import json

    include = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["functions"][
        "api/index.py"
    ]["includeFiles"]
    for required in ("src/**", "data/**", "static/**"):
        assert required in include, f"{required} missing from includeFiles"
