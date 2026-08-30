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
            _SCRIPT / "ui" / "pet_window_ui.py",
            _SCRIPT / "ui" / "tray_icon.py",
            _SCRIPT / "ui" / "world_objects" / "speaker.py",
        )
        self.assertTrue(all(path.is_file() for path in expected))

    def test_core_voice_contains_only_generic_runtime(self):
        modules = {path.name for path in (_CORE / "voice").glob("*.py")}
        self.assertEqual(modules, {"__init__.py", "core.py", "random_sound.py"})

    def test_ai_settings_does_not_reach_into_tray_implementation(self):
        source = (_SCRIPT / "ui" / "ai_settings_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("lib.script.ui.tray_icon", source)


if __name__ == "__main__":
    unittest.main()
