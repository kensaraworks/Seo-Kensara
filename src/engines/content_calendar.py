"""Content calendar and queue management for Module 2.10.

This module is deliberately deterministic. It decides whether content should be
generated, how queue capacity is enforced, how pending review items are ordered,
and when the CEO should be alerted about content gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any, Iterable


MAX_PENDING_QUEUE = 10
CEO_ALERT_THRESHOLD = 7
NEWSJACK_SCORE_THRESHOLD = 12
CONTENT_GAP_LOOKAHEAD_DAYS = 7
CONTENT_GAP_PENDING_THRESHOLD = 3


class CalendarAction(str, Enum):
    SKIP = "skip"
    TIER2_INDUSTRY_PLAYBOOK = "tier2_industry_playbook"
    TIER1_REGULATORY_DEEP_DIVE = "tier1_regulatory_deep_dive"
    SUPPORTING_CLUSTER_POST = "supporting_cluster_post"
    TIER3_NEWSJACK = "tier3_newsjack"
    NEWSLETTER_DIGEST = "newsletter_digest"
    PILLAR_REFRESH = "pillar_refresh"


class QueueAlertType(str, Enum):
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_FULL = "capacity_full"
    CONTENT_GAP = "content_gap"


@dataclass(frozen=True)
class CalendarSlot:
    """One deterministic calendar recommendation for a given date."""

    run_date: date
    action: CalendarAction
    tier: int | None
    content_type: str
    reason: str
    source: str = "calendar"
    industry: str | None = None
    requires_manual_trigger: bool = False


@dataclass(frozen=True)
class QueueCapacity:
    pending_count: int
    max_pending: int = MAX_PENDING_QUEUE
    alert_threshold: int = CEO_ALERT_THRESHOLD

    @property
    def is_full(self) -> bool:
        return self.pending_count >= self.max_pending

    @property
    def should_alert_ceo(self) -> bool:
        return self.pending_count >= self.alert_threshold

    @property
    def remaining_slots(self) -> int:
        return max(0, self.max_pending - self.pending_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_count": self.pending_count,
            "max_pending": self.max_pending,
            "alert_threshold": self.alert_threshold,
            "is_full": self.is_full,
            "should_alert_ceo": self.should_alert_ceo,
            "remaining_slots": self.remaining_slots,
        }


@dataclass(frozen=True)
class ContentGapAlert:
    date_range_start: date
    date_range_end: date
    suggested_keywords: list[dict[str, Any]] = field(default_factory=list)

    @property
    def message(self) -> str:
        suggestions = ", ".join(
            item.get("keyword", "") for item in self.suggested_keywords[:3]
            if item.get("keyword")
        )
        if not suggestions:
            suggestions = "top 3 cluster gap keywords from coverage score"
        return (
            "Content gap detected - no posts scheduled for "
            f"{self.date_range_start.isoformat()} to {self.date_range_end.isoformat()}. "
            f"Suggest generating: {suggestions}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": QueueAlertType.CONTENT_GAP.value,
            "date_range_start": self.date_range_start.isoformat(),
            "date_range_end": self.date_range_end.isoformat(),
            "suggested_keywords": self.suggested_keywords[:3],
            "message": self.message,
        }


INDUSTRY_ROTATION = (
    "fintech",
    "healthtech",
    "edtech",
    "e-commerce",
    "saas",
    "insurance",
    "banking",
    "hr-tech",
)


def evaluate_queue_capacity(pending_count: int) -> QueueCapacity:
    """Return the queue capacity state for Module 2.10.B."""

    return QueueCapacity(pending_count=max(0, pending_count))


def should_generate_newsjack(story_score: int, pending_count: int) -> tuple[bool, str]:
    """Apply the daily Tier 3 newsjack gate.

    A story must score at least 12, and the pending review queue must not be
    full. This keeps the agent from generating weak daily posts or overwhelming
    the CEO review queue.
    """

    capacity = evaluate_queue_capacity(pending_count)
    if capacity.is_full:
        return False, "pending queue is full; pause intelligence-triggered generation"
    if story_score < NEWSJACK_SCORE_THRESHOLD:
        return False, "story score below Tier 3 newsjack threshold"
    return True, "story qualifies for Tier 3 newsjack"


def get_calendar_slot(
    run_date: date | None = None,
    *,
    intelligence_score: int = 0,
) -> CalendarSlot:
    """Return the planned content slot for a date.

    Weekly cadence:
    - Monday: Tier 2 industry playbook from the 8-week rotation.
    - Wednesday: Tier 1 if intelligence is strong, otherwise Tier 2.
    - Friday: supporting cluster gap post.
    - First day of month: newsletter digest.
    - First day of quarter: pillar refresh candidate.
    """

    run_date = run_date or date.today()

    if intelligence_score >= NEWSJACK_SCORE_THRESHOLD:
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.TIER3_NEWSJACK,
            tier=3,
            content_type="tier3",
            reason="intelligence layer scored a story >= 12",
            source="intelligence",
        )

    if run_date.day == 1 and run_date.month in (1, 4, 7, 10):
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.PILLAR_REFRESH,
            tier=0,
            content_type="pillar_refresh",
            reason="quarterly pillar refresh for lowest-scoring cluster pillar",
        )

    if run_date.day == 1:
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.NEWSLETTER_DIGEST,
            tier=None,
            content_type="newsletter",
            reason="first of month newsletter digest",
            requires_manual_trigger=False,
        )

    weekday = run_date.weekday()
    if weekday == 0:
        industry = INDUSTRY_ROTATION[run_date.isocalendar().week % len(INDUSTRY_ROTATION)]
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.TIER2_INDUSTRY_PLAYBOOK,
            tier=2,
            content_type="tier2",
            reason="Monday industry playbook slot from the 8-week rotation",
            industry=industry,
        )

    if weekday == 2:
        if intelligence_score >= 10:
            return CalendarSlot(
                run_date=run_date,
                action=CalendarAction.TIER1_REGULATORY_DEEP_DIVE,
                tier=1,
                content_type="tier1",
                reason="Wednesday news-driven regulatory deep dive slot",
                source="intelligence",
            )
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.TIER2_INDUSTRY_PLAYBOOK,
            tier=2,
            content_type="tier2",
            reason="Wednesday slot defaults to Tier 2 when intelligence is moderate",
        )

    if weekday == 4:
        return CalendarSlot(
            run_date=run_date,
            action=CalendarAction.SUPPORTING_CLUSTER_POST,
            tier=2,
            content_type="supporting_cluster",
            reason="Friday supporting cluster post for gap keyword coverage",
            source="cluster_gap",
        )

    return CalendarSlot(
        run_date=run_date,
        action=CalendarAction.SKIP,
        tier=None,
        content_type="none",
        reason="no forced daily post; quality over frequency",
    )


def build_calendar_window(
    start_date: date | None = None,
    days: int = CONTENT_GAP_LOOKAHEAD_DAYS,
    *,
    intelligence_scores: dict[date, int] | None = None,
) -> list[CalendarSlot]:
    """Return planned slots for the next N days."""

    start_date = start_date or date.today()
    intelligence_scores = intelligence_scores or {}
    return [
        get_calendar_slot(
            start_date + timedelta(days=offset),
            intelligence_score=intelligence_scores.get(start_date + timedelta(days=offset), 0),
        )
        for offset in range(days)
    ]


def review_priority(item: dict[str, Any]) -> tuple[int, int, float, str]:
    """Return a sortable priority tuple for CEO review ordering.

    Lower tuple values are reviewed first. Implements 2.10.C:
    Tier 3 newsjacks, ranking position 8-20, zero-coverage cluster keywords,
    pillar pages, regular Tier 2 posts, then refresh posts.
    """

    content_type = str(item.get("content_type") or item.get("post_type") or "").lower()
    tier = _as_int(item.get("tier"))
    rank_position = _as_int(item.get("rank_position") or item.get("current_rank"))
    zero_coverage = _as_bool(item.get("zero_coverage") or item.get("is_zero_coverage"))
    queued_score = _as_float(item.get("priority_score"), default=0.0)
    title = str(item.get("title") or item.get("keyword") or "")

    if tier == 3 or content_type in {"tier3", "newsjack", "tier3_newsjack"}:
        bucket = 1
    elif rank_position is not None and 8 <= rank_position <= 20:
        bucket = 2
    elif zero_coverage:
        bucket = 3
    elif tier == 0 or "pillar" in content_type:
        bucket = 4
    elif tier == 2 or content_type in {"tier2", "supporting_cluster"}:
        bucket = 5
    elif "refresh" in content_type:
        bucket = 6
    else:
        bucket = 7

    rank_sort = rank_position if rank_position is not None else 999
    return (bucket, rank_sort, -queued_score, title.lower())


def sort_pending_review_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort pending review records by the Module 2.10.C review priority."""

    return sorted(items, key=review_priority)


