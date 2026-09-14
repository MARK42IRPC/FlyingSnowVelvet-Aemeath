"""Byte-budgeted LRU pool behind the startup UI pre-render cache.

Startup pre-rendering builds a few lazily-created toolkit windows ahead of time
and keeps them in memory so the first open is instant. Those windows live in a
dedicated pool with a hard byte budget: the pool only accounts the estimated
size each entry declares, evicts the least-recently-used entry once the budget
is exceeded and hands the evicted payload back to the caller through an
eviction callback (which is where the toolkit releases the window).

The pool deliberately imports no GUI library: the caller supplies the size
estimate and the release callback, so this stays backend-neutral and unit
testable. Entries can be protected from eviction (for example while the window
is visible); when nothing can be evicted the new entry is rejected instead of
silently exceeding the budget.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


#: Hard ceiling for the startup UI cache: 30 MiB.
DEFAULT_BUDGET_BYTES = 30 * 1024 * 1024
MAX_BUDGET_BYTES = 30 * 1024 * 1024

EvictCallback = Callable[[str, Any], None]
ProtectCallback = Callable[[str, Any], bool]


@dataclass(frozen=True, slots=True)
class UiCacheStats:
    """Snapshot of the pool for logs and tests."""

    entries: int
    bytes: int
    budget_bytes: int
    evictions: int
    rejected: int


class UiCachePool:
    """Least-recently-used pool bounded by an estimated byte budget."""

    def __init__(
        self,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        *,
        on_evict: EvictCallback | None = None,
    ) -> None:
        self._budget = max(1, min(int(budget_bytes), MAX_BUDGET_BYTES))
        self._on_evict = on_evict
        self._entries: "OrderedDict[str, tuple[Any, int]]" = OrderedDict()
        self._evictions = 0
        self._rejected = 0

    # ── 只读状态 ─────────────────────────────────────────────────────

    @property
    def budget_bytes(self) -> int:
        return self._budget

    @property
    def bytes(self) -> int:
        return sum(size for _payload, size in self._entries.values())

    @property
    def keys(self) -> tuple[str, ...]:
        """Keys ordered from least to most recently used."""
        return tuple(self._entries.keys())

    @property
    def stats(self) -> UiCacheStats:
        return UiCacheStats(
            entries=len(self._entries),
            bytes=self.bytes,
            budget_bytes=self._budget,
            evictions=self._evictions,
            rejected=self._rejected,
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    # ── 读写 ─────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Return the payload and mark it as most recently used."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry[0]

    def touch(self, key: str) -> bool:
        """Refresh the recency of ``key`` without reading its payload."""
        if key not in self._entries:
            return False
        self._entries.move_to_end(key)
        return True

    def store(
        self,
        key: str,
        payload: Any,
        size_bytes: int,
        *,
        protect: ProtectCallback | None = None,
    ) -> bool:
        """Insert ``payload`` after making room; return whether it was kept.

        An entry larger than the whole budget, or one that cannot be stored
        because every existing entry is protected, is rejected instead of
        pushing the pool over budget.
        """
        size = max(0, int(size_bytes))
        self._entries.pop(key, None)
        if size > self._budget:
            self._rejected += 1
            return False
        while self.bytes + size > self._budget:
            if self.evict_one(protect=protect) is None:
                self._rejected += 1
                return False
        self._entries[key] = (payload, size)
        return True

    def discard(self, key: str) -> bool:
        """Drop one entry and run its eviction callback."""
        entry = self._entries.pop(key, None)
        if entry is None:
            return False
        self._notify_evict(key, entry[0])
        return True

    def evict_one(self, *, protect: ProtectCallback | None = None) -> str | None:
        """Evict the least recently used unprotected entry."""
        for key, (payload, _size) in list(self._entries.items()):
            if protect is not None and protect(key, payload):
                continue
            del self._entries[key]
            self._evictions += 1
            self._notify_evict(key, payload)
            return key
        return None

    def clear(self) -> None:
        """Evict every entry."""
        for key in list(self._entries.keys()):
            self.discard(key)

    def _notify_evict(self, key: str, payload: Any) -> None:
        callback = self._on_evict
        if callback is None:
            return
        try:
            callback(key, payload)
        except Exception:
            pass


__all__ = [
    "DEFAULT_BUDGET_BYTES",
    "MAX_BUDGET_BYTES",
    "EvictCallback",
    "ProtectCallback",
    "UiCachePool",
    "UiCacheStats",
]
