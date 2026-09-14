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

            def release_all(self):
                calls.append("preloader_release")

        class ApprovalController:
            def __init__(self):
                calls.append("approval_init")

            def start(self):
                calls.append("approval_start")

            def cleanup(self):
                calls.append("approval_cleanup")

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
                prewarm_runtime_ui_cache=lambda: Preloader(),
            ),
            "lib.script.ui.office_approval_controller": types.SimpleNamespace(
                OfficeApprovalController=ApprovalController,
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
            from lib.script.app.qt_application_ui import QtApplicationUiHost

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
        self.assertEqual(calls.count("preloader_release"), 1)
        self.assertEqual(calls.count("announcement_cleanup"), 1)
        self.assertEqual(calls.count("approval_start"), 1)
        self.assertEqual(calls.count("approval_cleanup"), 1)
        self.assertEqual(calls.count("game_cleanup"), 1)
        self.assertEqual(calls.count("ui_cleanup"), 1)
        self.assertEqual(calls.count("animation_cleanup"), 1)
        self.assertIn("announcement_open", calls)
        self.assertIn("ui_hide", calls)

    def test_startup_wait_prewarm_reuses_one_preloader(self):
        calls = []

        class Animation:
            pass

        class Preloader:
            pass

        def eager_prewarm():
            calls.append("prewarm")
            return Preloader()

        def staged_preload():
            calls.append("staged")
            return Preloader()

        class AnnouncementController:
            def __init__(self, application):
                pass

            def start(self):
                pass

            def cleanup(self):
                pass

        class ApprovalController:
            def __init__(self):
                pass

            def start(self):
                pass

            def cleanup(self):
                pass

        modules = {
            "lib.script.SEanima.animation": types.SimpleNamespace(
                get_start_exit_animation=lambda: Animation(),
            ),
            "lib.script.ui.announcement_dialog": types.SimpleNamespace(
                AnnouncementController=AnnouncementController,
            ),
            "lib.script.ui.preloader": types.SimpleNamespace(
                preload_runtime_ui=staged_preload,
                prewarm_runtime_ui_cache=eager_prewarm,
            ),
            "lib.script.ui.office_approval_controller": types.SimpleNamespace(
                OfficeApprovalController=ApprovalController,
            ),
        }

        with patch.dict(sys.modules, modules):
            from lib.script.app.qt_application_ui import QtApplicationUiHost

            host = QtApplicationUiHost()
            host.prewarm_runtime_ui()
            host.prewarm_runtime_ui()
            host.start_runtime(object())

        self.assertEqual(calls, ["prewarm"])

    def test_prewarm_is_skipped_when_the_toggle_is_off(self):
        calls = []

        class Animation:
            pass

        class Preloader:
            def stop(self):
                calls.append("stop")

            def release_all(self):
                calls.append("release")

        class AnnouncementController:
            def __init__(self, application):
                pass

            def start(self):
                pass

            def cleanup(self):
                pass

        class ApprovalController:
            def __init__(self):
                pass

            def start(self):
                pass

            def cleanup(self):
                pass

        modules = {
            "lib.script.SEanima.animation": types.SimpleNamespace(
                get_start_exit_animation=lambda: Animation(),
            ),
            "lib.script.ui.announcement_dialog": types.SimpleNamespace(
                AnnouncementController=AnnouncementController,
            ),
            "lib.script.ui.preloader": types.SimpleNamespace(
                preload_runtime_ui=lambda: (calls.append("staged"), Preloader())[1],
                prewarm_runtime_ui_cache=lambda: (calls.append("prewarm"), None)[1],
            ),
            "lib.script.ui.office_approval_controller": types.SimpleNamespace(
                OfficeApprovalController=ApprovalController,
            ),
        }

        with patch.dict(sys.modules, modules):
            from lib.script.app.qt_application_ui import QtApplicationUiHost

            host = QtApplicationUiHost()
            host.prewarm_runtime_ui()
            host.start_runtime(object())

        self.assertEqual(calls, ["prewarm", "staged"])


if __name__ == "__main__":
    unittest.main()
