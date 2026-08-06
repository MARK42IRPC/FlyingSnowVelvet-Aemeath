import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


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


class YuanbaoBrowserLaunchFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_manager_tries_next_local_chromium_candidate(self):
        manager = bm.BrowserManager()
        browser = Mock()
        context = Mock()
        page = Mock()
        context.on = Mock()
        context.new_page = AsyncMock(return_value=page)
        browser.new_context = AsyncMock(return_value=context)
        chromium = Mock()
        chromium.launch = AsyncMock(side_effect=[RuntimeError("broken runtime"), browser])
        playwright = Mock()
        playwright.chromium = chromium

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "chromium-1209" / "chrome.exe"
            second = Path(temp_dir) / "chromium-1208" / "chrome.exe"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            manager.browser = None
            manager.context = None
            manager.page = None
            manager.playwright = playwright
            manager._request_listener_registered = False
            with patch.object(
                bm,
                "_candidate_local_chromium_executables",
                return_value=[first, second],
            ), patch.object(manager, "_load_page", new=AsyncMock()):
                await manager._init_browser()

        self.assertIs(manager.browser, browser)
        self.assertEqual(chromium.launch.await_count, 2)
        self.assertEqual(chromium.launch.await_args_list[0].kwargs["executable_path"], str(first))
        self.assertEqual(chromium.launch.await_args_list[1].kwargs["executable_path"], str(second))
        manager.browser = None
        manager.context = None
        manager.page = None
        manager.playwright = None

    async def test_page_load_retries_with_dom_content_loaded_boundary(self):
        manager = bm.BrowserManager()
        page = Mock()
        page.goto = AsyncMock(side_effect=[RuntimeError("temporary network error"), None])
        page.wait_for_timeout = AsyncMock()
        manager.page = page

        with patch.object(bm.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
            await manager._load_page()

        self.assertEqual(page.goto.await_count, 2)
        self.assertEqual(page.goto.await_args_list[0].kwargs["wait_until"], "domcontentloaded")
        sleep_mock.assert_awaited_once_with(1.0)
        manager.page = None


if __name__ == "__main__":
    unittest.main()
