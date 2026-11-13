"""托盘菜单条目几何：二级项的文字中心必须与同级项对齐，且不顶到箭头。"""

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

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

from lib.script.ui.tray_menu import _TrayMenuHintStyle


class TrayMenuHintStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _style(self) -> _TrayMenuHintStyle:
        style = _TrayMenuHintStyle(QApplication.style())
        self.addCleanup(style.deleteLater)
        return style

    def test_submenu_row_keeps_text_center_aligned_with_plain_row(self):
        style = self._style()
        row = QRect(0, 0, 94, 24)

        plain = style._item_text_rect(row, False)
        submenu = style._item_text_rect(row, True)

        self.assertEqual(submenu.center().x(), plain.center().x())
        # 留位只压缩二级项自己的可写宽度，普通项保持原有的居中区间。
        self.assertEqual(plain.left(), row.left() + style._pad_x + style._text_shift_x)
        self.assertEqual(plain.width(), row.width() - style._pad_x * 2)
        self.assertLess(submenu.width(), plain.width())

    def test_submenu_text_rect_never_reaches_into_the_arrow_room(self):
        style = self._style()
        row = QRect(0, 0, 94, 24)
        submenu = style._item_text_rect(row, True)

        arrow_left = row.right() - style._arrow_inset - style._arrow_w
        self.assertLessEqual(submenu.right(), arrow_left - 1)
        # 箭头绘制点比预留位更靠右：预留位仍是保守值，绘制时收回一点。
        self.assertLess(style._arrow_inset, style._arrow_pad)

    def test_narrow_row_degrades_to_empty_rect_instead_of_negative_width(self):
        style = self._style()
        row = QRect(0, 0, 12, 24)
        rect = style._item_text_rect(row, True)
        self.assertGreaterEqual(rect.width(), 0)


if __name__ == "__main__":
    unittest.main()
