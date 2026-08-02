import subprocess
import sys
import textwrap
import unittest
from collections import defaultdict
from pathlib import Path

from lib.core.event.center import EventType
from lib.core.graphics.types import Point
from lib.core.pet_movement_runtime import PetMovementRuntime


class _EventCenter:
    def __init__(self):
        self.listeners = defaultdict(list)
        self.published = []

    def subscribe(self, event_type, callback):
        self.listeners[event_type].append(callback)

    def unsubscribe(self, event_type, callback):
        callbacks = self.listeners[event_type]
        if callback in callbacks:
            callbacks.remove(callback)

    def publish(self, event):
        self.published.append(event)
        for callback in list(self.listeners[event.type]):
            callback(event)
            if event.handled:
                break


class PetMovementRuntimeTests(unittest.TestCase):
    def _make_runtime(self):
        center = _EventCenter()
        position = {"value": Point(0, 0)}
        state = {"value": "idle"}
        applied = []
        state_requests = []
        direction_changes = []

        def apply_position(point):
            position["value"] = point
            applied.append(point)

        def request_state(new_state, by_event):
            state["value"] = new_state
            state_requests.append((new_state, by_event))

        runtime = PetMovementRuntime(
            event_center=center,
            get_position=lambda: position["value"],
            on_position_update=apply_position,
            get_state=lambda: state["value"],
            request_state=request_state,
            on_direction_change=direction_changes.append,
        )
        return runtime, center, position, state, applied, state_requests, direction_changes

    def test_queue_and_interpolation_run_without_window_objects(self):
        runtime, center, _, _, applied, state_requests, _ = self._make_runtime()
        try:
            runtime.start_move(Point(100, 0))

            self.assertTrue(runtime.is_moving)
            self.assertEqual(runtime.target, Point(100, 0))
            self.assertEqual(state_requests, [("moving", False)])

            runtime.update_tick()
            rendered = runtime.update_frame(1.0)

            self.assertIsInstance(rendered, Point)
            self.assertEqual(applied[-1], rendered)
            self.assertGreater(rendered.x, 0)

            runtime.stop_move()

            self.assertFalse(runtime.is_moving)
            self.assertEqual(state_requests[-1], ("idle", False))
            done_events = [event for event in center.published if event.type is EventType.PET_MOVE_DONE]
            self.assertEqual(done_events[-1].data["result"], "cancelled")
        finally:
            runtime.cleanup()

    def test_dragging_and_teleport_normalize_point_like_values(self):
        runtime, center, position, _, applied, _, _ = self._make_runtime()
        try:
            self.assertEqual(runtime.begin_user_drag(), Point(0, 0))
            self.assertTrue(runtime.is_user_dragging)

            runtime.start_move((50, 60))
            self.assertFalse(runtime.is_moving)

            dragged = runtime.update_user_drag_position((12, 34))
            self.assertEqual(dragged, Point(12, 34))
            self.assertEqual(position["value"], Point(12, 34))
            self.assertEqual(runtime.end_user_drag(), Point(12, 34))

            teleported = runtime.teleport((80, 90))
            self.assertEqual(teleported, Point(80, 90))
            self.assertEqual(applied[-1], Point(80, 90))
        finally:
            runtime.cleanup()
            runtime.cleanup()

        self.assertFalse(center.listeners[EventType.PET_MOVE_ENQUEUE])
        self.assertFalse(center.listeners[EventType.PET_MOVE_PASS])

    def test_runtime_import_and_instantiation_do_not_require_pyqt(self):
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

            from collections import defaultdict
            from lib.core.graphics.types import Point
            from lib.core.pet_movement_runtime import PetMovementRuntime

            class Center:
                def __init__(self): self.listeners = defaultdict(list)
                def subscribe(self, event_type, callback): self.listeners[event_type].append(callback)
                def unsubscribe(self, event_type, callback): self.listeners[event_type].remove(callback)
                def publish(self, event):
                    for callback in list(self.listeners[event.type]):
                        callback(event)
                        if event.handled: break

            center = Center()
            runtime = PetMovementRuntime(
                event_center=center,
                get_position=lambda: Point(),
                on_position_update=lambda point: None,
                get_state=lambda: "idle",
                request_state=lambda state, by_event: None,
            )
            runtime.start_move(Point(10, 0))
            assert runtime.is_moving
            runtime.cleanup()
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
