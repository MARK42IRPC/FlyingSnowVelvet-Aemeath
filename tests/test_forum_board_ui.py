"""主论坛子页面：列表、详情、点赞与回复的界面行为。"""

from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import Future
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from lib.core.forum_api import (
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumSession,
    ForumTag,
    ForumUser,
)
from lib.core.forum_session import ForumSessionStore
from lib.script.ui.forum_board import TAG_CHIP_LIMIT, ForumBoardPage


def post(index: int, **overrides) -> ForumPost:
    payload = {
        "id": index,
        "title": f"第 {index} 篇",
        "content": f"正文 {index}",
        "excerpt": f"摘要 {index}",
        "tags": ("general",),
        "author": ForumUser(id=1, username="demo_user", display_name="演示"),
        "reply_count": 2,
        "like_count": index,
        "view_count": 10,
        "created_at": 1_789_477_000,
    }
    payload.update(overrides)
    return ForumPost(**payload)


def reply(index: int, **overrides) -> ForumReply:
    payload = {
        "id": index,
        "post_id": 1,
        "content": f"回复 {index}",
        "author": ForumUser(id=2, username="someone", display_name="某人"),
        "like_count": 1,
        "created_at": 1_789_477_200,
    }
    payload.update(overrides)
    return ForumReply(**payload)


def page_of(posts, **overrides) -> ForumPostPage:
    payload = {"posts": tuple(posts), "page": 1, "per_page": 20, "total": len(posts), "total_pages": 1}
    payload.update(overrides)
    return ForumPostPage(**payload)


class FakeService:
    """记录被调用的协调器替身。"""

    def __init__(self) -> None:
        self.calls: list = []
        self.has_more_posts = False
        self.has_more_replies = False
        self.replies_total = 0
        self.post_total = 0
        self.current_post: ForumPost | None = None
        self.reply_error = ""
        self.snapshot = False
        self.cleaned = False

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def restore_snapshot(self) -> bool:
        self._record("restore_snapshot")
        return self.snapshot

    def load_posts(self, **kwargs) -> bool:
        self._record("load_posts", **kwargs)
        return True

    def refresh_posts(self) -> bool:
        self._record("refresh_posts")
        return True

    def load_more_posts(self) -> bool:
        self._record("load_more_posts")
        return True

    def load_more_replies(self) -> bool:
        self._record("load_more_replies")
        return True

    def open_post(self, post_id) -> bool:
        self._record("open_post", post_id=post_id)
        return True

    def close_post(self) -> None:
        self._record("close_post")
        self.current_post = None

    def post_reply(self, post_id, content, *, parent_id=None) -> str:
        self._record("post_reply", post_id=post_id, content=content, parent_id=parent_id)
        return self.reply_error

    def toggle_like(self, kind, target_id, *, liked) -> bool:
        self._record("toggle_like", kind=kind, target_id=target_id, liked=liked)
        return True

    def load_tags(self) -> bool:
        self._record("load_tags")
        return True

    def cache_summary(self) -> dict:
        return {"files": 0, "bytes": 0}

    def clear_cache(self):
        self._record("clear_cache")

    def check_health(self) -> bool:
        self._record("check_health")
        return True

    def cleanup(self) -> None:
        self.cleaned = True


class BoardPageTestCase(unittest.TestCase):
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
        self.page = ForumBoardPage(session=self.store)
        self.addCleanup(self._dispose)
        self.service = FakeService()
        self.page._service = self.service
        self.recorded: list = []
        self.page.subtitle_changed.connect(lambda: self.recorded.append("subtitle"))
        self.page.show()
        self.app.processEvents()

    def _dispose(self) -> None:
        self.page.cleanup()
        self.page.deleteLater()
        self.app.processEvents()

    def login(self) -> None:
        self.store.set(ForumSession(
            token="t" * 64,
            user=ForumUser(id=1, username="demo_user", display_name="演示"),
            expires_at=4_000_000_000,
        ))

    def release(self, widget, point) -> None:
        event = QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(point), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )
        widget.mouseReleaseEvent(event)


