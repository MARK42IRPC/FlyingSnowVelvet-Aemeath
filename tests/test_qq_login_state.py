import ast
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from lib.script.cloudmusic._mixin_login import _LoginMixin

_LOGIN_SOURCE = Path(__file__).resolve().parents[1] / "lib" / "script" / "cloudmusic" / "_mixin_login.py"


class QQLoginStateTests(unittest.TestCase):
    @staticmethod
    def _image_bytes(image_format="JPEG"):
        buffer = BytesIO()
        Image.new("RGB", (64, 64), "white").save(buffer, format=image_format)
        return buffer.getvalue()

    def test_qq_qr_response_normalizes_supported_images_to_png(self):
        login = object.__new__(_LoginMixin)
        response = Mock(content=self._image_bytes(), text="")

        result = login._qq_qr_png_from_response(response)

        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_qq_qr_response_rejects_non_image_payload(self):
        login = object.__new__(_LoginMixin)
        response = Mock(content=b"<html>upstream error</html>", text="upstream error")

        self.assertIsNone(login._qq_qr_png_from_response(response))

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



class QQBrowserLoginWorkerStructureTests(unittest.TestCase):
    """锁住 `_qq_browser_login_worker` 的首轮守卫写法。

    历史上这里写的是 `if (not has_uin if 'has_uin' in locals() else True):`，
    靠 `locals()` 判「首轮有没有算过 uin」，静态分析看不到赋值点（曾触发 F821），
    也很难读懂。现在改成循环前显式 `has_uin = False` + `if not has_uin:`，
    行为不变（首轮按未登录处理），但可静态验证。
    """

    @staticmethod
    def _worker_node():
        tree = ast.parse(_LOGIN_SOURCE.read_text(encoding="utf-8"), filename=str(_LOGIN_SOURCE))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_qq_browser_login_worker":
                return node
        raise AssertionError("未找到 _qq_browser_login_worker")

    def test_has_uin_is_initialized_before_the_poll_loop(self):
        worker = self._worker_node()
        loops = [node for node in ast.walk(worker) if isinstance(node, ast.While)]
        self.assertTrue(loops, "登录轮询循环不见了")
        loop = min(loops, key=lambda node: node.lineno)

        pre_loop_assignments = [
            node
            for node in ast.walk(worker)
            if isinstance(node, ast.Assign)
            and node.lineno < loop.lineno
            and any(
                isinstance(target, ast.Name) and target.id == "has_uin"
                for target in node.targets
            )
        ]
        self.assertEqual(len(pre_loop_assignments), 1, "has_uin 未在轮询循环前初始化")
        self.assertIsInstance(pre_loop_assignments[0].value, ast.Constant)
        self.assertIs(pre_loop_assignments[0].value.value, False)

    def test_no_locals_based_dead_condition_remains(self):
        source = _LOGIN_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("'has_uin' in locals()", source)
        self.assertNotIn('"has_uin" in locals()', source)


if __name__ == "__main__":
    unittest.main()
