import unittest
from concurrent.futures import Future

from lib.core.forum import (
    FORUM_DEFAULT_ACCENT,
    FORUM_DEFAULT_NICKNAME,
    FORUM_MAX_CONTENT,
    FORUM_POST_COOLDOWN_SECS,
    ForumService,
    format_relative_time,
    normalize_accent,
    parse_feed,
    parse_post_result,
)


class FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


def immediate_future(value=None, error=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)
    return future


def run_inline(worker, *args):
    try:
        return immediate_future(worker(*args))
    except Exception as exc:
        return immediate_future(error=exc)


def feed_payload(messages, *, total=None):
    return {
        "ok": True,
        "total": len(messages) if total is None else total,
        "count": len(messages),
        "messages": messages,
    }


class ForumParsingTests(unittest.TestCase):
    def test_normalize_accent_falls_back_for_unknown_values(self):
        self.assertEqual(normalize_accent("Pink"), "pink")
        self.assertEqual(normalize_accent("snow"), "snow")
        self.assertEqual(normalize_accent("rainbow"), FORUM_DEFAULT_ACCENT)
        self.assertEqual(normalize_accent(None, fallback="cyan"), "cyan")

    def test_parse_feed_keeps_valid_messages_only(self):
        messages, total = parse_feed(
            feed_payload(
                [
                    {"id": 12, "nickname": "匿名", "content": "你好", "accent": "blue", "created_at": 1},
                    {"id": "bad", "content": "坏 id"},
                    {"id": 13, "content": "   "},
                    {"id": 14, "nickname": "", "content": "空昵称", "accent": "??"},
                ],
                total=9,
            )
        )

        self.assertEqual([message.id for message in messages], [12, 14])
        self.assertEqual(messages[0].accent, "blue")
        self.assertEqual(messages[1].nickname, "匿名")
        self.assertEqual(messages[1].accent, FORUM_DEFAULT_ACCENT)
        self.assertEqual(total, 9)

    def test_parse_feed_rejects_broken_payloads(self):
        with self.assertRaises(ValueError):
            parse_feed(["not", "a", "dict"])
        with self.assertRaises(ValueError):
            parse_feed({"ok": True})
        with self.assertRaises(ValueError):
            parse_feed({"ok": False, "error": "维护中"})

    def test_parse_feed_total_falls_back_to_message_count(self):
        messages, total = parse_feed(feed_payload([{"id": 1, "content": "a"}]))
        self.assertEqual(len(messages), 1)
        self.assertEqual(total, 1)

    def test_parse_post_result_reads_created_message(self):
        message = parse_post_result(
            {
                "ok": True,
                "message": {"id": 17, "nickname": "小明", "content": "你好", "accent": "cyan", "created_at": 5},
                "total": 6,
            }
        )
        self.assertEqual((message.id, message.nickname, message.accent), (17, "小明", "cyan"))
        with self.assertRaises(ValueError):
            parse_post_result({"ok": True, "message": {"content": "缺 id"}})

    def test_format_relative_time_buckets(self):
        now = 1_700_000_000_000
        self.assertEqual(format_relative_time(now - 5_000, now_ms=now), "刚刚")
        self.assertEqual(format_relative_time(now - 3 * 60_000, now_ms=now), "3 分钟前")
        self.assertEqual(format_relative_time(now - 5 * 3_600_000, now_ms=now), "5 小时前")
        self.assertEqual(format_relative_time(now - 3 * 86_400_000, now_ms=now), "3 天前")
        self.assertEqual(format_relative_time(0, now_ms=now), "")
        self.assertEqual(format_relative_time(None, now_ms=now), "")