class ListTests(BoardPageTestCase):
    def test_rows_render_title_excerpt_and_counts(self) -> None:
        self.page.on_posts(page_of([post(1), post(2)]), False)
        self.app.processEvents()
        self.assertEqual(len(self.page._rows), 2)
        row = self.page._rows[1]
        self.assertEqual(row._title.text(), "第 1 篇")
        self.assertIn("摘要 1", row._excerpt.text())
        meta_texts = [label.text() for label in row.findChildren(type(row._meta))]
        self.assertTrue(any("回复 2 · 点赞 1 · 浏览 10" in text for text in meta_texts), meta_texts)
        self.assertIn("已显示 2 / 2", self.page._list_hint.text())
        self.assertIn("subtitle", self.recorded)

    def test_replacing_the_page_clears_old_rows(self) -> None:
        self.page.on_posts(page_of([post(1)]), False)
        self.page.on_posts(page_of([post(2)]), False)
        self.app.processEvents()
        self.assertEqual(sorted(self.page._rows), [2])

    def test_append_keeps_existing_rows_and_drops_duplicates(self) -> None:
        self.page.on_posts(page_of([post(1)]), False)
        self.page.on_posts(page_of([post(1), post(2)], page=2), True)
        self.app.processEvents()
        self.assertEqual(sorted(self.page._rows), [1, 2])

    def test_empty_result_explains_itself(self) -> None:
        self.page.on_posts(page_of(()), False)
        self.assertIn("没有找到帖子", self.page._list_hint.text())

    def test_clicking_a_row_opens_the_detail(self) -> None:
        self.page.on_posts(page_of([post(7)]), False)
        self.app.processEvents()
        row = self.page._rows[7]
        self.release(row, row._title.geometry().center())
        self.assertEqual(self.page._stack.currentIndex(), 1)
        self.assertEqual([call for call in self.service.calls if call[0] == "open_post"][-1][1]["post_id"], 7)

    def test_clicking_the_like_button_does_not_open_the_detail(self) -> None:
        self.page.on_posts(page_of([post(7)]), False)
        self.app.processEvents()
        row = self.page._rows[7]
        self.release(row, row._like.geometry().center())
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertEqual([call for call in self.service.calls if call[0] == "open_post"], [])

    def test_like_button_toggles_the_opposite_state(self) -> None:
        self.login()
        self.page.on_posts(page_of([post(7)]), False)
        self.app.processEvents()
        self.page._rows[7]._like.click()
        self.assertEqual(
            [call for call in self.service.calls if call[0] == "toggle_like"][-1][1],
            {"kind": "post", "target_id": 7, "liked": True},
        )

    def test_like_result_updates_the_row(self) -> None:
        self.page.on_posts(page_of([post(7)]), False)
        self.app.processEvents()
        row = self.page._rows[7]
        row.set_like_busy(True)
        self.page.on_likes("post", 7, True, 9)
        self.assertIn("9", row._like.text())
        self.assertTrue(row._like.isEnabled())

    def test_sort_buttons_switch_the_query(self) -> None:
        self.page.set_sort("hot")
        self.assertEqual(self.service.calls[-1][1]["sort"], "hot")
        self.assertTrue(self.page._sort_buttons["hot"].isChecked())
        self.assertFalse(self.page._sort_buttons["new"].isChecked())
        count = len(self.service.calls)
        self.page.set_sort("hot")
        self.assertEqual(len(self.service.calls), count)

    def test_tag_chips_apply_a_filter(self) -> None:
        tags = tuple(ForumTag(name=f"tag{index}", count=10 - index) for index in range(TAG_CHIP_LIMIT + 3))
        self.page.on_tags(tags)
        self.app.processEvents()
        self.assertEqual(self.page._tag_row.count(), TAG_CHIP_LIMIT + 1)
        chips = [
            self.page._tag_row.itemAt(index).widget() for index in range(self.page._tag_row.count())
        ]
        chips[1].click()
        self.assertEqual(self.service.calls[-1][1]["tag"], "tag0")
        self.assertTrue(chips[1].isChecked())

    def test_search_sends_the_keyword(self) -> None:
        self.page._search.setText("关键字")
        self.page._search_button.click()
        self.assertEqual(self.service.calls[-1][1]["query"], "关键字")

    def test_scrolling_near_the_bottom_loads_more(self) -> None:
        self.page.on_posts(page_of([post(index) for index in range(20)], has_more=True), False)
        self.app.processEvents()
        bar = self.page._list_scroll.verticalScrollBar()
        self.page._on_list_scrolled(bar.maximum())
        self.assertIn("load_more_posts", [name for name, _ in self.service.calls])

    def test_more_button_visibility_follows_the_service(self) -> None:
        self.service.has_more_posts = True
        self.page.on_posts(page_of([post(1)], has_more=True), False)
        self.assertTrue(self.page._more_button.isVisible())
        self.service.has_more_posts = False
        self.page.on_posts(page_of([post(1)]), False)
        self.assertFalse(self.page._more_button.isVisible())


