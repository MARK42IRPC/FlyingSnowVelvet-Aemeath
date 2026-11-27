"""End-to-end lifecycle of the shared announcement popup and controller."""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory

from tests_ng.support.qt import app

from PyQt5.QtCore import QObject, pyqtSignal

from lib.core.event.center import Event, EventType, get_event_center
from lib.script.ui.announcement_dialog import AnnouncementController

_PAYLOAD = 'title: "重要公告"\nsubtitle: "版本提示"\ntext: "第一条内容"\n'


class _FakeApplication(QObject):
    """Mimic the Qt host the controller treats as a dispatcher parent."""

    announcement_requested = pyqtSignal()


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield _PAYLOAD.encode("utf-8")

    def close(self) -> None:
        return None


def _immediate_submit(func, *args):
    """Run the worker synchronously and hand back an already-resolved Future."""
    future: Future = Future()
    try:
        future.set_result(func(*args))
    except Exception as exc:  # noqa: BLE001 - mirror the runner contract
        future.set_exception(exc)
    return future


class AnnouncementLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.application = _FakeApplication()
        self.controller = AnnouncementController(
            self.application,
            submit_io=_immediate_submit,
            request_get=lambda *a, **k: _FakeResponse(),
            state_path=root / "announcement.json",
            cache_path=root / "announcement.txt",
        )
        self.addCleanup(self.controller.cleanup)

    def _pump(self) -> None:
        for _ in range(4):
            self.app.processEvents()

    def test_controller_accepts_positional_application(self):
        """The original regression: a positional application must not raise."""
        self.assertIsNotNone(self.controller)

    def test_tick_event_does_not_call_a_missing_method(self):
        self.controller.open_from_tray()
        self._pump()
        center = get_event_center()
        # Publishing TICK used to call a deleted ``dialog.tick()``.
        center.publish(Event(EventType.TICK, {}))
        self._pump()
        self.assertTrue(self.controller._dialog.wants_visible())

    def test_manual_open_shows_document_with_fade_in(self):
        self.controller.open_from_tray()
        self._pump()
        dialog = self.controller._dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.wants_visible())
        self.assertTrue(dialog.isVisible())
        self.assertIn("重要公告", dialog._window.find("header").text())
        self.assertIn("第一条内容", dialog._window.find("body").toPlainText())
        # Fade-in is enabled for the reused tool window.
        animation = dialog._window._opacity_animation
        self.assertIsNotNone(animation)

    def test_dismiss_hides_without_leaving_visible_window(self):
        self.controller.open_from_tray()
        self._pump()
        dialog = self.controller._dialog
        dialog._on_semantic("announcement_close")
        animation = dialog._window._opacity_animation
        animation.setCurrentTime(animation.duration())
        self._pump()
        self.assertFalse(dialog.wants_visible())
        self.assertFalse(dialog.isVisible())

    def test_theme_switch_restyles_open_dialog(self):
        self.controller.open_from_tray()
        self._pump()
        dialog = self.controller._dialog
        center = get_event_center()
        center.publish(Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"workbench_light_theme": True}}}))
        self._pump()
        self.assertIn("background:", dialog.styleSheet())
        center.publish(Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"workbench_light_theme": False}}}))
        self._pump()
        self.assertTrue(dialog.wants_visible())

    def test_cleanup_is_idempotent(self):
        self.controller.open_from_tray()
        self._pump()
        self.controller.cleanup()
        self.controller.cleanup()

    def test_shared_popup_ignores_workbench_ancestor_qss_cascade(self):
        """A popup launched from SettingsPage must keep the shared font size and button height."""
        from PyQt5.QtWidgets import QFrame, QWidget

        from lib.core.render.backends.qt.widgets.message_box_host import _build_message_box
        from lib.core.render.visuals.message_box import message_box_window_spec
        from lib.script.workbench.theme import workbench_stylesheet

        workbench = QWidget()
        workbench.setStyleSheet(workbench_stylesheet())
        host = QFrame(workbench)
        host.setObjectName("WorkbenchPageHost")
        page = QWidget(host)
        page.setObjectName("SettingsPage")
        self.addCleanup(workbench.deleteLater)

        spec = message_box_window_spec(
            title="确认", text="确定执行吗？", primary_text="确定", cancel_text="取消", destructive=True
        )
        unparented = _build_message_box(None, spec)
        parented = _build_message_box(page, spec)
        self.addCleanup(unparented.cleanup)
        self.addCleanup(parented.cleanup)
        unparented.widget.show()
        parented.widget.show()
        self._pump()

        p_free = unparented.find("primary")
        p_nested = parented.find("primary")
        self.assertEqual(p_nested.font().pixelSize(), p_free.font().pixelSize())
        self.assertEqual(p_nested.height(), p_free.height())
        self.assertIsNone(parented.widget.parentWidget())
        self.assertIs(parented._anchor_parent, page)

    def test_direct_widget_show_still_triggers_fade_in(self):
        """Even if a caller invokes widget.show() directly, a fading popup must animate in."""
        from lib.core.render.backends.qt.widgets import tier_d_seams
        from lib.core.render.backends.qt.widgets.spec_host import build_spec_window
        from lib.core.render.visuals.window_specs import help_window_spec

        window = build_spec_window(
            help_window_spec(title="帮助", text="正文", width=440, height=380),
            font_factory=tier_d_seams.ui_font,
            presentation_host=tier_d_seams.presentation_host(),
            dialog=False,
        )
        self.addCleanup(window.cleanup)
        window.widget.show()
        self._pump()
        self.assertTrue(window.is_requested_visible())
        self.assertIsNotNone(window._opacity_animation)
        self.assertGreater(float(window._opacity_animation.endValue()), 0.0)


if __name__ == "__main__":
    unittest.main()
