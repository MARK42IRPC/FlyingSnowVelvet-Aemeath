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

from PyQt5.QtWidgets import QApplication, QWidget

from lib.core.event.center import Event, EventType, get_event_center
from lib.script.ui.help_window import (
    HELP_DEFAULT_TITLE,
    HELP_EMPTY_TEXT,
    DesktopPetHelpDialog,
    HelpWindowController,
    parse_help_payload,
)
from lib.script.ui.workbench_settings_layout import SettingsPageScaffold


class ParseHelpPayloadTests(unittest.TestCase):
    def test_reads_title_and_text(self):
        self.assertEqual(
            parse_help_payload({"title": "音量", "text": "说明文字"}),
            ("音量", "说明文字"),
        )

    def test_strips_whitespace(self):
        self.assertEqual(
            parse_help_payload({"title": "  音量  ", "text": "\n 说明 \n"}),
            ("音量", "说明"),
        )

    def test_empty_title_falls_back(self):
        title, text = parse_help_payload({"title": "", "text": "有正文"})
        self.assertEqual(title, HELP_DEFAULT_TITLE)
        self.assertEqual(text, "有正文")

    def test_non_mapping_payload_does_not_raise(self):
        self.assertEqual(parse_help_payload(None), (HELP_DEFAULT_TITLE, ""))
        self.assertEqual(parse_help_payload("不是字典"), (HELP_DEFAULT_TITLE, ""))


class HelpSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_section_without_help_text_hides_the_button(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "说明")
        section = scaffold.add_section("基础")
        self.addCleanup(page.deleteLater)

        self.assertTrue(section.help_button.isHidden())
        self.assertEqual(section.help_text(), "")

    def test_help_section_shows_button_and_publishes_event(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "说明")
        section = scaffold.add_help_section("基础", "说明", help_text="这一段是帮助")
        self.addCleanup(page.deleteLater)

        # 不靠 `isVisible()`：这个按钮真正的可见性还取决于父页面和滚动视口有没有
        # 显示出来，测试只看按钮自身有没有被显式隐藏。
        self.assertFalse(section.help_button.isHidden())
        self.assertEqual(section.help_text(), "这一段是帮助")

        received: list[tuple[str, str]] = []
        center = get_event_center()

        def listener(event) -> None:
            received.append(
                (
                    str((event.data or {}).get("title") or ""),
                    str((event.data or {}).get("text") or ""),
                )
            )

        center.subscribe(EventType.HELP_WINDOW_REQUEST, listener)
        self.addCleanup(
            center.unsubscribe, EventType.HELP_WINDOW_REQUEST, listener
        )

        section.help_button.click()

        self.assertEqual(received, [("基础", "这一段是帮助")])

    def test_set_help_text_toggles_the_button(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "说明")
        section = scaffold.add_section("基础")
        self.addCleanup(page.deleteLater)

        section.set_help_text("补充说明")
        self.assertFalse(section.help_button.isHidden())

        section.set_help_text("")
        self.assertTrue(section.help_button.isHidden())


class HelpWindowControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = HelpWindowController()

    def tearDown(self):
        self.controller.cleanup()

    def test_request_event_creates_a_help_window(self):
        get_event_center().publish(
            Event(EventType.HELP_WINDOW_REQUEST, {"title": "标题", "text": "正文"})
        )

        dialog = self.controller.get_dialog()
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog._header_label.text(), "标题")
        self.assertEqual(dialog._body.text(), "正文")

    def test_second_request_destroys_the_previous_window(self):
        first = self.controller.show_help("第一", "第一条")
        second = self.controller.show_help("第二", "第二条")

        self.assertIsNot(self.controller.get_dialog(), first)
        self.assertIs(self.controller.get_dialog(), second)
        self.assertEqual(second._body.text(), "第二条")

    def test_empty_text_falls_back_to_placeholder(self):
        dialog = self.controller.show_help("只有标题", "   ")
        self.assertEqual(dialog._body.text(), HELP_EMPTY_TEXT)

    def test_repeated_request_keeps_only_one_dialog(self):
        for index in range(3):
            get_event_center().publish(
                Event(
                    EventType.HELP_WINDOW_REQUEST,
                    {"title": f"标题{index}", "text": f"正文{index}"},
                )
            )

        self.assertEqual(self.controller.get_dialog()._body.text(), "正文2")

    def test_cleanup_unsubscribes_and_drops_the_dialog(self):
        self.controller.show_help("标题", "正文")
        self.controller.cleanup()

        self.assertIsNone(self.controller.get_dialog())
        # 清理之后再发事件不应重新建窗（订阅已撤）。
        get_event_center().publish(
            Event(EventType.HELP_WINDOW_REQUEST, {"title": "标题", "text": "正文"})
        )
        self.assertIsNone(self.controller.get_dialog())

    def test_cleanup_is_idempotent(self):
        self.controller.show_help("标题", "正文")
        self.controller.cleanup()
        self.controller.cleanup()
        self.assertIsNone(self.controller.get_dialog())


class HelpDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_show_help_replaces_content_and_is_scrollable(self):
        dialog = DesktopPetHelpDialog()
        self.addCleanup(dialog.cleanup)

        dialog.show_help("第一节", "第一段")
        self.assertEqual(dialog._body.text(), "第一段")
        dialog.show_help("第二节", "第二段")
        self.assertEqual(dialog._header_label.text(), "第二节")
        self.assertEqual(dialog._body.text(), "第二段")

    def test_dialog_is_a_plain_text_label(self):
        dialog = DesktopPetHelpDialog()
        self.addCleanup(dialog.cleanup)
        dialog.show_help("标题", "<b>不是富文本</b>")
        self.assertEqual(dialog._body.textFormat(), PyQt5.QtCore.Qt.PlainText)
        self.assertEqual(dialog._body.text(), "<b>不是富文本</b>")


if __name__ == "__main__":
    unittest.main()