def detect_content_gap(
    scheduled_slots: Iterable[CalendarSlot | dict[str, Any]],
    pending_count: int,
    top_gap_keywords: list[dict[str, Any]],
    *,
    start_date: date | None = None,
) -> ContentGapAlert | None:
    """Return a CEO alert when the next week has no scheduled content.

    This is alert-only by design. It never auto-generates content.
    """

    start_date = start_date or date.today()
    has_scheduled_content = any(_slot_has_content(slot) for slot in scheduled_slots)
    if has_scheduled_content or pending_count >= CONTENT_GAP_PENDING_THRESHOLD:
        return None
    return ContentGapAlert(
        date_range_start=start_date,
        date_range_end=start_date + timedelta(days=CONTENT_GAP_LOOKAHEAD_DAYS - 1),
        suggested_keywords=top_gap_keywords[:3],
    )


def capacity_alert_payload(pending_count: int) -> dict[str, Any] | None:
    """Build a CEO alert payload when the review queue is near or at capacity."""

    capacity = evaluate_queue_capacity(pending_count)
    if capacity.is_full:
        return {
            "type": QueueAlertType.CAPACITY_FULL.value,
            "message": (
                f"Pending queue has reached {pending_count} items. "
                "Pause intelligence-triggered generation until review clears."
            ),
            **capacity.to_dict(),
        }
    if capacity.should_alert_ceo:
        return {
            "type": QueueAlertType.CAPACITY_WARNING.value,
            "message": (
                f"Pending queue has {pending_count} items. "
                "CEO review is approaching the 10-item cap."
            ),
            **capacity.to_dict(),
        }
    return None


def _slot_has_content(slot: CalendarSlot | dict[str, Any]) -> bool:
    if isinstance(slot, CalendarSlot):
        return slot.action not in {CalendarAction.SKIP, CalendarAction.NEWSLETTER_DIGEST}
    action = str(slot.get("action", "")).lower()
    return action not in {"", CalendarAction.SKIP.value, CalendarAction.NEWSLETTER_DIGEST.value}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
