import unittest
from unittest.mock import Mock, patch

from lib.script.cloudmusic._mixin_login import _LoginMixin


class QQLoginStateTests(unittest.TestCase):
    def test_confirmed_basic_qq_auth_is_a_usable_login_state(self):
        cookies = {"p_uin": "o123456", "p_skey": "basic-auth"}

        access = _LoginMixin._qq_cookie_map_login_access(
            cookies,
            official_login_confirmed=True,
        )

        self.assertEqual(access, "basic")

    def test_basic_qq_auth_is_not_accepted_before_official_confirmation(self):
        cookies = {"p_uin": "o123456", "p_skey": "basic-auth"}

        access = _LoginMixin._qq_cookie_map_login_access(
            cookies,
            official_login_confirmed=False,
        )

        self.assertEqual(access, "")

    def test_music_auth_cookie_provides_full_login_access(self):
        cookies = {"uin": "123456", "qqmusic_key": "music-auth"}

        access = _LoginMixin._qq_cookie_map_login_access(
            cookies,
            official_login_confirmed=False,
        )

        self.assertEqual(access, "full")

    def test_qr_session_cookie_is_not_a_login_state(self):
        cookies = {"qrsig": "pending-login"}

        access = _LoginMixin._qq_cookie_map_login_access(
            cookies,
            official_login_confirmed=True,
        )

        self.assertEqual(access, "")

    def test_post_login_navigation_timeout_still_collects_written_cookies(self):
        login = object.__new__(_LoginMixin)
        context = Mock()
        page = Mock()
        page.goto.side_effect = TimeoutError("domcontentloaded timed out")
        expected = {"p_uin": "o123456", "p_skey": "basic-auth"}

        with patch.object(
            _LoginMixin,
            "_qq_collect_context_cookie_map",
            return_value=expected,
        ), patch.object(
            _LoginMixin,
            "_qq_collect_storage_state_map",
            return_value={},
        ):
            result = login._qq_sync_login_context(
                context,
                page,
                "https://graph.qq.com/authorized",
            )

        self.assertEqual(result, expected)
        visited = [call.args[0] for call in page.goto.call_args_list]
        self.assertNotIn(_LoginMixin._QQ_LOGIN_S_URL, visited)


if __name__ == "__main__":
    unittest.main()
