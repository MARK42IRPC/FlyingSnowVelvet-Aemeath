"""AI 设置族整族出列（第 77 轮，批次 2）。

`lib/script/ui/ai_settings_*` 按《render 层边界契约》第 3 节档位归类后一次性出列：

- 纯逻辑（无 Qt、无产品包）：`config_schema` / `labels` / `config_parse` / `validation` /
  `validators` / `defaults` / `contributions` / `descriptions` → `lib/core/ai_settings/`；
- Qt 件：`ai_settings_tabs` / `ai_settings_editors` / `ai_settings_page_sections` /
  `ai_settings_about` / `ai_settings_config_page` / `ai_settings_config_store` /
  `ai_settings_page` / `ai_settings_update` / `ai_settings_shell` → `backends/qt/widgets/`；
- 产品侧总装 `ai_settings_panel.py` 保持为垫片，五个 mixin 经 `render_bridge` 解析。

本模块钉住族级不变量：

- 九个产品垫片都不含 `PyQt5`，也不静态 import 档位 D（名字与 seam 注入都经 `render_bridge`）；
- 档位 D 宿主不 import `lib.script`；
- 八个 core 逻辑模块后端中立；
- `AISettingsPanel` 的继承面（五个 mixin）与关键公开符号解析到宿主。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT = _REPO_ROOT / "lib" / "script" / "ui"
_TIER_D = _REPO_ROOT / "lib" / "core" / "render" / "backends" / "qt" / "widgets"
_CORE = _REPO_ROOT / "lib" / "core" / "ai_settings"

#: 除总装垫片外，全部垫片都不含 `PyQt5`；`ai_settings_panel.py` 是产品侧总装，仍以 `QWidget`
#: 作多继承基类（它是本条边界内的真实 Qt 使用者，仍留在 `frozen_ui_qt_importers` 里）。
_PRODUCT_FILES = (
    "ai_settings_about.py",
    "ai_settings_config_page.py",
    "ai_settings_config_store.py",
    "ai_settings_editors.py",
    "ai_settings_page.py",
    "ai_settings_page_sections.py",
    "ai_settings_shell.py",
    "ai_settings_tabs.py",
    "ai_settings_update.py",
)
_TIER_D_FILES = (
    "ai_settings_about.py",
    "ai_settings_config_page.py",
    "ai_settings_config_store.py",
    "ai_settings_editors.py",
    "ai_settings_page.py",
    "ai_settings_page_sections.py",
    "ai_settings_shell.py",
    "ai_settings_tabs.py",
    "ai_settings_update.py",
)
_CORE_FILES = (
    "config_schema.py",
    "labels.py",
    "config_parse.py",
    "validation.py",
    "validators.py",
    "defaults.py",
    "contributions.py",
    "descriptions.py",
)


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class AISettingsFamilyTests(unittest.TestCase):
    def test_product_shims_are_not_qt_or_tier_d_implementations(self):
        for name in _PRODUCT_FILES:
            with self.subTest(module=name):
                path = _PRODUCT / name
                self.assertTrue(path.is_file(), name)
                names = _imported_names(path)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [], name
                )
                self.assertEqual(
                    [n for n in names if n.startswith("lib.core.render.backends.qt")],
                    [],
                    f"{name} 垫片不得静态 import 档位 D",
                )

    def test_tier_d_hosts_never_import_product_modules(self):
        for name in _TIER_D_FILES:
            with self.subTest(module=name):
                names = _imported_names(_TIER_D / name)
                self.assertEqual(
                    [n for n in names if n == "lib.script" or n.startswith("lib.script.")], []
                )

    def test_core_logic_modules_are_backend_neutral(self):
        for name in _CORE_FILES:
            with self.subTest(module=name):
                names = _imported_names(_CORE / name)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], []
                )
                self.assertEqual(
                    [n for n in names if n == "lib.script" or n.startswith("lib.script.")], []
                )

    def test_public_names_resolve_to_the_host(self):
        from lib.core.render.backends.qt.widgets import ai_settings_config_page as host_cfg
        from lib.core.render.backends.qt.widgets import ai_settings_shell as host_shell
        from lib.script.ui import ai_settings_config_page as product_cfg
        from lib.script.ui import ai_settings_shell as product_shell

        self.assertIs(product_shell.AISettingsShellMixin, host_shell.AISettingsShellMixin)
        self.assertIs(product_cfg.ConfigPageMixin, host_cfg.ConfigPageMixin)

    def test_panel_keeps_its_mixin_inheritance(self):
        from lib.script.ui.ai_settings_panel import AISettingsPanel

        mro = [cls.__name__ for cls in AISettingsPanel.__mro__]
        for name in (
            "AISettingsShellMixin",
            "AISettingsPageMixin",
            "ConfigStoreMixin",
            "ConfigPageMixin",
            "ConfigEditorMixin",
        ):
            self.assertIn(name, mro, name)
        # 外壳的 `paintEvent` 仍来自 host，而不是面板自己重写。
        from lib.core.render.backends.qt.widgets.ai_settings_shell import AISettingsShellMixin

        self.assertIs(AISettingsPanel.paintEvent, AISettingsShellMixin.paintEvent)

    def test_panel_project_root_hook_overrides_every_split_module(self):
        from lib.script.ui import ai_settings_panel as panel_module
        from lib.script.ui.ai_settings_panel import AISettingsPanel

        root = panel_module._project_root()
        for hook in (
            "_shell_project_root",
            "_editor_project_root",
            "_page_project_root",
            "_store_project_root",
        ):
            self.assertEqual(getattr(AISettingsPanel, hook)(), root, hook)


if __name__ == "__main__":
    unittest.main()
