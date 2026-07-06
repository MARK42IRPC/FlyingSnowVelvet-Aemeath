"""Shared lifecycle helpers for local subprocess-backed services."""

import subprocess
import threading
from typing import Optional, Tuple

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

logger = get_logger(__name__)


class LocalHostedServiceBase:
    """Common APP_PRE_START and process-state lifecycle for local services."""

    def __init__(self, service_name: str, prestart_task_name: str):
        self._service_name = service_name
        self._prestart_task_name = prestart_task_name
        self._ec = get_event_center()
        self._proc_lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._started_by_app = False
        self._prestart_lock = threading.Lock()
        self._prestart_started = False

    def _subscribe_app_pre_start(self) -> None:
        self._ec.subscribe(EventType.APP_PRE_START, self._on_app_pre_start)

    def _unsubscribe_app_pre_start(self) -> None:
        self._ec.unsubscribe(EventType.APP_PRE_START, self._on_app_pre_start)

    def _on_app_pre_start(self, event: Event) -> None:
        del event
        self.kickoff_prestart()

    def _should_prestart(self) -> bool:
        return True

    def kickoff_prestart(self) -> None:
        if not self._should_prestart():
            return
        with self._prestart_lock:
            if self._prestart_started:
                return
            self._prestart_started = True
        future = get_compute_hub().submit_latest(
            self._prestart_task_name,
            self._prestart_worker,
            executor="io",
        )
        if future is None:
            logger.debug('[%s] 预启动任务仍在运行，跳过重复提交', self._service_name)

    def _prestart_worker(self):
        raise NotImplementedError

    def _tracked_process(self) -> Optional[subprocess.Popen]:
        with self._proc_lock:
            return self._process

    def _has_running_tracked_process(self) -> bool:
        with self._proc_lock:
            return self._process is not None and self._process.poll() is None

    def _set_started_process(self, proc: subprocess.Popen) -> None:
        self._process = proc
        self._started_by_app = True

    def _mark_process_start_failed(self) -> None:
        self._process = None
        self._started_by_app = False

    def _clear_tracked_process_if(self, proc: subprocess.Popen) -> None:
        with self._proc_lock:
            if self._process is proc:
                self._process = None
                self._started_by_app = False

    def _take_tracked_process(self) -> Tuple[Optional[subprocess.Popen], bool]:
        with self._proc_lock:
            proc = self._process
            started = self._started_by_app
            self._process = None
            self._started_by_app = False
        return proc, started
