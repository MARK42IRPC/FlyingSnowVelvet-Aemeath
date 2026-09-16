"""社区页网络协调器：回调、分页守卫、缓存与登录态联动。"""

from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from lib.core import forum_cache, forum_images
from lib.core.forum_api import (
    FORUM_IMAGE_MAX_BYTES,
    ForumApiError,
    ForumLikeResult,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumImage,
    ForumSession,
    ForumTag,
    ForumUser,
)
from lib.core.forum_community import CommunityService, friendly_error, post_to_cache
from lib.core.forum_session import ForumSessionStore


def run_now(worker, *args, **kwargs) -> Future:
    """同步执行：测试里不需要线程，回调立刻发生，断言就能直接写。"""
    future: Future = Future()
    try:
        future.set_result(worker(*args, **kwargs))
    except Exception as exc:
        future.set_exception(exc)
    return future


def never(worker, *args, **kwargs) -> Future:
    return Future()


class FakeListener:
    def __init__(self) -> None:
        self.posts: list = []
        self.post: list = []
        self.replies: list = []
        self.reply_posted: list = []
        self.likes: list = []
        self.tags: list = []
        self.account: list = []
        self.session: list = []
        self.status: list = []
        self.errors: list = []
        self.health: list = []
        self.session_error: list = []
        self.thread_posted: list = []
        self.user_activity: list = []
        self.image_uploaded: list = []
        self.image_error: list = []
        self.thumbnails: list = []

    def on_posts(self, page, append, **kwargs) -> None:
        self.posts.append((page, append, kwargs))

    def on_post(self, post) -> None:
        self.post.append(post)

    def on_replies(self, page, append) -> None:
        self.replies.append((page, append))

    def on_reply_posted(self, reply, floor) -> None:
        self.reply_posted.append((reply, floor))

    def on_thread_posted(self, post) -> None:
        self.thread_posted.append(post)

    def on_user_activity(self, user, posts, replies) -> None:
        self.user_activity.append((user, posts, replies))

    def on_image_uploaded(self, image) -> None:
        self.image_uploaded.append(image)

    def on_image_error(self, message) -> None:
        self.image_error.append(message)

    def on_thumbnail(self, image_id, data) -> None:
        self.thumbnails.append((image_id, data))

    def on_likes(self, kind, target_id, liked, count) -> None:
        self.likes.append((kind, target_id, liked, count))

    def on_tags(self, tags) -> None:
        self.tags.append(tuple(tags))

    def on_account(self, user) -> None:
        self.account.append(user)

    def on_session(self, session) -> None:
        self.session.append(session)

    def on_session_error(self, action, message) -> None:
        self.session_error.append((action, message))

    def on_status(self, text, tone="") -> None:
        self.status.append((text, tone))

    def on_error(self, message) -> None:
        self.errors.append(message)

    def on_health(self, payload) -> None:
        self.health.append(payload)


