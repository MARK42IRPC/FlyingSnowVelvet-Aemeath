import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class PetHostContractTests(unittest.TestCase):
    def test_callback_protocol_imports_without_pyqt(self):
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

            from lib.core.graphics.types import Point
            from lib.core.input.types import KeyboardInput, MouseButton, MouseInput
            from lib.core.pet_host import PetHostCallbacks

            class Callbacks:
                def prepare_render(self): return object()
                def handle_pointer_enter(self): pass
                def handle_pointer_leave(self): pass
                def handle_pointer_press(self, event): assert isinstance(event, MouseInput)
                def handle_pointer_move(self, event): assert isinstance(event, MouseInput)
                def handle_pointer_release(self, button): assert button == MouseButton.LEFT
                def handle_window_moved(self, position): assert position == Point(1, 2)
                def handle_key_press(self, event): assert isinstance(event, KeyboardInput)
                def handle_key_release(self, event): assert isinstance(event, KeyboardInput)
                def handle_host_close(self): pass

            callbacks: PetHostCallbacks = Callbacks()
            callbacks.handle_window_moved(Point(1, 2))
            callbacks.handle_pointer_release(MouseButton.LEFT)
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

    def test_pet_window_source_does_not_own_native_qwidget_events(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "lib" / "core" / "pet_window.py").read_text(encoding="utf-8")

        for method_name in (
            "paintEvent",
            "mousePressEvent",
            "mouseMoveEvent",
            "mouseReleaseEvent",
            "keyPressEvent",
            "keyReleaseEvent",
            "moveEvent",
            "closeEvent",
        ):
            self.assertNotIn(f"def {method_name}", source)
        self.assertNotIn("mouse_input_from_qt", source)
        self.assertNotIn("keyboard_input_from_qt", source)
        self.assertNotIn("render_draw_core", source)
        self.assertNotIn("lib.core.qt_bridge", source)
        self.assertNotIn("lib.script.ui", source)

    def test_qt_pet_window_is_the_composition_boundary(self):
        from lib.core.pet_window import PetWindow
        from lib.core.qt_bridge.pet_widget import QtPetWidget
        from lib.core.qt_bridge.pet_window import QtPetWindow

        self.assertTrue(issubclass(QtPetWindow, PetWindow))
        self.assertTrue(issubclass(QtPetWindow, QtPetWidget))
        self.assertFalse(QtPetWindow.__abstractmethods__)


if __name__ == "__main__":
    unittest.main()
