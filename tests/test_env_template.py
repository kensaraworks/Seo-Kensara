"""The generated .env template and its validator.

A bulk import into a dashboard is unforgiving: a typo'd name or a blank value
that overrides a default produces a site that starts and quietly does nothing,
with no error anywhere. These tests keep the template honest and the checker
able to catch each failure before the import.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.vercel.example"


def _run_checker(path: Path):
    return subprocess.run(
        [sys.executable, "scripts/check_env.py", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_template_exists_and_passes_its_own_checker():
    assert TEMPLATE.exists(), "run scripts/make_env_template.py"
    result = _run_checker(TEMPLATE)
    assert result.returncode == 0, result.stdout


def test_template_covers_every_settings_field():
    from src.config import Settings

    from scripts.make_env_template import OMITTED

    present = {
        line.split("=", 1)[0].strip()
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    }
    expected = {name.upper() for name in Settings.model_fields} - set(OMITTED)
    assert expected <= present, f"template is missing: {sorted(expected - present)}"


def test_fields_with_real_defaults_are_never_emitted_blank():
    """A blank value overrides the default with an empty string."""
    from src.config import Settings

    defaults = {name.upper(): field.default for name, field in Settings.model_fields.items()}
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        default = defaults.get(key.strip())
        if default in (None, "", False) or default is None:
            continue
        assert value.strip(), f"{key.strip()} is blank but defaults to {default!r}"


def test_template_contains_no_placeholder_values():
    from src.config import PLACEHOLDER_SECRETS

    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        value = line.partition("=")[2].strip().lower()
        assert value not in (PLACEHOLDER_SECRETS - {""}), f"placeholder in template: {line}"


def test_template_holds_no_credentials():
    """It is committed, so it must never carry a real value."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for marker in ("sk-", "gsk_", "nvapi-", "eyJ", "tvly-"):
        assert marker not in text, f"{marker!r} looks like a live credential in {TEMPLATE.name}"


@pytest.mark.parametrize(
    "line,expected_fragment",
    [
        ("NVIDIA_MODEL_BLOG=", "overrides its default"),
        ("TAVILY_API_KEY=replace_me", "placeholder"),
        ('SERPER_API_KEY="sk-quoted"', "quoted"),
        ("GROQ_API_KEYZ=x", "nothing in the app reads"),
        ("DATA_DIR=drafts", "should not be set here"),
    ],
)
def test_checker_rejects_each_import_hazard(tmp_path, line, expected_fragment):
    env_file = tmp_path / "candidate.env"
    env_file.write_text(line + "\n", encoding="utf-8")
    result = _run_checker(env_file)
    assert result.returncode == 1, f"{line!r} should have been rejected\n{result.stdout}"
    assert expected_fragment in result.stdout


def test_checker_flags_a_duplicated_name(tmp_path):
    env_file = tmp_path / "candidate.env"
    env_file.write_text("GROQ_API_KEY=one\nGROQ_API_KEY=two\n", encoding="utf-8")
    result = _run_checker(env_file)
    assert result.returncode == 1
    assert "more than once" in result.stdout
