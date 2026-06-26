from datetime import date

from src.engines.content_calendar import (
    CalendarAction,
    CalendarSlot,
    build_calendar_window,
    capacity_alert_payload,
    detect_content_gap,
    evaluate_queue_capacity,
    get_calendar_slot,
    should_generate_newsjack,
    sort_pending_review_items,
)


def test_newsjack_requires_score_12_and_available_queue_capacity():
    allowed, reason = should_generate_newsjack(story_score=11, pending_count=0)
    assert allowed is False
    assert "below" in reason

    allowed, reason = should_generate_newsjack(story_score=12, pending_count=10)
    assert allowed is False
    assert "full" in reason

    allowed, _ = should_generate_newsjack(story_score=12, pending_count=6)
    assert allowed is True


def test_weekly_calendar_cadence_and_skip_days():
    monday = get_calendar_slot(date(2026, 6, 29))
    assert monday.action == CalendarAction.TIER2_INDUSTRY_PLAYBOOK
    assert monday.tier == 2

    wednesday = get_calendar_slot(date(2026, 7, 8), intelligence_score=10)
    assert wednesday.action == CalendarAction.TIER1_REGULATORY_DEEP_DIVE
    assert wednesday.tier == 1

    friday = get_calendar_slot(date(2026, 7, 3))
    assert friday.action == CalendarAction.SUPPORTING_CLUSTER_POST

    saturday = get_calendar_slot(date(2026, 7, 4))
    assert saturday.action == CalendarAction.SKIP


def test_queue_capacity_alerts_at_7_and_blocks_at_10():
    warning = evaluate_queue_capacity(7)
    assert warning.should_alert_ceo is True
    assert warning.is_full is False

    full = evaluate_queue_capacity(10)
    assert full.is_full is True

    payload = capacity_alert_payload(10)
    assert payload is not None
    assert payload["type"] == "capacity_full"


def test_review_priority_matches_module_210_order():
    items = [
        {"title": "Regular Tier 2", "tier": 2},
        {"title": "Refresh", "content_type": "refresh"},
        {"title": "Pillar", "content_type": "pillar"},
        {"title": "Near page one", "rank_position": 12},
        {"title": "Zero coverage", "zero_coverage": True},
        {"title": "Newsjack", "tier": 3},
    ]

    ordered = sort_pending_review_items(items)
    assert [item["title"] for item in ordered] == [
        "Newsjack",
        "Near page one",
        "Zero coverage",
        "Pillar",
        "Regular Tier 2",
        "Refresh",
    ]


def test_content_gap_alert_only_when_no_scheduled_content_and_low_queue():
    empty_slots = [
        CalendarSlot(
            run_date=date(2026, 7, 4),
            action=CalendarAction.SKIP,
            tier=None,
            content_type="none",
            reason="weekend",
        )
    ]

    alert = detect_content_gap(
        scheduled_slots=empty_slots,
        pending_count=2,
        top_gap_keywords=[{"keyword": "dpdpa consent fintech"}],
        start_date=date(2026, 7, 4),
    )
    assert alert is not None
    assert "dpdpa consent fintech" in alert.message

    no_alert = detect_content_gap(
        scheduled_slots=build_calendar_window(date(2026, 6, 29), days=7),
        pending_count=2,
        top_gap_keywords=[],
        start_date=date(2026, 6, 29),
    )
    assert no_alert is None
