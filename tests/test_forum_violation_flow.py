"""违规留言的端到端链路：服务层拦截 → 12 秒冷却 → 窗口提示与语音。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.forum import FORUM_POST_COOLDOWN_SECS, ForumService
from lib.core.forum_filter import ForumViolation
from lib.script.ui.forum_window import ForumWindow, violation_message
from lib.script.voice.forum_violation import ForumViolationSound
from tests.test_forum_service import run_inline


def _service(clock=None):
    return ForumService(
        dispatch=lambda callback: callback(),
        on_page=lambda page: None,
        on_error=lambda message: None,
        submit_io=run_inline,
        request_post=lambda *args, **kwargs: None,
        **({"clock": clock} if clock is not None else {}),
    )


class ServiceViolationTests(unittest.TestCase):
    def test_violation_is_returned_as_a_structured_reason(self):
        service = _service()
        result = service.post("点我 https://spam.example")
        self.assertIsInstance(result, ForumViolation)
        self.assertEqual(result.reason, "link")

    def test_violation_starts_the_same_twelve_second_cooldown(self):
        now = [1000.0]
        service = _service(clock=lambda: now[0])
        service.post("加微信")
        self.assertAlmostEqual(
            service.cooldown_remaining(), FORUM_POST_COOLDOWN_SECS, places=3
        )
        # 冷却期内再发（哪怕内容干净）也要被挡下，无法连续试探过滤规则。
        self.assertIsInstance(service.post("这条是干净的"), str)

    def test_clean_content_still_posts(self):
        sent = []
        service = ForumService(
            dispatch=lambda callback: callback(),
            on_page=lambda page: None,
            on_error=lambda message: None,
            on_posted=sent.append,
            submit_io=run_inline,
            request_post=_fake_post,
        )
        self.assertIsNone(service.post("今天天气真好"))


def _fake_post(*args, **kwargs):
    class _Response:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "message": {
                    "id": 42,
                    "nickname": "小明",
                    "content": kwargs["json"]["content"],
                    "accent": kwargs["json"]["accent"],
                    "created_at": 1,
                },
            }

        def close(self):
            return None

    return _Response()


class ViolationMessageTests(unittest.TestCase):
    def test_each_reason_has_its_own_copy(self):
        link = violation_message(ForumViolation("link", "https://a.com"))
        number = violation_message(ForumViolation("long_number", "123456"))
        word = violation_message(ForumViolation("banned_word", "加微信"))
        self.assertIn("网址", link)
        self.assertIn("数字", number)
        self.assertIn("违规词", word)
        self.assertIn("https://a.com", link)

    def test_unknown_reason_falls_back(self):
        self.assertIn("不允许", violation_message(ForumViolation("???", "")))


class WindowViolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        cleanup_event_center()
        self.voices: list[dict] = []
        get_event_center().subscribe(
            EventType.VOICE_REQUEST,
            lambda event: self.voices.append(dict(event.data)),
        )

    def tearDown(self) -> None:
        cleanup_event_center()

    def test_violation_plays_a_voice_and_marks_the_status(self):
        window = ForumWindow()
        try:
            window._service = _service()
            window._input.setText("加微信找我")
            window._sync_composer_state()
            window._on_send()

            self.assertEqual(window._status.property("tone"), "warn")
            self.assertIn("违规词", window._status.text())
            # 输入框保留原文，用户可以就地改掉。
            self.assertEqual(window._input.text(), "加微信找我")
            # 语音来自违规音频目录，走 VOICE_REQUEST 触发型链路。
            self.assertEqual(len(self.voices), 1)
            self.assertEqual(self.voices[0]["audio_type"], "voice")
            self.assertIn("forum", self.voices[0]["source"])
        finally:
            window.cleanup()

    def test_violation_sound_uses_the_forum_directory(self):
        sound = ForumViolationSound()
        # 目录里必须真的有语音，否则提示只有文字、没有声音。
        self.assertGreater(sound.file_count, 0)


class ViolationVoiceCategoryTests(unittest.TestCase):
    """提示语音要跟违规类别对上：骂人的留言不能配「广告」的话术。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        cleanup_event_center()
        self.voices: list[dict] = []
        get_event_center().subscribe(
            EventType.VOICE_REQUEST,
            lambda event: self.voices.append(dict(event.data)),
        )
        self.addCleanup(cleanup_event_center)

    def _send(self, text: str) -> str:
        """发一条注定被拦下的留言，返回这次播放的音频路径。"""
        window = ForumWindow()
        try:
            window._service = _service()
            window._input.setText(text)
            window._sync_composer_state()
            window._on_send()
            self.assertEqual(len(self.voices), 1, text)
            return self.voices[-1]["source"]
        finally:
            window.cleanup()

    def test_each_category_plays_its_own_folder(self):
        cases = (
            ("看看 https://spam.example", "链接"),
            ("我的号是 123456789", "数字"),
            ("傻逼", "辱骂"),
            ("加微信找我", "引流"),
            ("低价出装备", "广告"),
            # 违法违规还没有专属话术，回落到通用。
            ("外挂哪里买", "通用"),
        )
        for text, folder in cases:
            self.voices.clear()
            source = self._send(text)
            self.assertIn(os.path.join("forum", "违规时", folder), source, text)

    def test_sound_reports_which_categories_have_audio(self):
        sound = ForumViolationSound()
        self.assertGreater(sound.file_count, 0)
        for category in ("link", "number", "abuse", "traffic", "promotion"):
            self.assertTrue(sound.has_category(category), category)
        # 表中没有的类别（违法违规）与空类别都由「通用」兜底。
        self.assertFalse(sound.has_category("illegal"))
        self.assertFalse(sound.has_category(""))


if __name__ == "__main__":
    unittest.main()
