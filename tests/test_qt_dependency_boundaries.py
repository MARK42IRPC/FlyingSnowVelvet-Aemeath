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
            "yuanbao_free_api",
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
        )
        for path in manager_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

    def test_core_event_protocol_has_no_toolkit_render_callback(self):
        self.assertFalse(hasattr(EventType, "DRAW_RENDER"))

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
            from lib.core.pet_window import PetWindow
            from lib.core.physics import PhysicsWorld
            from lib.core.screen_utils import get_virtual_screen_rect

            assert DrawCore()._backend.__class__.__name__ == "_NullDrawBackend"
            assert [item.backend_id for item in BackendRouter().descriptors()] == [
                "qt", "directx", "opengl", "vulkan"
            ]
            assert get_virtual_screen_rect().width > 0
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


if __name__ == "__main__":
    unittest.main()
