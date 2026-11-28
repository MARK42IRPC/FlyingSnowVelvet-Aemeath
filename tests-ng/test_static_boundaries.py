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

    def test_message_boxes_are_owned_by_render_host(self):
        """业务 Qt 宿主不得各自复制 QMessageBox；只允许消息框 render host 直接构造。"""
        allowed = {
            CORE / "render" / "backends" / "qt" / "widgets" / "message_box_host.py",
        }
        violations = []
        for path in (CORE / "render" / "backends" / "qt" / "widgets").rglob("*.py"):
            if path in allowed:
                continue
            source = path.read_text(encoding="utf-8-sig")
            if "QMessageBox" in source:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_shared_message_box_palette_is_not_duplicated_in_business_qss(self):
        """确认/提示框颜色与按钮尺寸只由 message_box 视觉规格维护。"""
        forbidden = (
            "QMessageBox#OfficeConfirmDialog",
            "QMessageBox#VoicePackageConfirmRemove",
            "QMessageBox QPushButton#VoicePackageConfirmRemove",
        )
        violations = []
        for path in (CORE / "render").rglob("*.py"):
            if path == CORE / "render" / "visuals" / "message_box.py":
                continue
            source = path.read_text(encoding="utf-8-sig")
            for marker in forbidden:
                if marker in source:
                    violations.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(violations, [])

    def test_popup_styles_have_a_single_source(self):
        """所有弹窗共用公告壳：样式只能由 announcement_chrome 产出。"""
        chrome = (CORE / "render" / "visuals" / "announcement_chrome.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("def announcement_stylesheet(", chrome)
        self.assertIn("def announcement_shell_spec(", chrome)

        specs = (CORE / "render" / "visuals" / "window_specs.py").read_text(
            encoding="utf-8-sig"
        )
        for forbidden in ("office_stylesheet", "floating_window_stylesheet", "stylesheet="):
            self.assertNotIn(forbidden, specs)

        # 产品窗口不得各自拼 QSS，也不得再引用旧窗口样式工厂。
        for relative in (
            "lib/script/ui/announcement_dialog.py",
            "lib/script/ui/help_window.py",
            "lib/script/ui/update_dialog.py",
            "lib/script/ui/office_approval_dialog.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8-sig")
            for forbidden in (
                "office_stylesheet",
                "floating_window_stylesheet",
                "announcement_stylesheet(",
            ):
                self.assertNotIn(forbidden, f"{relative}: {source}")

    def test_message_box_styles_delegate_to_the_shared_shell(self):
        message_box = (CORE / "render" / "visuals" / "message_box.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("return announcement_stylesheet(mode)", message_box)

    def test_button_feedback_has_one_of_two_explicit_render_paths(self):
        """按钮反馈只能是共享模态消息框或 INFORMATION 气泡，不得新造裸提醒窗口。"""
        host = CORE / "render" / "backends" / "qt" / "widgets" / "message_box_host.py"
        spec_host_path = CORE / "render" / "backends" / "qt" / "widgets" / "spec_host.py"
        spec_host = spec_host_path.read_text(encoding="utf-8-sig")
        host_source = host.read_text(encoding="utf-8-sig")
        self.assertIn("def ask_confirmation(", host_source)
        self.assertIn("def show_message(", host_source)
        dialog_module = (SCRIPT / "ui" / "confirm_dialog.py").read_text(encoding="utf-8-sig")
        bridge_source = (SCRIPT / "ui" / "render_bridge.py").read_text(encoding="utf-8-sig")
        self.assertIn("from lib.script.ui.render_bridge import ask_confirmation, show_message", dialog_module)
        self.assertIn("def ask_confirmation(", bridge_source)
        announcement_module = (SCRIPT / "ui" / "announcement_dialog.py").read_text(encoding="utf-8-sig")
        self.assertIn("AnnouncementService(", announcement_module)
        self.assertIn("on_loading=self._on_loading", announcement_module)
        self.assertIn("on_document=self._on_document", announcement_module)
        self.assertIn("announcement_requested.connect(self.open_from_tray)", announcement_module)
        message_box = (CORE / "render" / "visuals" / "message_box.py").read_text(encoding="utf-8-sig")
        self.assertIn("announcement_shell_spec", message_box)
        self.assertIn("announcement_stylesheet", message_box)
        announcement_chrome = (
            CORE / "render" / "visuals" / "announcement_chrome.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("fade=fade", announcement_chrome)
        self.assertIn("fade=True if fade is None else bool(fade)", message_box)
        self.assertIn("def request_modal_done", spec_host)
        self.assertIn("_modal_loop", spec_host)
        self.assertIn("def exec_modal(", spec_host)
        self.assertIn("self.center_on()", spec_host)
        popup_design = (CORE / "render" / "popup_design.py").read_text(encoding="utf-8-sig")
        self.assertIn("px(34, 30)", popup_design)
        self.assertIn("QPushButton[windowActionRole=\"danger\"]", announcement_chrome)
        bubble = SCRIPT / "ui" / "bubble.py"
        bubble_source = bubble.read_text(encoding="utf-8-sig")
        self.assertIn("EventType.INFORMATION", bubble_source)

        violations = []
        for path in (CORE / "render" / "backends" / "qt" / "widgets").rglob("*.py"):
            if path == host:
                continue
            source = path.read_text(encoding="utf-8-sig")
            if "QMessageBox" in source:
                violations.append(str(path.relative_to(ROOT)))
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
