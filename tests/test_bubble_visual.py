from __future__ import annotations

import os
import unittest

import PyQt5

_QT_ROOT = os.path.dirname(PyQt5.__file__)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QFont, QFontMetrics, QImage, QPainter
from PyQt5.QtWidgets import QApplication

from config.config import UI
from lib.core.event.center import cleanup_event_center
from lib.core.graphics.application_visuals import BubbleVisualDescription
from lib.core.graphics.commands import TextCommand
from lib.core.graphics.types import Rect
from lib.core.layer_manager import get_layer_manager
from lib.core.qt_bridge.colors import COLORS
from lib.core.qt_bridge.font import draw_mixed_text, measure_mixed_text, wrap_mixed_text
from lib.script.ui.bubble import Bubble, BubbleInfo


class BubbleVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        cleanup_event_center()

    @staticmethod
    def _legacy_size_and_lines(bubble: Bubble, text: str):
        content_width = UI["bubble_max_width"] - bubble._border_width * 4
        lines = wrap_mixed_text(text, content_width, bubble._font, bubble._digit_font)
        line_height = max(
            QFontMetrics(bubble._font).height(),
            QFontMetrics(bubble._digit_font).height(),
        )
        if len(lines) == 1:
            width = min(
                measure_mixed_text(lines[0], bubble._font, bubble._digit_font),
                content_width,
            ) + bubble._padding * 2
        else:
            width = UI["bubble_max_width"]
        return (width, len(lines) * line_height + bubble._padding * 2), lines

    @staticmethod
    def _legacy_image(bubble: Bubble, text: str, align: str) -> QImage:
        width, height = bubble.get_text_size(text)
        image = QImage(width, height, QImage.Format_RGBA8888)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        outer = QRect(0, 0, width, height)
        border = bubble._border_width
        painter.fillRect(outer, COLORS["black"])
        painter.fillRect(outer.adjusted(border, border, -border, -border), COLORS["cyan"])
        content = outer.adjusted(border * 2, border * 2, -border * 2, -border * 2)
        painter.fillRect(content, COLORS["pink"])
        painter.setPen(COLORS["black"])
        line_height = max(
            QFontMetrics(bubble._font).height(),
            QFontMetrics(bubble._digit_font).height(),
        )
        lines = wrap_mixed_text(text, content.width(), bubble._font, bubble._digit_font)
        start_y = content.top() + (content.height() - len(lines) * line_height) // 2
        horizontal = Qt.AlignLeft if align == "left" else Qt.AlignHCenter
        for index, line in enumerate(lines):
            draw_mixed_text(
                painter,
                QRect(content.left(), start_y + index * line_height, content.width(), line_height),
                line,
                bubble._font,
                bubble._digit_font,
                horizontal | Qt.AlignVCenter,
            )
        painter.end()
        return image

    @staticmethod
    def _shared_image(bubble: Bubble, text: str, align: str) -> QImage:
        visual = bubble._build_visual(text, align)
        width, height = int(visual.size.width), int(visual.size.height)
        image = QImage(width, height, QImage.Format_RGBA8888)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        bubble._draw_backend.render(visual.batch, painter, Rect(0, 0, width, height))
        painter.end()
        return image

    @staticmethod
    def _image_bytes(image: QImage) -> bytes:
        bits = image.constBits()
        bits.setsize(image.byteCount())
        return bytes(bits)

    def test_shared_presenter_preserves_qt_wrap_and_size(self):
        bubble = Bubble()
        try:
            for text in (
                "服务已就绪",
                "测试123abc",
                "这是一个很长的气泡文本，包含12345数字和English words，用于检查自动换行是否保持Qt基准。",
                "第一行\n第二行123",
            ):
                expected_size, expected_lines = self._legacy_size_and_lines(bubble, text)
                visual = bubble._build_visual(text)
                self.assertIsInstance(visual, BubbleVisualDescription)
                self.assertEqual(
                    (int(visual.size.width), int(visual.size.height)),
                    expected_size,
                )
                self.assertEqual(visual.lines, tuple(expected_lines))
        finally:
            get_layer_manager().unregister(bubble)
            bubble.close()

    def test_shared_batch_matches_legacy_qt_pixels(self):
        bubble = Bubble()
        try:
            cases = (
                ("测试123abc", "center"),
                ("第一行\n第二行123", "center"),
                ("这是一个很长的左对齐气泡文本，包含12345数字和English。", "left"),
            )
            for text, align in cases:
                with self.subTest(text=text, align=align):
                    legacy = self._legacy_image(bubble, text, align)
                    shared = self._shared_image(bubble, text, align)
                    self.assertEqual(shared.size(), legacy.size())
                    self.assertEqual(self._image_bytes(shared), self._image_bytes(legacy))
        finally:
            get_layer_manager().unregister(bubble)
            bubble.close()

    def test_bubble_widget_stores_shared_visual_description(self):
        bubble = Bubble()
        try:
            bubble._current_bubble = BubbleInfo("状态123", 1, 2, "left")
            bubble.adjust_size_to_text("状态123")
            self.assertIsInstance(bubble._visual, BubbleVisualDescription)
            self.assertEqual((bubble.width(), bubble.height()), (
                int(bubble._visual.size.width),
                int(bubble._visual.size.height),
            ))
        finally:
            get_layer_manager().unregister(bubble)
            bubble.close()

    def test_scaled_rich_text_rect_covers_rendered_glyph_advance(self):
        bubble = Bubble()
        try:
            for text in (
                r"\scalebox{1.23}{\text{雪羽绒的末端文字}}",
                r"\textcolor{purple}{\Huge \text{月光所及皆是你}}",
            ):
                with self.subTest(text=text):
                    visual = bubble._build_visual(text)
                    commands = [
                        command
                        for command in visual.batch.commands
                        if isinstance(command, TextCommand)
                    ]
                    self.assertTrue(commands)
                    for command in commands:
                        font = QFont(command.font.family)
                        font.setPixelSize(command.font.pixel_size)
                        font.setBold(command.font.bold)
                        rendered_width = QFontMetrics(font).horizontalAdvance(command.text)
                        self.assertGreaterEqual(command.rect.width, rendered_width)
        finally:
            get_layer_manager().unregister(bubble)
            bubble.close()


if __name__ == "__main__":
    unittest.main()
