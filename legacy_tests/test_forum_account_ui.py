"""账号子页面：登录表单、资料展示与两个清理按钮。"""

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

from lib.core import forum_cache
from lib.core.forum_api import ForumPost, ForumPostPage, ForumReplyPage, ForumSession, ForumUser
from lib.core.forum_cache import ForumCacheReport
from lib.core.forum_session import ForumSessionStore, session_path
from lib.script.ui.forum_account import ForumAccountPage


class FakeService:
    def __init__(self) -> None:
        self.calls: list = []
        self.login_error = ""
        self.register_error = ""
        self.logout_result = True
        self.cache_report = ForumCacheReport(files=2, bytes=2048)
        self.snapshot = False
        self.cleaned = False

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def restore_snapshot(self) -> bool:
        return self.snapshot

    def login(self, username, password) -> str:
        self._record("login", username=username, password=password)
        return self.login_error

    def register(self, username, password, display_name=None) -> str:
        self._record("register", username=username, display_name=display_name)
        return self.register_error

    def logout(self, *, forget: bool = False) -> bool:
        self._record("logout", forget=forget)
        return self.logout_result

    def refresh_account(self) -> bool:
        self._record("refresh_account")
        return True

    def load_user(self, username=None) -> bool:
        self._record("load_user", username=username)
        return True

    def check_health(self) -> bool:
        self._record("check_health")
        return True

    def clear_cache(self):
        self._record("clear_cache")
        return self.cache_report

    def cache_summary(self) -> dict:
        return forum_cache.cache_stats()

    def cleanup(self) -> None:
        self.cleaned = True


def user(**overrides) -> ForumUser:
    payload = {
        "id": 1,
        "username": "demo_user",
        "display_name": "演示",
        "role": "user",
        "post_count": 3,
        "reply_count": 5,
        "created_at": 1_789_477_000,
    }
    payload.update(overrides)
    return ForumUser(**payload)


def post(post_id: int, title: str) -> ForumPost:
    return ForumPost(id=post_id, title=title, content="正文", author=user(), reply_count=2, like_count=1)


def post_page(posts=()) -> ForumPostPage:
    items = tuple(posts)
    return ForumPostPage(posts=items, page=1, per_page=20, total=len(items), total_pages=1, has_more=False)


def reply_page(total: int = 0) -> ForumReplyPage:
    return ForumReplyPage(replies=(), page=1, per_page=20, total=total, total_pages=0)


def session(user_obj: ForumUser | None = None) -> ForumSession:
    return ForumSession(token="t" * 64, user=user_obj or user(), expires_at=4_000_000_000)


class AccountPageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.store = ForumSessionStore()
        self.page = ForumAccountPage(session=self.store)
        self.addCleanup(self._dispose)
        self.service = FakeService()
        self.page._service = self.service
        self.page.show()
        self.app.processEvents()

    def _dispose(self) -> None:
        self.page.cleanup()
        self.page.deleteLater()
        self.app.processEvents()

    def login(self) -> None:
        """登录态的唯一事实源是 store：写进去，页面会自己收到通知。"""
        self.store.set(session())