class FakeClient:
    """假客户端：只实现服务用到的方法，并把调用记下来。"""

    def __init__(self) -> None:
        self.token = ""
        self.calls: list = []
        self.post_pages: dict[int, ForumPostPage] = {}
        self.reply_pages: dict[int, ForumReplyPage] = {}
        self.tags_result: tuple[ForumTag, ...] = ()
        self.post = ForumPost(id=12, title="标题", content="正文", reply_count=1)
        self.reply = ForumReply(id=7, post_id=12, content="写得好")
        self.health_result = {"status": "ok", "service": "fxxr-forum"}
        self.session_result = ForumSession(token="t" * 64, user=ForumUser(id=1, username="demo"))
        self.user_result = ForumUser(id=1, username="demo")
        self.image_result = ForumImage(id="a" * 32, url="/api/images/" + "a" * 32)
        self.image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        self.image_data_urls: list = []
        self.error: Exception | None = None

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error

    def list_posts(self, **kwargs):
        self._record("list_posts", **kwargs)
        return self.post_pages.get(kwargs.get("page", 1), ForumPostPage(page=kwargs.get("page", 1)))

    def tags(self, limit=50):
        self._record("tags", limit=limit)
        return self.tags_result

    def get_post(self, post_id):
        self._record("get_post", post_id=post_id)
        return self.post

    def list_replies(self, post_id, **kwargs):
        self._record("list_replies", post_id=post_id, **kwargs)
        return self.reply_pages.get(kwargs.get("page", 1), ForumReplyPage(page=kwargs.get("page", 1)))

    def create_post(self, title, content, *, tags=None, images=None):
        self._record("create_post", title=title, content=content, tags=tags, images=images)
        return self.post

    def upload_image(self, data_url):
        self._record("upload_image")
        self.image_data_urls.append(data_url)
        return self.image_result

    def fetch_image(self, image_id):
        self._record("fetch_image", image_id=image_id)
        return self.image_bytes

    def user_profile(self, username):
        self._record("user_profile", username=username)
        return self.user_result

    def user_posts(self, username, **kwargs):
        self._record("user_posts", username=username, **kwargs)
        return self.post_pages.get(kwargs.get("page", 1), ForumPostPage(page=kwargs.get("page", 1)))

    def user_replies(self, username, **kwargs):
        self._record("user_replies", username=username, **kwargs)
        return self.reply_pages.get(kwargs.get("page", 1), ForumReplyPage(page=kwargs.get("page", 1)))

    def create_reply(self, post_id, content, parent_id=None):
        self._record("create_reply", post_id=post_id, content=content, parent_id=parent_id)
        return self.reply

    def like_post(self, post_id, *, liked=True):
        self._record("like_post", post_id=post_id, liked=liked)
        return ForumLikeResult(target_type="post", target_id=post_id, liked=liked, like_count=3)

    def like_reply(self, reply_id, *, liked=True):
        self._record("like_reply", reply_id=reply_id, liked=liked)
        return ForumLikeResult(target_type="reply", target_id=reply_id, liked=liked, like_count=2)

    def login(self, username, password):
        self._record("login", username=username)
        return self.session_result

    def register(self, username, password, display_name=None):
        self._record("register", username=username, display_name=display_name)
        return self.session_result

    def logout(self):
        self._record("logout")

    def me(self):
        self._record("me")
        return self.user_result

    def health(self):
        self._record("health")
        return self.health_result


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = FakeClient()
        self.listener = FakeListener()
        self.store = ForumSessionStore()
        self.service = CommunityService(
            dispatch=lambda callback: callback(),
            listener=self.listener,
            client=self.client,
            session=self.store,
            submit_io=run_now,
            page_size=2,
        )
        self.addCleanup(self.service.cleanup)

    def login(self) -> None:
        self.store.set(self.client.session_result)


