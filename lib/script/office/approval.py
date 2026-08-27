"""Task-scoped approval memory layered over DSH one-shot grants."""

from __future__ import annotations

import threading

from .contracts import ApprovalDecision


class TaskApprovalPolicy:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._allow_all_tasks: set[str] = set()

    def should_auto_allow(self, task_id: str) -> bool:
        with self._lock:
            return str(task_id or "") in self._allow_all_tasks

    def resolve(self, task_id: str, decision: ApprovalDecision | str) -> str:
        resolved = ApprovalDecision(str(decision or "").strip().lower())
        if resolved is ApprovalDecision.ALLOW_TASK:
            with self._lock:
                self._allow_all_tasks.add(str(task_id or ""))
            return "allowed-once"
        if resolved is ApprovalDecision.ALLOW:
            return "allowed-once"
        return "rejected"

    def forget(self, task_id: str) -> None:
        with self._lock:
            self._allow_all_tasks.discard(str(task_id or ""))

    def clear(self) -> None:
        with self._lock:
            self._allow_all_tasks.clear()