class DetailTests(BoardPageTestCase):
    def open(self, **overrides) -> ForumPost:
        entry = post(1, content="# 标题\n\n正文 **粗体**\n\n- 一\n\n```\ncode\n```", **overrides)
        self.service.current_post = entry
        self.page.on_post(entry)
        self.app.processEvents()
        return entry

    def test_detail_renders_title_meta_and_body_blocks(self) -> None:
        self.open()
        self.assertEqual(self.page._stack.currentIndex(), 1)
        self.assertEqual(self.page._detail_title.text(), "第 1 篇")
        self.assertIn("演示", self.page._detail_meta.text())
        self.assertIn("#general", self.page._detail_meta.text())
        self.assertGreaterEqual(self.page._detail_body.count(), 4)

    def test_locked_post_says_so(self) -> None:
        self.open(is_locked=True)
        self.assertIn("已锁定", self.page._detail_meta.text())

    def test_replies_carry_floor_numbers_and_nesting(self) -> None:
        self.service.replies_total = 2
        self.page.on_replies(
            ForumReplyPage(replies=(reply(7), reply(8, parent_id=7)), page=1, per_page=20, total=2, has_more=False),
            False,
        )
        self.app.processEvents()
        self.assertEqual(sorted(self.page._reply_rows), [7, 8])
        self.assertEqual(self.page._reply_rows[7].floor, 1)
        first = self.page._reply_rows[7].findChildren(type(self.page._replies_title))
        self.assertTrue(any("1 楼" in label.text() for label in first))
        nested = self.page._reply_rows[8].findChildren(type(self.page._replies_title))
        self.assertTrue(any("回复 1 楼" in label.text() for label in nested))
        self.assertIn("回复（2）", self.page._replies_title.text())

    def test_more_replies_button_follows_the_service(self) -> None:
        self.page._stack.setCurrentIndex(1)
        self.service.has_more_replies = True
        self.page.on_replies(ForumReplyPage(replies=(reply(7),), page=1, total=5, has_more=True), False)
        self.app.processEvents()
        self.assertTrue(self.page._more_replies.isVisible())
        self.page._more_replies.click()
        self.assertIn("load_more_replies", [name for name, _ in self.service.calls])

    def test_empty_replies_show_a_hint(self) -> None:
        self.page._stack.setCurrentIndex(1)
        self.page.on_replies(ForumReplyPage(replies=(), page=1), False)
        self.app.processEvents()
        self.assertTrue(self.page._empty_reply_hint().isVisible())

    def test_back_button_returns_to_the_list(self) -> None:
        self.open()
        self.page._back_button.click()
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertIn("close_post", [name for name, _ in self.service.calls])

    def test_like_button_on_the_detail_uses_the_current_post(self) -> None:
        self.login()
        self.open()
        self.page._detail_like.click()
        self.assertEqual(
            [call for call in self.service.calls if call[0] == "toggle_like"][-1][1],
            {"kind": "post", "target_id": 1, "liked": True},
        )
        self.assertIn("点赞", self.page._detail_like.text())


