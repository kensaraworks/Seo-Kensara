"""Requirements layout guards.

The dependencies are split three ways:

    requirements.txt           web runtime — what Vercel installs
    requirements-pipeline.txt  scraping / LLM / Google SDKs (long-running hosts)
    requirements-dev.txt       test tooling

The split exists because the serverless bundle has a hard size limit, so these
tests assert that the heavy packages stay out of the runtime file and that the
runtime file alone is enough to serve the web app.
"""
from pathlib import Path

RUNTIME = Path("requirements.txt")
PIPELINE = Path("requirements-pipeline.txt")
DEV = Path("requirements-dev.txt")


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        lines.append(line)
    return lines


def _names(path: Path) -> set[str]:
    return {line.split("==", 1)[0].split("[", 1)[0].strip().lower() for line in _requirement_lines(path)}


def test_all_requirements_are_pinned():
    for path in (RUNTIME, PIPELINE, DEV):
        unpinned = [line for line in _requirement_lines(path) if "==" not in line]
        assert unpinned == [], f"Unpinned requirements in {path}: {unpinned}"


def test_runtime_has_what_the_web_app_imports():
    required = {"fastapi", "uvicorn", "pydantic", "pydantic-settings", "httpx", "jinja2", "structlog"}
    missing = sorted(required - _names(RUNTIME))
    assert missing == [], f"Missing runtime packages in {RUNTIME}: {missing}"


def test_heavy_packages_stay_out_of_the_serverless_bundle():
    """These pull ~150MB of wheels the serverless web app never imports."""
    heavy = {
        "google-api-python-client",
        "curl-cffi",
        "pytrends",
        "feedparser",
        "beautifulsoup4",
        "openai",
        "groq",
    }
    leaked = sorted(heavy & _names(RUNTIME))
    assert leaked == [], f"Heavy packages must live in {PIPELINE}, not {RUNTIME}: {leaked}"


def test_pipeline_carries_the_agent_dependencies():
    required = {"feedparser", "beautifulsoup4", "curl-cffi", "pytrends", "openai", "groq", "tavily-python"}
    missing = sorted(required - _names(PIPELINE))
    assert missing == [], f"Missing pipeline packages in {PIPELINE}: {missing}"


def test_pipeline_and_dev_build_on_the_runtime_file():
    assert "-r requirements.txt" in PIPELINE.read_text(encoding="utf-8")
    assert "-r requirements-pipeline.txt" in DEV.read_text(encoding="utf-8")


def test_test_tooling_is_not_in_the_runtime_bundle():
    leaked = sorted({"pytest", "pytest-asyncio", "pytest-mock"} & _names(RUNTIME))
    assert leaked == [], f"Test tooling must live in {DEV}: {leaked}"
