import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.office.storage import OfficeTaskStore


class OfficeTaskStoreTests(unittest.TestCase):
    def test_round_trip_task_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            store = OfficeTaskStore(path)
            created = store.create("实现实时任务状态", workspace, reasoning_effort="max")
            store.update(created["id"], status="running", session_id="session-1")
            store.add_message(created["id"], "assistant", "开始处理")
            store.add_event(created["id"], "tool/call", {"name": "read_file"})

            loaded = OfficeTaskStore(path).get(created["id"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "running")
        self.assertEqual(loaded["session_id"], "session-1")
        self.assertEqual(loaded["messages"][-1]["text"], "开始处理")
        self.assertEqual(loaded["events"][-1]["type"], "tool/call")

    def test_rejects_invalid_reasoning_effort(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OfficeTaskStore(Path(tmpdir) / "tasks.json")
            with self.assertRaises(ValueError):
                store.create("task", Path(tmpdir), reasoning_effort="medium")

    def test_latest_uses_modification_timestamp_not_creation_order(self):
        timestamps = iter((
            "2026-08-17T01:00:00.000+00:00",
            "2026-08-17T01:01:00.000+00:00",
            "2026-08-17T01:02:00.000+00:00",
            "2026-08-17T01:03:00.000+00:00",
            "2026-08-17T01:04:00.000+00:00",
        ))
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "lib.script.office.storage.utc_now_text",
            side_effect=lambda: next(timestamps),
        ):
            store = OfficeTaskStore(Path(tmpdir) / "tasks.json")
            first = store.create("first", Path(tmpdir))
            store.update(first["id"], status="completed", session_id="session-first")
            second = store.create("second", Path(tmpdir))
            store.update(second["id"], status="completed", session_id="session-second")
            store.update(first["id"], reasoning_effort="max")

            latest = store.latest()

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], first["id"])

    def test_latest_resumable_ignores_newer_task_without_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OfficeTaskStore(Path(tmpdir) / "tasks.json")
            resumable = store.create("可恢复任务", Path(tmpdir))
            store.update(
                resumable["id"],
                status="completed",
                session_id="session-1",
            )
            newer_without_session = store.create("尚未建立会话", Path(tmpdir))

            latest = store.latest_resumable()

        self.assertEqual(latest["id"], resumable["id"])
        self.assertNotEqual(latest["id"], newer_without_session["id"])

    def test_delete_removes_task_and_persists_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            store = OfficeTaskStore(path)
            first = store.create("保留任务", Path(tmpdir))
            second = store.create("删除任务", Path(tmpdir))

            self.assertTrue(store.delete(second["id"]))
            self.assertFalse(store.delete(second["id"]))

            loaded = OfficeTaskStore(path)

        self.assertIsNotNone(loaded.get(first["id"]))
        self.assertIsNone(loaded.get(second["id"]))


if __name__ == "__main__":
    unittest.main()
