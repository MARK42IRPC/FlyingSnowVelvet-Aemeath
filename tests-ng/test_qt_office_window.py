"""Office mode page and its independent office window.

Every test closes the singleton window it opens.  The suite runs each class in
its own interpreter, so even a leak here cannot reach another class.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests_ng.support.qt import app

from lib.script.office.ipc import OfficeFileIpc
from lib.script.ui import office_page
from lib.script.ui.office_mode_page import OfficeModePage
from lib.script.ui.office_page import OfficeWorkbenchPage


class OfficeModePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        office_page.cleanup_office_window()
        self.addCleanup(office_page.cleanup_office_window)

    def _page(self) -> OfficeModePage:
        page = OfficeModePage(embedded=True)
        self.addCleanup(page.deleteLater)
        return page

    def test_config_page_keeps_the_manager_cards_and_save_button(self):
        page = self._page()
        labels = [
            label.text()
            for label in page.findChildren(object, "SettingsSectionTitle")
        ]
        titles = [text for text in labels if isinstance(text, str) and text]
        self.assertEqual(titles, ["办公配置", "技能管理", "插件管理"])
        self.assertTrue(page._save_button.isVisibleTo(page))

    def test_open_button_creates_the_singleton_office_window(self):
        page = self._page()
        page._open_button.click()
        first = office_page.get_office_window()
        self.assertIsInstance(first, OfficeWorkbenchPage)
        self.assertFalse(first.is_embedded)
        self.assertTrue(first.isVisible())
        self.assertTrue(first._poll_timer.isActive())

        page._open_button.click()
        self.assertIs(office_page.get_office_window(), first)

    def test_cleanup_releases_the_window_and_restores_opacity(self):
        window = office_page.open_office_window()
        self.assertTrue(window.isVisible())
        self._finish_fade_out(window)

        office_page.cleanup_office_window()
        self.app.processEvents()

        self.assertIsNone(office_page.get_office_window())
        self.assertFalse(window.isVisible())
        self.assertAlmostEqual(window.windowOpacity(), 1.0, places=2)

    def test_window_hides_cleanly_while_fading_in(self):
        window = office_page.open_office_window()
        self.assertEqual(window._opacity_anim.state(), window._opacity_anim.Running)
        self._finish_fade_out(window)
        self.assertFalse(window.isVisible())
        self.assertEqual(window._opacity_anim.state(), window._opacity_anim.Stopped)

    def _finish_fade_out(self, window) -> None:
        """Drive the fade-out to its finished signal without sleeping 200 ms."""
        window.fade_out()
        window._opacity_anim.setCurrentTime(window._opacity_anim.duration())
        self.app.processEvents()

    def test_window_hide_with_external_close_callback_defers_to_owner(self):
        window = office_page.open_office_window()
        closed: list[int] = []
        window.set_external_close_callback(lambda: closed.append(1))
        window.hide()
        self.assertEqual(closed, [1])
        self.assertTrue(window.isVisible())
        window.set_external_close_callback(None)
        window.close()


class OfficeWorkbenchPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def _page(self) -> tuple[OfficeWorkbenchPage, OfficeFileIpc]:
        root = Path(tempfile.mkdtemp(prefix="office-ipc-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        ipc = OfficeFileIpc(root / "ipc")
        page = OfficeWorkbenchPage(embedded=True, ipc=ipc)
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        return page, ipc

    def test_new_task_sends_workspace_and_reasoning(self):
        page, ipc = self._page()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            ipc.publish({"mode": "companion", "workspace": str(workspace), "active_task_id": None}, [])
            page.refresh_workbench_page()
            page._workspace_edit.setText(str(workspace))
            page._effort_slider.set_effort("max")
            page._prompt_edit.setPlainText("create a project")
            page._submit_prompt()
            command = ipc.consume()[0]
        self.assertEqual(command["command"], "new_task")
        self.assertEqual(command["data"]["workspace"], str(workspace))
        self.assertEqual(command["data"]["reasoning_effort"], "max")

    def test_effort_slider_has_five_levels_and_wire_values(self):
        from lib.script.office.contracts import REASONING_EFFORTS
        from lib.script.ui.office_effort_slider import EFFORT_LEVELS

        self.assertEqual([value for value, _ in EFFORT_LEVELS], list(REASONING_EFFORTS))
        page, _ipc = self._page()
        page._effort_slider.set_effort("low")
        self.assertEqual(page._effort_slider.effort(), "low")
        page._effort_slider.set_effort("not-a-level")
        self.assertIn(page._effort_slider.effort(), REASONING_EFFORTS)

    def test_star_particles_follow_tick_while_slider_is_held(self):
        from lib.core.event.center import Event, EventType, get_event_center
        from lib.script.ui.office_page import EFFORT_STAR_PARTICLE_ID

        page, _ipc = self._page()
        page.resize(1120, 760)
        page.show()
        self.app.processEvents()
        center = get_event_center()
        received: list = []
        center.subscribe(EventType.PARTICLE_REQUEST, received.append)
        self.addCleanup(center.unsubscribe, EventType.PARTICLE_REQUEST, received.append)

        page._effort_slider.set_effort("ultra")
        page._begin_effort_star_trail()
        center.publish(Event(EventType.TICK))
        center.publish(Event(EventType.TICK))
        page._end_effort_star_trail()
        center.publish(Event(EventType.TICK))

        self.assertEqual(len(received), 3)
        self.assertTrue(
            all(event.data["particle_id"] == EFFORT_STAR_PARTICLE_ID for event in received)
        )


class TrayOfficeEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        from lib.script.ui.tray_icon import TrayIcon

        office_page.cleanup_office_window()
        self.tray = TrayIcon()
        self.tray._create_menu()
        self.addCleanup(self.tray._menu.deleteLater)
        self.addCleanup(office_page.cleanup_office_window)

    def test_tray_menu_lists_the_office_page_next_to_the_forum(self):
        from config.tooltip_config import TOOLTIPS

        texts = [action.text() for action in self.tray._menu.actions()]
        self.assertIn("办公页面", texts)
        self.assertEqual(texts.index("办公页面"), texts.index("雪绒论坛") + 1)
        self.assertIn("tray_office", TOOLTIPS)

    def test_office_action_reuses_the_shared_window(self):
        action = next(a for a in self.tray._menu.actions() if a.text() == "办公页面")
        action.trigger()
        first = office_page.get_office_window()
        self.assertIsNotNone(first)
        action.trigger()
        self.assertIs(office_page.get_office_window(), first)

    def test_office_action_reports_failure_as_information(self):
        from lib.core.event.center import EventType

        published: list = []
        with patch.object(
            self.tray._event_center,
            "publish",
            side_effect=lambda event: published.append((event.type, event.data)),
        ), patch(
            "lib.script.ui.office_page.open_office_window",
            side_effect=RuntimeError("window unavailable"),
        ):
            self.tray._on_office_page()
        self.assertEqual([item[0] for item in published], [EventType.INFORMATION])
        self.assertIn("window unavailable", published[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
