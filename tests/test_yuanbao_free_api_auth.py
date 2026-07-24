import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.security import HTTPAuthorizationCredentials


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from src.dependencies import auth
from src.utils import common
from src.services.browser.browser_manager import BrowserManager

browser_manager_module = importlib.import_module("src.services.browser.browser_manager")


class YuanbaoFreeApiAuthTests(unittest.IsolatedAsyncioTestCase):
    def _fresh_manager(self) -> BrowserManager:
        manager = BrowserManager()
        manager.browser = None
        manager.context = None
        manager.page = None
        manager.playwright = None
        manager._route_handler = None
        manager._last_auth_headers = None
        manager._request_listener_registered = False
        manager._is_logged_in = False
        manager._login_in_progress = False
        manager._last_error = ""
        manager._last_message = ""
        manager._cookie_marker_warned = False
        manager._login_confirmed_via_ui = False
        manager._storage_state_saved = False
        manager._last_header_capture_at = 0.0
        manager._header_refresh_attempts = 0
        manager._header_refresh_failures = 0
        manager._header_refresh_blocked_until = 0.0
        return manager

    async def test_generate_headers_returns_browser_headers(self):
        headers = {"x-uskey": "ready"}
        with patch.object(common.browser_manager, "get_headers", new=AsyncMock(return_value=headers)) as get_headers_mock:
            result = await common.generate_headers()

        self.assertEqual(result, headers)
        get_headers_mock.assert_awaited_once()

    async def test_generate_headers_raises_when_headers_missing(self):
        with patch.object(common.browser_manager, "get_headers", new=AsyncMock(return_value=None)) as get_headers_mock:
            with self.assertRaises(Exception) as context:
                await common.generate_headers()

        self.assertEqual(str(context.exception), "无法获取请求头，请确保已登录")
        get_headers_mock.assert_awaited_once()

    async def test_authorized_headers_returns_headers(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="sk-test")
        headers = {"x-uskey": "ready"}
        with patch.object(auth, "validate_api_key", return_value=True), patch.object(
            auth,
            "generate_headers",
            new=AsyncMock(return_value=headers),
        ) as generate_mock:
            result = await auth.get_authorized_headers(credentials)

        self.assertEqual(result, headers)
        generate_mock.assert_awaited_once()

    async def test_browser_manager_returns_cached_headers_when_session_confirmed(self):
        manager = self._fresh_manager()
        manager.page = object()
        manager._last_auth_headers = {"x-uskey": "cached", "cookie": "hy_user=u; hy_token=t"}
        manager._is_logged_in = True

        with patch.object(manager, "ensure_browser", new=AsyncMock()) as ensure_mock, patch.object(
            manager, "_has_authenticated_session", new=AsyncMock(return_value=True)
        ) as session_mock:
            result = await manager.get_headers()

        self.assertEqual(result, {"x-uskey": "cached", "cookie": "hy_user=u; hy_token=t"})
        ensure_mock.assert_awaited_once()
        session_mock.assert_awaited_once()

    async def test_browser_manager_falls_back_to_cached_headers_when_live_capture_misses(self):
        manager = self._fresh_manager()
        page = AsyncMock()
        page.route = AsyncMock()
        page.unroute = AsyncMock()
        manager.page = page
        manager._last_auth_headers = {"x-uskey": "cached", "cookie": "hy_user=u; hy_token=t"}
        manager._login_confirmed_via_ui = True

        with patch.object(manager, "ensure_browser", new=AsyncMock()) as ensure_mock, patch.object(
            manager, "_has_authenticated_session", new=AsyncMock(return_value=False)
        ) as session_mock, patch.object(browser_manager_module.settings, "header_timeout", 0.01):
            result = await manager.get_headers()

        self.assertEqual(result, {"x-uskey": "cached", "cookie": "hy_user=u; hy_token=t"})
        ensure_mock.assert_awaited_once()
        session_mock.assert_awaited_once()
        page.route.assert_awaited_once()
        page.unroute.assert_awaited()
        page.reload.assert_not_called()

    async def test_browser_manager_marks_refresh_failure_when_page_missing(self):
        manager = self._fresh_manager()
        manager.page = None

        result = await manager._try_refresh_headers_from_live_page()

        self.assertIsNone(result)
        self.assertEqual(manager._header_refresh_failures, 1)


if __name__ == "__main__":
    unittest.main()
