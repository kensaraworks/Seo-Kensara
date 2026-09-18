"""Scheduler job-table sanity checks.

Every job is declared as `module:function` and resolved lazily, so a typo would
otherwise only show up at 6am IST on a Thursday.
"""
from __future__ import annotations

import pytest

from src.ui import scheduler


def test_job_ids_are_unique():
    ids = [spec[0] for spec in scheduler.JOB_SPECS]
    assert len(ids) == len(set(ids)), "duplicate job ids would silently replace each other"


@pytest.mark.parametrize("spec", scheduler.JOB_SPECS, ids=lambda s: s[0])
def test_every_job_target_resolves_to_a_callable(spec):
    job_id, target, trigger_kwargs, name = spec
    assert callable(scheduler._resolve(target)), f"{job_id}: {target} is not callable"
    assert name, f"{job_id} has no display name"
    assert trigger_kwargs, f"{job_id} has no trigger"


@pytest.mark.parametrize("spec", scheduler.JOB_SPECS, ids=lambda s: s[0])
def test_every_trigger_is_valid_cron(spec):
    from apscheduler.triggers.cron import CronTrigger

    CronTrigger(timezone=scheduler.TIMEZONE, **spec[2])


def test_enforcement_tracker_runs_weekly():
    spec = next(s for s in scheduler.JOB_SPECS if s[0] == "enforcement_tracker_update")
    assert spec[2]["day_of_week"] == "thu"


def test_scheduler_skips_jobs_whose_module_is_missing(monkeypatch):
    """One unimportable agent must not stop the others from being scheduled."""
    def explode(target):
        if "enforcement" in target:
            raise ImportError("simulated missing dependency")
        return lambda: None

    monkeypatch.setattr(scheduler, "_resolve", explode)
    built = scheduler.build_scheduler()
    assert built is not None
    try:
        ids = {job.id for job in built.get_jobs()}
        assert "enforcement_tracker_update" not in ids
        assert "news_scan" in ids
    finally:
        built.shutdown(wait=False)