class PostListTests(ServiceTestCase):
    def test_first_page_reports_posts_and_writes_the_snapshot(self) -> None:
        self.client.post_pages[1] = ForumPostPage(
            posts=(ForumPost(id=1, title="甲"), ForumPost(id=2, title="乙")),
            page=1, per_page=2, total=5, total_pages=3, has_more=True,
        )
        self.assertTrue(self.service.load_posts())
        page, append, kwargs = self.listener.posts[-1]
        self.assertEqual([post.id for post in page.posts], [1, 2])
        self.assertFalse(append)
        self.assertEqual(kwargs, {})
        self.assertEqual(self.service.post_total, 5)
        self.assertTrue(self.service.has_more_posts)

        cached = forum_cache.read_entry("board-new")
        self.assertEqual([item["id"] for item in cached["posts"]], [1, 2])

    def test_more_pages_append_until_the_end(self) -> None:
        self.client.post_pages[1] = ForumPostPage(posts=(ForumPost(id=1),), page=1, total=2, total_pages=2, has_more=True)
        self.service.load_posts()
        self.client.post_pages[2] = ForumPostPage(posts=(ForumPost(id=2),), page=2, total=2, total_pages=2, has_more=False)
        self.assertTrue(self.service.load_more_posts())
        self.assertEqual(self.client.calls[-1][1]["page"], 2)
        self.assertEqual(self.listener.posts[-1][1], True)
        self.assertFalse(self.service.has_more_posts)
        self.assertFalse(self.service.load_more_posts())

    def test_sort_change_switches_the_cache_entry(self) -> None:
        self.client.post_pages[1] = ForumPostPage(posts=(ForumPost(id=1),), page=1, total=1, has_more=False)
        self.service.load_posts(sort="hot")
        self.assertEqual(self.client.calls[-1][1]["sort"], "hot")
        self.assertIsNotNone(forum_cache.read_entry("board-hot"))

    def test_filters_are_sent_and_not_cached(self) -> None:
        self.service.load_posts(tag="general", query="关键字")
        call = self.client.calls[-1][1]
        self.assertEqual((call["tag"], call["query"]), ("general", "关键字"))
        self.assertIsNone(forum_cache.read_entry("board-new"))

    def test_empty_filter_clears_the_previous_one(self) -> None:
        self.service.load_posts(tag="general")
        self.service.load_posts(tag="", query="")
        call = self.client.calls[-1][1]
        self.assertIsNone(call["tag"])
        self.assertIsNone(call["query"])

    def test_second_request_while_one_is_in_flight_is_ignored(self) -> None:
        service = CommunityService(
            dispatch=lambda callback: callback(),
            listener=self.listener,
            client=self.client,
            session=self.store,
            submit_io=never,
        )
        self.addCleanup(service.cleanup)
        self.assertTrue(service.load_posts())
        self.assertFalse(service.load_posts())

    def test_stale_generation_result_is_dropped(self) -> None:
        self.client.post_pages[1] = ForumPostPage(posts=(ForumPost(id=1),), page=1, total=1)
        self.service.load_posts()
        before = len(self.listener.posts)
        # 手工把代数推前，模拟「结果回来时用户已经换了排序」。
        with self.service._lock:
            self.service._generation += 1
        self.service._handle_posts((1, ForumPostPage(posts=(ForumPost(id=9),), page=1), False))
        self.assertEqual(len(self.listener.posts), before)

    def test_tags_are_cached_and_reported(self) -> None:
        self.client.tags_result = (ForumTag(name="general", count=12),)
        self.assertTrue(self.service.load_tags())
        self.assertEqual(self.listener.tags[-1][0].name, "general")
        self.assertEqual(forum_cache.read_entry("tags")["tags"][0]["name"], "general")

    def test_snapshot_restores_posts_and_tags(self) -> None:
        forum_cache.write_entry("board-new", {
            "sort": "new", "page": 1, "per_page": 2, "total": 1, "total_pages": 1,
            "has_more": False, "posts": [post_to_cache(ForumPost(id=5, title="快照"))],
        })
        forum_cache.write_entry("tags", {"tags": [{"name": "general", "count": 1}]})
        self.assertTrue(self.service.restore_snapshot())
        page, append, kwargs = self.listener.posts[-1]
        self.assertEqual(page.posts[0].title, "快照")
        self.assertTrue(kwargs["cached"])
        self.assertEqual(self.listener.tags[-1][0].name, "general")
        # 快照就是第一页：翻页不能把第一页又铺一遍。
        self.client.post_pages[2] = ForumPostPage(posts=(), page=2, has_more=False)
        self.service._has_more_posts = True
        self.service.load_more_posts()
        self.assertEqual(self.client.calls[-1][1]["page"], 2)

    def test_restore_without_snapshot_reports_nothing(self) -> None:
        self.assertFalse(self.service.restore_snapshot())
        self.assertEqual(self.listener.posts, [])


