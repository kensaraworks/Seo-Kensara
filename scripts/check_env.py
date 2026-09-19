#!/usr/bin/env python3
"""Check a .env file before importing it into Vercel.

    python scripts/check_env.py .env.vercel

Catches the mistakes that produce a site which starts but does nothing useful,
and which are invisible in the Vercel dashboard:

* a typo'd name — the variable is set, but nothing reads it
* a blank value for a field with a real default — pydantic treats "set to
  empty" as a value, so a blank NVIDIA_MODEL_BLOG overrides the model name
  rather than falling back to it
* a placeholder such as replace_me — the app reads it as unset, which looks
  identical to a missing key
* a quoted or whitespace-padded value — imported verbatim, so "sk-abc" arrives
  with the quotes attached and every API call fails on a bad credential
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import PLACEHOLDER_SECRETS, Settings  # noqa: E402
from scripts.make_env_template import EXTRA_VARS, OMITTED  # noqa: E402

SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def parse_env(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            problems.append(f"line {number}: no '=' — {line[:40]!r}")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in values:
            problems.append(f"line {number}: {key} is defined more than once")
        values[key] = value
    return values, problems


def known_defaults() -> dict[str, object]:
    defaults = {name.upper(): field.default for name, field in Settings.model_fields.items()}
    defaults.update({key: default for key, (default, _) in EXTRA_VARS.items()})
    return defaults


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", help="the .env file to check")
    args = parser.parse_args()

    path = Path(args.env_file)
    if not path.exists():
        print(f"✗ {path} does not exist")
        return 2

    values, errors = parse_env(path)
    defaults = known_defaults()
    warnings: list[str] = []

    for key, value in values.items():
        stripped = value.strip()

        # Checked before the known-field test: several of these ARE real fields,
        # they just must not be set on a serverless deployment.
        if key in OMITTED:
            errors.append(f"{key}: should not be set here — {OMITTED[key]}")
            continue

        if key not in defaults:
            errors.append(f"{key}: nothing in the app reads this name — check the spelling")
            continue

        if stripped != value:
            errors.append(f"{key}: has surrounding whitespace, which is imported verbatim")
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
            errors.append(f"{key}: is quoted — the quotes become part of the value")
        if stripped.lower() in PLACEHOLDER_SECRETS and stripped:
            errors.append(f"{key}: still holds the placeholder {stripped!r}; the app reads it as unset")

        default = defaults[key]
        if not stripped and default not in ("", None, False):
            errors.append(
                f"{key}: blank, which overrides its default ({default!r}) with an empty string. "
                "Give it a value or remove the line."
            )
        elif not stripped and any(hint in key for hint in SECRET_HINTS):
            warnings.append(f"{key}: no value — the feature it powers stays off")

    for key in sorted(set(defaults) - set(values) - set(OMITTED)):
        warnings.append(f"{key}: not in this file — the app will use its default")

    print(f"Checked {path} — {len(values)} variables\n")
    for message in errors:
        print(f"  ✗ {message}")
    for message in warnings:
        print(f"  · {message}")

    if errors:
        print(f"\n{len(errors)} problem(s) to fix before importing.")
        return 1
    print("\nNo problems. Import it, then redeploy — variables only reach")
    print("deployments created after they were saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
