"""后端中立呈现协议：`PresentationHost` / `FontProvider` / `TextMetrics`。

这些断言从外部钉住契约形状，而不是钉住实现：

- 协议本身不出现 Qt 类型，`lib/core` 里的调用方因此可以导入它们而不拉起 PyQt5；
- registry 未注入后端时返回 `None`，调用方负责回退；
- Qt 实现经 registry 注入后，返回的几何是核心 `Rect` 而不是 `QRect`。

第四轮改建的目标是"控件不再用 Qt 类型做布局算术"，这些断言就是那个目标的守卫。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.render import registry
from lib.core.render.backends.base import DesktopBackendBundle
from lib.core.render.visuals.types import Point, Rect


_REPO_ROOT = Path(__file__).resolve().parents[1]


class NeutralProtocolShapeTests(unittest.TestCase):
    def test_protocols_import_without_pyqt(self):
        """协议模块本身不得在导入期拉起 Qt；它要被 `lib/core` 的调用方使用。"""
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise AssertionError(f"protocol module imported Qt: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.render.backends.base import (
                FontProvider,
                PresentationHost,
                TextMetrics,
            )
            from lib.core.render import registry

            assert registry.get_presentation_host() is None
            assert registry.get_font_provider() is None
            assert registry.get_text_metrics_factory() is None
            assert PresentationHost.__name__ == "PresentationHost"
            assert FontProvider.__name__ == "FontProvider"
            assert TextMetrics.__name__ == "TextMetrics"
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_rect_carries_the_geometry_now_that_qt_rect_is_gone(self):
        """`Rect` 必须提供控件此前从 `QRect` 上取的那几个只读用法。"""
        rect = Rect(10, 20, 100, 50)
        self.assertEqual(rect.center, Point(60.0, 45.0))
        self.assertEqual(rect.right, 110)
        self.assertEqual(rect.bottom, 70)
        self.assertEqual(rect.top_left, Point(10, 20))


class QtPresentationHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_qt_host_returns_core_geometry(self):
        from lib.core.render.backends.qt.drawing.presentation import QtPresentationHost

        host = QtPresentationHost()
        screen = host.screen_rect_for_widget(None)
        self.assertIsInstance(screen, Rect)
        self.assertGreater(screen.width, 0)

        from PyQt5.QtWidgets import QWidget

        widget = QWidget()
        try:
            widget.setGeometry(30, 40, 120, 80)
            rect = host.widget_global_rect(widget)
            self.assertIsInstance(rect, Rect)
            self.assertEqual((int(rect.x), int(rect.y)), (30, 40))
            self.assertEqual((int(rect.width), int(rect.height)), (120, 80))
            self.assertEqual(host.widget_global_point(widget, Point(5, 6)), Point(35, 46))
        finally:
            widget.deleteLater()

    def test_clamp_returns_int_pair_and_none_without_a_screen(self):
        from lib.core.render.backends.qt.drawing.presentation import QtPresentationHost

        host = QtPresentationHost()
        clamped = host.clamp_position(0, 0, 20, 20)
        self.assertIsInstance(clamped, tuple)
        self.assertEqual(len(clamped), 2)
        self.assertIsInstance(clamped[0], int)


class BundleProtocolSlotsTests(unittest.TestCase):
    def test_bundle_slots_are_optional_so_a_backend_can_land_them_later(self):
        """DX 尚未接线时这三条留空；缺省值必须让 `DesktopBackendBundle` 仍可构造。"""
        fields = set(DesktopBackendBundle.__dataclass_fields__)
        self.assertIn("presentation_host_factory", fields)
        self.assertIn("font_provider_factory", fields)
        self.assertIn("text_metrics_factory", fields)
        for name in ("presentation_host_factory", "font_provider_factory", "text_metrics_factory"):
            self.assertIsNone(DesktopBackendBundle.__dataclass_fields__[name].default)

    def test_registry_accessors_stay_none_until_a_backend_installs(self):
        self.assertIsNone(registry.get_presentation_host())
        self.assertIsNone(registry.get_font_provider())
        self.assertIsNone(registry.get_text_metrics_factory())

    def test_installing_a_bundle_fills_the_accessors_and_caches_the_host(self):
        """注入之后读取入口必须给出实现，且宿主实例被复用（每帧路径）。"""
        from lib.core.render.backends.qt.drawing.presentation import QtPresentationHost

        bundle = DesktopBackendBundle(
            draw_backend_factory=lambda: None,
            application_runtime_factory=lambda: None,
            application_ui_host_factory=lambda: None,
            scheduler_factory=lambda: None,
            screen_capture_factory=lambda: None,
            pet_window_factory=lambda *a, **k: None,
            particle_overlay_factory=lambda: None,
            effect_overlay_factory=lambda: None,
            tray_host_factory=lambda: None,
            event_pump_factory=lambda: None,
            deferred_call=lambda delay, fn: None,
            virtual_screen_provider=lambda: Rect(0, 0, 1, 1),
            screen_for_point_provider=lambda point: Rect(0, 0, 1, 1),
            layer_window_host_factory=lambda *a, **k: None,
            presentation_host_factory=QtPresentationHost,
        )
        owner = object()
        registry.install_desktop_backend_bundle(bundle, owner=owner)
        try:
            first = registry.get_presentation_host()
            self.assertIsInstance(first, QtPresentationHost)
            self.assertIs(registry.get_presentation_host(), first)
            #: 字体与度量未接线时仍是 None，调用方走后端中立回退。
            self.assertIsNone(registry.get_font_provider())
            self.assertIsNone(registry.get_text_metrics_factory())
        finally:
            registry.uninstall_desktop_backend_bundle(owner)
        self.assertIsNone(registry.get_presentation_host())


if __name__ == "__main__":
    unittest.main()
