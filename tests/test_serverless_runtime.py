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
    """The entry point is the root app.py, matching Vercel's own detection.

    A catch-all rewrite to /api/index used to sit alongside it; the two
    competed and every request came back 302, looping between the dashboard and
    the login page.
    """
    import json

    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert "rewrites" not in config, "a catch-all rewrite competes with Vercel's routing"
    assert "app.py" in config["functions"]
    assert (ROOT / "app.py").exists()
    cron = config["crons"][0]
    assert cron["path"] == "/api/cron/enforcement-tracker"
    # Hobby plan allows at most a daily cron; weekly is within that.
    assert cron["schedule"].split()[2] == "*"
    assert config["functions"]["app.py"]["maxDuration"] <= 60


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
        "app.py"
    ]["includeFiles"]
    for required in ("src/**", "data/**", "static/**"):
        assert required in include, f"{required} missing from includeFiles"


# ── The recovery path must itself be unbreakable ──────────────────────────────

def test_recovery_app_builds_and_serves(tmp_path):
    """The recovery app used to crash while being constructed.

    src/asgi.py has `from __future__ import annotations`, so a `-> JSONResponse`
    return annotation on a FastAPI handler is a string resolved at module scope,
    where the function-local import does not exist:
        PydanticUndefinedAnnotation: name 'JSONResponse' is not defined
    A crashing recovery path turns a diagnosable error into an opaque
    FUNCTION_INVOCATION_FAILED, so it now uses only the standard library.
    """
    script = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "cls = type('B', (), {"
        "  'find_module': lambda self, n, p=None: self if n == 'src.ui.app' else None,"
        "  'load_module': lambda self, n: (_ for _ in ()).throw(ImportError('boom'))});"
        "sys.meta_path.insert(0, cls());"
        "import src.asgi as a;"
        "from starlette.testclient import TestClient;"
        "c = TestClient(a.app);"
        "h = c.get('/healthz');"
        "d = c.get('/enforcement-tracker/data.json');"
        "print(h.status_code, h.json()['status'], d.status_code,"
        "      sum(len(d.json().get(k, [])) for k in a.SECTIONS))" % str(ROOT)
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-3000:]
    status, state, dataset_status, rows = result.stdout.strip().splitlines()[-1].split()
    assert (status, state, dataset_status) == ("503", "startup_failed", "200")
    assert int(rows) > 0, "the dataset must stay available in recovery mode"


def test_recovery_path_imports_nothing_beyond_the_stdlib():
    """Guards the property that makes the recovery path trustworthy."""
    tree = ast.parse((ROOT / "src" / "asgi.py").read_text(encoding="utf-8"))
    third_party = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            third_party.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            third_party.add(node.module.split(".")[0])
    allowed = {"json", "os", "sys", "traceback", "pathlib", "__future__", "src", "typing"}
    assert third_party <= allowed, f"src/asgi.py must stay stdlib-only, found: {third_party - allowed}"


def test_no_route_returns_an_annotation_that_is_not_module_level():
    """The bug class above, across every module using postponed annotations."""
    offenders = []
    for path in list((ROOT / "src").rglob("*.py")) + [ROOT / "api/index.py", ROOT / "app.py"]:
        source = path.read_text(encoding="utf-8")
        if "from __future__ import annotations" not in source:
            continue
        tree = ast.parse(source)
        module_names = set()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_names.update(a.asname or a.name.split(".")[0] for a in node.names)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.decorator_list:
                continue
            decorator = ast.unparse(node.decorator_list[0])
            if not any(verb in decorator for verb in (".get(", ".post(", ".put(", ".delete(")):
                continue
            if node.returns is None:
                continue
            annotation = ast.unparse(node.returns).split("[")[0].strip()
            if annotation in {"None", "str", "dict", "int", "bool", "list"} or annotation in module_names:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} -> {annotation}")
    assert offenders == [], f"return annotations unresolvable at module scope: {offenders}"


# ── Configuration must not be able to kill the app ────────────────────────────

def test_a_malformed_env_var_degrades_instead_of_killing_the_app():
    """A ValidationError at import surfaces on serverless as an opaque
    invocation failure with no hint that an env var is to blame."""
    script = (
        "from src.config import settings, SETTINGS_ERRORS;"
        "from src.ui.app import app;"
        "print(settings.news_max_age_days, len(SETTINGS_ERRORS), len(app.routes) > 20)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "NEWS_MAX_AGE_DAYS": "not-an-integer"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    value, error_count, routes_ok = result.stdout.strip().splitlines()[-1].split()
    assert value == "90", "the declared default should be restored"
    assert int(error_count) == 1, "the discarded variable must be reported"
    assert routes_ok == "True"


# ── Credential visibility ─────────────────────────────────────────────────────

def test_get_secret_prefers_settings_then_environment(monkeypatch):
    from src.config import get_secret, settings

    monkeypatch.setattr(settings, "groq_api_key", "from-settings", raising=False)
    assert get_secret("GROQ_API_KEY") == "from-settings"

    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert get_secret("GROQ_API_KEY") == "from-env"


def test_placeholder_values_count_as_unset(monkeypatch):
    """Copying .env.example into a dashboard sets variables to 'replace_me'."""
    from src.config import get_secret, settings

    monkeypatch.setattr(settings, "tavily_api_key", "", raising=False)
    for placeholder in ("replace_me", "  ", "CHANGEME", "your_key_here"):
        monkeypatch.setenv("TAVILY_API_KEY", placeholder)
        assert get_secret("TAVILY_API_KEY") == "", f"{placeholder!r} should read as unset"


def test_credential_report_separates_missing_from_placeholder(monkeypatch):
    from src.config import credential_report, settings

    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    monkeypatch.setattr(settings, "nvidia_api_key", "", raising=False)
    monkeypatch.setattr(settings, "tavily_api_key", "", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-a-real-looking-value")
    monkeypatch.setenv("NVIDIA_API_KEY", "replace_me")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    report = credential_report()
    assert report["GROQ_API_KEY"]["state"] == "configured"
    assert report["NVIDIA_API_KEY"]["state"] == "placeholder"
    assert report["TAVILY_API_KEY"]["state"] == "missing"
    assert report["TAVILY_API_KEY"]["in_process_env"] is False


def test_credential_report_never_returns_a_value(monkeypatch):
    import json

    from src.config import credential_report, settings

    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-do-not-leak")
    assert "super-secret-do-not-leak" not in json.dumps(credential_report())


def test_health_endpoints_do_not_read_credentials_directly():
    """They used to use os.getenv() while the rest of the app used settings, so
    a key could be live for one and missing for the other."""
    source = (ROOT / "src/ui/routers/api.py").read_text(encoding="utf-8")
    assert 'os.getenv("GROQ_API_KEY")' not in source
    assert 'os.getenv("NVIDIA_API_KEY")' not in source
    assert 'get_secret("GROQ_API_KEY")' in source
