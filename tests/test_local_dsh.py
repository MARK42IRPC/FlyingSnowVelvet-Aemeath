from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core import dsh_runtime_contract as dsh_config
from lib.script.office import local_dsh


def _write_package(node_modules: Path, name: str, payload: dict) -> Path:
    directory = node_modules / "@deepseek-ai" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _build_install(
    root: Path,
    *,
    version: str | None = None,
    packages=None,
    with_entry: bool = True,
    package_name: str = "@deepseek-ai/dsh",
) -> Path:
    node_modules = root / "node_modules"
    resolved_version = version or dsh_config.DSH_VERSION
    for name in dsh_config.REQUIRED_DSH_PACKAGES if packages is None else packages:
        _write_package(
            node_modules,
            name,
            {"name": f"@deepseek-ai/{name}", "version": resolved_version},
        )
    dsh_root = node_modules / "@deepseek-ai" / "dsh"
    if with_entry:
        (dsh_root / "lib").mkdir(parents=True, exist_ok=True)
        (dsh_root / "lib" / "bin.js").write_text("// entry\n", encoding="utf-8")
    (dsh_root / "package.json").write_text(
        json.dumps(
            {
                "name": package_name,
                "version": resolved_version,
                "bin": {"dsh": "lib/bin.js"},
            }
        ),
        encoding="utf-8",
    )
    return root


class LocalDshProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._npm = patch.object(local_dsh, "_npm_global_prefix", return_value=None)
        self._npm.start()
        self.addCleanup(self._npm.stop)
        self._which = patch.object(local_dsh.shutil, "which", return_value=None)
        self._which.start()
        self.addCleanup(self._which.stop)
        local_dsh.reset_local_dsh_cache()
        self.addCleanup(local_dsh.reset_local_dsh_cache)

    def _use_override(self, root: Path) -> None:
        patcher = patch.dict(os.environ, {local_dsh.LOCAL_DSH_ENV: str(root)}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        local_dsh.reset_local_dsh_cache()

    def test_is_supported_version_matches_builtin_minor(self):
        self.assertTrue(local_dsh.is_supported_version(dsh_config.DSH_VERSION))
        self.assertTrue(local_dsh.is_supported_version("v0.1.9"))
        self.assertFalse(local_dsh.is_supported_version("0.2.0"))
        self.assertFalse(local_dsh.is_supported_version(""))

    def test_probe_finds_override_install(self):
        root = _build_install(self.root / "global")
        self._use_override(root)

        install = local_dsh.probe_local_dsh()

        self.assertIsNotNone(install)
        self.assertTrue(install.supported)
        self.assertEqual(install.version, dsh_config.DSH_VERSION)
        self.assertEqual(install.source, local_dsh.LOCAL_DSH_ENV)
        self.assertEqual(install.node_modules_root, root / "node_modules")
        self.assertEqual(
            install.entry,
            (root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").resolve(),
        )

    def test_probe_rejects_version_mismatch(self):
        root = _build_install(self.root / "global", version="0.2.3")
        self._use_override(root)

        self.assertIsNone(local_dsh.probe_local_dsh())

        status = local_dsh.local_dsh_status(project_root=self.root)
        self.assertFalse(status["available"])
        self.assertIn("版本不受支持", status["reason"])

    def test_probe_rejects_missing_packages(self):
        packages = [name for name in dsh_config.REQUIRED_DSH_PACKAGES if name != "dsh-llm"]
        root = _build_install(self.root / "global", packages=packages)
        self._use_override(root)

        self.assertIsNone(local_dsh.probe_local_dsh())

        status = local_dsh.local_dsh_status(project_root=self.root)
        self.assertIn("依赖不完整", status["reason"])
        self.assertIn("@deepseek-ai/dsh-llm", status["reason"])

    def test_probe_rejects_missing_entry(self):
        root = _build_install(self.root / "global", with_entry=False)
        self._use_override(root)

        self.assertIsNone(local_dsh.probe_local_dsh())
        self.assertIn("入口缺失", local_dsh.local_dsh_status(project_root=self.root)["reason"])

    def test_probe_rejects_unrelated_package(self):
        root = _build_install(self.root / "global", package_name="some-other-tool")
        self._use_override(root)

        self.assertIsNone(local_dsh.probe_local_dsh())

    def test_probe_caches_result(self):
        root = _build_install(self.root / "global")
        self._use_override(root)
        first = local_dsh.probe_local_dsh()

        manifest_path = root / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
        manifest_path.unlink()

        self.assertIs(local_dsh.probe_local_dsh(), first)

    def test_status_requires_node_runtime(self):
        root = _build_install(self.root / "global")
        self._use_override(root)
        with patch.object(local_dsh, "resolve_local_node_executable", return_value=None):
            status = local_dsh.local_dsh_status(project_root=self.root)

        self.assertFalse(status["available"])
        self.assertEqual(status["version"], dsh_config.DSH_VERSION)
        self.assertIn("Node", status["reason"])

    def test_status_reports_missing_install(self):
        self._use_override(self.root / "empty")

        status = local_dsh.local_dsh_status(project_root=self.root)

        self.assertFalse(status["available"])
        self.assertIn("未探测到", status["reason"])


if __name__ == "__main__":
    unittest.main()