class ForumServiceTests(unittest.TestCase):
    def _service(self, *, get=None, post=None, clock=None, limit=60, cooldown=FORUM_POST_COOLDOWN_SECS):
        self.pages = []
        self.errors = []
        self.posted = []
        self.requests = []
        state = {"now": 0.0}

        def request_get(url, **kwargs):
            self.requests.append(("get", url, kwargs))
            return get(url, **kwargs)

        def request_post(url, **kwargs):
            self.requests.append(("post", url, kwargs))
            return post(url, **kwargs)

        self.clock_value = state
        service = ForumService(
            dispatch=lambda callback: callback(),
            on_page=self.pages.append,
            on_error=self.errors.append,
            on_posted=self.posted.append,
            submit_io=run_inline,
            request_get=request_get,
            request_post=request_post,
            clock=clock or (lambda: self.clock_value["now"]),
            limit=limit,
            cooldown_secs=cooldown,
        )
        return service

    def test_refresh_reports_latest_page_and_oldest_id(self):
        payload = feed_payload(
            [
                {"id": 30, "content": "新", "accent": "pink"},
                {"id": 20, "content": "旧", "accent": "cyan"},
            ],
            total=42,
        )
        get = lambda url, **kwargs: FakeResponse(payload)
        service = self._service(get=get, limit=2)

        self.assertTrue(service.refresh())

        self.assertEqual(len(self.pages), 1)
        page = self.pages[0]
        self.assertEqual(page.mode, "latest")
        self.assertEqual(page.total, 42)
        self.assertEqual([message.id for message in page.messages], [30, 20])
        self.assertEqual(service.oldest_id, 20)
        self.assertFalse(service.reached_end)
        self.assertEqual(service.total, 42)
        self.assertEqual(self.requests[0][2]["params"], {"limit": 2})

    def test_short_page_marks_the_end_and_stops_paging(self):
        payload = feed_payload([{"id": 1, "content": "唯一一条"}])
        get = lambda url, **kwargs: FakeResponse(payload)
        service = self._service(get=get)
        service.refresh()

        self.assertTrue(service.reached_end)
        self.assertFalse(service.load_older())
        self.assertEqual(len(self.requests), 1)

    def test_load_older_uses_oldest_id_and_appends(self):
        payloads = {
            None: feed_payload([{"id": 30 - index, "content": f"留言{index}"} for index in range(3)], total=5),
            28: feed_payload([{"id": 2, "content": "更早"}], total=5),
        }
        seen_before = []

        def get(url, **kwargs):
            before = kwargs["params"].get("before")
            seen_before.append(before)
            return FakeResponse(payloads[before])

        service = self._service(get=get, limit=3)
        service.refresh()
        self.assertTrue(service.load_older())

        self.assertEqual(seen_before, [None, 28])
        self.assertEqual([page.mode for page in self.pages], ["latest", "older"])
        self.assertEqual(self.pages[1].messages[0].id, 2)
        self.assertEqual(service.oldest_id, 2)
        self.assertTrue(service.reached_end)

    def test_page_errors_surface_and_http_429_is_explained(self):
        get = lambda url, **kwargs: FakeResponse({}, status_code=429)
        service = self._service(get=get)
        service.refresh()

        self.assertEqual(self.pages, [])
        self.assertEqual(len(self.errors), 1)
        self.assertIn("限流", self.errors[0])
        self.assertFalse(service.loading_older)

    def test_refresh_drops_results_from_older_generations(self):
        payload = feed_payload([{"id": 5, "content": "x"}])
        service = self._service(get=lambda url, **kwargs: FakeResponse(payload))
        service.refresh()
        service.refresh()

        self.assertEqual(len(self.pages), 2)
        self.assertEqual(self.pages[-1].messages[0].id, 5)

    def test_post_enforces_content_rules_and_the_client_cooldown(self):
        service = self._service(
            get=lambda url, **kwargs: FakeResponse(feed_payload([])),
            post=lambda url, **kwargs: FakeResponse(
                {
                    "ok": True,
                    "message": {"id": 9, "nickname": "匿名", "content": kwargs["json"]["content"], "created_at": 1},
                    "total": 1,
                }
            ),
        )

        self.assertEqual(service.post("   "), "内容不能为空")
        self.assertIn("200", service.post("字" * (FORUM_MAX_CONTENT + 1)))
        self.assertIsNone(service.post("第一条"))
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0].id, 9)

        refused = service.post("第二条")
        self.assertIsNotNone(refused)
        self.assertIn("冷却", refused)
        self.assertEqual(len(self.posted), 1)

        self.clock_value["now"] = FORUM_POST_COOLDOWN_SECS
        self.assertIsNone(service.post("第二条"))
        self.assertEqual(len(self.posted), 2)

        post_request = [item for item in self.requests if item[0] == "post"][0]
        self.assertEqual(post_request[2]["json"]["accent"], FORUM_DEFAULT_ACCENT)
        self.assertEqual(post_request[2]["json"]["nickname"], "匿名")

    def test_post_keeps_blank_nickname_as_the_anonymous_default(self):
        service = self._service(
            get=lambda url, **kwargs: FakeResponse(feed_payload([])),
            post=lambda url, **kwargs: FakeResponse(
                {
                    "ok": True,
                    "message": {
                        "id": 11,
                        "nickname": kwargs["json"]["nickname"],
                        "content": kwargs["json"]["content"],
                        "created_at": 1,
                    },
                    "total": 1,
                }
            ),
        )

        self.assertIsNone(service.post("  留空白  ", nickname="   "))

        post_request = [item for item in self.requests if item[0] == "post"][0]
        self.assertEqual(post_request[2]["json"]["nickname"], FORUM_DEFAULT_NICKNAME)
        self.assertEqual(FORUM_DEFAULT_NICKNAME, "匿名")
        self.assertEqual(self.posted[0].nickname, "匿名")

    def test_post_failure_is_reported(self):
        service = self._service(
            get=lambda url, **kwargs: FakeResponse(feed_payload([])),
            post=lambda url, **kwargs: FakeResponse({}, status_code=500),
        )

        self.assertIsNone(service.post("会失败的留言"))
        self.assertEqual(len(self.posted), 0)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("HTTP 500", self.errors[0])

    def test_cleanup_stops_callbacks(self):
        service = self._service(get=lambda url, **kwargs: FakeResponse(feed_payload([])))
        service.cleanup()

        self.assertFalse(service.refresh())
        self.assertEqual(self.pages, [])
        self.assertEqual(service.post("x"), "论坛窗口已关闭")


if __name__ == "__main__":
    unittest.main()
