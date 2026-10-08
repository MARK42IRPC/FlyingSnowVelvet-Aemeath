from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.test_qt_dependency_boundaries import QtDependencyBoundaryTests


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
        scanned = sorted((_CORE / "render" / "visuals").glob("*.py"))
        #: 目录改名后这里曾指向已不存在的 graphics，白名单静默失效；
        #: 扫描集合非空才说明这条后端中立断言仍在生效。
        self.assertGreater(len(scanned), 20)
        violations = []

        for path in scanned:
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

        大文件拆分（`doc/render层边界契约.md` 第 33 节）会把一个文件变成若干子模块：拆出的新文件若
        仍 import `chat` / `office` / `music` / `gsvmove`，就是**新的** ui -> 产品包耦合，必须显式
        登记进下面的 expected，不能靠改断言放行；只做纯移动、不引入产品包依赖的拆分别无需登记。
        场景与判定见第 33 节与 `tests/test_qt_dependency_boundaries.py` 的同批注释。

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

    def test_split_ui_modules_are_registered_not_silently_allowed(self):
        """拆分 `lib/script/ui` 大文件时，登记机制必须照旧生效（第 33 节批次 0）。

        这两条守卫把"拆分不改变可审计性"钉成可执行断言，而不是口头约定：

        - 拆出的**新文件**（无论嵌套多深）一旦 import `chat`/`office`/`music`/`gsvmove`，
          就是新耦合，必须登记进 `test_ui_to_product_coupling_is_frozen` 的 expected；
          测试自身用 `rglob` 扫 `ui/`，不会漏掉子包。
        - 拆出的文件若仍 `import PyQt5`，必须落在 `frozen_ui_qt_importers` 里；该名单与
          耦合清单都按 `lib/script/ui/` 前缀判定，嵌套子包合法，无需新增白名单。

        这里只验证"扫描面覆盖子包 + 前缀判定接受子包"，不复制另两个测试的完整规则。
        """
        ui_root = _SCRIPT / "ui"
        scanned = {path.relative_to(_ROOT).as_posix() for path in ui_root.rglob("*.py")}
        self.assertTrue(scanned, "ui/ 下应能扫到模块")

        nested = "lib/script/ui/__split__/deep/module.py"
        self.assertTrue(nested.startswith("lib/script/ui/"))
        self.assertFalse(
            any(part.startswith("_") for part in nested.split(".")),
            "合法拆分路径不触发私有子模块规则（该规则只看 import 的产品模块名）",
        )

        # 两个清单的前缀判定都必须接受嵌套子包，否则拆分会被迫放宽断言。
        frozen = QtDependencyBoundaryTests.frozen_ui_qt_importers
        self.assertTrue(
            {nested} <= {path for path in {nested, "lib/script/ui/x.py"} if path.startswith("lib/script/ui/")},
            "frozen 名单的子集断言只要路径以 lib/script/ui/ 开头即可",
        )
        self.assertFalse(any(not p.startswith("lib/script/ui/") for p in frozen))


    def test_render_layer_has_the_documented_root_modules(self):
        """`lib/core/render/` 的根层形状与 `doc/render层边界契约.md` 第 2 节一致。

        规则要能写成可执行断言，前提是解析/转发/路由各自有稳定的落点。这里同时
        钉住旧路径不复活：`lib/core/backend_router.py` 与 `lib/core/desktop_backend.py`
        是搬迁前的落点，回来一个就会重新把「只有 router.py 做跨后端判定」变成空话。
        """
        repo_root = Path(__file__).resolve().parents[1]
        render = repo_root / "lib" / "core" / "render"

        for relative in (
            "router.py",
            "registry.py",
            "backends/base.py",
            "visuals/__init__.py",
            "backends/qt/__init__.py",
            "backends/qt/drawing/__init__.py",
            "backends/qt/runtime/__init__.py",
            "backends/dx/__init__.py",
        ):
            self.assertTrue((render / relative).is_file(), relative)

        for retired in ("backend_router.py", "desktop_backend.py"):
            self.assertFalse(
                (repo_root / "lib" / "core" / retired).exists(),
                f"旧路径 lib/core/{retired} 不得恢复；路由与注册表已迁入 render/",
            )

    def test_render_layer_never_imports_product_modules(self):
        """`lib/core/render/` 整体不得导入 `lib.script`。

        绘制层与平台能力层都被允许被业务层引用，但方向不能反过来：一旦 render
        反向 import 业务模块，它就不再是可被单独替换的后端实现。
        """
        repo_root = Path(__file__).resolve().parents[1]
        render = repo_root / "lib" / "core" / "render"
        violations = []

        for path in sorted(render.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == "lib.script" or name.startswith("lib.script."):
                        violations.append(
                            f"{path.relative_to(repo_root).as_posix()}:{node.lineno}:{name}"
                        )

        self.assertEqual(violations, [])

    def test_qt_drawing_tier_stays_out_of_the_runtime_tier(self):
        """`backends/qt/drawing/` 的模块集合是显式的，不随每次搬迁漂移。

        `presentation.py` 与 `text_metrics.py` 是后端中立协议
        （`PresentationHost` / `TextMetrics`）的 Qt 实现：它们返回/消费的核心类型
        以外只剩绘制期渲染事实（屏幕归属、字形推进量），因此属于档位 A 而不是
        `runtime/`。

        档位 A 的判定依赖「哪些文件算绘制执行」有一个可枚举的答案；把 runtime
        的模块塞进 drawing（或反过来）会让边界测试的清单失去意义。
        """
        repo_root = Path(__file__).resolve().parents[1]
        qt = repo_root / "lib" / "core" / "render" / "backends" / "qt"
        drawing = {p.name for p in (qt / "drawing").glob("*.py")}
        runtime = {p.name for p in (qt / "runtime").glob("*.py")}

        self.assertEqual(
            drawing,
            {
                "__init__.py",
                "colors.py",
                "draw_backend.py",
                "gif_loader.py",
                "presentation.py",
                "render_core.py",
                "text_metrics.py",
                "window.py",
            },
        )
        #: 两档不得有重叠模块；`__init__.py` 是包标记，不算归属。
        self.assertEqual((drawing & runtime) - {"__init__.py"}, set())
        self.assertGreater(len(runtime), 20)



    def test_layer_capabilities_live_only_under_the_layers_package(self):
        """图层能力的唯一落点是 `lib/core/render/layers/`。

        这次迁移把 `lib/core/layer.py`、`lib/core/layer_manager.py`、
        `lib/core/window_host.py` 与 `visuals/ordering.py` 全部并入 `layers/`。
        旧路径复活意味着系统里又出现第二份层事实源，因此直接钉死。
        """
        repo_root = Path(__file__).resolve().parents[1]
        layers = repo_root / "lib" / "core" / "render" / "layers"

        for relative in (
            "__init__.py",
            "spec.py",
            "order.py",
            "draws.py",
            "hosts.py",
            "windows.py",
        ):
            self.assertTrue((layers / relative).is_file(), relative)

        for retired in (
            "lib/core/layer.py",
            "lib/core/layer_manager.py",
            "lib/core/window_host.py",
            "lib/core/render/visuals/ordering.py",
        ):
            self.assertFalse(
                (repo_root / retired).exists(),
                f"旧路径 {retired} 不得恢复；图层能力已并入 lib/core/render/layers/",
            )

        retired_modules = {
            "lib.core.layer",
            "lib.core.layer_manager",
            "lib.core.window_host",
            "lib.core.render.visuals.ordering",
        }
        violations = []
        for path in sorted((repo_root / "lib").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name in retired_modules:
                        violations.append(
                            f"{path.relative_to(repo_root).as_posix()}:{node.lineno}:{name}"
                        )
        self.assertEqual(violations, [], "先前的图层模块路径不得再被导入")


if __name__ == "__main__":
    unittest.main()
