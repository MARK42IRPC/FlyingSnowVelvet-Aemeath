"""Shared compute executors for background work."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable, Optional


class ComputeHub:
    """Centralized background executors for IO / vector / CPU tasks."""

    def __init__(self) -> None:
        import os

        cpu_count = max(2, int(os.cpu_count() or 2))
        self._io_pool = ThreadPoolExecutor(
            max_workers=min(8, cpu_count * 2),
            thread_name_prefix="compute_io",
        )
        self._interactive_io_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="compute_interactive_io",
        )
        self._vector_pool = ThreadPoolExecutor(
            max_workers=max(1, min(2, cpu_count - 1)),
            thread_name_prefix="compute_vec",
        )
        self._state_lock = threading.Lock()
        self._closed = False
        self._futures: set[Future] = set()
        self._latest_lock = threading.Lock()
        self._latest_futures: dict[str, Future] = {}

    def submit_io(self, fn: Callable, *args, **kwargs) -> Future:
        return self._submit(self._io_pool, fn, *args, **kwargs)

    def submit_interactive_io(self, fn: Callable, *args, **kwargs) -> Future:
        """Run user-triggered I/O without waiting behind shared background work."""
        return self._submit(self._interactive_io_pool, fn, *args, **kwargs)

    def submit_vector(self, fn: Callable, *args, **kwargs) -> Future:
        return self._submit(self._vector_pool, fn, *args, **kwargs)

    def _submit(self, executor: ThreadPoolExecutor, fn: Callable, *args, **kwargs) -> Future:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("ComputeHub is already closed")
            future = executor.submit(fn, *args, **kwargs)
            self._futures.add(future)
        future.add_done_callback(self._discard_future)
        return future

    def _discard_future(self, future: Future) -> None:
        with self._state_lock:
            self._futures.discard(future)

    def submit_latest(
        self,
        slot: str,
        fn: Callable,
        *args,
        executor: str = "vector",
        **kwargs,
    ) -> Optional[Future]:
        """
        Submit only when the previous future in the same slot has finished.

        Returns None when a task is already running for the slot.
        """
        with self._latest_lock:
            future = self._latest_futures.get(slot)
            if future is not None and not future.done():
                return None
            if executor == "io":
                future = self.submit_io(fn, *args, **kwargs)
            elif executor == "vector":
                future = self.submit_vector(fn, *args, **kwargs)
            else:
                raise ValueError(f"Unsupported executor: {executor}")
            self._latest_futures[slot] = future
            return future

    def cleanup(self, timeout: float = 3.0) -> None:
        """Stop accepting work, cancel queued tasks, and wait briefly for active work."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        if futures:
            wait(futures, timeout=max(0.0, float(timeout)))
        self._io_pool.shutdown(wait=False, cancel_futures=True)
        self._interactive_io_pool.shutdown(wait=False, cancel_futures=True)
        self._vector_pool.shutdown(wait=False, cancel_futures=True)
        with self._latest_lock:
            self._latest_futures.clear()


_instance: ComputeHub | None = None


def get_compute_hub() -> ComputeHub:
    global _instance
    if _instance is None:
        _instance = ComputeHub()
    return _instance


def cleanup_compute_hub() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