class DetailTests(ServiceTestCase):
    def test_open_post_loads_body_and_first_reply_page(self) -> None:
        self.client.reply_pages[1] = ForumReplyPage(
            replies=(ForumReply(id=7, content="写得好"),), page=1, per_page=2, total=3, total_pages=2, has_more=True
        )
        self.assertTrue(self.service.open_post(12))
        self.assertEqual(self.listener.post[-1].id, 12)
        page, append = self.listener.replies[-1]
        self.assertEqual([reply.id for reply in page.replies], [7])
        self.assertFalse(append)
        self.assertEqual(self.service.replies_total, 3)
        self.assertTrue(self.service.has_more_replies)

    def test_more_replies_use_the_next_page(self) -> None:
        self.client.reply_pages[1] = ForumReplyPage(replies=(ForumReply(id=7),), page=1, total=2, total_pages=2, has_more=True)
        self.service.open_post(12)
        self.client.reply_pages[2] = ForumReplyPage(replies=(ForumReply(id=8),), page=2, total=2, has_more=False)
        self.assertTrue(self.service.load_more_replies())
        self.assertEqual(self.client.calls[-1][1]["page"], 2)
        self.assertFalse(self.service.has_more_replies)
        self.assertFalse(self.service.load_more_replies())

    def test_close_post_forgets_the_detail_state(self) -> None:
        self.service.open_post(12)
        self.service.close_post()
        self.assertIsNone(self.service.current_post)
        self.assertFalse(self.service.load_more_replies())

    def test_detail_result_arriving_after_leaving_is_dropped(self) -> None:
        """返回列表之后晚到的详情结果不能再把用户拽回详情。"""
        self.client.reply_pages[1] = ForumReplyPage(replies=(ForumReply(id=7),), page=1, total=1)
        self.service.open_post(12)
        generation = self.service._detail_generation
        self.service.close_post()
        before = len(self.listener.post)
        self.service._handle_post((generation, self.client.post, self.client.reply_pages[1]))
        self.assertEqual(len(self.listener.post), before)
        self.assertIsNone(self.service.current_post)
        self.assertEqual(self.service.replies_total, 0)

    def test_replies_page_arriving_after_leaving_is_dropped(self) -> None:
        self.client.reply_pages[1] = ForumReplyPage(
            replies=(ForumReply(id=7),), page=1, total=2, has_more=True
        )
        self.service.open_post(12)
        generation = self.service._detail_generation
        self.service.close_post()
        before = len(self.listener.replies)
        self.service._handle_replies((generation, 2, True, ForumReplyPage(replies=(ForumReply(id=8),), page=2)))
        self.assertEqual(len(self.listener.replies), before)
        self.assertFalse(self.service.has_more_replies)
        self.assertEqual(self.service.replies_total, 0)

    def test_reply_arriving_after_leaving_is_dropped(self) -> None:
        self.login()
        self.service.open_post(12)
        generation = self.service._detail_generation
        self.service.close_post()
        before = len(self.listener.reply_posted)
        self.service._handle_reply_posted((generation, self.client.reply))
        self.assertEqual(len(self.listener.reply_posted), before)
        self.assertEqual(self.service.replies_total, 0)

    def test_reopening_another_post_ignores_the_previous_answer(self) -> None:
        """换了帖子之后，上一篇迟到的正文不能盖在新帖子上。"""
        self.client.reply_pages[1] = ForumReplyPage(replies=(), page=1, total=0)
        self.service.open_post(11)
        stale = self.service._detail_generation
        self.client.post = ForumPost(id=12, title="第二篇", content="正文")
        self.service.open_post(12)
        self.service._handle_post((stale, ForumPost(id=11, title="第一篇", content="旧正文"), ForumReplyPage()))
        self.assertEqual(self.service.current_post.id, 12)

    def test_reply_requires_login_and_valid_text(self) -> None:
        self.service.open_post(12)
        self.assertEqual(self.service.post_reply(12, "  "), "回复不能为空")
        self.assertEqual(self.service.post_reply(12, "写得好"), "登录后才能回复")
        self.assertNotIn("create_reply", [name for name, _ in self.client.calls])

    def test_locked_post_refuses_replies(self) -> None:
        self.client.post = ForumPost(id=12, title="锁了", is_locked=True)
        self.login()
        self.service.open_post(12)
        self.assertIn("锁定", self.service.post_reply(12, "写得好"))

    def test_reply_is_sent_with_token_and_floor_number(self) -> None:
        self.client.reply_pages[1] = ForumReplyPage(replies=(ForumReply(id=7),), page=1, total=1, has_more=False)
        self.login()
        self.service.open_post(12)
        self.assertEqual(self.service.post_reply(12, " 写得好 ", parent_id=7), "")
        name, kwargs = self.client.calls[-1]
        self.assertEqual(name, "create_reply")
        self.assertEqual((kwargs["post_id"], kwargs["content"], kwargs["parent_id"]), (12, "写得好", 7))
        self.assertEqual(self.client.token, "t" * 64)
        reply, floor = self.listener.reply_posted[-1]
        self.assertEqual((reply.id, floor), (7, 2))
        self.assertEqual(self.service.replies_total, 2)


