import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

bm = importlib.import_module("src.services.browser.browser_manager")


class YuanbaoBrowserLaunchStrategyTests(unittest.TestCase):
    def test_headless_launch_args_explicitly_request_new_headless(self):
        args = bm._headless_launch_args()
        self.assertIn("--headless=new", args)
        self.assertIn("--no-first-run", args)
        self.assertIn("--no-default-browser-check", args)

    def test_find_local_chromium_executable_prefers_existing_candidate(self):
        fake_local = Path("C:/repo/resc/playwright/browsers/ms-playwright/chromium-1208/chrome-win64/chrome.exe")
        with patch.object(bm, "_candidate_local_chromium_executables", return_value=[fake_local]), patch.object(
            Path, "exists", return_value=True
        ), patch.object(Path, "is_file", return_value=True):
            result = bm._find_local_chromium_executable()

        self.assertEqual(result, fake_local)

    def test_browser_manager_no_longer_exposes_system_browser_probe(self):
        self.assertFalse(hasattr(bm, "_find_system_chromium_executable"))


if __name__ == "__main__":
    unittest.main()
