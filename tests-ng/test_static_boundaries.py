"""Static audits: what may import what, and what an offline payload may contain.

These tests read source text, not live objects, so they run in the plain
process without a QApplication.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "lib" / "core"
SCRIPT = ROOT / "lib" / "script"
QT_BACKEND = CORE / "render" / "backends" / "qt"


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            found.append((node.lineno, name))
    return found


class LayerBoundaryTests(unittest.TestCase):
    def test_core_does_not_import_product_modules(self):
        violations = []
        for path in CORE.rglob("*.py"):
            if path == CORE / "qt_desktop_pet.py":
                continue  # product composition entry point
            for line, module in _imports(path):
                if module == "lib.script" or module.startswith("lib.script."):
                    violations.append(f"{path.relative_to(ROOT)}:{line}: {module}")
        self.assertEqual(violations, [])

    def test_backend_neutral_core_does_not_import_qt(self):
        violations = []
        for path in (ROOT / "config").rglob("*.py"):
            violations.extend(self._qt_violations(path))
        for path in CORE.rglob("*.py"):
            if QT_BACKEND in path.parents:
                continue
            if path == CORE / "render" / "qt_bridge.py":
                continue  # the single Qt adapter inside render
            violations.extend(self._qt_violations(path))
        self.assertEqual(violations, [])

    def _qt_violations(self, path: Path) -> list[str]:
        hits = []
        for line, module in _imports(path):
            if (
                module == "PyQt5"
                or module.startswith("PyQt5.")
                or module == "lib.core.render.backends.qt"
                or module.startswith("lib.core.render.backends.qt.")
            ):
                hits.append(f"{path.relative_to(ROOT)}:{line}: {module}")
        return hits

    def test_ui_package_does_not_import_qt_directly(self):
        violations = []
        for path in (SCRIPT / "ui").rglob("*.py"):
            for line, module in _imports(path):
                if module == "PyQt5" or module.startswith("PyQt5."):
                    violations.append(f"{path.relative_to(ROOT)}:{line}: {module}")
        self.assertEqual(violations, [])


class PayloadBoundaryTests(unittest.TestCase):
    def test_offline_payload_excludes_sources_that_only_help_development(self):
        from scripts.build_offline_distribution import excluded

        for relative in (
            "tests/test_office_mode_page.py",
            "legacy_tests/test_office_mode_page.py",
            "tests-ng/test_qt_office_window.py",
            "scripts/run_tests.py",
            "fulltest.log",
            "py.ini",
            ".localpage/index.html",
            "services/dsh-office-runtime/node_modules/package.json",
        ):
            with self.subTest(relative=relative):
                self.assertTrue(excluded(Path(relative)), relative)


class SuiteIsolationTests(unittest.TestCase):
    def test_qt_runner_never_uses_taskkill(self):
        script = (ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8")
        self.assertNotIn("taskkill", script)
        self.assertNotIn("tasklist", script)

    def test_release_builder_does_not_import_test_packages(self):
        for name in ("build_offline_distribution.py", "build_offline_installer.py"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("import tests", source, name)
            self.assertNotIn("from tests", source, name)


if __name__ == "__main__":
    unittest.main()
