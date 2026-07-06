"""Global upstream request gate and cooldown policy."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

from src.config import settings

logger = logging.getLogger(__name__)


class RequestGate:
    def __init__(self):
        self._semaphore = asyncio.Semaphore(max(1, int(settings.max_concurrent_requests or 1)))
        self._state_lock = asyncio.Lock()
        self._next_allowed_at = 0.0
        self._cooldown_until = 0.0
        self._current_cooldown_secs = max(0.0, float(settings.request_cooldown_initial_secs or 0.0))
        self._metrics: Dict[str, int | float] = {
            "granted": 0,
            "waited": 0,
            "cooldown_waits": 0,
            "auth_failures": 0,
            "rate_limits": 0,
            "successes": 0,
            "last_status": 0,
            "last_wait_ms": 0.0,
        }

    def _refresh_limits(self) -> None:
        target = max(1, int(settings.max_concurrent_requests or 1))
        current = getattr(self._semaphore, "_value", target)
        # 仅在首次初始化时精确创建；运行中不热切换 semaphore 容量，避免破坏已有等待者。
        if not hasattr(self, "_configured_max_concurrency"):
            self._configured_max_concurrency = target
        elif self._configured_max_concurrency != target:
            logger.info(
                "[Gate] max_concurrent_requests changed from %s to %s; next process restart will fully apply",
                self._configured_max_concurrency,
                target,
            )
            self._configured_max_concurrency = target
        del current

    @asynccontextmanager
    async def slot(self, label: str) -> AsyncIterator[None]:
        self._refresh_limits()
        loop = asyncio.get_running_loop()
        acquire_started = loop.time()
        await self._semaphore.acquire()
        wait_ms = max(0.0, (loop.time() - acquire_started) * 1000.0)
        if wait_ms >= 1.0:
            self._metrics["waited"] = int(self._metrics["waited"]) + 1

        try:
            async with self._state_lock:
                now = loop.time()
                wait_until = max(self._next_allowed_at, self._cooldown_until)
                sleep_secs = max(0.0, wait_until - now)
                self._metrics["last_wait_ms"] = round(wait_ms + sleep_secs * 1000.0, 2)
                if sleep_secs > 0:
                    self._metrics["cooldown_waits"] = int(self._metrics["cooldown_waits"]) + 1
                    logger.warning(
                        "[Gate] delaying upstream request label=%s sleep=%.2fs cooldown_until=%.2f next_allowed_at=%.2f",
                        label,
                        sleep_secs,
                        self._cooldown_until,
                        self._next_allowed_at,
                    )
                    await asyncio.sleep(sleep_secs)
                self._next_allowed_at = loop.time() + max(0.0, float(settings.request_min_interval_secs or 0.0))
                self._metrics["granted"] = int(self._metrics["granted"]) + 1
            yield
        finally:
            self._semaphore.release()

    async def record_status(self, label: str, status_code: int) -> None:
        code = int(status_code or 0)
        self._metrics["last_status"] = code
        if 200 <= code < 300:
            self._metrics["successes"] = int(self._metrics["successes"]) + 1
            self._current_cooldown_secs = max(0.0, float(settings.request_cooldown_initial_secs or 0.0))
            return

        if code in (401, 403):
            self._metrics["auth_failures"] = int(self._metrics["auth_failures"]) + 1
        if code == 429:
            self._metrics["rate_limits"] = int(self._metrics["rate_limits"]) + 1

        if code not in (401, 403, 429):
            return

        loop = asyncio.get_running_loop()
        async with self._state_lock:
            initial = max(0.0, float(settings.request_cooldown_initial_secs or 0.0))
            maximum = max(initial, float(settings.request_cooldown_max_secs or initial))
            cooldown_secs = self._current_cooldown_secs if self._current_cooldown_secs > 0 else initial
            cooldown_secs = max(initial, min(maximum, cooldown_secs))
            self._cooldown_until = max(self._cooldown_until, loop.time() + cooldown_secs)
            self._current_cooldown_secs = min(maximum, max(initial, cooldown_secs * 2 if cooldown_secs else initial))

        if code in (401, 403):
            try:
                from src.services.browser import browser_manager

                await browser_manager.invalidate_cached_headers(
                    f"upstream_auth_failed:{label}:{code}",
                    mark_logged_out=True,
                )
            except Exception as exc:
                logger.debug("[Gate] failed to invalidate browser auth state: %s", exc)

        logger.warning(
            "[Gate] upstream status=%s label=%s cooldown=%.1fs auth_failures=%s rate_limits=%s",
            code,
            label,
            cooldown_secs,
            self._metrics["auth_failures"],
            self._metrics["rate_limits"],
        )

    def snapshot(self) -> Dict[str, int | float]:
        return dict(self._metrics)


request_gate = RequestGate()
