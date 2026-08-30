import gc
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMenu

from lib.script.ui.tray_icon import TrayIcon
from lib.core.tray_host import TrayCommand
from lib.script.ui.announcement_dialog import (
    AnnouncementBlock,
    AnnouncementController,
    AnnouncementDocument,
    AnnouncementPreferences,
    DesktopPetAnnouncementDialog,
    announcement_to_html,
    is_announcement_suppressed,
    load_announcement_preferences,
    parse_announcement,
    save_announcement_preferences,
    set_announcement_forever_suppressed,
)


class _ImmediateComputeHub:
    def submit_io(self, func, *args, **kwargs):
        func(*args, **kwargs)
        return Mock()


class AnnouncementFormatTests(unittest.TestCase):
    def test_parser_keeps_repeated_subtitle_and_text_blocks_in_order(self):
        document = parse_announcement(
            '''title:"\n测试公告\n"\n'''
            '''subtitle:"\n第一节\n"\n'''
            '''text:"\n第一段\n第二行\n"\n'''
            '''subtitle:"第二节"\n'''
            '''text:"第二段"\n'''
        )

        self.assertEqual(document.title, "测试公告")
        self.assertEqual(
            document.blocks,
            (
                AnnouncementBlock("subtitle", "第一节"),
                AnnouncementBlock("text", "第一段\n第二行"),
                AnnouncementBlock("subtitle", "第二节"),
                AnnouncementBlock("text", "第二段"),
            ),
        )

    def test_renderer_escapes_remote_markup(self):
        document = AnnouncementDocument(
            title='<script>alert("x")</script>',
            blocks=(AnnouncementBlock("text", "第一行\n<b>第二行</b>"),),
        )

        rendered = announcement_to_html(document)

        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<b>第二行</b>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("第一行<br>&lt;b&gt;第二行&lt;/b&gt;", rendered)

    def test_preferences_round_trip_and_suppress_only_matching_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state" / "announcement.json"
            preferences = AnnouncementPreferences(False, "2026-07-30")

            save_announcement_preferences(preferences, path)

            loaded = load_announcement_preferences(path)
            self.assertEqual(loaded, preferences)
            self.assertTrue(is_announcement_suppressed(loaded, date(2026, 7, 30)))
            self.assertFalse(is_announcement_suppressed(loaded, date(2026, 7, 31)))
            self.assertTrue(
                is_announcement_suppressed(
                    AnnouncementPreferences(suppress_forever=True),
                    date(2026, 7, 31),
                )
            )

    def test_permanent_suppression_update_preserves_today_preference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "announcement.json"
            save_announcement_preferences(
                AnnouncementPreferences(False, "2026-07-30"),
                path,
            )

            enabled = set_announcement_forever_suppressed(True, path)
            disabled = set_announcement_forever_suppressed(False, path)

            self.assertEqual(enabled, AnnouncementPreferences(True, "2026-07-30"))
            self.assertEqual(disabled, AnnouncementPreferences(False, "2026-07-30"))
            self.assertEqual(load_announcement_preferences(path), disabled)


