import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from src.dependencies import auth
from src.utils import common


class YuanbaoFreeApiAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_headers_retries_until_headers_ready(self):
        headers = {"x-uskey": "ready"}
        with patch.object(
            common.browser_manager,
            "get_headers",
            new=AsyncMock(side_effect=[None, headers]),
        ) as get_headers_mock, patch.object(common.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
            result = await common.generate_headers(retries=2, delay=0.01)

        self.assertEqual(result, headers)
        self.assertEqual(get_headers_mock.await_count, 2)
        sleep_mock.assert_awaited_once_with(0.01)

    async def test_authorized_headers_returns_503_when_headers_not_ready(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="sk-test")
        with patch.object(auth, "validate_api_key", return_value=True), patch.object(
            auth,
            "generate_headers",
            new=AsyncMock(side_effect=common.HeadersUnavailableError("headers not ready")),
        ):
            with self.assertRaises(HTTPException) as context:
                await auth.get_authorized_headers(credentials)

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.detail, "headers not ready")


if __name__ == "__main__":
    unittest.main()
