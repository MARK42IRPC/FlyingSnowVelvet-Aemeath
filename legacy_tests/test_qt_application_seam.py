"""Qt render backend must reach the live QApplication through one injectable getter."""
from __future__ import annotations

import unittest

from lib.core.render.backends.qt.runtime.application import get_application, set_application_getter
from lib.core.render.backends.qt.runtime import font as qt_font
from lib.core.render.backends.qt.runtime import screen as qt_screen
class _FakeApplication:
    def __init__(self, screens=(), primary=None):
        self._screens = list(screens)
        self._primary = primary
        self.font = None

    def screens(self):
        return list(self._screens)

    def primaryScreen(self):
        return self._primary

    def setFont(self, font):
        self.font = font


class _FakeScreen:
    def __init__(self, rect):
        self._rect = rect

    def geometry(self):
        return self._rect


class QtApplicationSeamTests(unittest.TestCase):
    def tearDown(self):
        set_application_getter(None)

    def test_getter_defaults_to_the_live_application(self):
        from PyQt5.QtWidgets import QApplication

        self.assertIs(get_application(), QApplication.instance())

    def test_setter_installs_and_restores_the_getter(self):
        fake = _FakeApplication()
        set_application_getter(lambda: fake)
        self.assertIs(get_application(), fake)

        set_application_getter(None)
        from PyQt5.QtWidgets import QApplication

        self.assertIs(get_application(), QApplication.instance())

    def test_setter_rejects_a_non_callable(self):
        with self.assertRaises(TypeError):
            set_application_getter("not callable")

    def test_font_registration_uses_the_injected_application(self):
        """\u5b57\u4f53\u6ce8\u518c\u5728\u65e0 application \u65f6\u5fc5\u987b\u77ed\u8def\uff0c\u800c\u4e0d\u662f\u76f4\u63a5\u53d6 QApplication.instance()\u3002"""
        qt_font.reset_registered_families()
        set_application_getter(lambda: None)
        family, _ = qt_font._ensure_font_families()
        from config import font_config as core_font

        self.assertEqual(family, core_font.get_ui_font_family())

    def test_screen_query_uses_the_injected_application(self):
        from PyQt5.QtCore import QRect

        qt_font.reset_registered_families()
        left = _FakeScreen(QRect(0, 0, 800, 600))
        right = _FakeScreen(QRect(800, 0, 1024, 768))
        set_application_getter(lambda: _FakeApplication([left, right], primary=left))

        rect = qt_screen.get_virtual_screen_rect()

        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (0, 0, 1824, 768))

    def test_qt_backend_looks_up_the_application_through_the_seam(self):
        """只允许 `application.py` 直接调 `QApplication.instance()`。"""
        from pathlib import Path

        qt_backend = (
            Path(__file__).resolve().parents[1]
            / "lib" / "core" / "render" / "backends" / "qt"
        )
        offenders = {}
        for path in sorted(qt_backend.glob("*.py")):
            if path.name == "application.py":
                continue
            source = path.read_text(encoding="utf-8-sig")
            if "QApplication.instance()" in source:
                offenders[path.name] = source.count("QApplication.instance()")
        self.assertEqual(
            offenders,
            {},
            "Qt 后端应走 application.get_application()，而不是直接取 QApplication.instance()",
        )

    def test_screen_query_falls_back_without_an_application(self):
        set_application_getter(lambda: None)
        # 无 application 时不得报错：先试静态 primaryScreen，再回退到固定的虚拟屏尺寸。
        rect = qt_screen.get_virtual_screen_rect()
        self.assertGreater(rect.width, 0)
        self.assertGreater(rect.height, 0)


if __name__ == "__main__":
    unittest.main()
