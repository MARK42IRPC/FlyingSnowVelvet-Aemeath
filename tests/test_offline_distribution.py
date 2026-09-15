import json
import hashlib
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

    def test_distribution_state_samples_content_instead_of_hashing_every_file(self):
        from unittest import mock

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
            package = python_home / "Lib" / "site-packages"
            package.mkdir(parents=True)
            installed = package / "huge.pyd"
            installed.write_bytes(b"p" * 4096)
            chunk = distribution.FINGERPRINT_SAMPLE_CHUNK_BYTES
            big = source / "big.bin"
            big.write_bytes(b"a" * (chunk * 3))

            def state():
                return distribution._distribution_build_state(
                    source=source,
                    python_home=python_home,
                    site_packages_sources=(site,),
                    node_runtime=node,
                    node_modules=modules,
                    directml_wheel=wheel,
                    without_music=False,
                )

            before = state()
            # 每个文件最多读一个采样块，整棵树不超过采样预算。
            self.assertEqual(before["source"]["total_bytes"], chunk * 3)
            self.assertEqual(before["source"]["sampled_bytes"], chunk)
            # 解释器自带的 site-packages 不是 payload 输入，不进解释器指纹。
            self.assertEqual(before["python_home"]["file_count"], 0)
            original_installed = installed.stat()
            installed.write_bytes(b"q" * 4096)
            os.utime(
                installed,
                ns=(original_installed.st_atime_ns, original_installed.st_mtime_ns),
            )
            self.assertEqual(state()["python_home"], before["python_home"])
            # 大文件只采样头部，保持大小与 mtime 不变地改内容也要改变指纹。
            original_big = big.stat()
            data = bytearray(big.read_bytes())
            data[0] = ord("b")
            big.write_bytes(bytes(data))
            os.utime(big, ns=(original_big.st_atime_ns, original_big.st_mtime_ns))
            self.assertNotEqual(state()["source"]["sha256"], before["source"]["sha256"])
            # 采样预算按整棵输入树封顶，不会把树整个读一遍。
            for index in range(3):
                (source / f"bulk-{index}.bin").write_bytes(b"z" * chunk)
            with mock.patch.object(
                distribution, "FINGERPRINT_SAMPLE_BUDGET_BYTES", chunk * 2
            ):
                capped = state()
            self.assertEqual(capped["source"]["sampled_bytes"], chunk * 2)
            self.assertGreater(capped["source"]["total_bytes"], chunk * 2)

    def test_resume_validation_checks_structure_not_content(self):
        # ``--resume`` 只回答「这份 payload 收完了没有」：marker 在、清单里的路径都能对上
        # 文件与其记录的大小。内容层面的正确性由打包前的启动自检与打包时的两次哈希比对负责，
        # 所以同样大小的内容改动不再让工作区失效。
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
            self.assertTrue(
                distribution._is_complete_staged_distribution(workspace, payload)
            )
            content.unlink()
            self.assertFalse(
                distribution._is_complete_staged_distribution(workspace, payload)
            )
            content.write_bytes(b"payload")
            self.assertTrue(
                distribution._is_complete_staged_distribution(workspace, payload)
            )
            content.write_bytes(b"payload and more")
            self.assertFalse(
                distribution._is_complete_staged_distribution(workspace, payload)
            )
            marker.unlink()
            self.assertFalse(
                distribution._is_complete_staged_distribution(workspace, payload)
            )

    def test_manifest_entries_carry_path_and_size_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = Path(tmpdir) / "payload"
            (payload / "app").mkdir(parents=True)
            (payload / "app" / "data.txt").write_bytes(b"payload")
            entries = distribution.build_manifest(payload)
            self.assertEqual(
                entries,
                [{"path": "app/data.txt", "size": 7}],
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

    def test_source_batch_entry_is_excluded_from_payload(self):
        self.assertTrue(distribution.excluded(Path("启动程序.bat")))

    def test_generated_release_config_ships_no_batch_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_root = Path(tmpdir) / "app"
            distribution.write_release_launcher_config(app_root)
            self.assertFalse((app_root / "启动程序.bat").exists())
            config = (app_root / "py.ini").read_text(encoding="utf-8")
            self.assertIn("..\\runtime\\python311\\pythonw.exe", config)

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

    def test_archive_view_keeps_only_the_bundled_about_page_documents(self):
        # Both staging scripts must agree on the one ``doc`` subtree that ships.
        self.assertEqual(
            distribution.APP_DOC_ASSET_DIRECTORY, installer.APP_DOC_ASSET_DIRECTORY
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = Path(tmpdir) / "payload"
            doc_root = payload / "app" / installer.APP_DOC_ASSET_DIRECTORY
            doc_root.mkdir(parents=True, exist_ok=True)
            doc_root.joinpath("开发贡献.txt").write_text(
                "贡献:开发者-Mark42\n", encoding="utf-8"
            )
            sponsor = doc_root / "如果想给作者买鸡腿饭的话" / "喵.jpg"
            sponsor.parent.mkdir(parents=True)
            sponsor.write_bytes(b"\xff\xd8\xff")
            scratch = payload / "app" / "doc" / "维护手册.md"
            scratch.parent.mkdir(parents=True, exist_ok=True)
            scratch.write_text("内部文档", encoding="utf-8")

            paths = {relative for _, relative in installer._archive_entries(payload)}

            self.assertIn("app/doc/贡献名单和主播的狗盆/开发贡献.txt", paths)
            self.assertIn(
                "app/doc/贡献名单和主播的狗盆/如果想给作者买鸡腿饭的话/喵.jpg", paths
            )
            self.assertNotIn("app/doc/维护手册.md", paths)

    def test_archive_view_drops_local_preview_pages(self):
        # Staging skips the preview folder; the installer filter refuses a stale
        # ``app/local_pages`` tree staged by an older workspace.
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = Path(tmpdir) / "payload"
            for relative in (
                Path("app") / ".localpage" / "index.html",
                Path("app") / "local_pages" / "assets" / "fonts" / "HarmonyOS_Sans_SC_Bold.ttf",
                Path("app") / "lib" / "core" / "qt_desktop_pet.py",
            ):
                path = payload / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            paths = {relative for _, relative in installer._archive_entries(payload)}

            self.assertIn("app/lib/core/qt_desktop_pet.py", paths)
            self.assertFalse(
                [path for path in paths if ".localpage" in path or "local_pages" in path]
            )

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

    def test_cuda_voice_runtime_is_bundled_where_the_contract_looks_for_it(self):
        from lib.core import voice_runtime_contract

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "repo"
            build_output = source_root / distribution.CUDA_VOICE_RUNTIME_BUILD_CANDIDATES[0]
            build_output.parent.mkdir(parents=True)
            build_output.write_bytes(b"MZ" + b"\0" * 4096)
            payload = root / "workspace" / "payload"
            payload.mkdir(parents=True)

            details = distribution.stage_cuda_voice_runtime(source_root, payload)

            target = (
                payload
                / distribution.CUDA_VOICE_RUNTIME_DIRECTORY
                / distribution.CUDA_VOICE_RUNTIME_DLL_NAME
            )
            self.assertTrue(details["bundled"])
            self.assertEqual(details["path"], "runtime/cuda-voice/fsv_cuda_voice_runtime.dll")
            self.assertEqual(details["size"], target.stat().st_size)
            self.assertEqual(
                details["sha256"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            # The release layout and the runtime contract are two independent
            # spellings of the same path: <install>/app next to
            # runtime/cuda-voice/fsv_cuda_voice_runtime.dll.
            self.assertEqual(
                voice_runtime_contract.get_bundled_cuda_voice_runtime_path(payload / "app"),
                target,
            )
            self.assertEqual(
                distribution.CUDA_VOICE_RUNTIME_DLL_NAME,
                voice_runtime_contract.CUDA_VOICE_RUNTIME_DLL_NAME,
            )
            self.assertEqual(
                distribution.CUDA_VOICE_RUNTIME_DIRECTORY.name,
                voice_runtime_contract.CUDA_VOICE_RUNTIME_DIR_NAME,
            )

    def test_missing_cuda_voice_runtime_build_output_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = root / "payload"
            payload.mkdir()
            self.assertIsNone(
                distribution.stage_cuda_voice_runtime(root / "repo" / "unbuilt", payload)
            )
            # A build machine without the CUDA toolkit must still produce a
            # release; it just ships without the optional runtime.
            self.assertEqual(list(payload.iterdir()), [])

    def test_cuda_voice_runtime_staging_rejects_a_non_pe_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "repo"
            build_output = source_root / distribution.CUDA_VOICE_RUNTIME_BUILD_CANDIDATES[0]
            build_output.parent.mkdir(parents=True)
            build_output.write_text("not a dll", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                distribution.stage_cuda_voice_runtime(source_root, root / "payload")

    def test_app_doc_assets_are_staged_where_the_workbench_reads_them(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "repo"
            doc_root = source_root / distribution.APP_DOC_ASSET_DIRECTORY
            (doc_root / "如果想给作者买鸡腿饭的话").mkdir(parents=True)
            doc_root.joinpath("开发贡献.txt").write_text(
                "贡献:开发者-Mark42\n===https://space.bilibili.com/486401719\n",
                encoding="utf-8",
            )
            (doc_root / "如果想给作者买鸡腿饭的话" / "喵.png").write_bytes(b"\x89PNG\r\n")
            app = root / "payload" / "app"

            details = distribution.stage_app_doc_assets(source_root, app)

            self.assertTrue(details["bundled"])
            self.assertEqual(details["path"], "doc/贡献名单和主播的狗盆")
            self.assertEqual(details["files"], 2)
            self.assertTrue(
                (app / "doc" / "贡献名单和主播的狗盆" / "开发贡献.txt").is_file()
            )
            self.assertTrue(
                (
                    app
                    / "doc"
                    / "贡献名单和主播的狗盆"
                    / "如果想给作者买鸡腿饭的话"
                    / "喵.png"
                ).is_file()
            )

    def test_missing_app_doc_assets_are_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            app = root / "payload" / "app"

            details = distribution.stage_app_doc_assets(root / "repo", app)

            self.assertFalse(details["bundled"])
            self.assertEqual(details["files"], 0)
            self.assertFalse(app.exists())

    def test_staged_app_doc_assets_satisfy_the_workbench_contract(self):
        from unittest import mock

        from lib.script.ui import ai_settings_panel

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "repo"
            doc_root = source_root / distribution.APP_DOC_ASSET_DIRECTORY
            doc_root.mkdir(parents=True)
            doc_root.joinpath("开发贡献.txt").write_text(
                "\n".join(
                    (
                        "贡献:开发者-Mark42的铁镐（Mark42IRPC）-保留所有权利",
                        "===https://space.bilibili.com/486401719",
                        "贡献:配音-猫咪",
                        "===https://space.bilibili.com/1838261330",
                        "贡献:服务器支持-TDSI服务器",
                        "===https://tdsi.top",
                        "贡献:启动动画-K39゜",
                        "===https://space.bilibili.com/419336032",
                        "贡献:关闭动画-大yi巴狐狸_",
                        "===https://space.bilibili.com/220895159",
                    )
                ),
                encoding="utf-8",
            )
            sponsor_image = (
                doc_root
                / "如果想给作者买鸡腿饭的话"
                / "喵-感谢支持喵-欢迎工单喵.jpg"
            )
            sponsor_image.parent.mkdir(parents=True)
            sponsor_image.write_bytes(b"\xff\xd8\xff")
            app = root / "payload" / "app"
            distribution.stage_app_doc_assets(source_root, app)

            with mock.patch.object(ai_settings_panel, "_project_root", lambda: app):
                self.assertTrue(ai_settings_panel._contribution_list_path().is_file())
                self.assertTrue(ai_settings_panel._sponsor_author_image_path().is_file())
                records = ai_settings_panel._load_contribution_records()

            # Without the staged document the workbench falls back to the three
            # built-in records, which is exactly the reported regression.
            self.assertEqual(len(ai_settings_panel._MANUAL_CONTRIBUTION_RECORDS), 3)
            self.assertGreater(len(records), len(ai_settings_panel._MANUAL_CONTRIBUTION_RECORDS))

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
                "@mixmark-io/domino/.yarn/plugins/@yarnpkg/plugin-workspace-tools.cjs": b"yarn plugin",
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            result = distribution.prune_node_modules(root)

            self.assertEqual(result["removed_files"], 12)
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
            self.assertFalse((root / "@mixmark-io" / "domino" / ".yarn").exists())

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
                "jieba_fast/analyse/analyzer.py",
                "jieba_fast/source/jieba_fast_functions_wrap.cxx",
                "jieba_fast/posseg/prob_emit.p",
                "jieba_fast/finalseg/prob_start.p",
                "jieba_fast/posseg/prob_emit.py",
                "jieba_fast/dict.txt",
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

            self.assertEqual(result["removed_files"], 11)
            self.assertTrue((root / "win32com" / "client" / "__init__.py").is_file())
            self.assertTrue((root / "jieba_fast" / "posseg" / "prob_emit.py").is_file())
            self.assertTrue((root / "jieba_fast" / "dict.txt").is_file())
            self.assertFalse((root / "playwright" / "async_api").exists())
            self.assertFalse((root / "win32comext").exists())
            self.assertFalse((root / "jieba_fast" / "analyse").exists())
            self.assertFalse((root / "jieba_fast" / "source").exists())
            self.assertFalse((root / "jieba_fast" / "posseg" / "prob_emit.p").exists())

    def test_unused_site_distributions_leave_no_import_package_or_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative in (
                "jieba/__init__.py",
                "jieba/posseg/prob_emit.py",
                "jieba-0.42.1.dist-info/METADATA",
                "jieba_fast/__init__.py",
                "jieba_fast-0.53.dist-info/METADATA",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            result = distribution.prune_python_unused_distributions(root)

            self.assertEqual(result["removed_files"], 3)
            self.assertFalse((root / "jieba").exists())
            self.assertFalse((root / "jieba-0.42.1.dist-info").exists())
            self.assertTrue((root / "jieba_fast" / "__init__.py").is_file())
            self.assertTrue((root / "jieba_fast-0.53.dist-info" / "METADATA").is_file())

    def test_pure_python_tokenizer_is_not_a_distribution_root(self):
        # Every consumer imports the compiled ``jieba_fast`` fork, and the
        # dependency installer never installs the pure package, so collecting it
        # would only widen the gap between offline and online installs.
        self.assertNotIn("jieba", distribution.DEFAULT_BASE_DISTRIBUTIONS)
        self.assertIn("jieba-fast", distribution.DEFAULT_BASE_DISTRIBUTIONS)
        self.assertIn("jieba", distribution.UNUSED_SITE_DISTRIBUTIONS)


if __name__ == "__main__":
    unittest.main()
