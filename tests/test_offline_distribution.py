import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import build_offline_distribution as distribution
from scripts import build_offline_installer as installer


class OfflineDistributionTests(unittest.TestCase):
    def test_installer_font_subset_is_compact_and_keeps_visible_copy(self):
        from scripts.build_offline_installer import create_installer_font_subset
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "installer.ttf"
            create_installer_font_subset(output)
            self.assertLess(output.stat().st_size, 200_000)
            self.assertGreater(output.stat().st_size, 20_000)
    def test_distribution_state_round_trips_and_resume_requires_same_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            python_home = root / "python"
            site = root / "site"
            node = root / "node"
            modules = root / "modules"
            wheel = root / "directml.whl"
            for path in (source, python_home, site, node, modules):
                path.mkdir()
            wheel.write_bytes(b"wheel")
            state = distribution._distribution_build_state(
                source=source,
                python_home=python_home,
                site_packages_sources=(site,),
                node_runtime=node,
                node_modules=modules,
                directml_wheel=wheel,
                without_music=False,
            )
            workspace = root / "workspace"
            workspace.mkdir()
            distribution._write_distribution_state(workspace, state)
            self.assertEqual(distribution._read_distribution_state(workspace), state)
            wheel.write_bytes(b"changed")
            changed = distribution._distribution_build_state(
                source=source,
                python_home=python_home,
                site_packages_sources=(site,),
                node_runtime=node,
                node_modules=modules,
                directml_wheel=wheel,
                without_music=False,
            )
            self.assertNotEqual(changed, state)

            nested = source / "nested" / "input.txt"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"one")
            before_nested = distribution._distribution_build_state(
                source=source,
                python_home=python_home,
                site_packages_sources=(site,),
                node_runtime=node,
                node_modules=modules,
                directml_wheel=wheel,
                without_music=False,
            )
            original_stat = nested.stat()
            nested.write_bytes(b"two")
            # Preserve the timestamp and size to ensure the content digest,
            # rather than directory metadata, detects the changed input.
            os.utime(
                nested,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            after_nested = distribution._distribution_build_state(
                source=source,
                python_home=python_home,
                site_packages_sources=(site,),
                node_runtime=node,
                node_modules=modules,
                directml_wheel=wheel,
                without_music=False,
            )
            self.assertNotEqual(after_nested, before_nested)

    def test_resume_validation_rejects_missing_or_changed_payload_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            payload = workspace / "payload"
            payload.mkdir(parents=True)
            marker = payload / distribution.PAYLOAD_MARKER_NAME
            marker.write_bytes(distribution.PAYLOAD_MARKER_BYTES)
            content = payload / "app" / "data.txt"
            content.parent.mkdir(parents=True)
            content.write_bytes(b"payload")
            manifest = [
                {
                    "path": item.relative_to(payload).as_posix(),
                    "size": item.stat().st_size,
                    "sha256": distribution.sha256(item),
                }
                for item in sorted(payload.rglob("*"))
                if item.is_file()
            ]
            workspace.mkdir(exist_ok=True)
            (workspace / "manifest.json").write_text(
                json.dumps({"files": manifest}), encoding="utf-8"
            )
            self.assertTrue(
                distribution._is_complete_staged_distribution(workspace, payload)
            )
            content.write_bytes(b"changed")
            self.assertFalse(
                distribution._is_complete_staged_distribution(workspace, payload)
            )

    def test_python_runtime_keeps_sqlite_for_bundled_nltk_frontend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_home = root / "python-home"
            runtime = root / "runtime"
            for name in (
                "python.exe",
                "pythonw.exe",
                "python311.dll",
                "python3.dll",
                "vcruntime140.dll",
                "vcruntime140_1.dll",
            ):
                path = python_home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"runtime")
            (python_home / "Lib" / "sqlite3").mkdir(parents=True)
            (python_home / "Lib" / "sqlite3" / "__init__.py").write_text(
                "from _sqlite3 import *\n",
                encoding="utf-8",
            )
            (python_home / "DLLs").mkdir()
            for name in ("_sqlite3.pyd", "sqlite3.dll"):
                (python_home / "DLLs" / name).write_bytes(b"sqlite")

            distribution.copy_python_runtime(python_home, runtime)

            self.assertTrue((runtime / "Lib" / "sqlite3" / "__init__.py").is_file())
            self.assertTrue((runtime / "DLLs" / "_sqlite3.pyd").is_file())
            self.assertTrue((runtime / "DLLs" / "sqlite3.dll").is_file())

    def test_agent_and_seanima_directories_are_release_resources(self):
        self.assertFalse(distribution.excluded(Path("resc/agent/office_system_prompt.txt")))
        self.assertFalse(distribution.excluded(Path("resc/GIF/SEanima/demo/0001.webp")))
        self.assertTrue(distribution.excluded(Path("resc/GIF/SEanima.zip")))
        self.assertTrue(distribution.excluded(Path("build/offline-release/workspace/payload.zip")))
        self.assertTrue(distribution.excluded(Path(".venv/Lib/site-packages/runtime.py")))

    def test_generated_release_batch_is_ascii_and_uses_launcher_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_root = Path(tmpdir) / "app"
            distribution.write_release_launcher_config(app_root)
            raw = (app_root / "启动程序.bat").read_bytes()
            self.assertTrue(raw.startswith(b"@echo off"))
            self.assertNotIn(b"\xef\xbb\xbf", raw)
            self.assertTrue(all(byte < 128 for byte in raw))
            self.assertIn(b"FlyingSnowVelvetLauncher.exe", raw)

    def test_installer_rejects_version_different_from_workspace_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "manifest.json").write_text(
                json.dumps({"version": "LTS1"}), encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                installer.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--version",
                        "LTS2",
                    ]
                )

    def test_manifest_keeps_seanima_directory_and_excludes_zip_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = Path(tmpdir) / "payload"
            frame = payload / "app" / "resc" / "GIF" / "SEanima" / "demo" / "0001.webp"
            frame.parent.mkdir(parents=True)
            frame.write_bytes(b"frame")
            archive = payload / "app" / "resc" / "GIF" / "SEanima.zip"
            archive.write_bytes(b"legacy archive")
            paths = {relative for _, relative in installer._archive_entries(payload)}
            self.assertIn("app/resc/GIF/SEanima/demo/0001.webp", paths)
            self.assertNotIn("app/resc/GIF/SEanima.zip", paths)

    def test_directml_wheel_is_expanded_as_minimal_isolated_overlay(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wheel_path = root / distribution.DIRECTML_WHEEL_NAME
            dist_info = "onnxruntime_directml-1.22.0.dist-info"
            with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as wheel:
                for name in distribution.DIRECTML_RUNTIME_FILES:
                    wheel.writestr(name, b"runtime")
                wheel.writestr(
                    f"{dist_info}/METADATA",
                    "Metadata-Version: 2.1\nName: onnxruntime-directml\nVersion: 1.22.0\n",
                )
                wheel.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\n")
                wheel.writestr("onnxruntime/tools/unused.py", "raise AssertionError\n")

            details = distribution.stage_directml_runtime(wheel_path, root / "payload")
            runtime_root = root / "payload" / distribution.DIRECTML_RUNTIME_DIRECTORY
            site_packages = runtime_root / "Lib" / "site-packages"
            marker = json.loads(
                (runtime_root / distribution.DIRECTML_MARKER_NAME).read_text(encoding="utf-8")
            )

            self.assertTrue(details["bundled"])
            self.assertEqual(marker["format"], "fsv-bundled-directml-overlay")
            self.assertTrue((site_packages / "onnxruntime" / "capi" / "DirectML.dll").is_file())
            self.assertFalse((site_packages / "onnxruntime" / "tools").exists())
            self.assertFalse(any((root / "payload").rglob("*.whl")))

    def test_directml_wheel_rejects_path_traversal(self):
        with self.assertRaises(RuntimeError):
            distribution._safe_wheel_member("../outside.dll")

    def test_node_pruning_keeps_runtime_and_licenses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            files = {
                "package/index.js": b"module.exports = 1;",
                "package/index.js.map": b"map",
                "package/index.d.ts": b"declare const value: number;",
                "package/native.pdb": b"symbols",
                "package/src/native.cc": b"build source",
                "package/test/unit.js": b"test",
                "package/examples/demo.js": b"example",
                "package/README.md": b"documentation",
                "package/LICENSE.md": b"license",
                "@img/sharp-wasm32/lib/sharp.node.wasm": b"wasm",
                "@img/sharp-win32-x64/lib/sharp.node": b"native",
                "node-pty/prebuilds/win32-arm64/pty.node": b"arm64",
                "node-pty/prebuilds/win32-x64/pty.node": b"x64",
                "node-pty/third_party/conpty/OpenConsole.exe": b"build copy",
                "koffi/src/koffi/src/static.js": b"export const config = {};",
                "koffi/src/koffi/src/static.cjs": b"module.exports = {};",
                "koffi/src/koffi/src/trampolines.cjs": b"module.exports = {};",
                "koffi/src/koffi/src/call.cc": b"build source",
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            result = distribution.prune_node_modules(root)

            self.assertEqual(result["removed_files"], 11)
            self.assertTrue((root / "package" / "index.js").is_file())
            self.assertTrue((root / "package" / "LICENSE.md").is_file())
            self.assertTrue((root / "@img" / "sharp-win32-x64" / "lib" / "sharp.node").is_file())
            self.assertTrue((root / "node-pty" / "prebuilds" / "win32-x64" / "pty.node").is_file())
            self.assertTrue((root / "koffi" / "src" / "koffi" / "src" / "static.js").is_file())
            self.assertTrue((root / "koffi" / "src" / "koffi" / "src" / "static.cjs").is_file())
            self.assertTrue((root / "koffi" / "src" / "koffi" / "src" / "trampolines.cjs").is_file())
            self.assertFalse((root / "koffi" / "src" / "koffi" / "src" / "call.cc").exists())
            self.assertFalse((root / "package" / "index.d.ts").exists())
            self.assertFalse((root / "package" / "test").exists())
            self.assertFalse((root / "@img" / "sharp-wasm32").exists())
            self.assertFalse((root / "node-pty" / "third_party").exists())

    def test_playwright_node_is_shared_only_when_hash_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            site = root / "site-packages"
            private_node = site / "playwright" / "driver" / "node.exe"
            shared_node = root / "node.exe"
            private_node.parent.mkdir(parents=True, exist_ok=True)
            private_node.write_bytes(b"same-node")
            shared_node.write_bytes(b"same-node")

            self.assertTrue(distribution.share_playwright_node(site, shared_node))
            self.assertFalse(private_node.exists())

            private_node.parent.mkdir(parents=True, exist_ok=True)
            private_node.write_bytes(b"different-node")
            with self.assertRaises(RuntimeError):
                distribution.share_playwright_node(site, shared_node)
            self.assertTrue(private_node.exists())

    def test_python_pruning_removes_only_explicit_nonruntime_trees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative in (
                "playwright/async_api/client.py",
                "playwright/driver/package/types/types.d.ts",
                "pythonwin/pywin.py",
                "win32comext/shell/__init__.py",
                "isapi/README.txt",
                "adodbapi/README.txt",
                "PyWin32.chm",
                "win32com/client/__init__.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            result = distribution.prune_python_nonruntime_artifacts(root)

            self.assertEqual(result["removed_files"], 7)
            self.assertTrue((root / "win32com" / "client" / "__init__.py").is_file())
            self.assertFalse((root / "playwright" / "async_api").exists())
            self.assertFalse((root / "win32comext").exists())


if __name__ == "__main__":
    unittest.main()