class ReplyComposerTests(BoardPageTestCase):
    def open(self) -> None:
        entry = post(1)
        self.service.current_post = entry
        self.page.on_post(entry)
        self.app.processEvents()

    def test_logged_out_shows_the_login_button_instead_of_the_input(self) -> None:
        self.open()
        self.assertTrue(self.page._reply_login.isVisible())
        self.assertFalse(self.page._reply_send.isVisible())
        self.assertFalse(self.page._reply_input.isVisible())
        self.assertTrue(self.page._detail_login_hint.isVisible())

    def test_login_request_is_reported_to_the_window(self) -> None:
        seen: list = []
        self.page.login_requested.connect(lambda: seen.append(True))
        self.page._reply_login.click()
        self.assertEqual(seen, [True])

    def test_send_stays_disabled_until_there_is_text(self) -> None:
        self.login()
        self.open()
        self.assertTrue(self.page._reply_send.isVisible())
        self.assertFalse(self.page._reply_send.isEnabled())
        self.page._reply_input.setText("写得好")
        self.assertTrue(self.page._reply_send.isEnabled())

    def test_sending_uses_the_selected_floor(self) -> None:
        self.login()
        self.open()
        self.page.on_replies(ForumReplyPage(replies=(reply(7),), page=1, total=1), False)
        self.page._set_reply_target(7)
        self.assertIn("1 楼", self.page._reply_target_label.text())
        self.page._reply_input.setText("  写得好  ")
        self.page._on_send_reply()
        self.assertEqual(
            [call for call in self.service.calls if call[0] == "post_reply"][-1][1],
            {"post_id": 1, "content": "  写得好  ", "parent_id": 7},
        )

    def test_service_refusals_land_in_the_hint(self) -> None:
        self.login()
        self.open()
        self.service.reply_error = "这篇帖子已锁定，暂时不能回复"
        self.page._reply_input.setText("写得好")
        self.page._on_send_reply()
        self.assertIn("锁定", self.page._detail_hint.text())

    def test_posted_reply_is_appended_and_the_input_cleared(self) -> None:
        self.login()
        self.open()
        self.service.replies_total = 1
        self.page._reply_input.setText("写得好")
        self.page.on_reply_posted(reply(9), 2)
        self.app.processEvents()
        self.assertEqual(self.page._reply_input.text(), "")
        self.assertEqual(self.page._reply_rows[9].floor, 2)


class StatusTests(BoardPageTestCase):
    def test_subtitle_reports_the_post_total_and_detail_title(self) -> None:
        self.assertEqual(self.page.subtitle(), "主论坛")
        self.service.post_total = 12
        self.assertIn("共 12 篇帖子", self.page.subtitle())
        self.service.current_post = post(1)
        self.page._stack.setCurrentIndex(1)
        self.assertIn("第 1 篇", self.page.subtitle())

    def test_status_and_errors_are_shown_on_both_views(self) -> None:
        self.page.on_status("正在读取帖子…")
        self.assertEqual(self.page._list_hint.text(), "正在读取帖子…")
        self.page.on_error("论坛请求失败")
        self.assertEqual(self.page._detail_hint.text(), "论坛请求失败")
        self.assertEqual(self.page._detail_hint.property("tone"), "warn")

    def test_cleanup_stops_the_service(self) -> None:
        self.page.cleanup()
        self.assertTrue(self.service.cleaned)


class BackFromDetailTests(unittest.TestCase):
    """「返回列表」要真的回得去：晚到的详情结果不能再把页面拽回详情。

    这一例刻意用真的 `CommunityService`，只把提交口换成一个还没完成的 Future，
    因为问题就出在「请求比用户慢」上——替身服务永远复现不了。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.page = ForumBoardPage(session=ForumSessionStore())
        self.addCleanup(self._dispose)
        self.future = Future()
        self.page._service._submit_io = lambda worker, *args: self.future
        self.page.resize(760, 620)
        self.page.show()
        self.app.processEvents()

    def _dispose(self) -> None:
        self.page.cleanup()
        self.page.deleteLater()
        self.app.processEvents()

    def _settle(self) -> None:
        for _ in range(4):
            self.app.processEvents()

    def test_back_stays_on_the_list_when_the_post_arrives_late(self) -> None:
        self.page.open_post(7)
        generation = self.page._service._detail_generation
        self.assertEqual(self.page._stack.currentIndex(), 1)

        self.page._back_button.click()
        self.assertEqual(self.page._stack.currentIndex(), 0)

        self.future.set_result((generation, post(7), ForumReplyPage(replies=(), page=1, total=0)))
        self._settle()
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertEqual(self.page.subtitle(), "主论坛")

    def test_back_survives_a_late_failure_too(self) -> None:
        self.page.open_post(7)
        self.page._back_button.click()
        self.future.set_exception(RuntimeError("断网"))
        self._settle()
        self.assertEqual(self.page._stack.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()