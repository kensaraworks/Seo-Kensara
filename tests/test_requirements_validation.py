from pathlib import Path


def _iter_requirement_lines(req_path: Path):
    for raw in req_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield line


def test_requirements_are_pinned():
    req_path = Path("requirements.txt")
    lines = list(_iter_requirement_lines(req_path))

    # Deployment requirement: no unpinned entries.
    unpinned = [line for line in lines if "==" not in line]
    assert unpinned == [], f"Unpinned requirements found: {unpinned}"


def test_required_runtime_packages_present():
    req_path = Path("requirements.txt")
    lines = list(_iter_requirement_lines(req_path))

    names = {line.split("==", 1)[0].lower() for line in lines}
    required = {
        "chromadb",
        "sentence-transformers",
        "rank-bm25",
        "langchain-text-splitters",
        "pytrends",
        "curl-cffi",
    }
    missing = sorted(required - names)
    assert missing == [], f"Missing required runtime packages in requirements.txt: {missing}"
