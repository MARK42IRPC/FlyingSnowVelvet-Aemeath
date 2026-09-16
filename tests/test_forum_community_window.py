"""社区窗口：三个子页面的页签、副标题与生命周期。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.forum_api import ForumSession, ForumUser
from lib.core.forum_session import ForumSessionStore, session_path
from lib.script.ui import forum_account as forum_account_module
from lib.script.ui import forum_board as forum_board_module
from lib.script.ui.forum_window import FORUM_DEFAULT_PAGE, FORUM_PAGES, ForumWindow


class FakeService:
    """两个子页面共用的服务替身：只记录，不发请求。"""

    instances: list = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list = []
        self.cleaned = False
        self.current_post = None
        self.post_total = 0
        self.replies_total = 0
        self.has_more_posts = False
        self.has_more_replies = False
        FakeService.instances.append(self)

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def restore_snapshot(self) -> bool:
        return False

    def refresh_posts(self) -> bool:
        self._record("refresh_posts")
        return True

    def refresh_account(self) -> bool:
        self._record("refresh_account")
        return True

    def check_health(self) -> bool:
        self._record("check_health")
        return True

    def load_user(self, username=None) -> bool:
        self._record("load_user", username=username)
        return True

    def load_more_posts(self) -> bool:
        self._record("load_more_posts")
        return True

    def load_more_replies(self) -> bool:
        self._record("load_more_replies")
        return True

    def close_post(self) -> None:
        self._record("close_post")

    def cache_summary(self) -> dict:
        return {"files": 0, "bytes": 0}

    def cleanup(self) -> None:
        self.cleaned = True


def session() -> ForumSession:
    return ForumSession(
        token="t" * 64,
        user=ForumUser(id=1, username="demo_user", display_name="演示"),
        expires_at=4_000_000_000,
    )


class WindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        FakeService.instances = []
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self._board_patch = patch.object(forum_board_module, "CommunityService", FakeService)
        self._account_patch = patch.object(forum_account_module, "CommunityService", FakeService)
        self._board_patch.start()
        self._account_patch.start()
        self.addCleanup(self._board_patch.stop)
        self.addCleanup(self._account_patch.stop)
        self.window = ForumWindow()
        self.addCleanup(self._dispose)
        self.window.resize(880, 720)
        self.window.show()
        self.app.processEvents()

    def _dispose(self) -> None:
        self.window.cleanup()
        self.window.deleteLater()
        self.app.processEvents()


class NavigationTests(WindowTestCase):
    def test_three_tabs_start_on_the_wall(self) -> None:
        self.assertEqual(list(self.window._tab_buttons), [key for key, _ in FORUM_PAGES])
        self.assertEqual(self.window.page(), FORUM_DEFAULT_PAGE)
        self.assertTrue(self.window._tab_buttons["wall"].isChecked())
        self.assertTrue(self.window._composer.isVisible())

    def test_switching_hides_the_wall_composer(self) -> None:
        self.window.set_page("board")
        self.app.processEvents()
        self.assertEqual(self.window._stack.currentWidget(), self.window._board)
        self.assertFalse(self.window._composer.isVisible())
        self.assertTrue(self.window._tab_buttons["board"].isChecked())

    def test_each_page_refreshes_on_first_visit(self) -> None:
        self.window.set_page("board")
        self.assertIn("refresh_posts", [name for name, _ in self.window._board._service.calls])
        self.window.set_page("account")
        self.assertIn("check_health", [name for name, _ in self.window._account._service.calls])

    def test_refresh_can_be_skipped(self) -> None:
        before = list(self.window._board._service.calls)
        self.window.set_page("board", refresh=False)
        self.assertEqual(self.window._board._service.calls, before)

    def test_unknown_page_falls_back_to_the_wall(self) -> None:
        self.window.set_page("没有这个页面")
        self.assertEqual(self.window.page(), FORUM_DEFAULT_PAGE)
        self.assertEqual(self.window._stack.currentWidget(), self.window._wall)

    def test_refresh_button_follows_the_active_page(self) -> None:
        self.window.set_page("board", refresh=False)
        self.window.refresh()
        self.assertEqual(
            [name for name, _ in self.window._board._service.calls], ["refresh_posts"]
        )

    def test_login_request_from_the_board_opens_the_account_page(self) -> None:
        self.window.set_page("board")
        self.window._board.login_requested.emit()
        self.assertEqual(self.window.page(), "account")


class SubtitleTests(WindowTestCase):
    def test_wall_subtitle_is_remembered_across_pages(self) -> None:
        self.window._on_page(_page_with_total())
        self.app.processEvents()
        wall_subtitle = self.window._subtitle.text()
        self.assertIn("条留言", wall_subtitle)
        self.window.set_page("board")
        self.assertIn("主论坛", self.window._subtitle.text())
        # 留言墙已经读过第一页（生产路径里由 open_forum_window 触发）。
        self.window._wall_loaded = True
        spy = Mock(wraps=self.window.refresh)
        self.window.refresh = spy
        self.window.set_page("wall")
        self.assertEqual(self.window._subtitle.text(), wall_subtitle)
        # 切回来不再重新请求留言墙：页签来回切不该反复打接口。
        spy.assert_not_called()

    def test_account_subtitle_follows_the_session(self) -> None:
        self.window.set_page("account")
        self.assertIn("未登录", self.window._subtitle.text())
        self.window._session_store.set(session())
        self.app.processEvents()
        self.assertIn("已登录 演示", self.window._subtitle.text())

    def test_badge_tracks_the_session(self) -> None:
        self.assertEqual(self.window._account_badge.text(), "未登录")
        self.window._session_store.set(session())
        self.assertIn("演示", self.window._account_badge.text())
        self.window._session_store.clear()
        self.assertEqual(self.window._account_badge.text(), "未登录")


class LifecycleTests(WindowTestCase):
    def test_cleanup_releases_the_sub_pages(self) -> None:
        board = self.window._board._service
        account = self.window._account._service
        self.window.cleanup()
        self.assertTrue(board.cleaned)
        self.assertTrue(account.cleaned)

    def test_cleanup_unsubscribes_from_the_session_store(self) -> None:
        spy = Mock(wraps=self.window._on_session_changed)
        self.window._on_session_changed = spy
        self.window.cleanup()
        self.window._session_store.set(session())
        spy.assert_not_called()

    def test_saved_session_is_restored_on_startup(self) -> None:
        store = ForumSessionStore()
        store.set(session())
        window = ForumWindow()
        self.addCleanup(window.deleteLater)
        try:
            self.assertIn("演示", window._account_badge.text())
            self.assertTrue(window._account.subtitle().endswith("演示"))
        finally:
            window.cleanup()

    def test_session_file_never_leaks_into_the_browsing_cache(self) -> None:
        self.window._session_store.set(session())
        cache_root = session_path().parent.parent.parent.parent / "cache"
        files = list(cache_root.rglob("*")) if cache_root.exists() else []
        self.assertEqual([path for path in files if path.is_file()], [])


def _page_with_total():
    from lib.core.forum import ForumMessage, ForumPage

    return ForumPage(
        messages=(ForumMessage(id=1, nickname="甲", content="你好", accent="pink", created_at=1),),
        mode="latest",
        total=1,
    )


if __name__ == "__main__":
    unittest.main()