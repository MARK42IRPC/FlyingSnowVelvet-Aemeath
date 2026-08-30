from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core.event.center import Event, EventType, cleanup_event_center, get_event_center
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.office.contracts import InteractionMode, OfficeTaskStatus
from lib.script.office.ipc import OfficeFileIpc
from lib.script.office.service import OfficeService
from lib.script.office.storage import OfficeTaskStore
from tests.timing_fakes import FakeScheduler


class _ModeService:
    def __init__(self) -> None:
        self.mode = InteractionMode.COMPANION
        self.generation = 0

    def snapshot(self):
        return self.mode, self.generation

    def set_mode(self, mode, *, source=""):
        del source
        self.mode = InteractionMode(mode)
        self.generation += 1


class _Runtime:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cleanup_count = 0
        self.sent = []
        self.running = False

    def start(self, **kwargs) -> None:
        del kwargs

    def send(self, payload: dict) -> None:
        self.sent.append(payload)

    def cleanup(self) -> None:
        self.cleanup_count += 1


class OfficeServiceLifecycleTests(unittest.TestCase):
    def tearDown(self):
        cleanup_event_center()

    def _service(self, root: Path):
        scheduler = FakeScheduler()
        store = OfficeTaskStore(root / "tasks.json")
        ipc = OfficeFileIpc(root / "ipc")
        with patch(
            "lib.script.office.service.ensure_default_office_workspace",
            return_value=root / "workspace",
        ), patch(
            "lib.script.office.service.runtime_readiness_error",
            return_value="",
        ):
            service = OfficeService(
                scheduler=scheduler,
                mode_service=_ModeService(),
                task_store=store,
                ipc=ipc,
                runtime_factory=_Runtime,
            )
        return service, scheduler, store, ipc

    def test_cleanup_cancels_active_task_publishes_final_state_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, scheduler, store, ipc = self._service(Path(tmpdir))
            task = store.create("处理中", Path(tmpdir) / "workspace")
            task_id = task["id"]
            store.update(task_id, status=OfficeTaskStatus.RUNNING.value)
            service._active_task_id = task_id
            service._stream_buffers[task_id] = {
                "stream_text": "部分输出",
                "reasoning_text": "推理",
            }

            service.cleanup()
            service.cleanup()

            saved = store.get(task_id)
            self.assertEqual(saved["status"], OfficeTaskStatus.CANCELLED.value)
            self.assertEqual(saved["stream_text"], "部分输出")
            self.assertIn("退出", saved["error"])
            self.assertIsNone(ipc.read_state()["active_task_id"])
            self.assertEqual(ipc.read_state()["runtime_status"], "stopped")
            self.assertEqual(service._runtime.cleanup_count, 1)
            self.assertTrue(scheduler.cleaned)

    def test_cleanup_unregisters_new_hash_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, _store, _ipc = self._service(Path(tmpdir))
            self.assertIn(
                "new",
                [name for name, _usage, _description in get_hash_cmd_registry().get_all()],
            )

            service.cleanup()

            self.assertNotIn(
                "new",
                [name for name, _usage, _description in get_hash_cmd_registry().get_all()],
            )

    def test_warmup_uses_deduplicated_io_slot(self):
        class _Hub:
            def __init__(self):
                self.calls = []

            def submit_latest(self, slot, callback, **kwargs):
                self.calls.append((slot, callback, kwargs))
                return object()

        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, _store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            hub = _Hub()

            with patch("lib.script.office.service.get_compute_hub", return_value=hub):
                service.warmup_runtime()

            self.assertEqual(len(hub.calls), 1)
            slot, callback, kwargs = hub.calls[0]
            self.assertEqual(slot, "office_runtime_warmup")
            self.assertTrue(callable(callback))
            self.assertEqual(kwargs, {"executor": "io"})

    def test_ipc_polling_starts_after_backend_application_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, scheduler, _store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)

            ipc_timer = service._ipc_timer
            self.assertFalse(ipc_timer.active)

            get_event_center().publish(Event(EventType.APP_PRE_START, {}))

            self.assertTrue(ipc_timer.active)
            self.assertEqual(ipc_timer.interval_ms, 250)

    def test_completed_task_releases_stream_buffer_and_clears_stream_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            task = store.create("生成内容", Path(tmpdir) / "workspace")
            task_id = task["id"]
            store.update(task_id, status=OfficeTaskStatus.RUNNING.value)
            service._active_task_id = task_id
            service._apply_session_event(task_id, {
                "type": "assistant/chunk",
                "data": {"chunk": {"type": "text-delta", "text": "生成中"}},
            })

            service._on_runtime_event(Event(EventType.OFFICE_RUNTIME_EVENT, {
                "type": "task_idle",
                "taskId": task_id,
            }))

            saved = store.get(task_id)
            self.assertEqual(saved["status"], OfficeTaskStatus.COMPLETED.value)
            self.assertEqual(saved["stream_text"], "")
            self.assertNotIn(task_id, service._stream_buffers)
            self.assertIsNone(service._active_task_id)

    def test_default_input_resumes_latest_modified_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            timestamps = iter((
                "2026-08-17T01:00:00.000+00:00",
                "2026-08-17T01:01:00.000+00:00",
                "2026-08-17T01:02:00.000+00:00",
                "2026-08-17T01:03:00.000+00:00",
                "2026-08-17T01:04:00.000+00:00",
            ))
            with patch(
                "lib.script.office.storage.utc_now_text",
                side_effect=lambda: next(timestamps),
            ):
                latest = store.create("最早创建", Path(tmpdir) / "workspace")
                store.update(
                    latest["id"],
                    status=OfficeTaskStatus.COMPLETED.value,
                    session_id="session-latest",
                )
                newer = store.create("稍后创建", Path(tmpdir) / "workspace")
                store.update(
                    newer["id"],
                    status=OfficeTaskStatus.COMPLETED.value,
                    session_id="session-newer",
                )
                store.update(latest["id"], reasoning_effort="max")

            with patch.object(service, "_start_task_worker") as start:
                first_task_id = service.submit_default_text("继续完善")
                second_task_id = service.submit_default_text("再检查测试")

            self.assertEqual(first_task_id, latest["id"])
            self.assertEqual(second_task_id, latest["id"])
            self.assertEqual(len(store.snapshot()), 2)
            self.assertEqual(
                [item["text"] for item in store.get(latest["id"])["messages"][-2:]],
                ["继续完善", "再检查测试"],
            )
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0]["id"], latest["id"])
            self.assertTrue(start.call_args.kwargs["resume"])

    def test_default_input_skips_newer_task_without_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            resumable = store.create("有会话的任务", Path(tmpdir) / "workspace")
            store.update(
                resumable["id"],
                status=OfficeTaskStatus.COMPLETED.value,
                session_id="session-resumable",
            )
            newer = store.create("更新但没有会话", Path(tmpdir) / "workspace")
            store.update(
                newer["id"],
                status=OfficeTaskStatus.FAILED.value,
                error="启动失败",
            )

            with patch.object(service, "_start_task_worker") as start:
                task_id = service.submit_default_text("继续旧任务")

            self.assertEqual(task_id, resumable["id"])
            start.assert_called_once()
            self.assertTrue(start.call_args.kwargs["resume"])

    def test_hash_new_prepares_a_fresh_conversation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            previous = store.create("旧任务", Path(tmpdir) / "workspace")
            store.update(
                previous["id"],
                status=OfficeTaskStatus.COMPLETED.value,
                session_id="session-old",
            )

            handled = Event(EventType.INPUT_HASH, {"text": "new"})
            service._on_hash_command(handled)
            self.assertTrue(handled.handled)
            self.assertTrue(service._force_new_conversation)
            self.assertEqual(service._mode_service.mode, InteractionMode.OFFICE)

            with patch.object(service, "_start_task_worker") as start:
                fresh_id = service.submit_default_text("从头开始")

            self.assertIsNotNone(fresh_id)
            self.assertNotEqual(fresh_id, previous["id"])
            self.assertEqual(len(store.snapshot()), 2)
            start.assert_called_once()
            self.assertFalse(start.call_args.kwargs["resume"])

    def test_delete_task_removes_history_but_rejects_active_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            completed = store.create("已完成", Path(tmpdir) / "workspace")
            store.update(completed["id"], status=OfficeTaskStatus.COMPLETED.value)
            active = store.create("执行中", Path(tmpdir) / "workspace")
            store.update(active["id"], status=OfficeTaskStatus.RUNNING.value)

            self.assertTrue(service.delete_task(completed["id"]))
            self.assertIsNone(store.get(completed["id"]))
            self.assertFalse(service.delete_task(active["id"]))
            self.assertIsNotNone(store.get(active["id"]))

    def test_assistant_feedback_streams_to_bubble_and_only_final_requests_voice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            task = store.create("实现反馈", Path(tmpdir) / "workspace")
            task_id = task["id"]
            store.update(task_id, status=OfficeTaskStatus.RUNNING.value)
            information = []
            voice = []
            service._event_center.subscribe(
                EventType.INFORMATION,
                lambda event: information.append(event.data),
            )
            service._event_center.subscribe(
                EventType.AI_VOICE_REQUEST,
                lambda event: voice.append(event.data),
            )

            service._apply_session_event(task_id, {
                "type": "assistant/chunk",
                "data": {"chunk": {"type": "text-delta", "text": "正在修改"}},
            })
            service._flush_state()

            self.assertEqual(information[-1]["text"], "正在修改")
            self.assertTrue(information[-1]["force_replace"])
            self.assertEqual(information[-1]["source"], "office")
            self.assertEqual(voice, [])

            final_text = "### 完成\n已更新 app.py。\n```python\nprint('hidden')\n```"
            service._apply_session_event(task_id, {
                "type": "assistant/message",
                "data": {"message": {"content": [{"type": "text", "text": final_text}]}},
            })

            self.assertEqual(information[-1]["text"], final_text)
            self.assertEqual(information[-1]["align"], "left")
            self.assertEqual(len(voice), 1)
            self.assertEqual(voice[0]["source"], "office")
            self.assertEqual(voice[0]["task_id"], task_id)
            self.assertNotIn("mode_generation", voice[0])
            self.assertNotIn("hidden", voice[0]["text"])
            self.assertEqual(voice[0]["text_lang"], "auto")

    def test_thinking_feedback_is_queued_once_and_replaced_by_stream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _scheduler, store, _ipc = self._service(Path(tmpdir))
            self.addCleanup(service.cleanup)
            information = []
            removals = []
            service._event_center.subscribe(
                EventType.INFORMATION,
                lambda event: information.append(event.data),
            )
            service._event_center.subscribe(
                EventType.UI_BUBBLE_REMOVE,
                lambda event: removals.append(event.data),
            )

            with patch.object(service, "_start_task_worker"):
                task_id = service.submit_text("检查项目结构")

            thinking = [item for item in information if item.get("text") == "思考中"]
            self.assertEqual(len(thinking), 1)
            self.assertFalse(thinking[0].get("force_replace", False))
            self.assertFalse(thinking[0]["particle"])
            self.assertEqual(thinking[0]["source"], "office")
            self.assertEqual(thinking[0]["task_id"], task_id)

            service._apply_session_event(task_id, {
                "type": "assistant/chunk",
                "data": {"chunk": {"type": "reasoning-delta", "text": "先检查"}},
            })
            thinking = [item for item in information if item.get("text") == "思考中"]
            self.assertEqual(len(thinking), 1)

            service._apply_session_event(task_id, {
                "type": "assistant/chunk",
                "data": {"chunk": {"type": "text-delta", "text": "已找到入口"}},
            })
            service._flush_state()

            self.assertEqual(information[-1]["text"], "已找到入口")
            self.assertTrue(information[-1]["force_replace"])
            self.assertNotEqual(information[-1]["text"], "思考中")
            self.assertEqual(removals, [{
                "source": "office",
                "task_id": task_id,
                "kind": "thinking",
            }])
            self.assertEqual(store.get(task_id)["reasoning_text"], "先检查")


if __name__ == "__main__":
    unittest.main()
