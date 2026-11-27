"""File selection in application source must not call out to the native Qt dialog."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


class FilePickerBoundaryTests(unittest.TestCase):
    def test_application_has_no_native_qt_file_dialog(self):
        root = Path(__file__).resolve().parents[1]
        native_api = {
            "QFileDialog", "getOpenFileName", "getOpenFileNames", "getOpenFileUrl", "getOpenFileUrls",
            "getSaveFileName", "getSaveFileUrl", "getExistingDirectory", "getExistingDirectoryUrl",
        }
        matches = []
        for path in (root / "lib").rglob("*.py"):
            source = path.read_text(encoding="utf-8-sig")
            if not any(name in source for name in native_api):
                continue
            for node in ast.walk(ast.parse(source)):
                value = (
                    node.id if isinstance(node, ast.Name) else
                    node.name.rsplit(".", 1)[-1] if isinstance(node, ast.alias) else
                    node.attr if isinstance(node, ast.Attribute) else
                    node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else
                    None
                )
                if value in native_api:
                    matches.append(f"{path.relative_to(root)}:{node.lineno}: {value}")
        self.assertEqual(matches, [])
