import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from config.config import ANIMATION
from lib.core.draw_core import cleanup_draw_core
from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Point, Rect
from lib.core.layer_manager import cleanup_layer_manager
from lib.core.timing import register_timing_manager
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.pet_window import DxPetWindow, create_pet_window_factory
from lib.core.dx_bridge.screen import DxMonitor, DxScreenProvider


class _WindowHost:
    def __init__(self, width, height, **kwargs):
        self.width = width
        self.height = height
        self.callbacks = kwargs["callbacks"]
        self.geometry = Rect(kwargs["x"], kwargs["y"], width, height)
        self.kwargs = kwargs
        self.visible = False
        self.alive = True
        self.clickthrough = False
        self.repaint_count = 0
        self.cleanup_count = 0

    @property
    def identity(self):
        return 101

    def poll_events(self):
        return ()

    def is_alive(self):
        return self.alive

    def is_visible(self):
        return self.alive and self.visible

    def show(self):
        self.visible = True

    def get_geometry(self):
        return self.geometry

    def set_geometry(self, geometry):
        self.geometry = geometry

    def set_clickthrough(self, enabled):
        self.clickthrough = bool(enabled)

    def request_repaint(self, viewport=None):
        self.repaint_count += 1

    def raise_window(self):
        pass

    def stack_window(self, insert_after):
        return self.identity

    def cleanup(self):
        if not self.alive:
            return
        self.cleanup_count += 1
        self.alive = False
        self.visible = False


class _LayerManager:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.enforce_count = 0

    def register(self, host, layer, **kwargs):
        self.registered.append((host, layer, kwargs))

    def unregister(self, host):
        self.unregistered.append(host)

    def enforce_burst(self):
        self.enforce_count += 1


class _StartupSound:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.play_count = 0

    def play(self):
        self.play_count += 1


class _StateMachine:
    def __init__(self, owner, timing_manager):
        self.owner = owner
        self.timing_manager = timing_manager


def _idle_resource():
    return ImageResource(
        "idle",
        (RasterFrame(1, 1, b"\xff\xff\xff\xff", 100),),
    )


class DxPetWindowTests(unittest.TestCase):
    def setUp(self):
        cleanup_event_center()
        cleanup_draw_core()
        cleanup_layer_manager()
        register_timing_manager(None)

    def tearDown(self):
        cleanup_event_center()
        cleanup_draw_core()
        cleanup_layer_manager()
        register_timing_manager(None)

    def _create_pet(self, *, shutdown_ui=None):
        context = DxLoopContext()
        screen = DxMonitor(
            Rect(-200, 100, 800, 600),
            Rect(-200, 100, 800, 560),
            primary=True,
        )
        screen_provider = DxScreenProvider(lambda: (screen,))
        hosts = []
        layer_manager = _LayerManager()

        def create_host(width, height, **kwargs):
            host = _WindowHost(width, height, **kwargs)
            hosts.append(host)
            return host

        patches = (
            patch("lib.core.pet_window.Actions.get_random_action_from_group", return_value=None),
            patch("lib.core.dx_bridge.pet_window.get_layer_manager", return_value=layer_manager),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        factory = create_pet_window_factory(
            context,
            screen_provider=screen_provider,
            window_host_factory=create_host,
            state_machine_factory=_StateMachine,
            startup_sound_factory=_StartupSound,
            interaction_sound_factory=_StartupSound,
        )
        pet = factory({"idle": _idle_resource()}, object())
        pet._dx_shutdown_ui = shutdown_ui
        return pet, hosts[0], context, layer_manager

    def test_composition_drives_geometry_render_anchor_and_host_state(self):
        shutdown_ui = []
        pet, host, context, layer_manager = self._create_pet(
            shutdown_ui=lambda: shutdown_ui.append(True)
        )
        width, height = ANIMATION["pet_size"]
        expected_x = int(round(-200 + (800 - width) / 2))
        expected_y = int(round(100 + (600 - height) / 2))

        self.assertIsInstance(pet, DxPetWindow)
        self.assertIs(host.callbacks, pet)
        self.assertEqual(host.geometry, Rect(expected_x, expected_y, width, height))
        self.assertTrue(host.visible)
        self.assertEqual(host.repaint_count, 1)
        self.assertEqual(pet._startup_voice_sound.play_count, 1)
        self.assertEqual(context.registered_pollers(), (host,))
        self.assertEqual(layer_manager.registered[0][0], host)
        self.assertEqual(layer_manager.enforce_count, 1)

        batch = pet.prepare_render().build_batch()
        self.assertEqual(len(batch.commands), 1)
        self.assertEqual(
            (batch.commands[0].target_size.width, batch.commands[0].target_size.height),
            ANIMATION["pet_size"],
        )
        self.assertEqual(batch.commands[0].resource_id, "idle")

        responses = []
        get_event_center().subscribe(
            EventType.UI_ANCHOR_RESPONSE,
            lambda event: responses.append(event.data),
        )
        pet._host_publish_anchor_response(
            window_id="pet_window",
            anchor_id="bottom_right",
            ui_id="probe",
        )
        self.assertEqual(
            responses[-1]["anchor_point"],
            Point(expected_x + width, expected_y + height),
        )

        pet._host_move(Point(10, 20))
        pet._host_set_clickthrough(True)
        pet._host_request_repaint()
        self.assertEqual(host.geometry, Rect(10, 20, width, height))
        self.assertTrue(host.clickthrough)
        self.assertEqual(host.repaint_count, 2)

        pet.shutdown_host()
        pet.shutdown_host()
        self.assertEqual(shutdown_ui, [True])
        self.assertEqual(host.cleanup_count, 1)
        self.assertEqual(layer_manager.unregistered, [host])
        self.assertEqual(context.registered_pollers(), ())

    def test_native_close_requests_application_quit_once(self):
        pet, host, context, _layer_manager = self._create_pet()
        quit_events = []
        get_event_center().subscribe(
            EventType.APP_QUIT,
            lambda event: quit_events.append(event.data),
        )

        pet.handle_host_close()
        pet.handle_host_close()

        self.assertEqual(len(quit_events), 1)
        self.assertIs(quit_events[0]["entity"], pet)
        self.assertEqual(quit_events[0]["exit_code"], 0)
        self.assertTrue(host.alive)

        pet.shutdown_host()
        self.assertFalse(host.alive)
        self.assertEqual(context.registered_pollers(), ())

    def test_dx_pet_composition_imports_with_pyqt_blocked(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.dx_bridge.pet_window import DxPetWindow
            from lib.core.pet_window import PetWindow

            assert issubclass(DxPetWindow, PetWindow)
            assert not DxPetWindow.__abstractmethods__
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
