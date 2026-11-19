"""Prevent embedded feedback from diverging from desktop instance operations."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


class SharedFeedbackDependencyTests(unittest.TestCase):
    def test_both_desktop_backends_use_shared_instance_operations(self):
        root = Path(__file__).resolve().parents[1]
        for backend in ('qt/runtime', 'dx'):
            for category in ('particle', 'effect'):
                path = root / f'lib/core/render/backends/{backend}/{category}_system.py'
                tree = ast.parse(path.read_text(encoding='utf-8'))
                imported = {alias.name for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom) and node.module == 'lib.core.services.feedback_scene'
                            for alias in node.names}
                calls = {node.func.id for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
                for name in (f'create_{category}_instances', 'update_instances', f'interpolate_{category}s'):
                    self.assertIn(name, imported, path)
                    self.assertIn(name, calls, path)

    def test_shared_scene_has_no_toolkit_or_product_imports(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'lib/core/services/feedback_scene.py').read_text(encoding='utf-8'))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or '')
        for name in modules:
            self.assertFalse(name.startswith(('PyQt', 'PySide', 'lib.script', 'lib.core.render.backends')), name)


if __name__ == '__main__':
    unittest.main()
