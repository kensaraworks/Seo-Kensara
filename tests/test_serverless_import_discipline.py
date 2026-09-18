"""Guards on what the application imports at module scope.

A Vercel cold start pays for every module-level import, and the serverless
bundle installs only requirements.txt. This suite is the regression net for the
crash that took the site down: a router module that fails to import used to
raise NameError and take every route with it.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Packages that live in requirements-pipeline.txt and must never be imported
#: at module scope by anything on the web request path.
HEAVY_PACKAGES = {
    "feedparser",
    "bs4",
    "curl_cffi",
    "pytrends",
    "groq",
    "openai",
    "googleapiclient",
    "google",
    "tavily",
    "numpy",
    "chromadb",
}

#: Modules imported during application start-up.
STARTUP_MODULES = [
    "src/runtime.py",
    "src/config.py",
    "src/ui/app.py",
    "src/ui/tracker_view.py",
    "src/ui/routers/tracker.py",
    "src/store/enforcement_store.py",
    "src/db/supabase_client.py",
]


def _module_level_imports(path: Path) -> set[str]:
    """Top-level import names only — imports inside functions don't count."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Try):
            # `try: import x except ImportError:` is an explicitly optional import.
            continue
    return names


@pytest.mark.parametrize("module_path", STARTUP_MODULES)
def test_startup_modules_avoid_heavy_imports(module_path):
    leaked = sorted(_module_level_imports(ROOT / module_path) & HEAVY_PACKAGES)
    assert leaked == [], (
        f"{module_path} imports {leaked} at module scope. "
        "Move it inside the function that needs it — it is not in requirements.txt."
    )


def test_app_does_not_import_the_pipeline_at_module_scope():
    """src.main pulls in the whole scraping and LLM tree."""
    tree = ast.parse((ROOT / "src/ui/app.py").read_text(encoding="utf-8"))
    module_level = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {m for m in module_level if m.startswith(("src.main", "src.agents", "src.analytics", "src.scrapers"))}
    assert forbidden == set(), f"src/ui/app.py imports {sorted(forbidden)} at module scope"


def test_every_router_defines_a_router_object():
    """The regression that broke the site: a missing `router = APIRouter(...)`."""
    routers_dir = ROOT / "src/ui/routers"
    missing = []
    for path in sorted(routers_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assigns = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        if "router" not in assigns:
            missing.append(path.name)
    assert missing == [], f"Routers without a module-level `router`: {missing}"


def test_all_routers_register_without_error():
    from src.ui.app import ROUTER_ERRORS

    assert ROUTER_ERRORS == {}, f"Routers failed to register: {ROUTER_ERRORS}"


def test_app_imports_in_a_clean_subprocess():
    """Catches import-time side effects that an already-warm session would hide."""
    result = subprocess.run(
        [sys.executable, "-c", "from src.ui.app import app; print(len(app.routes))"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert int(result.stdout.strip().splitlines()[-1]) > 20


def test_vercel_entrypoint_exposes_an_asgi_app():
    result = subprocess.run(
        [sys.executable, "-c", "import api.index as m; print(type(m.app).__name__)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "FastAPI" in result.stdout
