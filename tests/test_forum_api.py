"""主站客户端的解析、请求构造与错误翻译。"""

from __future__ import annotations

import unittest

from lib.core.forum_api import (
    FORUM_API_BASE,
    FORUM_REPLY_MAX,
    ForumApiClient,
    ForumApiError,
    format_date,
    format_expiry,
    format_timestamp,
    parse_like_result,
    parse_post_page,
    parse_tags,
    parse_user,
    validate_password,
    validate_reply,
    validate_title,
    validate_username,
)


class FakeResponse:
    """最小响应对象：`requests` 那套里客户端只用到这几个属性。"""

    def __init__(self, payload, *, status_code: int = 200, content: bytes | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else b"{}"
        self.closed = False

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def close(self) -> None:
        self.closed = True


class FakeRequest:
    """记录调用并按顺序返回预置响应的假 `requests.request`。"""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "json": json, "params": params, "headers": dict(headers or {})}
        )
        if not self.responses:
            return FakeResponse({"ok": True, "data": {}})
        return self.responses.pop(0)


def ok(data):
    return FakeResponse({"ok": True, "data": data})


def fail(status, code, message):
    return FakeResponse({"ok": False, "error": {"code": code, "message": message}}, status_code=status)


class ParseTests(unittest.TestCase):
    def test_post_page_reads_counts_and_skips_broken_entries(self) -> None:
        page = parse_post_page({
            "posts": [
                {"id": 12, "title": "标题", "content": "正文", "tags": "general,Demo",
                 "author": {"id": 1, "username": "demo_user", "display_name": "演示"},
                 "reply_count": 3, "like_count": 2, "view_count": 41,
                 "is_pinned": True, "images": [{"id": "a"}], "liked_by_me": True,
                 "created_at": 1789477000, "last_reply_at": 1789477100},
                {"title": "没有 id"},
                "不是字典",
            ],
            "page": 2, "per_page": 20, "total": 21, "total_pages": 2, "has_more": True, "sort": "hot",
        })
        self.assertEqual(len(page.posts), 1)
        post = page.posts[0]
        self.assertEqual((post.id, post.title, post.tags), (12, "标题", ("general", "demo")))
        self.assertEqual(post.author.username, "demo_user")
        self.assertEqual((post.reply_count, post.like_count, post.view_count), (3, 2, 41))
        self.assertTrue(post.is_pinned)
        self.assertTrue(post.liked_by_me)
        self.assertEqual(post.image_count, 1)
        self.assertEqual((page.page, page.total, page.total_pages, page.has_more, page.sort), (2, 21, 2, True, "hot"))

    def test_has_more_falls_back_to_page_maths(self) -> None:
        page = parse_post_page({"posts": [], "page": 1, "per_page": 20, "total_pages": 3})
        self.assertTrue(page.has_more)
        last = parse_post_page({"posts": [], "page": 3, "per_page": 20, "total_pages": 3})
        self.assertFalse(last.has_more)

    def test_tags_accept_strings_and_dicts(self) -> None:
        tags = parse_tags([{"name": "General", "count": 12}, "demo", "", 7, None])
        self.assertEqual([(tag.name, tag.count) for tag in tags], [("general", 12), ("demo", 0)])

    def test_parse_user_needs_a_username(self) -> None:
        self.assertIsNone(parse_user(None))
        self.assertIsNone(parse_user({"display_name": "只有显示名"}))
        user = parse_user({"username": "demo", "role": "admin", "post_count": 2})
        self.assertEqual((user.username, user.role, user.post_count), ("demo", "admin", 2))
        self.assertEqual(user.label, "demo")

    def test_like_result_reads_state_and_count(self) -> None:
        result = parse_like_result({"target_type": "post", "target_id": 12, "liked": True, "like_count": 3})
        self.assertEqual((result.target_type, result.target_id, result.liked, result.like_count), ("post", 12, True, 3))


