"""Office task orchestration over the DSH JSONL runtime."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from PyQt5.QtCore import QTimer

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.logger import get_logger
from lib.core.timing.scheduler import PeriodicTimer, Scheduler
from lib.script.chat.handler_stream_presenter import (
    BUBBLE_MIN_TICKS,
    BUBBLE_MAX_TICKS,
    ChatHandlerStreamPresenterMixin,
    _build_ai_voice_text,
    _detect_ai_voice_language,
)

from .approval import TaskApprovalPolicy
from .contracts import (
    ACTIVE_TASK_STATUSES,
    DEFAULT_REASONING_EFFORT,
    InteractionMode,
    OfficeTaskStatus,
    normalize_reasoning_effort,
    utc_now_text,
)
from .ipc import OfficeFileIpc
from .mode import InteractionModeService, get_interaction_mode_service
from .runtime import DshOfficeRuntime, runtime_readiness_error
from .storage import OfficeTaskStore
from .workspace import ensure_default_office_workspace


logger = get_logger(__name__)

_OFFICE_NEW_COMMAND = "new"
_OFFICE_THINKING_TEXT = "思考中"

_FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_LINE_PREFIX_PATTERN = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|(?:[-*+]|\d+[.)])\s+|>\s*)"
)


def _flatten_content(message: object, block_type: str = "text") -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == block_type
    ).strip()


def _office_voice_source(text: str) -> str:
    """Keep prose suitable for speech while omitting fenced code and Markdown chrome."""
    without_code = _FENCED_CODE_PATTERN.sub("\n", str(text or ""))
    lines = []
    for raw_line in without_code.splitlines():
        line = _MARKDOWN_LINE_PREFIX_PATTERN.sub("", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


class OfficeService:
    def __init__(
        self,
        *,
        scheduler: Scheduler,
        mode_service: InteractionModeService | None = None,
        task_store: OfficeTaskStore | None = None,
        ipc: OfficeFileIpc | None = None,
        runtime_factory=None,
    ) -> None:
        self._event_center = get_event_center()
        self._scheduler = scheduler
        self._mode_service = mode_service or get_interaction_mode_service()
        self._store = task_store or OfficeTaskStore()
        self._ipc = ipc or OfficeFileIpc()
        self._approval_policy = TaskApprovalPolicy()
        self._runtime_factory = runtime_factory or DshOfficeRuntime
        self._lock = threading.RLock()
        self._cleaned = False
        self._runtime = self._runtime_factory(self._publish_runtime_event)
        self._workspace = ensure_default_office_workspace()
        self._active_task_id: str | None = None
        self._force_new_conversation = False
        self._new_task_revision = 0
        readiness_error = runtime_readiness_error()
        self._runtime_status = "not_installed" if readiness_error else "ready"
        self._last_error = readiness_error
        self._pending_approval: dict | None = None
        self._queued_followups: dict[str, list[str]] = {}
        self._stream_buffers: dict[str, dict[str, str]] = {}
        self._last_stream_feedback: dict[str, str] = {}
        self._thinking_feedback: set[str] = set()
        self._state_timer: PeriodicTimer = scheduler.create_periodic_timer(self._flush_state)
        self._ipc_timer: PeriodicTimer = scheduler.create_periodic_timer(self._poll_ipc)
        logger.info(
            "[OfficeService] 启动 office 服务 pid=%s ipc_root=%s store=%s",
            os.getpid(),
            self._ipc.root,
            self._store.path,
        )

        for task in self._store.snapshot():
            if str(task.get("status")) in ACTIVE_TASK_STATUSES:
                self._store.update(
                    str(task.get("id")),
                    status=OfficeTaskStatus.FAILED.value,
                    error="桌宠上次退出时任务仍在运行，可从历史任务继续",
                )

        self._event_center.subscribe(EventType.OFFICE_INPUT, self._on_office_input)
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.OFFICE_RUNTIME_EVENT, self._on_runtime_event)
        self._event_center.subscribe(EventType.INTERACTION_MODE_CHANGED, self._on_mode_changed)
        get_hash_cmd_registry().register(
            _OFFICE_NEW_COMMAND,
            "",
            "创建新的办公对话",
        )
        # QTimer 必须在 QApplication 存在后 start，否则事件循环启动后也不会触发。
        # 桌面进程在 ApplicationState.__init__ 里创建本服务时 QApplication 尚未建立，
        # 因此延迟到事件循环第一拍再启动 IPC 轮询。
        QTimer.singleShot(0, self._start_ipc_polling)
        self._flush_state()

    def _start_ipc_polling(self) -> None:
        with self._lock:
            if self._cleaned:
                return
        self._ipc_timer.start(250)

    @property
    def workspace(self) -> Path:
        return self._workspace

    def _publish_runtime_event(self, payload: dict) -> None:
        with self._lock:
            if self._cleaned:
                return
        self._event_center.publish(Event(EventType.OFFICE_RUNTIME_EVENT, payload))

    def _mark_state_dirty(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            if not self._state_timer.active:
                self._state_timer.start(80)

    def _state_payload(self) -> dict:
        mode, generation = self._mode_service.snapshot()
        with self._lock:
            return {
                "mode": mode.value,
                "generation": generation,
                "runtime_status": self._runtime_status,
                "runtime_error": self._last_error,
                "active_task_id": self._active_task_id,
                "new_task_revision": self._new_task_revision,
                "pending_approval": dict(self._pending_approval) if self._pending_approval else None,
                "workspace": str(self._workspace),
                "updated_at": utc_now_text(),
            }

    def _flush_state(self) -> None:
        with self._lock:
            if self._cleaned:
                return
        self._state_timer.stop()
        with self._lock:
            buffered = dict(self._stream_buffers)
        for task_id, values in buffered.items():
            try:
                self._store.update(task_id, **values)
            except KeyError:
                with self._lock:
                    if self._stream_buffers.get(task_id) == values:
                        self._stream_buffers.pop(task_id, None)
                continue
            self._publish_stream_feedback(task_id, values.get("stream_text", ""))
        state = self._state_payload()
        tasks = self._store.snapshot()
        self._ipc.publish(state, tasks)
        self._event_center.publish(Event(EventType.OFFICE_STATE_CHANGED, {
            **state,
            "tasks": tasks,
        }))

    def _on_mode_changed(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        if str(data.get("mode", "")) != InteractionMode.OFFICE.value:
            with self._lock:
                task_id = self._active_task_id
            if task_id:
                self._dismiss_thinking_feedback(task_id)
        self._mark_state_dirty()

    def _on_hash_command(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        command = str(data.get("text") or "").strip().lower()
        if command != _OFFICE_NEW_COMMAND:
            return
        # #new is an office shortcut. Switching here keeps it useful even
        # when the command box is currently in companion mode.
        self._mode_service.set_mode(InteractionMode.OFFICE, source="hash:new")
        event.mark_handled()
        self.request_new_conversation()

    def _publish_office_notice(self, text: str) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": str(text or ""),
            "min": 0,
            "max": 100,
            "align": "left",
            "particle": False,
            "source": "office",
        }))

    def request_new_conversation(self) -> bool:
        """Prepare a blank office conversation without creating an empty task."""
        active = self._store.active()
        if active is not None:
            self._set_error("当前已有任务运行，请先等待完成或取消")
            self._publish_office_notice("当前任务仍在运行，请先等待完成或取消")
            return False
        with self._lock:
            if self._cleaned:
                return False
            self._force_new_conversation = True
            self._new_task_revision += 1
        self._mark_state_dirty()
        self._publish_office_notice("已准备新的办公对话，请输入任务内容")
        return True

    def _on_office_input(self, event: Event) -> None:
        text = str((event.data or {}).get("text", "")).strip()
        if text:
            self.submit_default_text(text)

    def submit_default_text(self, text: str) -> str | None:
        """Continue the most recently modified resumable task for ambient input."""
        prompt = str(text or "").strip()
        if not prompt:
            return None
        if self._store.active() is not None:
            return self.submit_text(prompt)

        with self._lock:
            force_new = self._force_new_conversation
        if force_new:
            return self.submit_text(prompt)

        latest = self._store.latest_resumable()
        if latest is not None:
            task_id = str(latest.get("id") or "")
            if task_id and self.resume_task(task_id, prompt):
                return task_id
        return self.submit_text(prompt)

    def _publish_stream_feedback(self, task_id: str, text: object) -> None:
        display_text = str(text or "").strip()
        if not display_text:
            return
        with self._lock:
            if self._cleaned or self._last_stream_feedback.get(task_id) == display_text:
                return
            self._last_stream_feedback[task_id] = display_text
        self._dismiss_thinking_feedback(task_id)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": display_text,
            "min": 0,
            "max": 100,
            "align": "left",
            "particle": False,
            "force_replace": True,
            "source": "office",
            "task_id": task_id,
        }))

    def _publish_assistant_feedback(self, task_id: str, text: str) -> None:
        display_text = str(text or "").strip()
        if not display_text:
            return
        with self._lock:
            if self._cleaned:
                return
            self._last_stream_feedback.pop(task_id, None)
        self._dismiss_thinking_feedback(task_id)
        min_ticks = ChatHandlerStreamPresenterMixin._calc_stream_final_min_ticks(display_text)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": display_text,
            "min": min_ticks,
            "max": max(BUBBLE_MAX_TICKS, min_ticks),
            "align": "left",
            "particle": False,
            "force_replace": True,
            "source": "office",
            "task_id": task_id,
        }))

        voice_text = _build_ai_voice_text(_office_voice_source(display_text))
        if voice_text:
            self._event_center.publish(Event(EventType.AI_VOICE_REQUEST, {
                "text": voice_text,
                "text_lang": _detect_ai_voice_language(voice_text),
                "interruptible": True,
                "source": "office",
                "task_id": task_id,
            }))

    def _queue_thinking_feedback(self, task_id: str) -> None:
        value = str(task_id or "").strip()
        if not value:
            return
        with self._lock:
            if self._cleaned or value in self._thinking_feedback:
                return
            self._thinking_feedback.add(value)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": _OFFICE_THINKING_TEXT,
            "min": BUBBLE_MIN_TICKS,
            "max": BUBBLE_MAX_TICKS,
            "align": "left",
            "particle": False,
            "source": "office",
            "task_id": value,
            "kind": "thinking",
        }))

    def _dismiss_thinking_feedback(self, task_id: str) -> None:
        value = str(task_id or "").strip()
        if not value:
            return
        with self._lock:
            if self._cleaned or value not in self._thinking_feedback:
                return
            self._thinking_feedback.discard(value)
        self._event_center.publish(Event(EventType.UI_BUBBLE_REMOVE, {
            "source": "office",
            "task_id": value,
            "kind": "thinking",
        }))

    def warmup_runtime(self) -> None:
        """预热办公运行时，在用户打开办公页面时调用以减少首次任务的等待时间。"""
        with self._lock:
            if self._cleaned or self._runtime.running:
                return

        def warmup_worker() -> None:
            try:
                import config.ollama_config as office_config

                active_config = office_config.get_active_config()
                if active_config.get("api_type") != "openai_compatible":
                    return

                api_key = str(active_config.get("api_key") or "").strip()
                base_url = str(active_config.get("base_url") or "").strip()
                model = str(active_config.get("model") or "").strip()

                if not api_key or not base_url or not model:
                    return

                with self._lock:
                    if self._cleaned or self._runtime.running:
                        return

                self._runtime.start(
                    workspace=self._workspace,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                )
                logger.info("[OfficeService] 运行时预热完成")
            except Exception as exc:
                logger.debug("[OfficeService] 运行时预热失败: %s", exc)

        get_compute_hub().submit(warmup_worker)

    def submit_text(
        self,
        text: str,
        *,
        workspace: Path | None = None,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> str | None:
        prompt = str(text or "").strip()
        if not prompt:
            return None
        active = self._store.active()
        if active is not None:
            task_id = str(active["id"])
            self._store.add_message(task_id, "user", prompt)
            self._queue_thinking_feedback(task_id)
            if active.get("status") == OfficeTaskStatus.QUEUED.value:
                self._queued_followups.setdefault(task_id, []).append(prompt)
            else:
                try:
                    self._runtime.send({"type": "followup", "taskId": task_id, "text": prompt})
                except RuntimeError as exc:
                    self._fail_task(task_id, str(exc))
            self._mark_state_dirty()
            return task_id

        target = Path(workspace or self._workspace).resolve()
        task = self._store.create(
            prompt,
            target,
            reasoning_effort=normalize_reasoning_effort(reasoning_effort),
        )
        task_id = str(task["id"])
        with self._lock:
            self._active_task_id = task_id
            self._force_new_conversation = False
            self._runtime_status = "starting"
            self._last_error = ""
        self._queue_thinking_feedback(task_id)
        self._mark_state_dirty()
        self._start_task_worker(task, prompt, resume=False)
        return task_id

    def resume_task(self, task_id: str, text: str) -> bool:
        if self._store.active() is not None:
            self._set_error("当前已有任务运行，请先等待完成或取消")
            return False
        task = self._store.get(task_id)
        prompt = str(text or "").strip()
        if task is None or not prompt:
            return False
        if not str(task.get("session_id", "")).strip():
            self._set_error("该历史任务没有可恢复的 DSH 会话")
            return False
        self._store.add_message(task_id, "user", prompt)
        self._store.update(
            task_id,
            status=OfficeTaskStatus.QUEUED.value,
            error="",
            stream_text="",
            reasoning_text="",
        )
        with self._lock:
            self._active_task_id = task_id
            self._force_new_conversation = False
            self._runtime_status = "starting"
            self._last_error = ""
        self._queue_thinking_feedback(task_id)
        self._mark_state_dirty()
        self._start_task_worker(task, prompt, resume=True)
        return True

    def _start_task_worker(self, task: dict, prompt: str, *, resume: bool) -> None:
        def worker() -> None:
            try:
                with self._lock:
                    if self._cleaned:
                        return
                import config.ollama_config as office_config

                active_config = office_config.get_active_config()
                if active_config.get("api_type") != "openai_compatible":
                    raise RuntimeError("办公模式当前支持福利 API、手动 API 或元宝接口，请先在工作台切换")
                api_key = str(active_config.get("api_key") or "").strip()
                base_url = str(active_config.get("base_url") or "").strip()
                model = str(active_config.get("model") or "").strip()
                self._runtime.start(
                    workspace=Path(task["workspace"]),
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                )
                command = {
                    "type": "resume" if resume else "create",
                    "taskId": task["id"],
                    "workspace": task["workspace"],
                    "prompt": prompt,
                    "model": model,
                    "reasoningEffort": task.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
                }
                if resume:
                    command["sessionId"] = task["session_id"]
                with self._lock:
                    if self._cleaned:
                        abort = True
                    else:
                        abort = False
                        self._runtime.send(command)
                if abort:
                    self._runtime.cleanup()
            except Exception as exc:
                self._publish_runtime_event({
                    "protocol": "fsv-office/1",
                    "type": "task_error",
                    "taskId": task["id"],
                    "message": str(exc),
                })

        try:
            get_compute_hub().submit_interactive_io(worker)
        except RuntimeError as exc:
            self._fail_task(str(task["id"]), str(exc))

    def _on_runtime_event(self, event: Event) -> None:
        with self._lock:
            if self._cleaned:
                return
        data = event.data if isinstance(event.data, dict) else {}
        event_type = str(data.get("type", ""))
        task_id = str(data.get("taskId", ""))
        if event_type in {"ready", "configured"}:
            with self._lock:
                self._runtime_status = "ready"
                self._last_error = ""
        elif event_type == "task_created" and task_id:
            self._store.update(
                task_id,
                session_id=str(data.get("sessionId", "")),
                status=OfficeTaskStatus.RUNNING.value,
            )
            for queued in self._queued_followups.pop(task_id, []):
                self._runtime.send({"type": "followup", "taskId": task_id, "text": queued})
        elif event_type == "session_event" and task_id:
            self._apply_session_event(task_id, data.get("event"))
        elif event_type == "approval_request" and task_id:
            self._handle_approval_request(task_id, data)
        elif event_type == "task_idle" and task_id:
            self._dismiss_thinking_feedback(task_id)
            changes = self._take_stream_buffer(task_id)
            changes.update({
                "status": OfficeTaskStatus.COMPLETED.value,
                "stream_text": "",
            })
            self._store.update(task_id, **changes)
            self._approval_policy.forget(task_id)
            self._queued_followups.pop(task_id, None)
            with self._lock:
                if self._active_task_id == task_id:
                    self._active_task_id = None
                self._pending_approval = None
                self._last_stream_feedback.pop(task_id, None)
        elif event_type == "task_cancelled" and task_id:
            self._dismiss_thinking_feedback(task_id)
            changes = self._take_stream_buffer(task_id)
            changes["status"] = OfficeTaskStatus.CANCELLED.value
            self._store.update(task_id, **changes)
            self._approval_policy.forget(task_id)
            self._queued_followups.pop(task_id, None)
            with self._lock:
                if self._active_task_id == task_id:
                    self._active_task_id = None
                self._pending_approval = None
                self._last_stream_feedback.pop(task_id, None)
        elif event_type in {"task_error", "command_error"} and task_id:
            self._fail_task(task_id, str(data.get("message", "DSH 任务失败")))
        elif event_type in {"fatal", "process_exit"}:
            message = str(data.get("message") or f"DSH 运行时已退出 ({data.get('returnCode')})")
            active_id = self._active_task_id
            if active_id:
                self._fail_task(active_id, message)
            with self._lock:
                self._runtime_status = "error"
                self._last_error = message
        elif event_type == "reasoning_changed" and task_id:
            self._store.update(task_id, reasoning_effort=str(data.get("reasoningEffort", "high")))
        self._mark_state_dirty()

    def _take_stream_buffer(self, task_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._stream_buffers.pop(str(task_id or ""), {}))

    def _apply_session_event(self, task_id: str, event: object) -> None:
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type", ""))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "assistant/chunk":
            chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
            chunk_type = str(chunk.get("type", ""))
            if chunk_type in {"text-delta", "reasoning-delta"}:
                if chunk_type == "reasoning-delta":
                    self._queue_thinking_feedback(task_id)
                elif str(chunk.get("text", "")):
                    self._dismiss_thinking_feedback(task_id)
                field = "stream_text" if chunk_type == "text-delta" else "reasoning_text"
                with self._lock:
                    buffers = self._stream_buffers.setdefault(task_id, {
                        "stream_text": "",
                        "reasoning_text": "",
                    })
                    buffers[field] += str(chunk.get("text", ""))
            return
        if event_type == "assistant/message":
            text = _flatten_content(data.get("message"), "text")
            reasoning = _flatten_content(data.get("message"), "reasoning")
            if text:
                self._store.add_message(task_id, "assistant", text)
            with self._lock:
                self._stream_buffers[task_id] = {
                    "stream_text": "",
                    "reasoning_text": reasoning,
                }
            self._publish_assistant_feedback(task_id, text)
            return
        if event_type == "todo/write":
            todos = data.get("todos") if isinstance(data.get("todos"), list) else []
            self._store.update(task_id, todos=todos)
            return
        if event_type in {"tool/call", "tool/result", "turn/start", "turn/end"}:
            self._store.add_event(task_id, event_type, data)

    def _handle_approval_request(self, task_id: str, data: dict) -> None:
        approval_id = str(data.get("approvalId", ""))
        with self._lock:
            current_pending = self._pending_approval
        if (
            current_pending is not None
            and current_pending.get("task_id") == task_id
            and current_pending.get("approval_id") == approval_id
        ):
            return
        if self._approval_policy.should_auto_allow(task_id):
            self._runtime.send({
                "type": "approval",
                "approvalId": approval_id,
                "outcome": "allowed-once",
            })
            return
        pending = {
            "task_id": task_id,
            "approval_id": approval_id,
            "tool_name": str(data.get("toolName", "")),
            "reason": str(data.get("reason", "")),
            "command": data.get("command") if isinstance(data.get("command"), dict) else None,
        }
        with self._lock:
            self._pending_approval = pending
        self._store.update(task_id, status=OfficeTaskStatus.WAITING_APPROVAL.value)
        self._event_center.publish(Event(EventType.OFFICE_APPROVAL_REQUEST, dict(pending)))

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        with self._lock:
            pending = dict(self._pending_approval) if self._pending_approval else None
        if pending is None or pending.get("approval_id") != str(approval_id or ""):
            return False
        task_id = str(pending["task_id"])
        try:
            outcome = self._approval_policy.resolve(task_id, decision)
            self._runtime.send({
                "type": "approval",
                "approvalId": approval_id,
                "outcome": outcome,
            })
        except (RuntimeError, ValueError) as exc:
            self._fail_task(task_id, str(exc))
            return False
        with self._lock:
            self._pending_approval = None
        self._store.update(task_id, status=OfficeTaskStatus.RUNNING.value)
        self._mark_state_dirty()
        return True

    def cancel_task(self, task_id: str) -> bool:
        task = self._store.get(task_id)
        if task is None or str(task.get("status")) not in ACTIVE_TASK_STATUSES:
            return False
        try:
            self._runtime.send({"type": "cancel", "taskId": task_id})
        except RuntimeError as exc:
            self._fail_task(task_id, str(exc))
            return False
        return True

    def set_reasoning_effort(self, task_id: str, effort: str) -> bool:
        try:
            normalized = normalize_reasoning_effort(effort)
        except ValueError as exc:
            self._set_error(str(exc))
            return False
        task = self._store.get(task_id)
        if task is None:
            return False
        self._store.update(task_id, reasoning_effort=normalized)
        if str(task.get("status")) in ACTIVE_TASK_STATUSES:
            try:
                self._runtime.send({
                    "type": "set_reasoning",
                    "taskId": task_id,
                    "reasoningEffort": normalized,
                })
            except RuntimeError as exc:
                self._fail_task(task_id, str(exc))
                return False
        self._mark_state_dirty()
        return True

    def delete_task(self, task_id: str) -> bool:
        """Delete an inactive task from history; active work must be cancelled first."""
        value = str(task_id or "").strip()
        logger.info(
            "[OfficeService] delete_task 入口 task_id=%r pid=%s ipc_root=%s",
            value,
            os.getpid(),
            self._ipc.root,
        )
        task = self._store.get(value)
        if task is None:
            logger.warning("[OfficeService] delete_task 失败：任务不存在 task_id=%r", value)
            self._set_error("任务不存在或已被删除")
            return False
        if str(task.get("status") or "") in ACTIVE_TASK_STATUSES:
            logger.warning(
                "[OfficeService] delete_task 失败：任务运行中 task_id=%r status=%r",
                value,
                str(task.get("status") or ""),
            )
            self._set_error("运行中的任务不能删除，请先取消任务")
            return False
        with self._lock:
            if self._active_task_id == value:
                logger.warning(
                    "[OfficeService] delete_task 失败：active_task_id 匹配 task_id=%r",
                    value,
                )
                self._set_error("运行中的任务不能删除，请先取消任务")
                return False
            self._stream_buffers.pop(value, None)
            self._last_stream_feedback.pop(value, None)
        self._queued_followups.pop(value, None)
        self._approval_policy.forget(value)
        if not self._store.delete(value):
            logger.warning("[OfficeService] delete_task 失败：store.delete 返回 False task_id=%r", value)
            self._set_error("任务不存在或已被删除")
            return False
        with self._lock:
            self._last_error = ""
        self._mark_state_dirty()
        logger.info("[OfficeService] delete_task 成功 task_id=%r", value)
        return True

    def _poll_ipc(self) -> None:
        with self._lock:
            if self._cleaned:
                return
        for command in self._ipc.consume():
            name = str(command.get("command", ""))
            data = command.get("data") if isinstance(command.get("data"), dict) else {}
            try:
                if name == "set_mode":
                    self._event_center.publish(Event(EventType.INTERACTION_MODE_SET, {
                        "mode": data.get("mode"),
                        "source": "workbench",
                    }))
                elif name == "new_task":
                    self._mode_service.set_mode(InteractionMode.OFFICE, source="workbench")
                    if self._store.active() is not None:
                        self._set_error("当前已有任务运行，请先等待完成或取消")
                        continue
                    self.submit_text(
                        str(data.get("text", "")),
                        workspace=Path(str(data.get("workspace") or self._workspace)),
                        reasoning_effort=str(data.get("reasoning_effort") or DEFAULT_REASONING_EFFORT),
                    )
                elif name == "followup":
                    self._mode_service.set_mode(InteractionMode.OFFICE, source="workbench")
                    task_id = str(data.get("task_id", ""))
                    text = str(data.get("text", ""))
                    active = self._store.active()
                    if active and str(active.get("id")) == task_id:
                        self.submit_text(text)
                    else:
                        self.resume_task(task_id, text)
                elif name == "cancel":
                    self.cancel_task(str(data.get("task_id", "")))
                elif name == "set_reasoning":
                    self.set_reasoning_effort(
                        str(data.get("task_id", "")),
                        str(data.get("reasoning_effort", "")),
                    )
                elif name == "delete":
                    logger.info(
                        "[OfficeService] 收到删除命令 task_id=%r pid=%s ipc_root=%s",
                        str(data.get("task_id", "")),
                        os.getpid(),
                        self._ipc.root,
                    )
                    self.delete_task(str(data.get("task_id", "")))
                elif name == "approval":
                    self.resolve_approval(
                        str(data.get("approval_id", "")),
                        str(data.get("decision", "")),
                    )
            except Exception as exc:
                logger.exception("[OfficeService] IPC command failed: %s", name)
                self._set_error(str(exc))

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = str(message or "")
        self._mark_state_dirty()

    def _fail_task(self, task_id: str, message: str) -> None:
        self._dismiss_thinking_feedback(task_id)
        changes = self._take_stream_buffer(task_id)
        changes.update({
            "status": OfficeTaskStatus.FAILED.value,
            "error": str(message or "DSH 任务失败"),
        })
        try:
            self._store.update(task_id, **changes)
        except KeyError:
            pass
        self._approval_policy.forget(task_id)
        self._queued_followups.pop(task_id, None)
        with self._lock:
            if self._active_task_id == task_id:
                self._active_task_id = None
            self._pending_approval = None
            self._last_stream_feedback.pop(task_id, None)
            self._runtime_status = "error"
            self._last_error = str(message or "DSH 任务失败")
        self._mark_state_dirty()

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            thinking_task_ids = tuple(self._thinking_feedback)
        for task_id in thinking_task_ids:
            self._dismiss_thinking_feedback(task_id)
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            active_task_id = self._active_task_id
            buffered = (
                dict(self._stream_buffers.get(active_task_id, {}))
                if active_task_id
                else {}
            )
            self._stream_buffers.clear()
            self._last_stream_feedback.clear()
            self._thinking_feedback.clear()
            self._queued_followups.clear()
            self._active_task_id = None
            self._force_new_conversation = False
            self._pending_approval = None
            self._runtime_status = "stopped"
        self._event_center.unsubscribe(EventType.OFFICE_INPUT, self._on_office_input)
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.OFFICE_RUNTIME_EVENT, self._on_runtime_event)
        self._event_center.unsubscribe(EventType.INTERACTION_MODE_CHANGED, self._on_mode_changed)
        get_hash_cmd_registry().unregister(_OFFICE_NEW_COMMAND)
        self._ipc_timer.stop()
        self._state_timer.stop()
        if active_task_id:
            buffered.update({
                "status": OfficeTaskStatus.CANCELLED.value,
                "error": "桌宠退出，任务已停止",
            })
            try:
                self._store.update(active_task_id, **buffered)
            except KeyError:
                pass
        try:
            self._ipc.publish(self._state_payload(), self._store.snapshot())
        except OSError:
            logger.exception("[OfficeService] Failed to publish final office state")
        try:
            self._runtime.cleanup()
        finally:
            self._approval_policy.clear()
            self._scheduler.cleanup()


_instance: OfficeService | None = None


def get_office_service(*, scheduler: Scheduler | None = None) -> OfficeService:
    global _instance
    if _instance is None:
        if scheduler is None:
            raise RuntimeError("首次创建 OfficeService 时必须注入 Scheduler")
        _instance = OfficeService(scheduler=scheduler)
    return _instance


def cleanup_office_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
