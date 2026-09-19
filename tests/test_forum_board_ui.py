"""主论坛子页面：列表、详情、点赞与回复的界面行为。"""

from __future__ import annotations

import base64
import os
import pathlib
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

from PyQt5 import sip
from PyQt5.QtCore import QBuffer, QEvent, QIODevice, QPointF, Qt
from PyQt5.QtGui import QFont, QMouseEvent, QPixmap, QTextCursor
from PyQt5.QtWidgets import QApplication, QLabel

from lib.core.forum_api import (
    FORUM_CONTENT_MAX,
    FORUM_IMAGES_PER_POST,
    ForumImage,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumSession,
    ForumTag,
    ForumUser,
)
from lib.core.forum_session import ForumSessionStore
from lib.core.forum_markdown import IMAGE_PLACEHOLDER
from lib.script.ui import forum_board
from lib.script.ui.forum_board import (
    FORUM_IMAGE_MAX_PIXELS,
    TAG_CHIP_LIMIT,
    ForumBoardPage,
    ForumDetailImage,
    ForumImageThumb,
)
from lib.script.ui.forum_text import MarkupText

#: 一张 1×1 的真 PNG（`QPixmap` 解得开）；缩略图那几条用例拿它当「一张图」。
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def png_bytes(width: int, height: int) -> bytes:
    """现场造一张真 PNG（像素尺寸就是要的那个数），给「按比例铺开」那几条用例用。"""
    pixmap = QPixmap(int(width), int(height))
    pixmap.fill(Qt.red)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


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
        self.thread_error = ""
        self.upload_error = ""
        self.uploaded: list = []
        self.upload_result = ForumImage(id="a" * 32, bytes=128)
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

    def post_thread(self, title, content, *, tags=None, images=None) -> str:
        self._record("post_thread", title=title, content=content, tags=tags, images=images)
        return self.thread_error

    def upload_image(self, data, *, name="") -> str:
        # 键名不叫 name：`_record(name, **kwargs)` 的第一个参数就是它，会撞上。
        self._record("upload_image", size=len(data), filename=name)
        return self.upload_error

    def load_thumbnail(self, image_id) -> bool:
        self._record("load_thumbnail", image_id=image_id)
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

    def test_the_reply_area_survives_being_cleared_twice(self) -> None:
        """空态提示不能跟着回复行被清掉。

        它和回复行挤在同一个布局里：早先 `_clear_reply_rows()` 会把整个布局清空，
        `self._empty_hint` 于是成了指向已销毁 QLabel 的野引用。下一句
        `_sync_replies_state()` 在 `setVisible` 上抛
        「wrapped C/C++ object of type QLabel has been deleted」，整个回调当场中断——
        线上看到的就是「回复列表不显示」（2026-09-16 日志实测，看第二篇帖子必现）。
        """
        self.page._stack.setCurrentIndex(1)
        self.page.on_replies(ForumReplyPage(replies=(), page=1), False)
        self._settle()
        self.page.on_replies(ForumReplyPage(replies=(), page=1), False)
        self._settle()
        self.assertTrue(self.page._empty_hint.isVisible())

    def test_a_reply_still_lands_after_the_reply_area_was_cleared(self) -> None:
        """空帖 → 换一篇有回复的帖子 → 再发一条：日志里 `on_reply_posted` 就是这么挂的。"""
        self.page._stack.setCurrentIndex(1)
        self.page.on_replies(ForumReplyPage(replies=(), page=1), False)
        self._settle()
        self.page.on_replies(ForumReplyPage(replies=(reply(7),), page=1, total=1), False)
        self._settle()
        self.page.on_reply_posted(reply(9), 2)
        self._settle()
        self.assertEqual(sorted(self.page._reply_rows), [7, 9])

    def test_the_reply_list_survives_a_destroyed_empty_hint(self) -> None:
        """空态提示被别处销毁，也不能把整片回复列表带走。

        `self._empty_hint` 只是个 Python 侧引用：底下的 C++ 对象一没，`setVisible()` 就抛
        「wrapped C/C++ object of type QLabel has been deleted」，`on_replies` 当场中断——
        用户看到的就是「回复列表不显示」（2026-09-16 线上日志实测）。这里是比「别删它」更强
        的一道闸：真被删了就当它没有、重建一个，回复行照铺。
        """
        self.page._stack.setCurrentIndex(1)
        sip.delete(self.page._empty_hint)
        self.page.on_replies(ForumReplyPage(replies=(reply(7),), page=1, total=1), False)
        self._settle()
        self.assertEqual(sorted(self.page._reply_rows), [7])
        self.assertIn("回复（1）", self.page._replies_title.text())
        self.assertFalse(self.page._empty_reply_hint().isVisible())
        self.assertIs(self.page._empty_reply_hint().parent(), self.page._replies_host)

    def test_a_second_page_of_the_same_post_keeps_the_hint_alive(self) -> None:
        """同一篇帖子反复铺回复（切页 / 重进详情）时，空态提示始终是同一个活控件。"""
        self.page._stack.setCurrentIndex(1)
        hint = self.page._empty_reply_hint()
        self._settle()
        for _ in range(3):
            self.page.on_replies(ForumReplyPage(replies=(reply(7),), page=1, total=1), False)
            self._settle()
        self.assertIs(self.page._empty_reply_hint(), hint)
        self.assertIs(self.page._empty_hint.parent(), self.page._replies_host)
        self.assertEqual(sorted(self.page._reply_rows), [7])
        self.assertEqual(self.page._replies_layout.indexOf(hint), 0)
        self.assertFalse(hint.isVisible())

    def _settle(self) -> None:
        """跑一遍事件循环，并让 `deleteLater()` 真的落地。

        `processEvents()` 不会处理 DeferredDelete：不补这一下，被删的控件在测试里
        仍然「活着」，这类野引用 bug 就抓不到了。
        """
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

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


