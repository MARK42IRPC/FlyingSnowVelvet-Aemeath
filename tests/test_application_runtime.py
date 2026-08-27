import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class ApplicationRuntimeContractTests(unittest.TestCase):
    def test_protocol_imports_and_runs_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.application_runtime import ApplicationRuntime

            class Runtime:
                def create_application(self, logger, argv=None): return object()
                def connect_exit_acknowledged(self, application, callback): pass
                def schedule_once(self, delay_ms, callback): callback()
                def run_event_loop(self, application): return 0
                def process_events(self, application): pass
                def request_exit(self, application, exit_code): pass
                def close_all_windows(self, application): pass

            runtime: ApplicationRuntime = Runtime()
            called = []
            runtime.schedule_once(0, lambda: called.append(True))
            assert called == [True]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_application_coordinator_has_no_direct_pyqt_calls(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "lib" / "script" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("from PyQt5", source)
        self.assertNotIn("import PyQt5", source)
        self.assertNotIn("QtApplicationRuntime", source)
        self.assertNotIn("QtScheduler", source)
        self.assertNotIn("QtScreenCapture", source)
        self.assertNotIn("QtPetWindow", source)
        self.assertNotIn("ParticleOverlay", source)
        self.assertNotIn("EffectOverlay", source)
        self.assertNotIn("QTimer", source)
        self.assertNotIn("QEvent", source)
        self.assertNotIn("lib.core.qt_bridge", source)
        self.assertNotIn("lib.script.ui", source)
        self.assertNotIn("lib.script.gemes", source)
        self.assertNotIn("configure_selected_backend", source)
        self.assertNotIn("register_backend", source)

    def test_application_coordinator_instantiates_with_fake_hosts_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            from unittest.mock import patch

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.backend_router import BackendSelection
            from lib.core.desktop_backend import DesktopBackendBundle
            from lib.core.graphics.types import Rect
            from lib.script import main as app_main

            class EventCenter:
                def subscribe(self, event_type, callback): pass

            class ApplicationUi:
                def prepare_application(self, application): pass
                def prepare_runtime(self): pass
                def start_runtime(self, application): pass
                def open_announcement(self): pass
                def begin_shutdown(self): pass
                def stop_runtime(self): pass
                def cleanup(self): pass
                def has_exit_animation(self): return False
                def finalize(self): pass

            class GameMode:
                def configure_runtime(self, pet, particles, effects): pass

            class Service: pass

            ui = ApplicationUi()
            bundle = DesktopBackendBundle(
                draw_backend_factory=lambda: object(),
                application_runtime_factory=lambda: object(),
                application_ui_host_factory=lambda: ui,
                scheduler_factory=lambda: object(),
                screen_capture_factory=lambda: object(),
                pet_window_factory=lambda gifs, overlay: object(),
                particle_overlay_factory=lambda: object(),
                effect_overlay_factory=lambda: object(),
                tray_host_factory=lambda: object(),
                event_pump_factory=lambda callback: object(),
                deferred_call=lambda delay_ms, callback: None,
                virtual_screen_provider=lambda: Rect(0, 0, 1, 1),
                screen_for_point_provider=lambda point: Rect(0, 0, 1, 1),
                layer_window_host_factory=lambda window: object(),
            )
            replacements = {
                "get_event_center": lambda: EventCenter(),
                "get_game_mode_service": lambda: GameMode(),
                "get_gsvmove_service": lambda: Service(),
                "get_bug_tracker_service": lambda: Service(),
                "get_microphone_stt_service": lambda: Service(),
                "get_microphone_push_to_talk_manager": lambda: Service(),
                "get_voice_request_handler": lambda: Service(),
                "get_cmd_center": lambda: Service(),
                "get_interaction_mode_service": lambda: Service(),
                "get_office_service": lambda **kwargs: Service(),
                "get_ollama_manager": lambda **kwargs: Service(),
                "get_chat_handler": lambda **kwargs: Service(),
                "get_stream_memory": lambda **kwargs: Service(),
            }
            with patch.multiple(app_main, **replacements), patch(
                "lib.core.voice.core.get_voice_core",
                return_value=Service(),
            ):
                state = app_main.ApplicationState(
                    application_runtime=object(),
                    application_ui_host=ui,
                    backend_bundle=bundle,
                    backend_selection=BackendSelection("fake", "fake", False),
                )

            assert state._application_ui is ui
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_qt_runtime_sets_resource_window_icon(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

            from lib.core.qt_bridge.application_runtime import QtApplicationRuntime

            class Logger:
                def info(self, *args, **kwargs): pass
                def warning(self, *args, **kwargs): pass

            app = QtApplicationRuntime().create_application(Logger(), [])
            icon = app.windowIcon()
            assert not icon.isNull()
            assert not icon.pixmap(16, 16).isNull()
            app.quit()
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
