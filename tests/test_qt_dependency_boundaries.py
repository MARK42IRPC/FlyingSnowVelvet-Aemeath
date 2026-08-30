import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.event.center import EventType


class QtDependencyBoundaryTests(unittest.TestCase):
    @staticmethod
    def _qt_import_violations(repo_root: Path, paths) -> list[str]:
        violations = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "PyQt5"
                    or name.startswith("PyQt5.")
                    or name == "lib.core.qt_bridge"
                    or name.startswith("lib.core.qt_bridge.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break
        return violations

    def test_config_and_core_do_not_import_qt_or_qt_bridge(self):
        repo_root = Path(__file__).resolve().parents[1]
        roots = (repo_root / "config", repo_root / "lib" / "core")
        excluded = repo_root / "lib" / "core" / "qt_bridge"

        violations = []
        for root in roots:
            for path in root.rglob("*.py"):
                if excluded in path.parents:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    if any(
                        name == "PyQt5"
                        or name.startswith("PyQt5.")
                        or name == "lib.core.qt_bridge"
                        or name.startswith("lib.core.qt_bridge.")
                        for name in names
                    ):
                        violations.append(str(path.relative_to(repo_root)))

        self.assertEqual(violations, [])

    def test_backend_neutral_core_does_not_import_qt_ui_implementations(self):
        repo_root = Path(__file__).resolve().parents[1]
        core_root = repo_root / "lib" / "core"
        excluded = core_root / "qt_bridge"
        violations = []

        for path in core_root.rglob("*.py"):
            if excluded in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "lib.script.ui" or name.startswith("lib.script.ui.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break

        self.assertEqual(violations, [])

    def test_repository_qt_imports_stay_in_explicit_toolkit_boundaries(self):
        repo_root = Path(__file__).resolve().parents[1]
        scan_roots = (
            repo_root / "config",
            repo_root / "lib",
            repo_root / "scripts",
        )
        paths = [
            path
            for root in scan_roots
            for path in root.rglob("*.py")
        ]
        qt_imports = {
            Path(path).as_posix()
            for path in self._qt_import_violations(repo_root, paths)
        }

        allowed_files = {
            "lib/script/SEanima/animation_player.py",
            "lib/script/bug_tracker/__main__.py",
            "lib/script/bug_tracker/window.py",
            "lib/script/cloudmusic/_qt_player.py",
            "lib/script/gemes/MAIN/manager_window.py",
            "lib/script/gemes/MAIN/runtime.py",
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/constants.py",
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/render.py",
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/widget.py",
            "lib/script/main.py",
            "lib/script/app/qt_backend_bootstrap.py",
            "lib/script/app/qt_application_ui.py",
            "lib/script/app/workbench_helper_entry.py",
            "lib/script/workbench/components.py",
            "lib/script/workbench/settings/page_layout.py",
        }

        unexpected = sorted(
            path
            for path in qt_imports
            if not path.startswith("lib/core/qt_bridge/")
            and not path.startswith("lib/script/ui/")
            and path not in allowed_files
        )
        self.assertEqual(unexpected, [])

    def test_backend_neutral_script_modules_do_not_import_qt_or_qt_bridge(self):
        repo_root = Path(__file__).resolve().parents[1]
        script_root = repo_root / "lib" / "script"
        directory_names = (
            "chat",
            "effects",
            "gsvmove",
            "mainpet",
            "microphone_stt",
            "music",
            "tool_dispatcher",
        )
        paths = []
        for name in directory_names:
            paths.extend((script_root / name).rglob("*.py"))
        paths.extend(
            script_root / "SEanima" / name
            for name in ("animation.py", "clip.py", "decoder.py", "effects.py")
        )
        lahai_root = (
            script_root
            / "gemes"
            / "packages"
            / "official"
            / "lahai_tetris"
            / "code"
            / "lahai_tetris_pkg"
        )
        paths.extend(lahai_root / name for name in ("model.py", "skills.py"))

        violations = self._qt_import_violations(repo_root, paths)

        self.assertEqual(violations, [])

    def test_backend_neutral_scripts_do_not_import_qt_ui_implementations(self):
        repo_root = Path(__file__).resolve().parents[1]
        script_root = repo_root / "lib" / "script"
        allowed_files = {
            script_root / "main.py",
            script_root / "app" / "qt_application_ui.py",
            script_root / "app" / "qt_backend_bootstrap.py",
            script_root / "app" / "workbench_helper_entry.py",
            script_root / "workbench" / "components.py",
            script_root / "workbench" / "settings" / "page_layout.py",
        }
        violations = []

        for path in script_root.rglob("*.py"):
            if script_root / "ui" in path.parents or path in allowed_files:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "lib.script.ui" or name.startswith("lib.script.ui.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break

        self.assertEqual(violations, [])

    def test_world_object_managers_only_use_backend_neutral_facades(self):
        repo_root = Path(__file__).resolve().parents[1]
        manager_paths = list((repo_root / "lib" / "script").glob("obj-*/manager.py"))
        violations = self._qt_import_violations(repo_root, manager_paths)
        self.assertEqual(violations, [])

        forbidden_tokens = (
            "QPoint",
            "QRect",
            "QImage",
            "QPixmap",
            "QWidget",
            "qt_bridge",
            ".geometry()",
            ".get_center()",
            ".width()",
            ".height()",
            ".x()",
            ".y()",
            "pixmap",
            "lib.script.ui",
            "PhysicsBody",
            "physics_body",
            "_fading",
            "_drag_offset",
            "_frozen",
            "_flipped",
            "list[object]",
        )
        for path in manager_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")
            self.assertIn("load_image_resource", source)
            self.assertIn("WorldObjectInstance", source)
            self.assertIn("create_world_object", source)

    def test_world_object_contract_has_no_native_asset_or_instance_types(self):
        repo_root = Path(__file__).resolve().parents[1]
        path = repo_root / "lib" / "core" / "world_objects.py"
        source = path.read_text(encoding="utf-8-sig")
        for token in (
            "QImage",
            "QPixmap",
            "QWidget",
            "PhysicsBody",
            "WorldObjectImagePair",
            "flipped_image",
        ):
            self.assertNotIn(token, source, token)
        self.assertIn("class WorldObjectRequest", source)
        self.assertIn("class WorldObjectInstance", source)
        self.assertIn("class WorldObjectMotion", source)

    def test_core_event_protocol_has_no_toolkit_render_callback(self):
        self.assertFalse(hasattr(EventType, "DRAW_RENDER"))

    def test_core_graphics_contract_has_no_toolkit_images_or_painter_callbacks(self):
        repo_root = Path(__file__).resolve().parents[1]
        graphics_root = repo_root / "lib" / "core" / "graphics"
        contract_paths = (
            graphics_root / "backend.py",
            graphics_root / "commands.py",
            graphics_root / "resources.py",
            graphics_root / "scene.py",
        )
        forbidden_tokens = (
            "QImage",
            "QPixmap",
            "QPainter",
            "QRect",
            "PaintCallback",
            "RenderItem",
            "RenderRequest",
        )

        for path in contract_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

        self.assertFalse((repo_root / "lib" / "core" / "render_core.py").exists())
        self.assertFalse((repo_root / "lib" / "core" / "render_layer.py").exists())

    def test_layer_manager_only_uses_backend_neutral_window_hosts(self):
        repo_root = Path(__file__).resolve().parents[1]
        contract_paths = (
            repo_root / "lib" / "core" / "layer_manager.py",
            repo_root / "lib" / "core" / "window_host.py",
        )
        forbidden_tokens = (
            "QWidget",
            "SetWindowPos",
            "HWND_TOPMOST",
            ".isVisible()",
            ".raise_()",
            ".winId()",
        )

        for path in contract_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

        source = contract_paths[1].read_text(encoding="utf-8-sig")
        self.assertIn("class LayerWindowHost(Protocol)", source)

    def test_core_runtime_imports_when_pyqt_is_unavailable(self):
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

            import config.config
            from lib.core.backend_router import BackendRouter
            from lib.core.draw_core import DrawCore
            from lib.core.event.center import EventCenter
            from lib.core.graphics.commands import DrawRequest
            from lib.core.graphics.gif_loader import GifLoader
            from lib.core.graphics.resources import ImageResource, RasterFrame
            from lib.core.layer_manager import LayerManager
            from lib.core.pet_window import PetWindow
            from lib.core.physics import PhysicsWorld
            from lib.core.screen_utils import get_virtual_screen_rect

            draw_core = DrawCore()
            frame = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
            draw_core.register_resource(ImageResource("pet", (frame,)))
            draw_core.add_draw_request(DrawRequest("pet"))
            assert draw_core.build_batch().commands[0].frame is frame
            assert draw_core._backend.__class__.__name__ == "_NullDrawBackend"
            assert GifLoader([]).load_all() == {}
            assert [item.backend_id for item in BackendRouter().descriptors()] == [
                "qt", "directx", "opengl", "vulkan"
            ]
            assert get_virtual_screen_rect().width > 0
            layer_manager = LayerManager()
            layer_window = object()
            layer_manager.register(layer_window, "PANEL", name="probe")
            layer_manager.enforce_now()
            assert layer_manager.snapshot()[0][3:] == ("probe", True)
            assert PetWindow.__name__ == "PetWindow"
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

    def test_dx_interaction_queries_and_ui_package_import_do_not_load_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise AssertionError(f"DX imported Qt: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            import lib.script.ui
            from lib.core.event.center import Event, EventType
            from lib.core.event.key_handler import KeyEventHandler
            from lib.core.game_obstacles import get_game_obstacle_rect
            from lib.core.graphics.types import Point, Rect
            from lib.core.input.types import Key
            from lib.script.mainpet.state import StateMachine

            class Entity:
                def get_core_geometry(self): return Rect(0, 0, 20, 20)
                def get_core_position(self): return Point(0, 0)
                def is_moving(self): return False
                def play_animation(self, *_args, **_kwargs): pass

            entity = Entity()
            state = StateMachine.__new__(StateMachine)
            state._entity = entity
            assert get_game_obstacle_rect() is None
            assert state._is_wander_target_blocked_by_lahai(Point(80, 40)) is False

            handler = KeyEventHandler.__new__(KeyEventHandler)
            handler._entity = entity
            handler._on_key_press(Event(EventType.KEY_PRESS, {"key": Key.LEFT}))
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
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