class ComposerTests(BoardPageTestCase):
    """发帖页：进入、本地字段转发、发布成功回到列表。"""

    def test_toolbar_carries_every_sort_and_the_compose_entry(self) -> None:
        from lib.core.forum_api import FORUM_SORT_LABELS

        self.assertEqual(set(self.page._sort_buttons), set(FORUM_SORT_LABELS))
        self.assertEqual(self.page._compose_button.text(), "发帖")

    def test_logged_out_composer_asks_for_login(self) -> None:
        self.page.open_composer()
        self.assertEqual(self.page._stack.currentIndex(), 2)
        self.assertTrue(self.page._thread_error.isVisible())
        self.assertIn("登录", self.page._thread_error.text())
        self.assertFalse(self.page._compose_send.isEnabled())

    def test_logged_in_composer_is_ready(self) -> None:
        self.login()
        self.page.open_composer()
        self.assertFalse(self.page._thread_error.isVisible())
        self.assertTrue(self.page._compose_send.isEnabled())

    def test_publishing_forwards_title_body_and_tags(self) -> None:
        self.login()
        self.page.open_composer()
        self.page._thread_title.setText("新主题")
        self.page._thread_body.setPlainText("正文内容")
        self.page._thread_tags.setText("自测, 接口")
        self.page._compose_send.click()
        call = [item for item in self.service.calls if item[0] == "post_thread"][-1]
        self.assertEqual(
            call[1],
            {"title": "新主题", "content": "正文内容", "tags": "自测, 接口", "images": []},
        )

    def test_service_refusal_stays_on_the_composer(self) -> None:
        self.login()
        self.service.thread_error = "标题至少 2 个字"
        self.page.open_composer()
        self.page._thread_title.setText("短")
        self.page._compose_send.click()
        self.assertEqual(self.page._stack.currentIndex(), 2)
        self.assertEqual(self.page._thread_error.text(), "标题至少 2 个字")

    def test_published_thread_lands_on_top_of_the_list(self) -> None:
        self.login()
        self.page.on_posts(page_of([post(2), post(3)]), False)
        self.page.open_composer()
        self.page._thread_title.setText("新主题")
        self.page._thread_body.setPlainText("正文内容")
        self.page._thread_tags.setText("自测")
        self.page.on_thread_posted(post(99, title="刚发的帖子"))
        self.assertEqual(self.page._stack.currentIndex(), 0)
        self.assertEqual(self.page._thread_title.text(), "")
        self.assertEqual(self.page._thread_body.toPlainText(), "")
        self.assertEqual(self.page._thread_tags.text(), "")
        first = self.page._list_layout.itemAt(0).widget()
        self.assertIs(first, self.page._rows[99])
        self.assertIn("已发布", self.page._list_hint.text())

    def test_counter_warns_when_the_body_is_too_long(self) -> None:
        from lib.core.forum_api import FORUM_CONTENT_MAX

        self.page.open_composer()
        self.page._thread_body.setPlainText("x" * (FORUM_CONTENT_MAX + 5))
        self.assertEqual(self.page._thread_counter.property("tone"), "warn")
        self.assertIn("超出", self.page._thread_counter.text())
        self.page._thread_body.setPlainText("短正文")
        self.assertEqual(self.page._thread_counter.property("tone"), "")
        self.assertEqual(self.page._thread_counter.text(), f"3 / {FORUM_CONTENT_MAX}")


