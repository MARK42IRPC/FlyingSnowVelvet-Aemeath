import unittest
from unittest.mock import patch

from lib.script.yuanbao_free_api import service as yuanbao_service


class YuanbaoFreeApiLoginProviderTests(unittest.TestCase):
    def test_request_service_login_posts_provider_payload(self):
        with patch.object(yuanbao_service, "_http_json", return_value={"success": True}) as http_mock:
            result = yuanbao_service._request_service_login("127.0.0.1", 18000, provider="qq", timeout=3.5)

        self.assertEqual(result, {"success": True})
        http_mock.assert_called_once_with(
            "http://127.0.0.1:18000/fsv/login",
            method="POST",
            timeout=3.5,
            payload={"provider": "qq"},
        )

    def test_describe_status_message_uses_provider_specific_copy(self):
        qq_message = yuanbao_service._describe_status_message(
            {"qrcode_exists": True, "login_provider": "qq"},
        )
        wechat_message = yuanbao_service._describe_status_message(
            {"qrcode_exists": True, "login_provider": "wechat"},
        )

        self.assertEqual(qq_message, "请使用手机QQ扫码登录元宝。")
        self.assertEqual(wechat_message, "请使用微信扫码登录元宝。")


if __name__ == "__main__":
    unittest.main()