class AnnouncementControllerTests(unittest.TestCase):
    def test_start_does_not_download_when_forever_suppressed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_path = root / "announcement.json"
            save_announcement_preferences(
                AnnouncementPreferences(suppress_forever=True),
                state_path,
            )
            controller = AnnouncementController(
                state_path=state_path,
                cache_path=root / "announcement.txt",
            )
            try:
                with patch(
                    "lib.script.ui.announcement_dialog.get_compute_hub"
                ) as get_hub:
                    self.assertFalse(controller.start())
                get_hub.assert_not_called()
            finally:
                controller.cleanup()

    def test_start_reloads_suppression_changed_after_controller_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_path = root / "announcement.json"
            controller = AnnouncementController(
                state_path=state_path,
                cache_path=root / "announcement.txt",
            )
            save_announcement_preferences(
                AnnouncementPreferences(suppress_forever=True),
                state_path,
            )
            try:
                with patch(
                    "lib.script.ui.announcement_dialog.get_compute_hub"
                ) as get_hub:
                    self.assertFalse(controller.start())
                get_hub.assert_not_called()
            finally:
                controller.cleanup()

    def test_successful_start_download_closes_response_and_caches_content(self):
        raw = 'title:"公告"\nsubtitle:"更新"\ntext:"内容"\n'
        response = Mock()
        response.iter_content.return_value = [raw.encode("utf-8")]
        dialog = Mock()
        dialog.wants_visible.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache_path = root / "announcement.txt"
            controller = AnnouncementController(
                state_path=root / "announcement.json",
                cache_path=cache_path,
            )
            controller._dialog = dialog
            controller._ensure_dialog = Mock(return_value=dialog)
            try:
                with patch(
                    "lib.script.ui.announcement_dialog.get_compute_hub",
                    return_value=_ImmediateComputeHub(),
                ), patch(
                    "lib.script.ui.announcement_dialog.requests.get",
                    return_value=response,
                ) as request:
                    self.assertTrue(controller.start())

                request_kwargs = request.call_args.kwargs
                self.assertTrue(request_kwargs["params"]["_"])
                self.assertEqual(request_kwargs["headers"]["Cache-Control"], "no-cache")
                response.raise_for_status.assert_called_once_with()
                response.close.assert_called()
                self.assertEqual(cache_path.read_text(encoding="utf-8"), raw)
                displayed = dialog.show_document.call_args.args[0]
                self.assertEqual(displayed.title, "公告")
                self.assertEqual(len(displayed.blocks), 2)
            finally:
                controller.cleanup()

    def test_every_tray_open_starts_a_new_download(self):
        dialog = Mock()
        hub = Mock()
        hub.submit_io.side_effect = (Mock(), Mock())

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            controller = AnnouncementController(
                state_path=root / "announcement.json",
                cache_path=root / "announcement.txt",
            )
            controller._ensure_dialog = Mock(return_value=dialog)
            try:
                with patch(
                    "lib.script.ui.announcement_dialog.get_compute_hub",
                    return_value=hub,
                ):
                    controller.open_from_tray()
                    controller.open_from_tray()

                self.assertEqual(hub.submit_io.call_count, 2)
                self.assertEqual(dialog.show_loading.call_count, 2)
                request_ids = [call.args[1] for call in hub.submit_io.call_args_list]
                self.assertEqual(request_ids, [1, 2])
            finally:
                controller.cleanup()

    def test_tray_download_failure_falls_back_to_cached_announcement(self):
        cached = 'title:"缓存公告"\ntext:"离线内容"\n'
        dialog = Mock()
        dialog.wants_visible.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache_path = root / "announcement.txt"
            cache_path.write_text(cached, encoding="utf-8")
            controller = AnnouncementController(
                state_path=root / "announcement.json",
                cache_path=cache_path,
            )
            controller._dialog = dialog
            controller._ensure_dialog = Mock(return_value=dialog)
            try:
                with patch(
                    "lib.script.ui.announcement_dialog.get_compute_hub",
                    return_value=_ImmediateComputeHub(),
                ), patch(
                    "lib.script.ui.announcement_dialog.requests.get",
                    side_effect=RuntimeError("network unavailable"),
                ) as request:
                    controller.open_from_tray()

                request.assert_called_once()
                dialog.show_error.assert_not_called()
                displayed = dialog.show_document.call_args.args[0]
                self.assertEqual(displayed.title, "缓存公告")
                self.assertEqual(displayed.blocks[0].text, "离线内容")
            finally:
                controller.cleanup()


class AnnouncementQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        cls.app = existing or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.app = None
        gc.collect()

    def test_dialog_has_compact_scrollable_body_and_required_actions(self):
        dialog = DesktopPetAnnouncementDialog()
        try:
            long_text = "\n".join(f"第 {index} 行公告内容" for index in range(160))
            dialog.show_document(
                AnnouncementDocument(
                    title="较长的桌宠公告",
                    blocks=(AnnouncementBlock("text", long_text),),
                )
            )
            self.app.processEvents()

            self.assertLessEqual(dialog.width(), 600)
            self.assertLessEqual(dialog.height(), 520)
            self.assertEqual(
                dialog._body.horizontalScrollBarPolicy(),
                Qt.ScrollBarAlwaysOff,
            )
            self.assertGreater(dialog._body.verticalScrollBar().maximum(), 0)
            self.assertEqual(dialog._today_button.text(), "今日不再显示")
            self.assertEqual(dialog._forever_button.text(), "永远不再显示")
        finally:
            dialog.cleanup()
            self.app.processEvents()

    def test_tray_menu_contains_announcement_action_and_emits_request(self):
        tray = TrayIcon()
        received = []
        tray.announcement_requested.connect(lambda: received.append(True))
        try:
            with patch("lib.script.ui.tray_icon.TrayContextMenu", QMenu), patch.object(
                tray, "_is_autostart_enabled", return_value=False
            ), patch(
                "lib.script.ui.tray_icon.get_game_mode_service"
            ) as game_mode_service:
                game_mode_service.return_value.is_enabled.return_value = False
                tray._create_menu()

            action = next(
                action for action in tray._menu.actions() if action.text() == "桌宠公告"
            )
            action.trigger()

            self.assertEqual(received, [True])
        finally:
            tray.cleanup()
            self.app.processEvents()

    def test_default_tray_icon_path_points_to_resource_icon(self):
        tray = TrayIcon()
        try:
            icon_path = Path(tray._resolve_default_icon_path())
            self.assertEqual(icon_path, Path(__file__).resolve().parents[1] / "resc" / "icon.ico")
            self.assertTrue(icon_path.is_file())
        finally:
            tray.cleanup()
            self.app.processEvents()

    def test_qt_tray_daily_actions_emit_shared_command(self):
        tray = TrayIcon()
        received = []
        tray.command_requested.connect(lambda command, checked: received.append((command, checked)))
        try:
            tray._on_cleanup_cache()
            tray._on_toggle_game_mode(True)
            self.assertEqual(
                received,
                [
                    (TrayCommand.CLEANUP_CACHE, None),
                    (TrayCommand.TOGGLE_GAME_MODE, True),
                ],
            )
        finally:
            tray.cleanup()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
