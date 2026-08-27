"""Stable data contracts shared by the office runtime and its UI."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum


class InteractionMode(str, Enum):
    COMPANION = "companion"
    OFFICE = "office"


class OfficeTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_TASK = "allow_task"
    REJECT = "reject"


REASONING_EFFORTS = ("off", "high", "max")
DEFAULT_REASONING_EFFORT = "high"
ACTIVE_TASK_STATUSES = frozenset({
    OfficeTaskStatus.QUEUED.value,
    OfficeTaskStatus.RUNNING.value,
    OfficeTaskStatus.WAITING_APPROVAL.value,
})


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_mode(value: object) -> InteractionMode:
    if isinstance(value, InteractionMode):
        return value
    try:
        return InteractionMode(str(value or "").strip().lower())
    except ValueError as exc:
        raise ValueError("interaction mode must be 'companion' or 'office'") from exc


def normalize_reasoning_effort(value: object) -> str:
    effort = str(value or DEFAULT_REASONING_EFFORT).strip().lower()
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {effort!r}")
    return effort


def task_title(prompt: str, limit: int = 36) -> str:
    text = " ".join(str(prompt or "").split())
    if not text:
        return "未命名任务"
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "..."
