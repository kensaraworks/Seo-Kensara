#!/usr/bin/env python3
"""Seed and verify the Supabase tables behind the enforcement tracker.

Run once after applying ``schema_supabase.sql``:

    python scripts/seed_supabase.py --check     # connectivity only, no writes
    python scripts/seed_supabase.py             # seed if the table is empty
    python scripts/seed_supabase.py --force     # re-apply the bundled rows

Seeding is additive: rows are upserted by ``id`` and nothing is deleted, so
re-running it never destroys rows a reviewer has corrected in the Supabase UI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.supabase_client import SupabaseDB, is_supabase_configured  # noqa: E402
from src.store import enforcement_store as store  # noqa: E402


def check() -> int:
    """Report configuration and table reachability. Returns a shell exit code."""
    if not is_supabase_configured():
        print("✗ Supabase is not configured.")
        print("  Set SUPABASE_URL and SUPABASE_SERVICE_KEY (Dashboard → Settings → API).")
        return 1

    print(f"✓ Supabase configured: {os.getenv('SUPABASE_URL', '(from .env)')}")

    ok = True
    for table in (store.TABLE, store.META_TABLE):
        try:
            SupabaseDB.select_sync(table, select="*", limit=1)
            rows = SupabaseDB.select_sync(table, select="*", limit=1000)
            print(f"✓ {table}: reachable, {len(rows)} row(s) visible")
        except Exception as exc:
            ok = False
            print(f"✗ {table}: {exc}")
            print("  Apply schema_supabase.sql in the Supabase SQL editor first.")

    snapshot = store.load_snapshot(use_cache=False)
    public = store.public_snapshot(snapshot)
    print(
        f"\nTracker source: {snapshot['source']}\n"
        f"  published (verified): {public['statistics']['total_all_sections']}\n"
        f"  pending review:       {public['statistics']['pending_review']}"
    )
    return 0 if ok else 1


def seed(force: bool) -> int:
    if not is_supabase_configured():
        print("✗ Supabase is not configured — nothing to seed.")
        return 1

    bundled = store.load_bundled_snapshot()
    counts = {section: len(bundled.get(section) or []) for section in store.SECTIONS}
    print("Bundled dataset: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    result = store.seed_from_bundle(force=force)
    print(json.dumps(result, indent=2))

    if result.get("status") == "skipped" and result.get("reason") == "table_already_populated":
        print("\nThe table already has rows; pass --force to re-apply the bundled data.")
        return 0
    return 0 if result.get("status") == "seeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify connectivity, write nothing")
    parser.add_argument("--force", action="store_true", help="re-apply the seed over existing rows")
    args = parser.parse_args()

    if args.check:
        return check()
    return seed(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
