"""Shared compute executors for background work."""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Callable, Optional


class ComputeHub:
    """Centralized background executors for IO / vector / CPU tasks."""

    def __init__(self) -> None:
        cpu_count = max(2, int(os.cpu_count() or 2))
        self._io_pool = ThreadPoolExecutor(
            max_workers=min(8, cpu_count * 2),
            thread_name_prefix="compute_io",
        )
        self._vector_pool = ThreadPoolExecutor(
            max_workers=max(1, min(2, cpu_count - 1)),
            thread_name_prefix="compute_vec",
        )
        self._cpu_pool = ProcessPoolExecutor(
            max_workers=max(1, cpu_count - 1),
        )
        self._latest_lock = threading.Lock()
        self._latest_futures: dict[str, Future] = {}

    def submit_io(self, fn: Callable, *args, **kwargs) -> Future:
        return self._io_pool.submit(fn, *args, **kwargs)

    def submit_vector(self, fn: Callable, *args, **kwargs) -> Future:
        return self._vector_pool.submit(fn, *args, **kwargs)

    def submit_cpu(self, fn: Callable, *args, **kwargs) -> Future:
        return self._cpu_pool.submit(fn, *args, **kwargs)

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
            elif executor == "cpu":
                future = self.submit_cpu(fn, *args, **kwargs)
            else:
                future = self.submit_vector(fn, *args, **kwargs)
            self._latest_futures[slot] = future
            return future

    def cleanup(self) -> None:
        self._io_pool.shutdown(wait=False, cancel_futures=False)
        self._vector_pool.shutdown(wait=False, cancel_futures=False)
        self._cpu_pool.shutdown(wait=False, cancel_futures=False)


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
