from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from config.config import UI
from lib.core.dx_bridge.application_ui import DxApplicationUiHost
from lib.core.dx_bridge.desktop_backend import DxDesktopBackend
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.offscreen import find_dx_library
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.event.center import Event, EventType, cleanup_event_center, get_event_center
from lib.core.graphics.types import Point, Rect, Size
from lib.core.graphics.application_visuals import (
    COMMAND_HINT_DEFAULT_ITEMS,
    notice_panel_size,
    qr_panel_size,
    resolve_qr_panel_layout,
)
from lib.core.input.types import Key, MouseButton
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.world_objects import (
    configure_world_object_backend,
    get_world_object_backend,
    reset_world_object_backend,
)


class _LayerManager:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register(self, host, layer, **kwargs):
        self.registered.append((host, layer, kwargs))

    def unregister(self, host):
        self.unregistered.append(host)

    def enforce_burst(self):
        return None


class _Host:
    _next_identity = 1

    def __init__(self, width, height, *, x=0, y=0, callbacks=None, **kwargs):
        self.identity = _Host._next_identity
        _Host._next_identity += 1
        self.callbacks = callbacks
        self.geometry = Rect(x, y, width, height)
        self.visible = False
        self.alive = True
        self.active = False
        self.repaint_count = 0
        self.cleanup_count = 0

    @property
    def native_handle(self):
        return self.identity

    def is_alive(self):
        return self.alive

    def is_visible(self):
        return self.alive and self.visible

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def activate(self):
        self.active = True

    def set_geometry(self, geometry):
        self.geometry = geometry

    def request_repaint(self, viewport=None):
        self.repaint_count += 1

    def poll_events(self):
        return ()

    def raise_window(self):
        return None

    def stack_window(self, insert_after):
        return self.native_handle

    def cleanup(self):
        if not self.alive:
            return
        self.alive = False
        self.visible = False
        self.cleanup_count += 1


