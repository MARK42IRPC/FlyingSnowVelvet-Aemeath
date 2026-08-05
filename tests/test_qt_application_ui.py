import sys
import types
import unittest
from unittest.mock import patch


class QtApplicationUiHostTests(unittest.TestCase):
    def test_runtime_cleanup_is_idempotent(self):
        calls = []

        class Animation:
            pass

        class AnnouncementController:
            def __init__(self, application):
                calls.append(("announcement_init", application))

            def start(self):
                calls.append("announcement_start")

            def open_from_tray(self):
                calls.append("announcement_open")

            def cleanup(self):
                calls.append("announcement_cleanup")

        class Preloader:
            def stop(self):
                calls.append("preloader_stop")

        modules = {
            "lib.script.SEanima.animation": types.SimpleNamespace(
                get_start_exit_animation=lambda: Animation(),
                cleanup_start_exit_animation=lambda: calls.append("animation_cleanup"),
            ),
            "lib.script.ui.announcement_dialog": types.SimpleNamespace(
                AnnouncementController=AnnouncementController,
            ),
            "lib.script.ui.preloader": types.SimpleNamespace(
                preload_runtime_ui=lambda: Preloader(),
            ),
            "lib.script.ui.shutdown": types.SimpleNamespace(
                hide_all_runtime_ui=lambda: calls.append("ui_hide"),
                cleanup_all_runtime_ui=lambda: calls.append("ui_cleanup"),
            ),
            "lib.script.gemes": types.SimpleNamespace(
                cleanup_game_runtime=lambda: calls.append("game_cleanup"),
            ),
        }

        with patch.dict(sys.modules, modules):
            from lib.core.qt_bridge.application_ui import QtApplicationUiHost

            host = QtApplicationUiHost()
            application = object()
            host.start_runtime(application)
            host.open_announcement()
            host.begin_shutdown()
            host.stop_runtime()
            host.stop_runtime()
            host.cleanup()
            host.cleanup()
            host.finalize()
            host.finalize()

        self.assertEqual(calls.count("preloader_stop"), 1)
        self.assertEqual(calls.count("announcement_cleanup"), 1)
        self.assertEqual(calls.count("game_cleanup"), 1)
        self.assertEqual(calls.count("ui_cleanup"), 1)
        self.assertEqual(calls.count("animation_cleanup"), 1)
        self.assertIn("announcement_open", calls)
        self.assertIn("ui_hide", calls)


if __name__ == "__main__":
    unittest.main()