class LoginFormTests(AccountPageTestCase):
    def test_logged_out_state_shows_the_form(self) -> None:
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertEqual(self.page.subtitle(), "账号页 · 未登录")
        texts = [label.text() for label in self.page._data_card.findChildren(type(self.page._cache_label))]
        self.assertTrue(any(str(session_path()) in text for text in texts), texts)

    def test_invalid_username_never_reaches_the_service(self) -> None:
        self.page._username.setText("ab")
        self.page._password.setText("demo-password-123")
        self.page._on_login()
        self.assertIn("至少 3 个字符", self.page._status.text())
        self.assertEqual([call for call in self.service.calls if call[0] == "login"], [])

    def test_login_forwards_the_fields(self) -> None:
        self.page._username.setText("demo_user")
        self.page._password.setText("demo-password-123")
        self.page._on_login()
        self.assertEqual(
            [call for call in self.service.calls if call[0] == "login"][-1][1],
            {"username": "demo_user", "password": "demo-password-123"},
        )

    def test_login_refusal_is_shown(self) -> None:
        self.service.login_error = "用户名或密码不对"
        self.page._username.setText("demo_user")
        self.page._password.setText("demo-password-123")
        self.page._on_login()
        self.assertEqual(self.page._status.text(), "用户名或密码不对")
        self.assertEqual(self.page._status.property("tone"), "warn")

    def test_register_sends_the_display_name(self) -> None:
        self.page._username.setText("demo_user")
        self.page._password.setText("demo-password-123")
        self.page._display_name.setText("演示")
        self.page._on_register()
        self.assertEqual(
            [call for call in self.service.calls if call[0] == "register"][-1][1],
            {"username": "demo_user", "display_name": "演示"},
        )


