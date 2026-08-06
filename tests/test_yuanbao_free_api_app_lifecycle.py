import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import app as yuanbao_app


class YuanbaoFreeApiAppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await yuanbao_app._cancel_login_task()

    async def test_lifespan_no_longer_starts_login_task_on_startup(self):
        with patch.object(yuanbao_app, "_ensure_login_task", new=AsyncMock()) as ensure_mock, patch.object(
            yuanbao_app.browser_manager, "close", new=AsyncMock()
        ) as close_mock:
            async with yuanbao_app.lifespan(yuanbao_app.app):
                ensure_mock.assert_not_awaited()

            close_mock.assert_awaited_once()

    async def test_fsv_login_passes_provider_to_login_task(self):
        class _FakeTask:
            def done(self) -> bool:
                return False

        with patch.object(
            yuanbao_app,
            "_ensure_login_task",
            new=AsyncMock(return_value=(_FakeTask(), True)),
        ) as ensure_mock, patch.object(
            yuanbao_app.browser_manager,
            "status",
            return_value={"login_in_progress": True, "login_provider": "qq"},
        ):
            response = await yuanbao_app.fsv_login(yuanbao_app.LoginRequest(provider="qq"))

        ensure_mock.assert_awaited_once_with(force=True, provider="qq")
        self.assertTrue(response["success"])
        self.assertEqual(response["login_provider"], "qq")

    async def test_concurrent_force_logins_leave_only_latest_task_running(self):
        wait_forever = asyncio.Event()

        async def fake_login(**_kwargs):
            await wait_forever.wait()

        with patch.object(
            yuanbao_app.browser_manager,
            "login",
            side_effect=fake_login,
        ), patch.object(yuanbao_app.browser_manager, "close", new=AsyncMock()):
            first, second = await asyncio.gather(
                yuanbao_app._ensure_login_task(force=True, provider="wechat"),
                yuanbao_app._ensure_login_task(force=True, provider="wechat"),
            )

        first_task = first[0]
        second_task = second[0]
        self.assertTrue(first_task.done())
        self.assertIs(yuanbao_app._login_task, second_task)
        self.assertFalse(second_task.done())

    async def test_status_exposes_stable_service_identity(self):
        from lib.script.yuanbao_free_api import service as launcher_service

        with patch.object(
            yuanbao_app.browser_manager,
            "status",
            return_value={"qrcode_path": "missing-qrcode.png"},
        ):
            response = await yuanbao_app.fsv_status()

        self.assertEqual(response["service_id"], launcher_service._SERVICE_ID)
        self.assertEqual(response["protocol_version"], launcher_service._SERVICE_PROTOCOL_VERSION)
        self.assertIn("instance_id", response)
