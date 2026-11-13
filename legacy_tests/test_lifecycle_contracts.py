import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class LifecycleContractTests(unittest.TestCase):
    def test_lifecycle_protocols_import_without_pyqt(self):
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

            from lib.core.application_ui import ApplicationUiHost
            from lib.core.overlay_host import OverlayHost
            from lib.core.pet_host import PetWindowHost
            from lib.core.tray_host import TrayHost

            class Overlay:
                def flush_immediately(self): pass
                def cleanup(self): pass

            class ApplicationUi:
                def prepare_application(self, application): pass
                def prepare_runtime(self): pass
                def prewarm_runtime_ui(self): pass
                def start_runtime(self, application): pass
                def open_announcement(self): pass
                def open_settings(self): pass
                def begin_shutdown(self): pass
                def stop_runtime(self): pass
                def cleanup(self): pass
                def has_exit_animation(self): return False
                def finalize(self): pass

            class Pet:
                def shutdown_host(self): pass

            class Tray:
                def connect_quit_requested(self, callback): pass
                def disconnect_quit_requested(self, callback): pass
                def connect_announcement_requested(self, callback): pass
                def disconnect_announcement_requested(self, callback): pass
                def connect_command_requested(self, callback): pass
                def disconnect_command_requested(self, callback): pass
                def set_menu_state(self, state): pass
                def initialize(self): return True
                def begin_shutdown(self): pass
                def cleanup(self): pass

            application_ui: ApplicationUiHost = ApplicationUi()
            overlay: OverlayHost = Overlay()
            pet: PetWindowHost = Pet()
            tray: TrayHost = Tray()
            application_ui.prepare_runtime()
            overlay.flush_immediately()
            pet.shutdown_host()
            assert tray.initialize()
            tray.cleanup()
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

    def test_application_coordinator_uses_lifecycle_surfaces(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "lib" / "script" / "main.py").read_text(encoding="utf-8")

        for token in (
            "lib.core.render.backends.qt",
            "lib.script.ui",
            "lib.script.gemes",
            "configure_selected_backend",
            "register_backend",
            "quit_requested.",
            "announcement_requested.",
            "deleteLater()",
            "_timing_manager",
            "self._pet.close()",
            "self._particles.close()",
            "self._effects.close()",
            "_tray_icon_cleanup",
        ):
            self.assertNotIn(token, source, token)

        self.assertIn("shutdown_host()", source)
        self.assertIn("disconnect_quit_requested", source)
        self.assertIn("disconnect_announcement_requested", source)
        self.assertIn("disconnect_command_requested", source)
        self.assertIn("set_menu_state", source)
        self.assertIn("self._application_ui", source)


    def test_init_stages_are_isolated_from_each_other(self):
        """``_on_init_ready`` 分段推进：一段失败不得阻断其后各段。

        事故背景：这里原本是一条直线调用序列，游戏包扩展抛
        ``ModuleNotFoundError`` 会跳过其后的托盘与运行时就绪动作，
        用户看到托盘图标、托盘菜单和音响搜索 UI 一起消失。现在每段独立
        捕获异常并记录阶段名，后续段必须照常执行。
        """
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import sys
            from unittest.mock import patch

            from lib.core.render.router import BackendSelection
            from lib.core.render.registry import DesktopBackendBundle
            from lib.core.render.visuals.types import Rect
            from lib.script import main as app_main

            executed = []

            class EventCenter:
                def subscribe(self, event_type, callback): pass

            class ApplicationUi:
                def prepare_application(self, application): pass
                def prepare_runtime(self): pass
                def prewarm_runtime_ui(self): pass
                def start_runtime(self, application): executed.append("runtime_ui")
                def open_announcement(self): pass
                def begin_shutdown(self): pass
                def stop_runtime(self): pass
                def cleanup(self): pass
                def has_exit_animation(self): return False
                def finalize(self): pass

            class GameMode:
                def configure_runtime(self, pet, particles, effects): pass

            class Tray:
                def set_menu_state(self, state): pass
                def disconnect_quit_requested(self, callback): pass
                def connect_quit_requested(self, callback): pass
                def disconnect_announcement_requested(self, callback): pass
                def connect_announcement_requested(self, callback): pass
                def disconnect_command_requested(self, callback): pass
                def connect_command_requested(self, callback): pass
                def initialize(self):
                    executed.append("tray")
                    return True

            class Service: pass

            ui = ApplicationUi()
            events = []

            def record(event_type, payload=None):
                events.append(event_type)

            bundle = DesktopBackendBundle(
                draw_backend_factory=lambda: object(),
                application_runtime_factory=lambda: object(),
                application_ui_host_factory=lambda: ui,
                scheduler_factory=lambda: object(),
                screen_capture_factory=lambda: object(),
                pet_window_factory=lambda gifs, overlay: object(),
                particle_overlay_factory=lambda: object(),
                effect_overlay_factory=lambda: object(),
                tray_host_factory=lambda: Tray(),
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
                state._script_dir = "."
                state._gifs = {"idle": object()}
                state._particles = object()
                state._effects = object()
                state._pet_window_factory = lambda gifs, overlay: object()
                state._publish_event = record
                # 让「运行时管理器」这一段失败：它必须不阻断托盘与运行时就绪。
                state._init_stage_runtime_managers = lambda: (_ for _ in ()).throw(
                    RuntimeError("simulated stage failure")
                )

                state._on_init_ready(None)

            assert "tray" in executed, executed
            assert "runtime_ui" in executed, executed
            assert events and events[-1] is not None, events
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
