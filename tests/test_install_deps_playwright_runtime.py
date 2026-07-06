import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "install_deps.py"
SPEC = importlib.util.spec_from_file_location("install_deps_under_test", MODULE_PATH)
install_deps = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(install_deps)


class InstallDepsPlaywrightRuntimeTests(unittest.TestCase):
    def test_ensure_browser_runtime_skips_install_when_runtime_exists(self):
        fake_runtime = PROJECT_ROOT / "resc" / "playwright" / "browsers" / "ms-playwright" / "chromium-1208" / "chrome-win64" / "chrome.exe"
        with patch.object(install_deps, "_find_playwright_browser_runtime", return_value=fake_runtime):
            result = install_deps.ensure_yuanbao_browser_runtime("py")

        self.assertTrue(result)

    def test_ensure_browser_runtime_fails_without_local_archive(self):
        original_exists = Path.exists

        def fake_exists(path_obj):
            if path_obj == install_deps.PLAYWRIGHT_RUNTIME_ARCHIVE:
                return False
            return original_exists(path_obj)

        with patch.object(install_deps, "_find_playwright_browser_runtime", return_value=None), patch.object(
            Path, "exists", new=fake_exists
        ):
            result = install_deps.ensure_yuanbao_browser_runtime("py")

        self.assertFalse(result)

    def test_ensure_browser_runtime_extracts_local_archive_into_repo_runtime_dir(self):
        fake_runtime = PROJECT_ROOT / "resc" / "playwright" / "browsers" / "ms-playwright" / "chromium-1208" / "chrome-win64" / "chrome.exe"
        original_exists = Path.exists
        temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_playwright_runtime"
        extracted_exe = temp_root / "extract" / "chrome-win64" / "chrome.exe"

        def fake_exists(path_obj):
            if path_obj == install_deps.PLAYWRIGHT_RUNTIME_ARCHIVE:
                return True
            if path_obj == extracted_exe:
                return True
            return original_exists(path_obj)

        with patch.object(
            install_deps,
            "_find_playwright_browser_runtime",
            side_effect=[None, fake_runtime],
        ), patch.object(
            install_deps, "_extract_zip_with_progress"
        ) as extract_mock, patch.object(install_deps.shutil, "move") as move_mock, patch.object(
            install_deps, "_rmtree_if_exists"
        ), patch.object(Path, "exists", new=fake_exists):
            result = install_deps.ensure_yuanbao_browser_runtime("C:\\Python311\\python.exe")

        self.assertTrue(result)
        extract_mock.assert_called_once_with(
            install_deps.PLAYWRIGHT_RUNTIME_ARCHIVE,
            temp_root / "extract",
        )
        move_mock.assert_called_once_with(
            str(temp_root / "extract" / "chrome-win64"),
            str(install_deps.PLAYWRIGHT_RUNTIME_TARGET_DIR / "chrome-win64"),
        )


if __name__ == "__main__":
    unittest.main()
