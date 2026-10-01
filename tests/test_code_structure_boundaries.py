from __future__ import annotations

import ast
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_CORE = _ROOT / "lib" / "core"
_SCRIPT = _ROOT / "lib" / "script"
_COMPOSITION_EXCEPTIONS = {_CORE / "qt_desktop_pet.py"}


def _script_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if name == "lib.script" or name.startswith("lib.script."):
                imports.append((node.lineno, name))
    return imports


class CodeStructureBoundaryTests(unittest.TestCase):
    def test_core_does_not_import_product_modules(self):
        violations = []
        for path in _CORE.rglob("*.py"):
            if path in _COMPOSITION_EXCEPTIONS:
                continue
            for line, module in _script_imports(path):
                violations.append(f"{path.relative_to(_ROOT)}:{line}: {module}")
        self.assertEqual(violations, [])

    def test_product_composition_modules_live_in_script(self):
        expected = (
            _SCRIPT / "app" / "qt_application_ui.py",
            _SCRIPT / "app" / "workbench_helper_entry.py",
            _SCRIPT / "plugin_registry.py",
            _SCRIPT / "ui" / "animation_player.py",
            _SCRIPT / "ui" / "bug_tracker_window.py",
            _SCRIPT / "ui" / "game_manager_window.py",
            _SCRIPT / "ui" / "game_runtime.py",
            _SCRIPT / "ui" / "pet_window_ui.py",
            _SCRIPT / "ui" / "tray_icon.py",
            _SCRIPT / "ui" / "workbench_components.py",
            _SCRIPT / "ui" / "workbench_settings_layout.py",
            _SCRIPT / "ui" / "world_objects" / "speaker.py",
        )
        self.assertTrue(all(path.is_file() for path in expected))

    def test_core_voice_contains_only_generic_runtime(self):
        modules = {path.name for path in (_CORE / "voice").glob("*.py")}
        self.assertEqual(modules, {"__init__.py", "core.py", "random_sound.py"})

    def test_ai_settings_does_not_reach_into_tray_implementation(self):
        source = (_SCRIPT / "ui" / "ai_settings_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("lib.script.ui.tray_icon", source)

    def test_ui_painting_stays_inside_the_toolkit_layers(self):
        """`ui/` 之外不得自绘：只有 Qt bridge、`ui/` 与官方游戏包可以碰 QPainter。

        阶段 1~3 把产品画法收到 `lib/core/render/visuals` 的 presenter 里，执行器只剩 Qt bridge 与 DX
        bridge。业务脚本里出现 `QPainter`/`paintEvent` 意味着又有一处画法绕过了共享层。
        """
        allowed_prefixes = (
            "lib/core/render/backends/qt/",
            "lib/script/ui/",
            "lib/script/gemes/packages/official/lahai_tetris/",
        )
        #: 以 AST 判定而不是扫文本：粒子脚本在注释里描述“渲染层用 drawEllipse”，
        #: 那是文档而不是自绘。
        painter_names = ("QPainter", "QPainterPath", "QRegion")
        paint_methods = ("drawEllipse", "drawPixmap", "fillRect")
        violations = []

        for base in (_CORE, _SCRIPT):
            for path in base.rglob("*.py"):
                relative = path.relative_to(_ROOT).as_posix()
                if relative.startswith(allowed_prefixes):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id in painter_names:
                        violations.append(f"{relative}:{node.lineno}:{node.id}")
                    elif isinstance(node, ast.Attribute) and node.attr in paint_methods:
                        violations.append(f"{relative}:{node.lineno}:{node.attr}")
                    elif isinstance(node, ast.FunctionDef) and node.name == "paintEvent":
                        violations.append(f"{relative}:{node.lineno}:paintEvent")

        self.assertEqual(violations, [])

    def test_shared_visual_modules_are_backend_neutral(self):
        """Presenter 必须能在无 PyQt、无 DX 的进程里加载：它们是两个后端共用的事实源。"""
        forbidden = (
            "PyQt5",
            "lib.script",
            "lib.core.render.backends.qt",
            "lib.core.render.backends.dx",
            "config.config_ui",
        )
        violations = []

        for path in sorted((_CORE / "graphics").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if any(
                        name == item or name.startswith(item + ".")
                        for item in forbidden
                    ):
                        violations.append(f"{path.name}:{node.lineno}:{name}")

        self.assertEqual(violations, [])

    def test_ui_to_product_coupling_is_frozen(self):
        """`ui/` 只能消费产品包的公开接口，且现有耦合已被清点并冻结。

        阶段 4 不把这些导入一刀切掉（工具页本来就是产品表现层，拆接口是另一件事），而是把它变成一份可审计的清单：
        新增耦合会叫停，清单里的条目被删掉后也会叫停（防止清单腐烂）。私有子模块一律不允许。
        """
        expected = {
            ("lib/script/ui/ai_settings_panel.py", "lib.script.chat.handler_auto_companion"),
            ("lib/script/ui/ai_settings_panel.py", "lib.script.chat.ollama_registry"),
            ("lib/script/ui/ai_settings_panel.py", "lib.script.chat.persona_storage"),
            ("lib/script/ui/ai_settings_panel.py", "lib.script.gsvmove"),
            ("lib/script/ui/ai_settings_panel.py", "lib.script.music"),
            ("lib/script/ui/ai_settings_storage.py", "lib.script.chat.ollama"),
            ("lib/script/ui/game_runtime.py", "lib.script.music.service"),
            ("lib/script/ui/office_approval_controller.py", "lib.script.office.ipc"),
            ("lib/script/ui/office_effort_slider.py", "lib.script.office.contracts"),
            ("lib/script/ui/office_mode_page.py", "lib.script.office"),
            ("lib/script/ui/office_mode_settings.py", "lib.script.chat.network_policy"),
            ("lib/script/ui/office_mode_settings.py", "lib.script.office"),
            ("lib/script/ui/office_page.py", "lib.script.office.contracts"),
            ("lib/script/ui/office_page.py", "lib.script.office.ipc"),
            ("lib/script/ui/office_page.py", "lib.script.office.service"),
            ("lib/script/ui/office_page.py", "lib.script.office.workspace"),
            ("lib/script/ui/office_style.py", "lib.script.office.contracts"),
            ("lib/script/ui/playlist_panel.py", "lib.script.music"),
            ("lib/script/ui/speaker_control_buttons.py", "lib.script.music"),
            ("lib/script/ui/speaker_search_dialog.py", "lib.script.music"),
            ("lib/script/ui/speaker_search_dialog.py", "lib.script.music.track_text"),
            ("lib/script/ui/speaker_volume_slider.py", "lib.script.music"),
            ("lib/script/ui/voice_package_installer.py", "lib.script.gsvmove"),
            ("lib/script/ui/voice_package_installer.py", "lib.script.gsvmove.package_manager"),
        }
        product_packages = ("chat", "office", "music", "gsvmove")
        found = set()

        for path in sorted((_SCRIPT / "ui").rglob("*.py")):
            relative = path.relative_to(_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if not any(
                        name == f"lib.script.{package}"
                        or name.startswith(f"lib.script.{package}.")
                        for package in product_packages
                    ):
                        continue
                    found.add((relative, name))

        self.assertEqual(sorted(found - expected), [], "新增的 ui -> 产品包耦合")
        self.assertEqual(sorted(expected - found), [], "清单里已不存在的耦合")

        private = sorted(
            item for item in found if any(part.startswith("_") for part in item[1].split(".")[3:])
        )
        self.assertEqual(private, [], "私有子模块不得被 ui 依赖")


if __name__ == "__main__":
    unittest.main()
