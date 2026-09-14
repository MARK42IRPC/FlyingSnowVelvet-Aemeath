import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.ui_cache import UiCachePool
from lib.script.ui.preloader import (
    WIDGET_FIXED_OVERHEAD_BYTES,
    UiPreloader,
    estimate_widget_bytes,
    preload_runtime_ui,
    prewarm_runtime_ui_cache,
    ui_cache_preload_enabled,
)


class FakeWidget:
    """缓存池只关心尺寸、可见性和一次离屏绘制，测试用假控件即可。"""

    def __init__(self, name, *, width=100, height=50, visible=False):
        self.name = name
        self._width = width
        self._height = height
        self._visible = visible
        self.paints = 0

    def width(self):
        return self._width

    def height(self):
        return self._height

    def isVisible(self):
        return self._visible

    def render(self, _painter):
        self.paints += 1


def drain(preloader):
    """推进分步预加载直到完成（不依赖事件循环）。"""
    while not preloader.finished:
        preloader._run_next()
    preloader.stop()


class UiPreloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self, widgets, *, budget=4 * 1024 * 1024):
        released = []
        preloader = UiPreloader(budget_bytes=budget)
        steps = []
        for name, widget in widgets:
            steps.append(
                (
                    name,
                    (lambda widget=widget: widget),
                    (lambda name=name: released.append(name)),
                )
            )
        preloader._steps = tuple(steps)
        return preloader, released

    def test_estimate_widget_bytes_counts_pixels_and_fixed_overhead(self):
        widget = FakeWidget("panel", width=200, height=100)
        self.assertEqual(
            estimate_widget_bytes(widget),
            200 * 100 * 4 + WIDGET_FIXED_OVERHEAD_BYTES,
        )

    def test_prewarm_respects_the_scheduler_toggle(self):
        with patch.dict("config.config.STARTUP", {"ui_cache_preload": False}):
            self.assertFalse(ui_cache_preload_enabled())
            self.assertIsNone(prewarm_runtime_ui_cache())

        with patch.dict("config.config.STARTUP", {"ui_cache_preload": True}):
            self.assertTrue(ui_cache_preload_enabled())
            preloader = prewarm_runtime_ui_cache()
            try:
                self.assertIsInstance(preloader, UiPreloader)
                self.assertFalse(preloader.finished)
            finally:
                preloader.stop()

    def test_missing_or_broken_startup_config_disables_prewarm(self):
        with patch.dict("config.config.STARTUP", {}, clear=True):
            self.assertFalse(ui_cache_preload_enabled())

    def test_steps_construct_paint_and_cache_every_widget(self):
        widgets = [
            ("playlist_panel", FakeWidget("playlist")),
            ("progress_panel", FakeWidget("progress")),
        ]
        preloader, _released = self._build(widgets)
        drain(preloader)

        self.assertTrue(preloader.finished)
        self.assertEqual(preloader.pool.keys, ("playlist_panel", "progress_panel"))
        for _name, widget in widgets:
            self.assertEqual(widget.paints, 1)
        self.assertEqual(preloader.stats.entries, 2)
        self.assertLessEqual(preloader.stats.bytes, preloader.pool.budget_bytes)

    def test_failing_step_does_not_stop_the_remaining_steps(self):
        good = FakeWidget("progress")

        def boom():
            raise RuntimeError("构造失败")

        preloader, _released = self._build([("playlist_panel", good)])
        preloader._steps = (
            ("broken", boom, lambda: None),
            *preloader._steps,
        )
        with patch("lib.script.ui.preloader._logger"):
            drain(preloader)

        self.assertTrue(preloader.finished)
        self.assertEqual(preloader.pool.keys, ("playlist_panel",))

    def test_widget_that_does_not_fit_is_released_immediately(self):
        widget = FakeWidget("playlist", width=400, height=400)
        preloader, released = self._build([("playlist_panel", widget)], budget=1024)
        drain(preloader)

        self.assertEqual(len(preloader.pool), 0)
        self.assertEqual(released, ["playlist_panel"])
        self.assertEqual(preloader.stats.rejected, 1)

    def test_visible_widget_is_protected_from_eviction(self):
        visible = FakeWidget("playlist", width=100, height=100, visible=True)
        late = FakeWidget("progress", width=100, height=100)
        budget = estimate_widget_bytes(visible) + 1024
        preloader, released = self._build(
            [("playlist_panel", visible), ("progress_panel", late)],
            budget=budget,
        )
        drain(preloader)

        self.assertEqual(preloader.pool.keys, ("playlist_panel",))
        self.assertEqual(released, ["progress_panel"])

    def test_evicting_an_old_widget_runs_its_release(self):
        first = FakeWidget("playlist", width=200, height=200)
        second = FakeWidget("progress", width=200, height=200)
        budget = estimate_widget_bytes(first) + 1024
        preloader, released = self._build(
            [("playlist_panel", first), ("progress_panel", second)],
            budget=budget,
        )
        drain(preloader)

        self.assertEqual(preloader.pool.keys, ("progress_panel",))
        self.assertEqual(released, ["playlist_panel"])

    def test_release_all_keeps_visible_widgets_and_drops_hidden_ones(self):
        hidden = FakeWidget("playlist")
        visible = FakeWidget("progress", visible=True)
        preloader, released = self._build(
            [("playlist_panel", hidden), ("progress_panel", visible)]
        )
        drain(preloader)

        preloader.release_all()

        self.assertEqual(preloader.pool.keys, ("progress_panel",))
        self.assertEqual(released, ["playlist_panel"])

    def test_preload_runtime_ui_starts_the_staged_loader(self):
        preloader = preload_runtime_ui()
        try:
            self.assertIsInstance(preloader, UiPreloader)
            self.assertFalse(preloader.finished)
            self.assertIsNotNone(preloader.parent())
        finally:
            preloader.stop()


if __name__ == "__main__":
    unittest.main()
