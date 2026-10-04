"""办公线性图标：中立规格与 Qt 渲染的一致性。

图标的图形事实（SVG 文本与尺寸）在后端中立的 `visuals/office_icons.py`；这里把每个
名字的成品 SVG 逐字符钉死，并确认 Qt 侧与旧函数名仍能渲染出可用的 `QIcon`。
"""

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

from lib.core.render.visuals.office_icons import (
    OFFICE_ICON_NAMES,
    office_icon_size,
    office_icon_svg,
)
from lib.script.ui import render_bridge
from lib.script.ui import office_icons as product_icons

_COLOR = "#123456"
_EXPECTED_SVG = {
    "new": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></g></svg>',
    "delete": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v5M14 11v5"/></g></svg>',
    "browse": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/><path d="M3 10h18"/></g></svg>',
    "cancel": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/></g></svg>',
    "submit": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></g></svg>',
    "reject": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></g></svg>',
    "warning": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4l9 16H3z"/><path d="M12 9.5V14"/><path d="M12 17v.2"/></g></svg>',
    "allow": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></g></svg>',
    "allow_task": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><g stroke="#123456" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 13l3.8 3.8L14.5 10.5"/><path d="M9.5 13l3.8 3.8L19.5 10.5"/></g></svg>',
}


class OfficeIconSpecTests(unittest.TestCase):
    def test_spec_covers_every_published_name(self):
        self.assertEqual(set(OFFICE_ICON_NAMES), set(_EXPECTED_SVG))

    def test_neutral_svg_matches_the_frozen_graphic(self):
        for name, expected in _EXPECTED_SVG.items():
            with self.subTest(name=name):
                self.assertEqual(office_icon_svg(name, _COLOR), expected)

    def test_sizes_are_positive(self):
        for name in OFFICE_ICON_NAMES:
            with self.subTest(name=name):
                self.assertGreater(office_icon_size(name), 0)


class OfficeIconQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_render_bridge_returns_a_real_icon(self):
        icon = render_bridge.render_office_icon("new", _COLOR)
        self.assertFalse(icon.isNull())
        self.assertFalse(icon.pixmap(16, 16).isNull())

    def test_pixmap_helper_honours_the_requested_pixel_size(self):
        pixmap = render_bridge.render_office_icon_pixmap("warning", _COLOR, 24)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), 24)
        self.assertEqual(pixmap.height(), 24)

    def test_product_shim_keeps_the_legacy_names(self):
        # 旧函数名仍可用，且与中立规格同源（同一颜色渲染同一图）。
        icon = product_icons.office_submit_icon(_COLOR)
        self.assertFalse(icon.isNull())
        for name in ("new", "delete", "browse", "cancel", "submit", "reject",
                     "warning", "allow", "allow_task"):
            factory = getattr(product_icons, "office_%s_icon" % name)
            self.assertFalse(factory(_COLOR).isNull(), name)


if __name__ == "__main__":
    unittest.main()