class DxDesktopBackendTests(unittest.TestCase):
    def setUp(self):
        cleanup_event_center()
        reset_world_object_backend()

    def tearDown(self):
        cleanup_event_center()
        reset_world_object_backend()

    def test_complete_bundle_imports_and_shares_context_with_pyqt_blocked(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__
            def blocked(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked")
                return original_import(name, *args, **kwargs)
            builtins.__import__ = blocked

            from lib.core.dx_bridge.desktop_backend import (
                cleanup_dx_desktop_backend,
                configure_dx_desktop_backend,
                get_dx_desktop_backend,
            )
            from lib.core.desktop_backend import get_desktop_backend_bundle
            from lib.core.world_objects import get_world_object_backend

            configure_dx_desktop_backend(warp=True)
            owner = get_dx_desktop_backend()
            bundle = get_desktop_backend_bundle()
            assert owner is not None and bundle is not None
            assert bundle.application_runtime_factory().context is owner.context
            assert bundle.scheduler_factory()._context is owner.context
            assert bundle.application_ui_host_factory()._context is owner.context
            assert get_world_object_backend() is owner.world_object_backend
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            cleanup_dx_desktop_backend()
            assert owner.cleaned
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

    def test_owner_cleanup_cancels_services_and_residual_hosts(self):
        owner = DxDesktopBackend(warp=True)
        configure_world_object_backend(owner.world_object_backend)
        timer = owner.create_scheduler().create_periodic_timer(lambda: None)
        timer.start(10)
        delivered = []
        pump = owner.create_event_pump(lambda: delivered.append(True))
        host = _Host(10, 10)
        owner.context.register_poller(host)

        owner.cleanup()
        owner.cleanup()
        pump.emit()
        owner.context.run_once()

        self.assertTrue(owner.cleaned)
        self.assertFalse(timer.active)
        self.assertEqual(delivered, [])
        self.assertEqual(host.cleanup_count, 1)
        self.assertEqual(owner.context.registered_pollers(), ())
        self.assertIsNone(get_world_object_backend())
        with self.assertRaisesRegex(RuntimeError, "cleaned"):
            owner.create_scheduler()

    def test_application_state_starts_and_exits_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(
            """
            import builtins
            import sys
            from dataclasses import replace
            from unittest.mock import patch

            original_import = builtins.__import__
            def blocked(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked")
                return original_import(name, *args, **kwargs)
            builtins.__import__ = blocked

            from lib.core.backend_router import BackendSelection
            from lib.core.dx_bridge.desktop_backend import DxDesktopBackend
            from lib.script import main as app_main

            class Service:
                def configure_login_dialog_initializer(self, initializer):
                    self.login_initializer = initializer

            class GameMode:
                def configure_runtime(self, pet, particles, effects): pass

            class Overlay:
                def __init__(self): self.cleaned = False
                def flush_immediately(self): pass
                def cleanup(self): self.cleaned = True

            class Pet:
                def __init__(self): self.cleaned = False
                def shutdown_host(self): self.cleaned = True

            class Tray:
                def __init__(self):
                    self.cleaned = False
                    self.quit_callbacks = []
                    self.announcement_callbacks = []
                def connect_quit_requested(self, callback): self.quit_callbacks.append(callback)
                def disconnect_quit_requested(self, callback):
                    self.quit_callbacks = [item for item in self.quit_callbacks if item != callback]
                def connect_announcement_requested(self, callback): self.announcement_callbacks.append(callback)
                def disconnect_announcement_requested(self, callback):
                    self.announcement_callbacks = [item for item in self.announcement_callbacks if item != callback]
                def connect_command_requested(self, callback): pass
                def disconnect_command_requested(self, callback): pass
                def set_menu_state(self, state): self.menu_state = state
                def initialize(self): return True
                def begin_shutdown(self): pass
                def cleanup(self): self.cleaned = True

            owner = DxDesktopBackend(warp=True)
            overlays = [Overlay(), Overlay()]
            pet = Pet()
            tray = Tray()
            bundle = replace(
                owner.bundle(),
                pet_window_factory=lambda gifs, particles: pet,
                particle_overlay_factory=lambda: overlays[0],
                effect_overlay_factory=lambda: overlays[1],
                tray_host_factory=lambda: tray,
            )
            services = {name: Service() for name in (
                "voice", "gsv", "bug", "microphone", "push_to_talk",
                "voice_handler", "cmd", "mode", "office", "ollama", "chat", "memory", "tool",
            )}
            replacements = {
                "initialize_app_logger": lambda *args, **kwargs: None,
                "cleanup_app_logger": lambda: None,
                "_new_log_startup_hardware_info": lambda *args, **kwargs: None,
                "_new_ensure_desktop_shortcut": lambda *args, **kwargs: None,
                "discover_all": lambda: None,
                "init_all_managers": lambda entity: {},
                "cleanup_all_managers": lambda: None,
                "get_game_mode_service": lambda: GameMode(),
                "cleanup_game_mode_service": lambda: None,
                "get_gsvmove_service": lambda: services["gsv"],
                "cleanup_gsvmove_service": lambda: None,
                "get_bug_tracker_service": lambda: services["bug"],
                "cleanup_bug_tracker_service": lambda: None,
                "get_microphone_stt_service": lambda: services["microphone"],
                "cleanup_microphone_stt_service": lambda: None,
                "get_microphone_push_to_talk_manager": lambda: services["push_to_talk"],
                "cleanup_microphone_push_to_talk_manager": lambda: None,
                "get_voice_request_handler": lambda: services["voice_handler"],
                "cleanup_voice_request_handler": lambda: None,
                "get_cmd_center": lambda: services["cmd"],
                "cleanup_cmd_center": lambda: None,
                "get_interaction_mode_service": lambda: services["mode"],
                "cleanup_interaction_mode_service": lambda: None,
                "get_office_service": lambda **kwargs: services["office"],
                "cleanup_office_service": lambda: None,
                "get_ollama_manager": lambda **kwargs: services["ollama"],
                "cleanup_ollama_manager": lambda: None,
                "get_chat_handler": lambda **kwargs: services["chat"],
                "cleanup_chat_handler": lambda: None,
                "get_stream_memory": lambda **kwargs: services["memory"],
                "cleanup_stream_memory": lambda: None,
                "get_tool_dispatcher": lambda **kwargs: services["tool"],
                "cleanup_tool_dispatcher": lambda: None,
                "cleanup_compute_hub": lambda: None,
                "ANIMATION": {"start_exit_enabled": False},
            }
            with patch.multiple(app_main, **replacements), patch(
                "lib.core.voice.core.get_voice_core",
                return_value=services["voice"],
            ), patch(
                "lib.script.main.GifLoader.load_all",
                return_value={"idle": object()},
            ):
                state = app_main.ApplicationState(
                    backend_bundle=bundle,
                    backend_selection=BackendSelection("directx", "directx", False),
                )
                state.start()
                state._application_runtime.schedule_once(0, state.request_exit)
                exit_code = state.run_event_loop()
                exit_code = state.finalize_after_event_loop(exit_code)

            assert exit_code == 0, ("exit_code", exit_code)
            assert pet.cleaned and tray.cleaned, (pet.cleaned, tray.cleaned)
            assert all(item.cleaned for item in overlays), [item.cleaned for item in overlays]
            assert owner.cleaned, "DX owner was not cleaned"
            pyqt_modules = [name for name in sys.modules if name.startswith("PyQt5")]
            assert not pyqt_modules, pyqt_modules
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                kernel32.GetCurrentProcess.argtypes = []
                kernel32.GetCurrentProcess.restype = ctypes.c_void_p
                psapi.EnumProcessModules.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                psapi.EnumProcessModules.restype = wintypes.BOOL
                psapi.GetModuleFileNameExW.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    wintypes.LPWSTR,
                    wintypes.DWORD,
                ]
                psapi.GetModuleFileNameExW.restype = wintypes.DWORD
                process = kernel32.GetCurrentProcess()
                modules = (ctypes.c_void_p * 2048)()
                required = wintypes.DWORD()
                assert psapi.EnumProcessModules(
                    process,
                    modules,
                    ctypes.sizeof(modules),
                    ctypes.byref(required),
                ), ctypes.get_last_error()
                module_count = min(
                    len(modules),
                    required.value // ctypes.sizeof(ctypes.c_void_p),
                )
                loaded_paths = []
                for index in range(module_count):
                    buffer = ctypes.create_unicode_buffer(32768)
                    if psapi.GetModuleFileNameExW(
                        process,
                        modules[index],
                        buffer,
                        len(buffer),
                    ):
                        loaded_paths.append(buffer.value.lower())
                qt_modules = [
                    path for path in loaded_paths
                    if path.rsplit("\\\\", 1)[-1].startswith(("qt5", "qt6"))
                ]
                assert not qt_modules, qt_modules
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


class DxApplicationUiHostTests(unittest.TestCase):
    def setUp(self):
        cleanup_event_center()
        self.context = DxLoopContext()
        self.provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(-100, 50, 900, 700),
        )
        self.hosts = []
        self.layers = _LayerManager()

        def create_host(width, height, **kwargs):
            host = _Host(width, height, **kwargs)
            self.hosts.append(host)
            return host

        patcher = patch(
            "lib.core.dx_bridge.application_ui.get_layer_manager",
            return_value=self.layers,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ui = DxApplicationUiHost(
            self.context,
            screen_provider=self.provider,
            window_host_factory=create_host,
            warp=True,
            announcement_opener=lambda url: True,
        )

    def tearDown(self):
        self.ui.cleanup()
        cleanup_event_center()

    @staticmethod
    def _qr_png() -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (12, 12), "white").save(output, format="PNG")
        return output.getvalue()

    def test_command_information_and_music_login_ui_are_native_and_cleanup(self):
        self.ui.prepare_runtime()

        inputs = []
        center = get_event_center()
        center.subscribe(EventType.INPUT_HASH, lambda event: inputs.append(event.data))
        center.publish(Event(EventType.UI_COMMAND_TOGGLE, {}))
        command = self.ui._panels["command"]
        self.assertEqual(
            command._size,
            (int(UI["cmd_window_width"]), int(UI["cmd_window_height"])),
        )
        self.assertTrue(command.host.visible)
        self.assertTrue(command.host.active)
        command.handle_ime_composition("qingli")
        self.assertEqual(command._composition, "qingli")
        command.handle_text_input("#清理")
        self.assertEqual(command._composition, "")
        command.handle_key_press(SimpleNamespace(key=Key.RETURN, text=""))
        self.assertEqual(inputs[-1]["text"], "清理")
        self.assertFalse(command.host.visible)

        center.publish(Event(EventType.INFORMATION, {"text": "服务已就绪", "min": 1, "max": 2}))
        bubble = self.ui._bubble
        self.assertIsNotNone(bubble)
        self.assertTrue(bubble.host.visible)
        self.assertTrue(bubble.prepare_render().commands)

        center.publish(Event(EventType.MUSIC_LOGIN_QR_SHOW, {
            "title": "音乐扫码登录",
            "status": "等待扫码",
            "qr_png": self._qr_png(),
        }))
        login = self.ui._panels["music-login"]
        self.assertEqual(login._size, qr_panel_size())
        self.assertTrue(login.host.visible)
        self.assertTrue(login.prepare_render().resource_revisions)

        self.ui.cleanup()
        self.ui.cleanup()
        self.assertEqual(self.context.registered_pollers(), ())
        self.assertTrue(all(host.cleanup_count == 1 for host in self.hosts))

    def test_information_force_replace_and_hide_clear_bubble_queue(self):
        self.ui.prepare_runtime()
        center = get_event_center()
        center.publish(Event(EventType.INFORMATION, {
            "text": "旧回复",
            "min": 100,
            "max": 200,
        }))
        center.publish(Event(EventType.INFORMATION, {
            "text": "等待中的回复",
            "min": 100,
            "max": 200,
            "source": "companion",
        }))
        bubble = self.ui._bubble
        self.assertEqual(bubble._current[0], "旧回复")
        self.assertEqual([item[0] for item in bubble._queue], ["等待中的回复"])

        center.publish(Event(EventType.INFORMATION, {
            "text": "办公思考",
            "min": 100,
            "max": 200,
            "source": "office",
            "task_id": "task-1",
            "kind": "thinking",
        }))
        self.assertEqual(
            [item[0] for item in bubble._queue],
            ["等待中的回复", "办公思考"],
        )
        center.publish(Event(EventType.UI_BUBBLE_REMOVE, {
            "source": "office",
            "task_id": "task-1",
            "kind": "thinking",
        }))
        self.assertEqual([item[0] for item in bubble._queue], ["等待中的回复"])

        center.publish(Event(EventType.INFORMATION, {
            "text": "办公流式回复",
            "min": 0,
            "max": 100,
            "force_replace": True,
        }))
        self.assertEqual(bubble._current[0], "办公流式回复")
        self.assertEqual(bubble._queue, [])

        center.publish(Event(EventType.INFORMATION, {
            "text": "不应在清空后出现",
            "min": 100,
            "max": 200,
        }))
        center.publish(Event(EventType.UI_BUBBLE_HIDE, {}))
        self.assertIsNone(bubble._current)
        self.assertEqual(bubble._queue, [])
        self.assertFalse(bubble.host.visible)

    def test_command_toggle_uses_pet_anchor_instead_of_screen_center(self):
        self.ui.prepare_runtime()
        entity = SimpleNamespace(get_core_geometry=lambda: Rect(100, 200, 150, 150))

        get_event_center().publish(Event(
            EventType.UI_COMMAND_TOGGLE,
            {"entity": entity},
        ))

        panel = self.ui._panels["command"]
        self.assertEqual(
            panel.host.geometry,
            Rect(
                100 + 150 + 6,
                200 + (150 - int(UI["cmd_window_height"])) // 2,
                int(UI["cmd_window_width"]),
                int(UI["cmd_window_height"]),
            ),
        )

        hint = self.ui._command_hint
        self.assertIsNotNone(hint)
        self.assertEqual(
            hint.host.geometry.y,
            panel.host.geometry.y + panel.host.geometry.height + 2,
        )

    def test_command_hint_is_native_interactive_and_tracks_input(self):
        registry = get_hash_cmd_registry()
        names = [f"DX测试{i}" for i in range(7)]
        for name in names:
            registry.register(name, "[参数]", "DX提示测试")
            self.addCleanup(registry.unregister, name)

        self.ui.prepare_runtime()
        get_event_center().publish(Event(EventType.UI_COMMAND_TOGGLE, {}))
        command = self.ui._panels["command"]
        hint = self.ui._command_hint
        self.assertIsNotNone(hint)
        self.assertTrue(hint.host.visible)
        self.assertEqual(hint._items, COMMAND_HINT_DEFAULT_ITEMS)

        second_row = hint.visual.row_rects[1]
        hint.handle_pointer_press(SimpleNamespace(
            pos=Point(second_row.x + 2, second_row.y + 2),
            button=MouseButton.LEFT,
        ))
        self.assertEqual(command._input, "#")

        command.handle_text_input("DX测试")
        self.assertEqual(hint._mode, "hash")
        self.assertEqual(len(hint._items), 7)
        command.handle_key_press(SimpleNamespace(key=Key.DOWN, text=""))
        command.handle_key_press(SimpleNamespace(key=Key.TAB, text=""))
        self.assertEqual(command._input, "#DX测试1 ")

        command.hide()
        self.assertFalse(hint.host.visible)

    def test_qr_action_button_uses_shared_batch_and_hides_on_click(self):
        self.ui.prepare_runtime()
        get_event_center().publish(Event(EventType.MUSIC_LOGIN_QR_SHOW, {
            "title": "音乐扫码登录",
            "status": "等待扫码",
            "qr_png": self._qr_png(),
        }))
        panel = self.ui._panels["music-login"]
        layout = resolve_qr_panel_layout(panel._size)
        action_point = Point(
            layout.action_rect.x + layout.action_rect.width / 2,
            layout.action_rect.y + layout.action_rect.height / 2,
        )
        self.assertGreaterEqual(len(panel.prepare_render().commands), 10)
        normal_fill = panel.prepare_render().commands[-2].fill
        panel.handle_pointer_move(SimpleNamespace(pos=action_point))
        self.assertNotEqual(panel.prepare_render().commands[-2].fill, normal_fill)
        panel.handle_pointer_press(SimpleNamespace(
            pos=action_point,
            button=MouseButton.LEFT,
        ))
        panel.handle_pointer_release(MouseButton.LEFT)
        self.assertFalse(panel.is_visible())

    def test_command_action_panel_is_complete_and_tracks_command(self):
        self.ui.prepare_runtime()
        entity = SimpleNamespace(get_core_geometry=lambda: Rect(100, 200, 150, 150))
        get_event_center().publish(Event(EventType.UI_COMMAND_TOGGLE, {"entity": entity}))
        actions = self.ui._action_panel
        self.assertIsNotNone(actions)
        self.assertTrue(actions.host.visible)
        self.assertEqual(actions._layout().size, Size(240, 96))
        self.assertEqual(tuple(name for name, _rect in actions._layout().rects), (
            "clickthrough", "scale_up", "scale_down", "close",
            "launch_wuwa", "chat_mode", "interaction_mode", "more_functions",
        ))
        action_rects = dict(actions._layout().rects)
        self.assertEqual(action_rects["more_functions"].x, action_rects["launch_wuwa"].x)
        self.assertEqual(
            action_rects["more_functions"].y + action_rects["more_functions"].height,
            action_rects["launch_wuwa"].y,
        )
        self.assertGreater(len(actions.prepare_render().commands), 20)
        close_rect = next(rect for name, rect in actions._layout().rects if name == "close")
        point = Point(close_rect.x + 4, close_rect.y + 4)
        actions.handle_pointer_move(SimpleNamespace(global_pos=point))
        actions.handle_pointer_press(SimpleNamespace(
            global_pos=point,
            button=MouseButton.LEFT,
        ))
        actions.handle_pointer_release(MouseButton.LEFT)
        self.assertEqual(actions._pressed, "")

    def test_command_actions_use_core_events_without_qt(self):
        self.ui.prepare_runtime()
        entity = SimpleNamespace(get_core_geometry=lambda: Rect(100, 200, 150, 150))
        get_event_center().publish(Event(EventType.UI_COMMAND_TOGGLE, {"entity": entity}))
        actions = self.ui._action_panel
        received = []
        center = get_event_center()
        center.subscribe(EventType.APP_QUIT, lambda event: received.append(event.type))
        actions._on_action("close")
        self.assertEqual(received, [EventType.APP_QUIT])
        center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, lambda event: received.append(event.data["enabled"]))
        actions._on_action("clickthrough")
        self.assertTrue(received[-1])
        center.subscribe(EventType.INTERACTION_MODE_SET, lambda event: received.append(event.data["toggle"]))
        actions._on_action("interaction_mode")
        self.assertTrue(received[-1])

    def test_office_approval_opens_reusable_helper_on_office_page(self):
        self.ui.prepare_runtime()
        launch = Mock(return_value=True)
        self.ui._workbench_opener = launch
        get_event_center().publish(Event(EventType.OFFICE_APPROVAL_REQUEST, {
            "task_id": "task-1",
            "approval_id": "approval-1",
        }))

        launch.assert_called_once_with("office")


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "DX application UI integration requires Windows and a built DX DLL",
)
class DxApplicationUiIntegrationTests(unittest.TestCase):
    def test_command_window_uses_qt_reference_geometry_and_colors(self):
        cleanup_event_center()
        context = DxLoopContext()
        provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(0, 0, 800, 600),
        )
        ui = DxApplicationUiHost(context, screen_provider=provider, warp=True)
        try:
            ui.prepare_runtime()
            get_event_center().publish(Event(EventType.UI_OPEN_CMD_WINDOW, {}))
            context.run_once()
            command = ui._panels["command"]
            width, height = command._size
            self.assertEqual((width, height), (
                int(UI["cmd_window_width"]),
                int(UI["cmd_window_height"]),
            ))
            pixels = command.host.readback_rgba()

            def pixel(x, y):
                offset = (y * width + x) * 4
                return tuple(pixels[offset:offset + 4])

            self.assertEqual(pixel(0, 0), (0, 0, 0, 255))
            self.assertEqual(pixel(2, 2), (173, 216, 230, 255))
            self.assertEqual(pixel(width // 2, height // 2), (255, 255, 255, 255))
        finally:
            ui.cleanup()
            cleanup_event_center()

    def test_notice_window_submits_nonempty_native_frame(self):
        cleanup_event_center()
        context = DxLoopContext()
        provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(0, 0, 800, 600),
        )
        ui = DxApplicationUiHost(
            context,
            screen_provider=provider,
            warp=True,
            announcement_opener=lambda url: True,
        )
        try:
            ui.prepare_runtime()
            get_event_center().publish(Event(EventType.INFORMATION, {
                "text": "DX application UI ready",
                "min": 1,
                "max": 2,
            }))
            context.run_once()
            bubble = ui._bubble
            self.assertIsNotNone(bubble)
            self.assertTrue(bubble.host.is_visible())
            pixels = bubble.host.readback_rgba()
            width, height = int(bubble.host.width), int(bubble.host.height)
            self.assertEqual(len(pixels), width * height * 4)
            self.assertTrue(any(pixels[index + 3] for index in range(0, len(pixels), 4)))
        finally:
            ui.cleanup()
            cleanup_event_center()
        self.assertEqual(context.registered_pollers(), ())


if __name__ == "__main__":
    unittest.main()