class RequestTests(unittest.TestCase):
    def _client(self, *responses, token: str = "") -> tuple[ForumApiClient, FakeRequest]:
        request = FakeRequest(*responses)
        return ForumApiClient(token=token, request=request), request

    def test_list_posts_builds_the_documented_query(self) -> None:
        client, request = self._client(ok({"posts": [], "page": 1, "total_pages": 1}))
        client.list_posts(page=2, per_page=999, sort="hot", tag="general", query="关键字")
        call = request.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], f"{FORUM_API_BASE}/posts")
        self.assertEqual(call["params"], {"page": 2, "per_page": 50, "sort": "hot", "tag": "general", "q": "关键字"})

    def test_unknown_sort_falls_back_to_new(self) -> None:
        client, request = self._client(ok({"posts": []}))
        client.list_posts(sort="random")
        self.assertEqual(request.calls[0]["params"]["sort"], "new")

    def test_token_becomes_a_bearer_header_only_when_authorized(self) -> None:
        client, request = self._client(ok({"user": {"username": "demo"}}), ok({"status": "ok"}), token="abc")
        client.me()
        client.health()
        self.assertEqual(request.calls[0]["headers"]["Authorization"], "Bearer abc")
        self.assertNotIn("Authorization", request.calls[1]["headers"])

    def test_register_posts_credentials_and_remembers_the_token(self) -> None:
        client, request = self._client(
            ok({"user": {"id": 1, "username": "demo_user"}, "token": "t" * 64, "expires_at": 1792069000})
        )
        session = client.register("demo_user", "demo-password-123", "演示")
        self.assertEqual(request.calls[0]["url"], f"{FORUM_API_BASE}/auth/register")
        self.assertEqual(
            request.calls[0]["json"],
            {"username": "demo_user", "password": "demo-password-123", "display_name": "演示"},
        )
        self.assertEqual(session.token, "t" * 64)
        self.assertEqual(client.token, "t" * 64)
        self.assertEqual(session.user.username, "demo_user")

    def test_unlike_uses_delete(self) -> None:
        client, request = self._client(ok({"target_type": "post", "target_id": 5, "liked": False, "like_count": 1}))
        result = client.like_post(5, liked=False)
        self.assertEqual(request.calls[0]["method"], "DELETE")
        self.assertEqual(request.calls[0]["url"], f"{FORUM_API_BASE}/posts/5/like")
        self.assertFalse(result.liked)

    def test_reply_with_parent_sends_parent_id(self) -> None:
        client, request = self._client(ok({"id": 7, "post_id": 12, "content": "写得好"}))
        reply = client.create_reply(12, "写得好", parent_id=3)
        self.assertEqual(request.calls[0]["json"], {"content": "写得好", "parent_id": 3})
        self.assertEqual(reply.id, 7)

    def test_error_payload_becomes_a_translated_exception(self) -> None:
        client, _request = self._client(fail(400, "invalid_request", '"title" must be at least 2 characters.'))
        with self.assertRaises(ForumApiError) as ctx:
            client.get_post(12)
        error = ctx.exception
        self.assertEqual((error.status, error.code), (400, "invalid_request"))
        self.assertIn("内容不符合论坛要求", error.friendly())
        self.assertFalse(error.is_auth_error())

    def test_auth_error_is_detected(self) -> None:
        client, _request = self._client(fail(401, "unauthorized", "Invalid username or password."))
        with self.assertRaises(ForumApiError) as ctx:
            client.login("demo", "wrong-password")
        self.assertTrue(ctx.exception.is_auth_error())
        self.assertIn("登录状态已失效", ctx.exception.friendly())

    def test_friendly_messages_cover_the_documented_codes(self) -> None:
        cases = {
            403: "没有权限",
            404: "不存在",
            405: "请求方法",
            409: "用户名已存在",
            413: "太大",
            415: "JSON",
            429: "太频繁",
            500: "服务端",
        }
        for status, fragment in cases.items():
            error = ForumApiError(status, "", "boom")
            self.assertIn(fragment, error.friendly(), status)

    def test_network_failure_and_bad_json_are_wrapped(self) -> None:
        def boom(*_args, **_kwargs):
            raise OSError("连接被拒绝")

        client = ForumApiClient(request=boom)
        with self.assertRaises(ForumApiError) as ctx:
            client.health()
        self.assertEqual(ctx.exception.status, 0)
        self.assertIn("网络连接失败", ctx.exception.friendly())

        client, _request = self._client(FakeResponse(ValueError("not json"), status_code=502))
        with self.assertRaises(ForumApiError) as ctx:
            client.health()
        self.assertEqual(ctx.exception.code, "bad_response")

    def test_oversized_response_is_rejected(self) -> None:
        big = FakeResponse({"ok": True, "data": {}}, content=b"x" * (4 * 1024 * 1024 + 1))
        client, _request = self._client(big)
        with self.assertRaises(ForumApiError) as ctx:
            client.health()
        self.assertEqual(ctx.exception.code, "too_large")

    def test_response_is_closed_after_every_call(self) -> None:
        response = ok({"status": "ok"})
        client, _request = self._client(response)
        client.health()
        self.assertTrue(response.closed)


class ValidatorTests(unittest.TestCase):
    def test_username_rules(self) -> None:
        self.assertIn("至少", validate_username("ab"))
        self.assertIn("最多", validate_username("a" * 25))
        self.assertIn("字母", validate_username("中文名字"))
        self.assertEqual(validate_username("demo_user-1"), "")

    def test_password_and_reply_rules(self) -> None:
        self.assertIn("至少", validate_password("short"))
        self.assertEqual(validate_password("demo-password-123"), "")
        self.assertIn("不能为空", validate_reply("   "))
        self.assertIn("不能超过", validate_reply("x" * (FORUM_REPLY_MAX + 1)))
        self.assertEqual(validate_reply("写得好"), "")

    def test_title_rules(self) -> None:
        self.assertIn("至少", validate_title("a"))
        self.assertEqual(validate_title("标题"), "")


class FormatterTests(unittest.TestCase):
    def test_relative_timestamps(self) -> None:
        now = 1_800_000_000
        self.assertEqual(format_timestamp(now - 10, now=now), "刚刚")
        self.assertEqual(format_timestamp(now - 600, now=now), "10 分钟前")
        self.assertEqual(format_timestamp(now - 7200, now=now), "2 小时前")
        self.assertEqual(format_timestamp(now - 86400 * 3, now=now), "3 天前")
        self.assertEqual(format_timestamp(0), "")
        self.assertEqual(format_timestamp("坏了"), "")

    def test_date_and_expiry(self) -> None:
        self.assertEqual(format_date(0), "")
        self.assertEqual(format_date(1_789_477_000), "2026-09-15")
        self.assertEqual(format_date(1_789_477_000, with_time=True), "2026-09-15 20:56")
        self.assertEqual(
            format_expiry(1_792_069_000, now=1_789_477_000),
            "2026-10-15 20:56（还有 30 天）",
        )
        self.assertEqual(format_expiry(0), "未知")

    def test_session_expiry_flag(self) -> None:
        from lib.core.forum_api import ForumSession

        self.assertFalse(ForumSession(token="t", expires_at=0).expired())
        self.assertTrue(ForumSession(token="t", expires_at=100, ).expired(now=200))
        self.assertFalse(ForumSession(token="t", expires_at=300).expired(now=200))


if __name__ == "__main__":
    unittest.main()