class ProfileTests(AccountPageTestCase):
    def test_session_switches_to_the_profile_and_clears_the_password(self) -> None:
        self.page._password.setText("demo-password-123")
        self.login()
        self.app.processEvents()
        self.assertEqual(self.page._stack.currentIndex(), 1)
        self.assertEqual(self.page._password.text(), "")
        self.assertEqual(self.page._profile_labels["username"].text(), "demo_user")
        self.assertEqual(self.page._profile_labels["display_name"].text(), "演示")
        self.assertEqual(self.page._profile_labels["counts"].text(), "3 / 5")
        self.assertIn("还有", self.page._profile_labels["expires_at"].text())
        self.assertEqual(self.page.subtitle(), "账号页 · 已登录 演示")

    def test_admin_role_is_translated(self) -> None:
        self.store.set(session(user(role="admin")))
        self.assertEqual(self.page._profile_labels["role"].text(), "管理员")

    def test_account_callback_only_updates_the_profile(self) -> None:
        self.login()
        self.page.on_account(user(display_name="新名字", post_count=9))
        self.assertEqual(self.page._profile_labels["display_name"].text(), "新名字")
        self.assertIn("资料已更新", self.page._status.text())

    def test_logout_button_asks_the_service(self) -> None:
        self.login()
        self.page._logout_button.click()
        self.assertEqual([call for call in self.service.calls if call[0] == "logout"][-1][1], {"forget": False})

    def test_login_form_switches_between_login_and_register(self) -> None:
        page = self.page
        self.assertTrue(page._display_name.isVisible() is False)
        page.set_mode("register")
        self.assertTrue(page._mode_buttons["register"].isChecked())
        self.assertIn("一个 IP 只能注册一个账号", page._form_hint.text())
        self.assertTrue(page._register_button.isVisible())
        self.assertFalse(page._login_button.isVisible())
        page.set_mode("login")
        self.assertIn("不区分大小写", page._form_hint.text())
        self.assertFalse(page._display_name.isVisible())

    def test_enter_key_routes_by_mode(self) -> None:
        page = self.page
        page._username.setText("demo_user")
        page._password.setText("demo-password-123")
        page._on_submit()
        self.assertEqual(self.service.calls[-1][0], "login")
        self.service.calls.clear()
        page.set_mode("register")
        page._on_submit()
        self.assertEqual(self.service.calls[-1][0], "register")

    def test_password_toggle_reveals_the_field(self) -> None:
        page = self.page
        page._toggle_password()
        self.assertEqual(page._password.echoMode(), page._password.Normal)
        self.assertEqual(page._password_toggle.text(), "隐藏")
        page._toggle_password()
        self.assertEqual(page._password.echoMode(), page._password.Password)

    def test_activity_rows_open_the_post(self) -> None:
        page = self.page
        self.login()
        self.assertEqual([call for call in self.service.calls if call[0] == "load_user"][-1][1], {"username": None})
        opened: list[int] = []
        page.post_requested.connect(opened.append)
        page.on_user_activity(user(), post_page([post(12, "带图首帖"), post(13, "第二篇")]), reply_page(5))
        self.assertIn("共 2 篇帖子、5 条回复", page._activity_hint.text())
        rows = [
            page._activity_layout.itemAt(index).widget()
            for index in range(page._activity_layout.count())
        ]
        self.assertEqual(len(rows), 2)
        rows[0].activated.emit(12)
        self.assertEqual(opened, [12])

    def test_empty_activity_explains_itself(self) -> None:
        self.login()
        self.page.on_user_activity(user(), post_page(), reply_page(3))
        self.assertIn("还没有发过帖子", self.page._activity_hint.text())

    def test_register_blocked_by_the_ip_rule_switches_to_login(self) -> None:
        page = self.page
        page.set_mode("register")
        page.on_session_error(
            "register", "注册失败：这个 IP 已经注册过账号了（一个 IP 只能注册一个）。"
        )
        self.assertEqual(page._mode, "login")
        self.assertTrue(page._login_button.isVisible())
        self.assertFalse(page._register_button.isVisible())

    def test_other_session_errors_keep_the_current_mode(self) -> None:
        page = self.page
        page.set_mode("register")
        page.on_session_error("login", "用户名或密码不对")
        page.on_session_error("register", "注册失败：用户名已被占用")
        self.assertEqual(page._mode, "register")

    def test_session_loss_returns_to_the_form(self) -> None:
        self.login()
        self.store.clear()
        self.app.processEvents()
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertEqual(self.page.subtitle(), "账号页 · 未登录")

    def test_refresh_asks_for_my_activity_before_the_profile(self) -> None:
        self.login()
        self.service.calls.clear()
        self.page.refresh()
        self.assertEqual([call[0] for call in self.service.calls], ["load_user", "refresh_account"])

    def test_refresh_routes_by_login_state(self) -> None:
        self.page.refresh()
        self.assertEqual([call for call in self.service.calls if call[0]][-1][0], "check_health")
        self.login()
        self.page.refresh()
        self.assertEqual([call for call in self.service.calls if call[0]][-1][0], "refresh_account")

    def test_health_callback_fills_the_server_label(self) -> None:
        self.page.on_health({"status": "ok", "service": "fxxr-forum", "latency_ms": 69, "db": {"users": 4, "posts": 12}})
        text = self.page._server_label.text()
        self.assertIn("fxxr-forum：ok", text)
        self.assertIn("延迟 69 ms", text)
        self.assertIn("用户 4 · 帖子 12", text)

    def test_broken_health_payload_is_handled(self) -> None:
        self.page.on_health(None)
        self.assertIn("没有返回", self.page._server_label.text())


class DataButtonTests(AccountPageTestCase):
    def test_forget_button_clears_login_data(self) -> None:
        self.login()
        self.page._forget_button.click()
        self.assertEqual([call for call in self.service.calls if call[0] == "logout"][-1][1], {"forget": True})

    def test_clear_cache_reports_the_report(self) -> None:
        self.page._cache_button.click()
        self.assertIn("已清理浏览缓存：2 个文件", self.page._status.text())
        self.assertIn("个文件", self.page._cache_label.text())

    def test_clear_cache_reports_failures(self) -> None:
        self.service.cache_report = ForumCacheReport(files=1, bytes=0, errors=("a.json: 拒绝访问",))
        self.page._cache_button.click()
        self.assertIn("没删掉", self.page._status.text())

    def test_cache_label_reads_the_real_cache_dir(self) -> None:
        forum_cache.write_entry("board-new", {"posts": []})
        self.page._sync_cache_label()
        self.assertIn("1 个文件", self.page._cache_label.text())

    def test_cleanup_unsubscribes_from_the_store(self) -> None:
        spy = Mock(wraps=self.page._sync_session)
        self.page._sync_session = spy
        self.page.cleanup()
        self.store.set(session())
        spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
