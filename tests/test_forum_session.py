"""登录态的落盘与共享会话对象。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from lib.core.forum_api import ForumSession, ForumUser
from lib.core.forum_session import (
    FORUM_SESSION_VERSION,
    ForumSessionStore,
    clear_session,
    load_session,
    save_session,
    session_path,
)


def _session(**overrides) -> ForumSession:
    payload = {
        "token": "t" * 64,
        "user": ForumUser(id=1, username="demo_user", display_name="演示"),
        "expires_at": 4_000_000_000,
    }
    payload.update(overrides)
    return ForumSession(**payload)


class SessionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_roundtrip_keeps_token_and_profile(self) -> None:
        self.assertTrue(save_session(_session()))
        path = session_path()
        self.assertTrue(path.exists())
        self.assertEqual(path.parent.name, "forum")
        self.assertEqual(path.parent.parent.name, "secrets")

        loaded = load_session()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.token, "t" * 64)
        self.assertEqual(loaded.user.username, "demo_user")
        self.assertEqual(loaded.user.display_name, "演示")

    def test_expired_session_is_not_written_or_loaded(self) -> None:
        self.assertFalse(save_session(_session(expires_at=100)))
        self.assertFalse(session_path().exists())
        self.assertTrue(save_session(_session()))
        self.assertIsNone(load_session(now=5_000_000_000))

    def test_tokenless_session_is_not_written(self) -> None:
        self.assertFalse(save_session(_session(token="")))
        self.assertFalse(session_path().exists())

    def test_broken_files_are_ignored(self) -> None:
        path = session_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text("{不是 json", encoding="utf-8")
        self.assertIsNone(load_session())

        path.write_text(json.dumps({"version": FORUM_SESSION_VERSION + 1, "token": "x"}), encoding="utf-8")
        self.assertIsNone(load_session())

        path.write_text(json.dumps({"version": FORUM_SESSION_VERSION, "token": ""}), encoding="utf-8")
        self.assertIsNone(load_session())

    def test_oversized_file_is_ignored(self) -> None:
        path = session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * (17 * 1024), encoding="utf-8")
        self.assertIsNone(load_session())

    def test_clear_removes_the_file_once(self) -> None:
        save_session(_session())
        self.assertTrue(clear_session())
        self.assertFalse(session_path().exists())
        self.assertFalse(clear_session())


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_load_reads_the_saved_session(self) -> None:
        save_session(_session())
        store = ForumSessionStore.load()
        self.assertTrue(store.logged_in())
        self.assertEqual(store.token(), "t" * 64)
        self.assertEqual(store.user().username, "demo_user")

    def test_empty_store_reports_logged_out(self) -> None:
        store = ForumSessionStore.load()
        self.assertFalse(store.logged_in())
        self.assertEqual(store.token(), "")
        self.assertIsNone(store.user())

    def test_listeners_are_notified_and_can_unsubscribe(self) -> None:
        store = ForumSessionStore()
        seen: list = []
        listener = seen.append
        store.subscribe(listener)
        store.subscribe(listener)  # 重复订阅只算一次
        store.set(_session())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[-1].user.username, "demo_user")
        store.unsubscribe(listener)
        store.clear()
        self.assertEqual(len(seen), 1)
        self.assertFalse(session_path().exists())

    def test_listener_errors_do_not_break_the_store(self) -> None:
        store = ForumSessionStore()

        def boom(_session) -> None:
            raise RuntimeError("监听者炸了")

        store.subscribe(boom)
        store.set(_session())
        self.assertTrue(store.logged_in())

    def test_memory_only_updates_skip_the_file(self) -> None:
        store = ForumSessionStore()
        store.set(_session(), persist=False)
        self.assertTrue(store.logged_in())
        self.assertFalse(session_path().exists())

    def test_set_without_token_clears_the_saved_file(self) -> None:
        save_session(_session())
        store = ForumSessionStore.load()
        store.set(_session(token=""), persist=True)
        self.assertFalse(session_path().exists())
        self.assertFalse(store.logged_in())

    def test_session_file_is_plain_readable_json(self) -> None:
        save_session(_session())
        payload = json.loads(session_path().read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], FORUM_SESSION_VERSION)
        self.assertEqual(payload["user"]["username"], "demo_user")
        self.assertIn("expires_at", payload)


if __name__ == "__main__":
    unittest.main()