class StatusTests(BoardPageTestCase):
    def test_subtitle_mentions_the_composer(self) -> None:
        self.page.open_composer()
        self.assertIn("发布新帖", self.page.subtitle())

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


class ComposerToolsTests(BoardPageTestCase):
    """发帖页的辅助：行内标记、文字色 / 描边色、图片上传。"""

    def open_composer(self) -> None:
        self.login()
        self.page.open_composer()
        self.app.processEvents()

    def select_all(self) -> None:
        cursor = self.page._thread_body.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(len(self.page._body_text()), QTextCursor.KeepAnchor)
        self.page._thread_body.setTextCursor(cursor)

    def test_markup_button_wraps_the_selection(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("正文内容")
        self.select_all()
        self.page._format_buttons["bold"].click()
        self.assertEqual(self.page._body_text(), "**正文内容**")
        self.assertTrue(self.page._format_buttons["bold"].isChecked())

    def test_markup_button_reflects_the_caret(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("**粗**")
        cursor = self.page._thread_body.textCursor()
        cursor.setPosition(2)
        self.page._thread_body.setTextCursor(cursor)
        self.assertTrue(self.page._format_buttons["bold"].isChecked())
        self.assertFalse(self.page._format_buttons["italic"].isChecked())

    def test_markup_button_wont_blow_the_character_limit(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("x" * FORUM_CONTENT_MAX)
        self.select_all()
        self.page._format_buttons["bold"].click()
        self.assertEqual(len(self.page._body_text()), FORUM_CONTENT_MAX)
        self.assertIn("超过", self.page._thread_error.text())

    def test_color_button_wraps_the_selection_and_reveals_its_slider(self) -> None:
        self.open_composer()
        self.assertFalse(self.page._color_host.isVisible())
        self.page._thread_body.setPlainText("红字")
        self.select_all()
        self.page._color_buttons["color"].click()
        text = self.page._body_text()
        self.assertTrue(text.startswith("[color=#"), text)
        self.assertTrue(text.endswith("[/color]"), text)
        self.assertIn("红字", text)
        self.assertTrue(self.page._color_host.isVisible())
        self.assertTrue(self.page._color_pickers["color"].isVisible())
        self.assertFalse(self.page._color_pickers["outline"].isVisible())

    def test_color_button_toggles_the_wrap_off_again(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("红字")
        self.select_all()
        self.page._color_buttons["color"].click()
        self.page._color_buttons["color"].click()
        self.assertEqual(self.page._body_text(), "红字")

    def test_the_outline_button_is_independent_from_the_text_colour(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("描边字")
        self.select_all()
        self.page._color_buttons["outline"].click()
        text = self.page._body_text()
        self.assertIn("[outline=#", text)
        self.assertNotIn("[color=", text)

    def test_dragging_the_picker_recolours_the_same_run(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("红字")
        self.select_all()
        self.page._color_buttons["color"].click()
        self.page._color_pickers["color"].set_color("#00ff00")
        text = self.page._body_text()
        self.assertEqual(text.count("[color="), 1)
        self.assertIn(f"[color={self.page._color_pickers['color'].color()}]", text)

    def test_image_button_follows_the_login_state(self) -> None:
        self.page.open_composer()
        self.assertFalse(self.page._image_button.isEnabled())
        self.login()
        self.page._sync_composer()
        self.assertTrue(self.page._image_button.isEnabled())

    def test_adding_an_image_goes_through_the_service(self) -> None:
        self.open_composer()
        target = pathlib.Path(self._tmp.name) / "封面.png"
        target.write_bytes(PNG_1PX)
        with patch.object(
            forum_board.QFileDialog, "getOpenFileName", return_value=(str(target), "")
        ):
            self.page._on_add_image()
        self.assertEqual([name for name, _ in self.service.calls], ["upload_image"])
        self.assertEqual(self.service.calls[-1][1]["filename"], "封面.png")
        self.assertTrue(self.page._uploading_image)
        self.assertFalse(self.page._image_button.isEnabled())
        self.assertEqual(self.page._image_button.text(), "正在上传…")

    def test_the_fifth_image_is_refused_before_the_file_dialog(self) -> None:
        self.open_composer()
        self.page._compose_images = [
            ForumImage(id=f"{index:032x}") for index in range(FORUM_IMAGES_PER_POST)
        ]
        self.page._on_add_image()
        self.assertIn(f"最多挂 {FORUM_IMAGES_PER_POST} 张图", self.page._thread_error.text())
        self.assertEqual([name for name, _ in self.service.calls], [])

    def upload_one(self) -> ForumImage:
        image = ForumImage(id="c" * 32, url="/api/images/" + "c" * 32)
        self.page._pending_image_bytes = PNG_1PX
        self.page._uploading_image = True
        self.page.on_image_uploaded(image)
        self.app.processEvents()
        return image

    def test_uploaded_image_is_attached_and_written_into_the_body(self) -> None:
        self.open_composer()
        self.upload_one()
        self.assertEqual([image.id for image in self.page._compose_images], ["c" * 32])
        self.assertIn(f"![图片](/api/images/{'c' * 32})", self.page._body_text())
        self.assertTrue(self.page._image_strip.isVisible())
        thumbs = self.page._image_strip.findChildren(ForumImageThumb)
        self.assertEqual(len(thumbs), 1)
        self.assertTrue(thumbs[0].has_data())

    def test_publishing_carries_the_uploaded_images(self) -> None:
        self.open_composer()
        self.upload_one()
        self.page._thread_title.setText("带图的帖子")
        self.page._compose_send.click()
        call = [item for item in self.service.calls if item[0] == "post_thread"][-1]
        self.assertEqual(call[1]["images"], ["c" * 32])

    def test_clicking_a_thumbnail_drops_it_from_the_post(self) -> None:
        self.open_composer()
        self.upload_one()
        thumb = self.page._image_strip.findChildren(ForumImageThumb)[0]
        self.release(thumb, QPointF(2, 2))
        self.app.processEvents()
        self.assertEqual(self.page._compose_images, [])
        self.assertFalse(self.page._image_strip.isVisible())

    def test_upload_failure_lands_on_the_error_line(self) -> None:
        self.open_composer()
        self.page._uploading_image = True
        self.page.on_image_error("图片超过论坛 1500 KB 的上限，压缩一下再传")
        self.assertIn("1500 KB", self.page._thread_error.text())
        self.assertTrue(self.page._thread_error.isVisible())
        self.assertEqual(self.page._compose_images, [])
        self.assertTrue(self.page._image_button.isEnabled())

    def test_publishing_clears_the_attached_images(self) -> None:
        self.open_composer()
        self.upload_one()
        self.page.on_thread_posted(post(99))
        self.app.processEvents()
        self.assertEqual(self.page._compose_images, [])
        self.assertFalse(self.page._image_strip.isVisible())


class ListThumbTests(BoardPageTestCase):
    """列表行的缩略图：按图 id 去服务层取，取回来的铺上，取不回来的别再要。"""

    def asked(self) -> list:
        return [item[1]["image_id"] for item in self.service.calls if item[0] == "load_thumbnail"]

    def test_rows_with_images_ask_for_thumbnails(self) -> None:
        entry = post(1, images=(ForumImage(id="a" * 32), ForumImage(id="b" * 32)))
        self.page.on_posts(page_of([entry]), False)
        self.app.processEvents()
        self.assertEqual(self.asked(), ["a" * 32, "b" * 32])
        self.assertEqual(len(self.page.findChildren(ForumImageThumb)), 2)

    def test_thumbnail_bytes_are_painted_onto_the_row(self) -> None:
        entry = post(1, images=(ForumImage(id="a" * 32),))
        self.page.on_posts(page_of([entry]), False)
        self.page.on_thumbnail("a" * 32, PNG_1PX)
        thumbs = self.page.findChildren(ForumImageThumb)
        self.assertEqual(len(thumbs), 1)
        self.assertTrue(thumbs[0].has_data())

    def test_a_failed_thumbnail_is_not_asked_for_again(self) -> None:
        entry = post(1, images=(ForumImage(id="a" * 32),))
        self.page.on_posts(page_of([entry]), False)
        self.page.on_thumbnail("a" * 32, b"")
        self.page._refresh_thumbs()
        self.assertEqual(self.asked(), ["a" * 32])

    def test_garbage_bytes_leave_the_placeholder(self) -> None:
        entry = post(1, images=(ForumImage(id="a" * 32),))
        self.page.on_posts(page_of([entry]), False)
        self.page.on_thumbnail("a" * 32, b"not an image")
        self.assertFalse(self.page.findChildren(ForumImageThumb)[0].has_data())

    def test_rows_without_an_image_list_keep_the_text_hint(self) -> None:
        self.page.on_posts(page_of([post(1, image_count=2)]), False)
        self.assertIn("【图片 ×2】", self.page._rows[1]._excerpt.text())

    def test_rows_with_images_do_not_repeat_the_count_in_text(self) -> None:
        entry = post(1, image_count=1, images=(ForumImage(id="a" * 32),))
        self.page.on_posts(page_of([entry]), False)
        self.assertNotIn("【图片", self.page._rows[1]._excerpt.text())


class DetailBodyTests(BoardPageTestCase):
    """详情页正文：纯文字走 QLabel，带标记 / 带颜色的一段换成能逐段描边的 `MarkupText`。"""

    def open(self, content: str, **overrides) -> None:
        entry = post(1, content=content, **overrides)
        self.service.current_post = entry
        self.page.on_post(entry)
        self.app.processEvents()

    def widgets(self) -> list:
        body = self.page._detail_body
        return [body.itemAt(index).widget() for index in range(body.count())]

    def rich(self) -> list:
        return [widget for widget in self.widgets() if isinstance(widget, MarkupText)]

    def test_a_plain_paragraph_stays_a_label(self) -> None:
        self.open("只是正文")
        self.assertEqual(self.rich(), [])
        self.assertEqual(self.widgets()[0].text(), "只是正文")

    def test_a_coloured_paragraph_is_rendered_by_markup_text(self) -> None:
        self.open("[color=#ff0000]红[/color]与[outline=#00ff00]绿[/outline]")
        self.assertEqual(len(self.rich()), 1)
        widget = self.rich()[0]
        self.assertEqual(widget.document().toPlainText(), "红与绿")
        self.assertNotIn("[color=", widget.document().toPlainText())
        # `QTextCursor.charFormat()` 说的是「光标前一个字」的格式，所以位置要往后挪一格。
        first = QTextCursor(widget.document())
        first.setPosition(1)
        self.assertEqual(first.charFormat().foreground().color().name(), "#ff0000")
        second = QTextCursor(widget.document())
        second.setPosition(3)
        self.assertEqual(second.charFormat().textOutline().color().name(), "#00ff00")
        self.assertGreater(second.charFormat().textOutline().width(), 0)

    def test_inline_markers_render_instead_of_being_shown(self) -> None:
        self.open("前**粗**后")
        self.assertEqual(len(self.rich()), 1)
        widget = self.rich()[0]
        self.assertEqual(widget.document().toPlainText(), "前粗后")
        cursor = QTextCursor(widget.document())
        cursor.setPosition(2)
        self.assertGreaterEqual(cursor.charFormat().fontWeight(), QFont.Bold)

    def test_a_code_block_keeps_its_markers_verbatim(self) -> None:
        self.open("```\n**不是加粗**\n```")
        self.assertEqual(self.rich(), [])
        self.assertIn("**不是加粗**", self.widgets()[0].text())

    def test_the_detail_lists_the_post_images(self) -> None:
        self.open("正文", images=(ForumImage(id="a" * 32),))
        self.assertEqual(len(self.page._detail_host.findChildren(ForumImageThumb)), 1)
        self.assertEqual(
            [item[1]["image_id"] for item in self.service.calls if item[0] == "load_thumbnail"],
            ["a" * 32],
        )

    def asked(self) -> list:
        return [
            item[1]["image_id"] for item in self.service.calls if item[0] == "load_thumbnail"
        ]

    def labels(self) -> list:
        return [widget for widget in self.widgets() if isinstance(widget, QLabel)]

    def text_of_labels(self) -> str:
        """正文里能看见的文字：纯文字段是 QLabel，带标记 / 带颜色的一段是 `MarkupText`。"""
        pieces = [
            label.text()
            for label in self.labels()
            if not isinstance(label, ForumDetailImage)
        ]
        pieces.extend(widget.document().toPlainText() for widget in self.rich())
        return " / ".join(pieces)

    def shown_pictures(self) -> list:
        return self.page._detail_host.findChildren(ForumDetailImage)

    def test_an_image_markdown_becomes_the_picture_in_place(self) -> None:
        """正文里那句图片 Markdown 就地铺成真图，那句「【图片】」占位符不再当字显示。"""
        ident = "a" * 32
        self.open(f"看图\n\n![图片](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.assertNotIn(IMAGE_PLACEHOLDER, self.text_of_labels())
        self.assertEqual([image.image_id for image in self.shown_pictures()], [ident])
        self.assertEqual(self.asked(), [ident])
        # 就地铺过的那张图不再往正文底下补一行缩略图（同一张图不铺两遍）。
        layouts = [
            self.page._detail_body.itemAt(index).layout()
            for index in range(self.page._detail_body.count())
        ]
        self.assertEqual(layouts, [None, None])

    def column_width(self) -> int:
        """正文栏的可用宽度：图就该铺满这里（宿主宽度还要刨掉它自己那圈外边距）。"""
        return self.page._detail_body.geometry().width()

    def content_box(self, image) -> tuple[int, int]:
        """图控件的**内容区**尺寸：边框不算，那是不放图的。"""
        return image.width() - image._frame(), image.height() - image._frame()

    def assert_fills_the_column(self, image) -> None:
        """位图正好铺满内容区（可以差一个像素的取整），控件又正好占满正文栏。

        两条都必须成立：位图比内容区大就是被裁（QLabel 不缩），比内容区小就是缩在一角留着空白
        ——两种都算排版坏了。
        """
        content = self.content_box(image)
        drawn = image.pixmap().size()
        self.assertLessEqual(abs(drawn.width() - content[0]), 1, (content, drawn))
        self.assertLessEqual(abs(drawn.height() - content[1]), 1, (content, drawn))
        self.assertGreaterEqual(content[0], self.column_width() - 2 * image._frame() - 1)
        self.assertLessEqual(content[0], self.column_width() + 1)

    def test_the_picture_fills_the_column_without_being_stretched(self) -> None:
        ident = "a" * 32
        self.page.resize(760, 640)
        self.app.processEvents()
        self.open(f"![图](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(1600, 800))
        self.app.processEvents()
        image = self.shown_pictures()[0]
        self.assert_fills_the_column(image)
        # 4:2 的图铺出来还是 4:2：等比缩放，不拉伸（两像素描边先刨掉）。
        width, height = self.content_box(image)
        self.assertAlmostEqual(width / height, 2.0, places=2)

    def test_a_small_picture_is_scaled_up_to_the_column(self) -> None:
        """比正文栏窄的图跟着放大：正文里的图缩在一角会显得整块排版断开。"""
        ident = "a" * 32
        self.page.resize(760, 640)
        self.app.processEvents()
        self.open(f"![图](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(60, 30))
        self.app.processEvents()
        image = self.shown_pictures()[0]
        self.assert_fills_the_column(image)
        width, height = self.content_box(image)
        self.assertGreater(width, 60)
        self.assertAlmostEqual(width / height, 2.0, places=2)

    def test_the_picture_refits_when_the_column_changes_width(self) -> None:
        ident = "a" * 32
        self.page.resize(760, 640)
        self.app.processEvents()
        self.open(f"![图](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(1600, 800))
        self.app.processEvents()
        wide = self.shown_pictures()[0].width()
        self.page.resize(460, 640)
        self.app.processEvents()
        image = self.shown_pictures()[0]
        self.assertLess(image.width(), wide)
        self.assert_fills_the_column(image)

    def test_the_first_frame_after_the_bytes_arrive_is_already_full_width(self) -> None:
        """字节到的**当场**就该是整幅图，不能先画一张小图再跳大。

        控件宽度最终是布局给的，但如果铺图时只按「占位框那点宽度」铺，第一帧看到的就是一张
        138x69 的小图贴在 138x358 的框里（离屏实测），下一帧才跳到 716x358——观感上是一次闪烁。
        """
        ident = "a" * 32
        self.page.resize(760, 640)
        self.app.processEvents()
        self.open(f"![图](/api/images/{ident})", images=(ForumImage(id=ident),))
        # 特意**不**跑事件循环：查的就是「铺图这一下」之后、布局接手之前的状态。
        self.page.on_thumbnail(ident, png_bytes(1600, 800))
        image = self.shown_pictures()[0]
        self.assert_fills_the_column(image)

    def test_the_picture_is_never_cropped_by_a_squeezed_layout(self) -> None:
        """正文长到出现竖滚动条时，图片仍然完整。

        图的高度原来是布局算的：布局在某一帧里没把它排开就会压到最小高度，位图比内容区高，
        QLabel 不缩放、**直接裁掉一条**（离屏实测 704x353 的控件被压成 704x84，位图却是
        702x351，用户看到的是图被拦腰截断）。高度改成按宽高比钉死之后，这一帧不会出现。
        """
        ident = "a" * 32
        body = "\n\n".join(f"第 {index} 段正文，用来把页面撑出竖滚动条。" for index in range(40))
        self.page.resize(760, 420)
        self.app.processEvents()
        self.open(f"![图](/api/images/{ident})\n\n{body}", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(1600, 800))
        for _ in range(4):
            self.app.processEvents()
            image = self.shown_pictures()[0]
            self.assert_fills_the_column(image)

    def test_a_tall_picture_keeps_its_ratio(self) -> None:
        """竖图也一样：铺满栏宽、按原比例给高度，不裁也不压。"""
        ident = "a" * 32
        self.open(f"![竖图](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(400, 1600))
        self.app.processEvents()
        image = self.shown_pictures()[0]
        self.assert_fills_the_column(image)
        width, height = self.content_box(image)
        self.assertAlmostEqual(width / height, 0.25, places=2)

    def test_an_absurd_aspect_ratio_is_capped(self) -> None:
        """病态长条不会被放大成几亿像素的位图。

        正文里的图铺满栏宽才好看，但等比放大是「栏宽说了算」的：一张 1x2000 的 PNG 不到 100 字节，
        按 704 的栏宽放大要 702x1404000 的位图（约 4 GB 的像素缓冲），进程直接崩。所以超预算的图
        按同一个比例整体缩回来——宽高比不变，只是不再铺满栏宽。
        """
        ident = "a" * 32
        self.page.resize(760, 640)
        self.app.processEvents()
        self.open(f"![长条](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.page.on_thumbnail(ident, png_bytes(1, 2000))
        self.app.processEvents()
        image = self.shown_pictures()[0]
        width, height = self.content_box(image)
        drawn = image.pixmap().size()
        self.assertLessEqual(width * height, FORUM_IMAGE_MAX_PIXELS)
        # 比例仍然是原图的（1:2000），只是缩小了：没有拉伸、也没有旋转。
        self.assertAlmostEqual(width / height, 1 / 2000, places=5)
        self.assertLessEqual(abs(drawn.width() - width), 1, (width, height, drawn))
        self.assertLessEqual(abs(drawn.height() - height), 1, (width, height, drawn))

    def test_switching_posts_takes_the_picture_away(self) -> None:
        ident = "a" * 32
        self.open(f"![图](/api/images/{ident})", images=(ForumImage(id=ident),))
        self.assertEqual(len(self.shown_pictures()), 1)
        self.open("另一篇，没有图")
        self.assertEqual(self.shown_pictures(), [])

    def test_an_external_picture_stays_text(self) -> None:
        """外站地址取不到字节，仍旧当文字显示（别摆一个永远空着的图框）。"""
        self.open("外站 ![x](https://example.com/a.png) 不动")
        self.assertEqual(self.shown_pictures(), [])
        self.assertIn("example.com", self.text_of_labels())

    def test_the_fallback_strip_only_carries_pictures_the_body_never_showed(self) -> None:
        inline, other = "a" * 32, "b" * 32
        self.open(
            f"![图](/api/images/{inline})",
            images=(ForumImage(id=inline), ForumImage(id=other)),
        )
        self.assertEqual([image.image_id for image in self.shown_pictures()], [inline])
        thumbs = self.page._detail_host.findChildren(ForumImageThumb)
        self.assertEqual([thumb.image_id for thumb in thumbs], [other])

    def test_switching_posts_takes_the_previous_images_away(self) -> None:
        """换一篇帖子时，正文配图那一行连它里面的缩略图都要一起清掉。

        配图行是 `addLayout()` 塞进 `_detail_body` 的**嵌套布局**：`takeAt()` 只把它从这一层
        摘下来，布局本身还攥着缩略图不放——只要那个布局对象还被谁引用着（信号、闭包、调试器
        都算），旧图就一直挂在详情页上，压在回复区那一块（用户看到的是「回复列表不显示」）。
        """
        self.open("正文", images=(ForumImage(id="a" * 32),))
        strip = self.page._detail_body.itemAt(1).layout()
        self.assertIsNotNone(strip)
        self.assertEqual(strip.count(), 2)  # 一张缩略图 + 末尾那条撑开的空白
        self.open("另一篇，没有图")
        self.assertEqual(self.page._detail_host.findChildren(ForumImageThumb), [])
        self.assertEqual(strip.count(), 0)
        self.assertEqual(self.page._detail_body.count(), 1)

if __name__ == "__main__":
    unittest.main()