class ThreadTests(ServiceTestCase):
    """发帖：登录与本地校验守在前面，成功之后要让界面把新帖插进列表。"""

    def test_thread_needs_a_login(self) -> None:
        self.assertEqual(self.service.post_thread("标题", "正文"), "登录后才能发帖")
        self.assertNotIn("create_post", [name for name, _ in self.client.calls])

    def test_thread_validates_locally(self) -> None:
        self.login()
        self.assertEqual(self.service.post_thread("短", "正文"), "标题至少 2 个字")
        self.assertEqual(self.service.post_thread("标题", "   "), "正文不能为空")
        self.assertEqual(self.service.post_thread("标题", "正文", tags="a,b,c,d,e,f"), "最多 5 个标签")
        self.assertNotIn("create_post", [name for name, _ in self.client.calls])

    def test_thread_is_sent_with_title_body_and_tags(self) -> None:
        self.login()
        self.assertEqual(self.service.post_thread(" 标题 ", " 正文 ", tags="general, Demo"), "")
        name, kwargs = self.client.calls[-1]
        self.assertEqual(name, "create_post")
        self.assertEqual((kwargs["title"], kwargs["content"], kwargs["tags"]), ("标题", "正文", "general, Demo"))
        self.assertEqual(self.client.token, "t" * 64)
        self.assertEqual(self.listener.thread_posted[-1].id, 12)
        self.assertEqual(self.service.post_total, 1)

    def test_failed_thread_releases_the_guard(self) -> None:
        self.login()
        self.client.error = ForumApiError(500, "internal_error", "boom")
        self.assertEqual(self.service.post_thread("标题", "正文"), "")
        self.assertTrue(self.listener.errors)
        self.client.error = None
        self.assertEqual(self.service.post_thread("标题", "正文"), "")
        self.assertEqual([name for name, _ in self.client.calls].count("create_post"), 2)

    def test_thread_carries_uploaded_images(self) -> None:
        self.login()
        self.assertEqual(self.service.post_thread("标题", "正文", images=["a" * 32]), "")
        self.assertEqual(self.client.calls[-1][1]["images"], ["a" * 32])
        self.assertEqual(
            self.service.post_thread("标题", "正文", images=["a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32]),
            "一个帖子最多挂 4 张图，现在有 5 张",
        )
        self.assertEqual([name for name, _ in self.client.calls].count("create_post"), 1)


class ImageTests(ServiceTestCase):
    """发帖页的图片：本地先拦一道，成功走回调，缩略图先吃磁盘缓存。"""

    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    def test_upload_needs_a_login(self) -> None:
        self.assertEqual(self.service.upload_image(self.PNG), "登录后才能上传图片")
        self.assertEqual(self.client.calls, [])

    def test_upload_validates_locally(self) -> None:
        self.login()
        self.assertIn("空的", self.service.upload_image(b""))
        self.assertIn("只收 PNG", self.service.upload_image("不是图片".encode("utf-8")))
        huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * FORUM_IMAGE_MAX_BYTES
        self.assertIn("1500 KB", self.service.upload_image(huge))
        self.assertEqual(self.client.calls, [])

    def test_uploaded_image_reaches_the_listener(self) -> None:
        self.login()
        self.assertEqual(self.service.upload_image(self.PNG, name="封面.png"), "")
        self.assertTrue(self.client.image_data_urls[-1].startswith("data:image/png;base64,"))
        self.assertEqual(self.client.token, "t" * 64)
        self.assertEqual(self.listener.image_uploaded[-1].id, "a" * 32)
        self.assertEqual(self.listener.image_error, [])
        self.assertIn("封面.png", self.listener.status[0][0])
        self.assertEqual(self.listener.status[-1], ("图片上传好了", ""))

    def test_upload_failure_reports_the_server_limit(self) -> None:
        self.login()
        self.client.error = ForumApiError(413, "payload_too_large", "Image is too large.")
        self.assertEqual(self.service.upload_image(self.PNG), "")
        self.assertIn("1500 KB", self.listener.image_error[-1])
        self.assertEqual(self.listener.status[-1][1], "warn")
        self.assertEqual(self.listener.image_uploaded, [])
        # 失败之后在途标记要放掉，下一张还能传。
        self.client.error = None
        self.assertEqual(self.service.upload_image(self.PNG), "")
        self.assertEqual(self.listener.image_uploaded[-1].id, "a" * 32)

    def test_second_upload_while_one_is_in_flight_is_refused(self) -> None:
        self.login()
        service = CommunityService(
            dispatch=lambda callback: callback(),
            listener=self.listener,
            client=self.client,
            session=self.store,
            submit_io=never,
        )
        self.addCleanup(service.cleanup)
        self.assertEqual(service.upload_image(self.PNG), "")
        self.assertIn("还在上传", service.upload_image(self.PNG))
        self.assertEqual([name for name, _ in self.client.calls].count("upload_image"), 0)

    def test_thumbnail_prefers_the_disk_cache(self) -> None:
        cached = b"\x89PNG\r\n\x1a\n" + b"cached"
        forum_images.write_image_bytes("b" * 32, cached)
        self.assertTrue(self.service.load_thumbnail("b" * 32))
        self.assertEqual(self.listener.thumbnails[-1], ("b" * 32, cached))
        self.assertNotIn("fetch_image", [name for name, _ in self.client.calls])

    def test_thumbnail_fetches_once_then_serves_the_cache(self) -> None:
        fetched = b"\x89PNG\r\n\x1a\n" + b"fetched"
        self.client.image_bytes = fetched
        self.assertTrue(self.service.load_thumbnail("c" * 32))
        self.assertEqual(self.listener.thumbnails[-1], ("c" * 32, fetched))
        self.assertEqual([name for name, _ in self.client.calls].count("fetch_image"), 1)
        self.assertEqual(forum_images.read_image_bytes("c" * 32), fetched)
        self.assertTrue(self.service.load_thumbnail("c" * 32))
        self.assertEqual([name for name, _ in self.client.calls].count("fetch_image"), 1)

    def test_thumbnail_failure_is_not_an_error(self) -> None:
        self.client.error = ForumApiError(500, "internal_error", "boom")
        self.assertTrue(self.service.load_thumbnail("d" * 32))
        self.assertEqual(self.listener.thumbnails[-1], ("d" * 32, b""))
        self.assertEqual(self.listener.errors, [])
        self.assertFalse(self.service.load_thumbnail(""))
        # 失败之后在途标记要放掉，下次还能再试。
        self.client.error = None
        self.assertTrue(self.service.load_thumbnail("d" * 32))
        self.assertEqual(self.listener.thumbnails[-1][1], self.client.image_bytes)


class UserActivityTests(ServiceTestCase):
    """`load_user()`：自己的资料与最近动态，账号页「最新帖子」用它。"""

    def test_load_user_uses_the_signed_in_username(self) -> None:
        self.login()
        self.assertTrue(self.service.load_user())
        names = [kwargs.get("username") for name, kwargs in self.client.calls if name.startswith("user_")]
        self.assertEqual(names, ["demo", "demo", "demo"])

    def test_load_user_accepts_another_username(self) -> None:
        self.login()
        self.service.load_user("someone")
        self.assertEqual(self.client.calls[-1][1]["username"], "someone")

    def test_load_user_reports_the_profile_and_activity(self) -> None:
        self.login()
        self.client.post_pages[1] = ForumPostPage(posts=(ForumPost(id=31, title="甲"),), page=1, total=3, has_more=True)
        self.client.reply_pages[1] = ForumReplyPage(replies=(ForumReply(id=41),), page=1, total=5)
        self.service.load_user()
        user, posts, replies = self.listener.user_activity[-1]
        self.assertEqual(user.username, "demo")
        self.assertEqual((posts.posts[0].id, posts.total), (31, 3))
        self.assertEqual((replies.replies[0].id, replies.total), (41, 5))
        self.assertIn("3 篇帖子", self.listener.status[-1][0])

    def test_load_user_without_a_username_does_nothing(self) -> None:
        self.assertFalse(self.service.load_user())
        self.assertEqual(self.client.calls, [])


class SessionErrorTests(ServiceTestCase):
    """登录 / 注册失败：文案要按表单来说，并且把动作一起告诉界面。"""

    def test_wrong_password_says_the_password_is_wrong(self) -> None:
        self.client.error = ForumApiError(401, "unauthorized", "Invalid username or password.")
        self.assertEqual(self.service.login("demo_user", "wrong-password"), "")
        action, message = self.listener.session_error[-1]
        self.assertEqual(action, "login")
        self.assertIn("用户名或密码不对", message)
        self.assertEqual(self.listener.status[-1], (message, "warn"))

    def test_register_conflict_keeps_the_server_wording(self) -> None:
        self.client.error = ForumApiError(409, "conflict", "这个 IP 已经注册过账号了（一个 IP 只能注册一个）。")
        self.service.register("demo_user", "demo-password-123")
        action, message = self.listener.session_error[-1]
        self.assertEqual(action, "register")
        self.assertIn("这个 IP 已经注册过账号了", message)
        self.assertIn("手机热点", message)

    def test_other_failures_do_not_report_a_form_action(self) -> None:
        self.client.error = ForumApiError(500, "internal_error", "boom")
        self.service.load_posts()
        self.assertEqual(self.listener.session_error, [])


class LikeTests(ServiceTestCase):
    def test_like_needs_a_login(self) -> None:
        self.assertFalse(self.service.toggle_like("post", 12, liked=True))
        self.assertEqual(self.listener.status[-1], ("登录后才能点赞", "warn"))
        self.assertNotIn("like_post", [name for name, _ in self.client.calls])

    def test_like_post_reports_the_new_state(self) -> None:
        self.login()
        self.assertTrue(self.service.toggle_like("post", 12, liked=True))
        self.assertEqual(self.client.calls[-1], ("like_post", {"post_id": 12, "liked": True}))
        self.assertEqual(self.listener.likes[-1], ("post", 12, True, 3))

    def test_unlike_reply_reports_the_new_state(self) -> None:
        self.login()
        self.service.toggle_like("reply", 7, liked=False)
        self.assertEqual(self.client.calls[-1], ("like_reply", {"reply_id": 7, "liked": False}))
        self.assertEqual(self.listener.likes[-1], ("reply", 7, False, 2))

    def test_repeated_like_while_in_flight_is_ignored(self) -> None:
        service = CommunityService(
            dispatch=lambda callback: callback(),
            listener=self.listener,
            client=self.client,
            session=self.store,
            submit_io=never,
        )
        self.addCleanup(service.cleanup)
        self.store.set(self.client.session_result)
        self.assertTrue(service.toggle_like("post", 12, liked=True))
        self.assertFalse(service.toggle_like("post", 12, liked=True))


class AccountTests(ServiceTestCase):
    def test_login_stores_the_session_and_notifies(self) -> None:
        self.assertEqual(self.service.login("demo_user", "demo-password-123"), "")
        self.assertEqual(self.client.calls[-1][0], "login")
        self.assertTrue(self.store.logged_in())
        self.assertEqual(self.listener.session[-1].token, "t" * 64)

    def test_login_validates_locally(self) -> None:
        self.assertIn("至少 8 位", self.service.login("demo_user", "short"))
        self.assertIn("至少 3 个字符", self.service.login("ab", "demo-password-123"))
        self.assertNotIn("login", [name for name, _ in self.client.calls])

    def test_register_sends_the_display_name_and_auto_logs_in(self) -> None:
        self.assertEqual(self.service.register("demo_user", "demo-password-123", "演示"), "")
        name, kwargs = self.client.calls[-1]
        self.assertEqual((name, kwargs["display_name"]), ("register", "演示"))
        self.assertTrue(self.store.logged_in())

    def test_logout_clears_local_state_after_the_server_call(self) -> None:
        self.login()
        self.assertTrue(self.service.logout())
        self.assertEqual(self.client.calls[-1][0], "logout")
        self.assertFalse(self.store.logged_in())
        self.assertIsNone(self.listener.session[-1])
        self.assertFalse(self.service.logout())

    def test_forget_clears_locally_even_when_the_server_call_runs_after(self) -> None:
        self.login()
        self.client.error = ForumApiError(0, "network", "断网")
        self.assertTrue(self.service.logout(forget=True))
        self.assertFalse(self.store.logged_in())
        # 请求仍然带着旧 token 发出，服务端那边才能作废这个会话。
        self.assertEqual(self.client.token, "t" * 64)
        self.assertIsNone(self.listener.session[-1])

    def test_account_refresh_updates_the_profile(self) -> None:
        self.login()
        self.assertTrue(self.service.refresh_account())
        self.assertEqual(self.listener.account[-1].username, "demo")
        self.assertEqual(self.store.user().username, "demo")

    def test_auth_error_drops_the_local_session(self) -> None:
        self.login()
        self.client.error = ForumApiError(401, "unauthorized", "expired")
        self.service.refresh_account()
        self.assertFalse(self.store.logged_in())
        self.assertIsNone(self.listener.session[-1])
        self.assertIn("登录状态已失效", self.listener.errors[-1])

    def test_refresh_without_a_token_does_nothing(self) -> None:
        self.assertFalse(self.service.refresh_account())
        self.assertNotIn("me", [name for name, _ in self.client.calls])

    def test_health_is_reported(self) -> None:
        self.assertTrue(self.service.check_health())
        self.assertEqual(self.listener.health[-1]["status"], "ok")

    def test_clear_cache_reports_file_count(self) -> None:
        forum_cache.write_entry("board-new", {"posts": []})
        report = self.service.clear_cache()
        self.assertEqual(report.files, 1)
        self.assertIn("已清理浏览缓存", self.listener.status[-1][0])
        self.assertEqual(self.service.cache_summary()["files"], 0)

    def test_failures_are_reported_as_messages(self) -> None:
        self.client.error = ForumApiError(429, "rate_limited", "too many")
        self.service.load_posts()
        self.assertIn("操作太频繁", self.listener.errors[-1])
        self.assertEqual(self.listener.status[-1][1], "warn")

    def test_cleanup_stops_further_work(self) -> None:
        self.service.cleanup()
        self.assertFalse(self.service.load_posts())
        self.assertFalse(self.service.refresh_account())


class FriendlyErrorTests(unittest.TestCase):
    def test_known_and_unknown_errors(self) -> None:
        self.assertIn("网络连接失败", friendly_error(ForumApiError(0, "network", "断网")))
        self.assertEqual(friendly_error(RuntimeError("炸了")), "请求失败：炸了")
        self.assertEqual(friendly_error(RuntimeError()), "请求失败")


if __name__ == "__main__":
    unittest.main()