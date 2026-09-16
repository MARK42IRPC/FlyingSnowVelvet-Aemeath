"""社区页的网络协调器：把主站客户端包成「后台请求 + 回主线程通知」。

主论坛与账号页共用同一份实现：请求跑在 `compute_hub` 的 IO 线程池里，结果通过
`dispatch`（窗口用 `pyqtSignal` 排队）回到 UI 线程，界面代码因此不需要认识线程。

回调由 `listener` 鸭子类型提供，缺哪个就不通知哪个：

- `on_posts(page, append)` / `on_post(post)` / `on_replies(page, append)`
- `on_reply_posted(reply, floor)` / `on_likes(kind, target_id, liked, count)`
- `on_tags(tags)` / `on_account(user)` / `on_session(session)` / `on_health(data)`
- `on_status(text, tone)` / `on_error(text)`

列表的首屏结果会顺手写进 `lib/core/forum_cache.py` 的小快照（只存第一页、无筛选的
列表与标签云），下次打开先铺快照再请求；快照是**公开内容**，不含 token。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
import threading

from lib.core import forum_cache
from lib.core.compute_hub import get_compute_hub
from lib.core.forum_api import (
    FORUM_API_PAGE_SIZE,
    FORUM_DEFAULT_SORT,
    ForumApiClient,
    ForumApiError,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumSession,
    ForumUser,
    validate_password,
    validate_reply,
    validate_username,
)
from lib.core.forum_session import ForumSessionStore
from lib.core.logger import get_logger

_logger = get_logger(__name__)

_LIST_CACHE_PREFIX = "board"
_TAGS_CACHE = "tags"


class CommunityService:
    """主站读写协调器；一个实例服务一个页面，共享注入进来的客户端与会话。"""

    def __init__(
        self,
        *,
        dispatch: Callable[[Callable[[], None]], None],
        listener=None,
        client: ForumApiClient | None = None,
        session: ForumSessionStore | None = None,
        submit_io: Callable[..., Future] | None = None,
        page_size: int = FORUM_API_PAGE_SIZE,
    ) -> None:
        self._dispatch = dispatch
        self._listener = listener
        self._client = client or ForumApiClient()
        self._session = session or ForumSessionStore()
        self._submit_io = submit_io or get_compute_hub().submit_io
        self._page_size = max(1, int(page_size))

        self._lock = threading.Lock()
        self._closed = False
        self._generation = 0
        self._loading_posts = False
        self._sort = FORUM_DEFAULT_SORT
        self._tag: str | None = None
        self._query: str | None = None
        self._posts_loaded = False
        self._has_more_posts = False
        self._post_total = 0
        self._post: ForumPost | None = None
        self._replies_page = 0
        self._replies_total = 0
        self._has_more_replies = False
        self._loading_replies = False
        self._like_inflight: set[tuple[str, int]] = set()
        self._account_loading = False

    # ── 只读状态 ─────────────────────────────────────────────────────

    @property
    def sort(self) -> str:
        return self._sort

    @property
    def tag(self) -> str | None:
        return self._tag

    @property
    def query(self) -> str | None:
        return self._query

    @property
    def post_total(self) -> int:
        return self._post_total

    @property
    def has_more_posts(self) -> bool:
        return self._has_more_posts

    @property
    def loading_posts(self) -> bool:
        with self._lock:
            return self._loading_posts

    @property
    def current_post(self) -> ForumPost | None:
        return self._post

    @property
    def replies_total(self) -> int:
        return self._replies_total

    @property
    def has_more_replies(self) -> bool:
        return self._has_more_replies

    def cache_summary(self) -> dict:
        return forum_cache.cache_stats()

    # ── 快照 ─────────────────────────────────────────────────────────

    def restore_snapshot(self) -> bool:
        """把上次的列表与标签云先铺上；返回是否铺过内容。"""
        restored = False
        cached = forum_cache.read_entry(self._list_cache_key())
        if cached is not None:
            page = _posts_from_cache(cached)
            if page is not None and page.posts:
                self._post_total = page.total or len(page.posts)
                self._has_more_posts = page.has_more
                # 快照就是第一页：翻页从第 2 页开始，不会把第 1 页又铺一遍。
                self._posts_loaded = 1
                self._notify("on_posts", page, False, cached=True)
                restored = True
        tags = forum_cache.read_entry(_TAGS_CACHE)
        if tags is not None:
            parsed = _tags_from_cache(tags)
            if parsed:
                self._notify("on_tags", parsed)
                restored = True
        return restored

    def _list_cache_key(self) -> str:
        return f"{_LIST_CACHE_PREFIX}-{self._sort}"

    # ── 帖子列表 ─────────────────────────────────────────────────────

    def load_posts(
        self,
        *,
        sort: str | None = None,
        tag: str | None = None,
        query: str | None = None,
    ) -> bool:
        """读取第一页；换了排序 / 标签 / 关键字时由调用方一并传进来。"""
        if self._closed:
            return False
        if sort is not None:
            self._sort = str(sort)
        if tag is not None or query is not None:
            self._tag = (str(tag).strip() or None) if tag is not None else None
            self._query = (str(query).strip() or None) if query is not None else None
        with self._lock:
            if self._loading_posts:
                return False
            self._loading_posts = True
            self._generation += 1
            generation = self._generation
        self._notify("on_status", "正在读取帖子…", "")
        return self._submit(self._load_posts_worker, self._handle_posts, generation, 1, False)

    def load_more_posts(self) -> bool:
        """翻下一页；正在加载或已到底时忽略。"""
        if self._closed or not self._has_more_posts:
            return False
        with self._lock:
            if self._loading_posts:
                return False
            self._loading_posts = True
            generation = self._generation
            page = max(1, self._page_count_loaded() + 1)
        self._notify("on_status", "正在读取更多帖子…", "")
        return self._submit(self._load_posts_worker, self._handle_posts, generation, page, True)

    def refresh_posts(self) -> bool:
        """重读第一页并顺带刷新标签云。"""
        self.load_tags()
        return self.load_posts()

    def load_tags(self) -> bool:
        if self._closed:
            return False
        return self._submit(self._load_tags_worker, self._handle_tags)

    def _page_count_loaded(self) -> int:
        return max(0, self._posts_loaded)

    def _load_posts_worker(self, generation: int, page: int, append: bool):
        result = self._client.list_posts(
            page=page,
            per_page=self._page_size,
            sort=self._sort,
            tag=self._tag,
            query=self._query,
        )
        return generation, result, append

    def _handle_posts(self, result) -> None:
        generation, page, append = result
        if self._closed:
            return
        with self._lock:
            if generation != self._generation:
                return
            self._loading_posts = False
            self._posts_loaded = page.page if append else 1
            self._has_more_posts = bool(page.has_more)
            self._post_total = page.total or len(page.posts)
        if not append and not self._tag and not self._query:
            forum_cache.write_entry(
                self._list_cache_key(), _posts_to_cache(page, self._sort)
            )
        self._notify("on_posts", page, append)
        self._notify(
            "on_status",
            f"共 {self._post_total} 篇帖子" if not append else "已加载更多帖子",
            "",
        )

    def _load_tags_worker(self):
        return self._client.tags()

    def _handle_tags(self, tags) -> None:
        if self._closed or not tags:
            return
        forum_cache.write_entry(_TAGS_CACHE, _tags_to_cache(tags))
        self._notify("on_tags", tags)

    # ── 帖子详情与回复 ───────────────────────────────────────────────

    def open_post(self, post_id) -> bool:
        """进入帖子详情：读正文与第一页回复。"""
        if self._closed:
            return False
        if not post_id:
            return False
        with self._lock:
            self._post = None
            self._replies_page = 0
            self._replies_total = 0
            self._has_more_replies = False
            self._loading_replies = True
        self._notify("on_status", "正在读取帖子…", "")
        return self._submit(self._load_post_worker, self._handle_post, int(post_id))

    def load_more_replies(self) -> bool:
        post = self._post
        if self._closed or post is None or not self._has_more_replies:
            return False
        with self._lock:
            if self._loading_replies:
                return False
            self._loading_replies = True
            page = max(1, self._replies_page + 1)
        return self._submit(self._load_replies_worker, self._handle_replies, post.id, page, True)

    def _load_post_worker(self, post_id: int):
        return post_id, self._client.get_post(post_id), self._client.list_replies(
            post_id, page=1, per_page=self._page_size
        )

    def _handle_post(self, result) -> None:
        post_id, post, replies = result
        if self._closed:
            return
        with self._lock:
            self._post = post
            self._replies_page = replies.page if replies.replies else 0
            self._replies_total = replies.total or len(replies.replies)
            self._has_more_replies = bool(replies.has_more)
            self._loading_replies = False
        self._notify("on_post", post)
        self._notify("on_replies", replies, False)
        self._notify("on_status", f"共 {self._replies_total} 条回复", "")

    def _load_replies_worker(self, post_id: int, page: int, append: bool):
        return page, append, self._client.list_replies(
            post_id, page=page, per_page=self._page_size
        )

    def _handle_replies(self, result) -> None:
        page_number, append, page = result
        if self._closed:
            return
        with self._lock:
            self._loading_replies = False
            self._replies_page = page_number
            self._replies_total = page.total or self._replies_total
            self._has_more_replies = bool(page.has_more)
        self._notify("on_replies", page, append)

    def close_post(self) -> None:
        with self._lock:
            self._post = None
            self._replies_page = 0
            self._replies_total = 0
            self._has_more_replies = False

    def post_reply(self, post_id, content, *, parent_id=None) -> str:
        """发一条回复；返回空串表示已提交，否则是要显示在状态栏的原因。"""
        if self._closed:
            return "窗口已关闭"
        error = validate_reply(content)
        if error:
            return error
        post = self._post
        if post is not None and post.is_locked:
            return "这篇帖子已锁定，暂时不能回复"
        if not self._session.logged_in():
            return "登录后才能回复"
        text = str(content).strip()
        self._notify("on_status", "正在发送回复…", "")
        if not self._submit(self._post_reply_worker, self._handle_reply_posted, int(post_id), text, parent_id):
            return "回复请求未能发出"
        return ""

    def _post_reply_worker(self, post_id: int, content: str, parent_id):
        self._sync_token()
        return self._client.create_reply(post_id, content, parent_id=parent_id)

    def _handle_reply_posted(self, reply: ForumReply) -> None:
        if self._closed:
            return
        with self._lock:
            floor = max(1, self._replies_total + 1)
            self._replies_total = floor
            if self._post is not None:
                self._post = _with_reply_count(self._post)
        self._notify("on_reply_posted", reply, floor)
        self._notify("on_status", "回复已发送", "")

    # ── 点赞 ─────────────────────────────────────────────────────────

    def toggle_like(self, kind: str, target_id: int, *, liked: bool) -> bool:
        """切换帖子 / 回复的点赞状态；`liked` 是目标状态（由界面按当前状态取反）。"""
        if self._closed or not target_id:
            return False
        if not self._session.logged_in():
            self._notify("on_status", "登录后才能点赞", "warn")
            return False
        key = (str(kind), int(target_id))
        with self._lock:
            if key in self._like_inflight:
                return False
            self._like_inflight.add(key)
        return self._submit(
            self._like_worker, self._handle_liked, key[0], key[1], bool(liked)
        )

    def _like_worker(self, kind: str, target_id: int, liked: bool):
        self._sync_token()
        if kind == "reply":
            result = self._client.like_reply(target_id, liked=liked)
        else:
            result = self._client.like_post(target_id, liked=liked)
        return kind, target_id, result

    def _handle_liked(self, result) -> None:
        kind, target_id, like_result = result
        if self._closed:
            return
        with self._lock:
            self._like_inflight.discard((kind, int(target_id)))
        self._notify("on_likes", kind, int(target_id), like_result.liked, like_result.like_count)

    # ── 账号 ─────────────────────────────────────────────────────────

    def login(self, username, password) -> str:
        error = _validate_credentials(username, password)
        if error:
            return error
        self._notify("on_status", "正在登录…", "")
        self._submit(self._login_worker, self._handle_session, str(username).strip(), str(password))
        return ""

    def register(self, username, password, display_name=None) -> str:
        error = _validate_credentials(username, password, allow_short_password=False)
        if error:
            return error
        self._notify("on_status", "正在注册…", "")
        self._submit(
            self._register_worker,
            self._handle_session,
            str(username).strip(),
            str(password),
            str(display_name or "").strip() or None,
        )
        return ""

    def _login_worker(self, username: str, password: str):
        return self._client.login(username, password)

    def _register_worker(self, username: str, password: str, display_name):
        # 注册成功即自动登录：服务端直接返回 token，拿到就落盘（细节 5）。
        return self._client.register(username, password, display_name)

    def _handle_session(self, session: ForumSession) -> None:
        if self._closed:
            return
        self._session.set(session)
        self._notify("on_session", session)
        self._notify("on_status", f"已登录：{session.user.label}", "")

    def logout(self, *, forget: bool = False) -> bool:
        """退出登录：先让服务端作废 token，再清本地。

        `forget=True` 是「清理登录数据」：本地文件与服务端 token 都要处理，但即使请求
        失败也照样清本地，用户点的是本地清理，不该被网络挡住。
        """
        if self._closed:
            return False
        token = self._session.token()
        if not token:
            self._session.clear()
            self._notify("on_session", None)
            return False
        if forget:
            # 先把 token 交出去再清本地：清完本地就没有凭据可发给服务端了。
            self._session.clear()
            self._notify("on_session", None)
            self._notify("on_status", "已清理本机登录数据", "")
            self._submit(self._logout_worker, lambda _result: None, token)
            return True
        self._submit(self._logout_worker, self._handle_logged_out, token)
        return True

    def _logout_worker(self, token: str):
        """用调用方给的 token 作废会话：退出路径会先清本地，不能读 store。"""
        self._client.token = str(token or "")
        self._client.logout()
        return None

    def _handle_logged_out(self, _result) -> None:
        if self._closed:
            return
        self._session.clear()
        self._notify("on_session", None)
        self._notify("on_status", "已退出登录", "")

    def refresh_account(self) -> bool:
        """用 `/auth/me` 校验 token 并刷新资料；401 直接清掉本地登录态。"""
        if self._closed or not self._session.token() or self._account_loading:
            return False
        self._account_loading = True
        return self._submit(self._me_worker, self._handle_account)

    def _me_worker(self):
        self._sync_token()
        return self._client.me()

    def _handle_account(self, user: ForumUser) -> None:
        self._account_loading = False
        if self._closed:
            return
        session = self._session.get()
        if session is not None:
            self._session.set(ForumSession(
                token=session.token,
                user=user,
                expires_at=session.expires_at,
                token_type=session.token_type,
            ))
        self._notify("on_account", user)

    def check_health(self) -> bool:
        """查一次论坛存活状态；账号页把它当成「论坛还在不在」的按钮。"""
        if self._closed:
            return False
        return self._submit(self._health_worker, self._handle_health)

    def _health_worker(self):
        return self._client.health()

    def _handle_health(self, payload) -> None:
        if self._closed:
            return
        self._notify("on_health", payload)

    def clear_cache(self) -> forum_cache.ForumCacheReport:
        report = forum_cache.clear_cache()
        self._notify("on_status", f"已清理浏览缓存（{report.files} 个文件）", "")
        return report

    # ── 生命周期 ─────────────────────────────────────────────────────

    def cleanup(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._loading_posts = False
            self._loading_replies = False
            self._like_inflight.clear()

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _sync_token(self) -> None:
        self._client.token = self._session.token()

    def _submit(self, worker, handler, *args) -> bool:
        try:
            future = self._submit_io(worker, *args)
        except Exception as exc:
            _logger.warning("[Community] 请求未能提交: %s", exc)
            self._handle_error(str(exc))
            return False

        def complete(done: Future) -> None:
            try:
                result = done.result()
            except Exception as exc:
                self._dispatch(lambda message=friendly_error(exc), error=exc: self._handle_error(message, error))
                return
            self._dispatch(lambda result=result: handler(result))

        future.add_done_callback(complete)
        return True

    def _handle_error(self, message: str, error: Exception | None = None) -> None:
        if self._closed:
            return
        with self._lock:
            self._loading_posts = False
            self._loading_replies = False
            self._account_loading = False
            self._like_inflight.clear()
        if error is not None and isinstance(error, ForumApiError) and error.is_auth_error():
            # token 失效：本地登录态留着只会一直 401。
            self._session.clear()
            self._notify("on_session", None)
        self._notify("on_error", message)
        self._notify("on_status", message, "warn")

    def _notify(self, name: str, *args, **kwargs) -> None:
        handler = getattr(self._listener, name, None) if self._listener is not None else None
        if handler is None:
            return
        try:
            handler(*args, **kwargs)
        except Exception as exc:
            _logger.warning("[Community] 回调 %s 失败: %s", name, exc)


def friendly_error(error: Exception) -> str:
    """把异常翻成一句中文；`ForumApiError` 自己会翻，其余按网络问题处理。"""
    if isinstance(error, ForumApiError):
        return error.friendly()
    text = str(error).strip()
    return f"请求失败：{text}" if text else "请求失败"


def _validate_credentials(username, password, *, allow_short_password: bool = False) -> str:
    error = validate_username(username)
    if error:
        return error
    if allow_short_password:
        if not str(password or ""):
            return "请输入密码"
        return ""
    return validate_password(password)


def _with_reply_count(post: ForumPost) -> ForumPost:
    """回复成功后列表里的条数要 +1，这里返回一份只改计数的副本。"""
    return ForumPost(
        id=post.id,
        title=post.title,
        content=post.content,
        excerpt=post.excerpt,
        tags=post.tags,
        author=post.author,
        reply_count=post.reply_count + 1,
        like_count=post.like_count,
        view_count=post.view_count,
        is_pinned=post.is_pinned,
        is_locked=post.is_locked,
        image_count=post.image_count,
        liked_by_me=post.liked_by_me,
        created_at=post.created_at,
        updated_at=post.updated_at,
        last_reply_at=post.last_reply_at,
    )


def _user_to_cache(user: ForumUser | None) -> dict:
    if user is None:
        return {}
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


def post_to_cache(post: ForumPost) -> dict:
    """把一篇帖子写成快照用的字典；字段名与服务端一致，恢复时复用同一套解析。"""
    return {
        "id": post.id,
        "title": post.title,
        "content": post.content,
        "excerpt": post.excerpt,
        "tags": list(post.tags),
        "author": _user_to_cache(post.author),
        "reply_count": post.reply_count,
        "like_count": post.like_count,
        "view_count": post.view_count,
        "is_pinned": post.is_pinned,
        "is_locked": post.is_locked,
        "liked_by_me": post.liked_by_me,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "last_reply_at": post.last_reply_at,
    }


def _posts_to_cache(page: ForumPostPage, sort: str) -> dict:
    return {
        "sort": sort,
        "page": page.page,
        "per_page": page.per_page,
        "total": page.total,
        "total_pages": page.total_pages,
        "has_more": page.has_more,
        "posts": [post_to_cache(post) for post in page.posts],
    }


def _posts_from_cache(payload) -> ForumPostPage | None:
    from lib.core.forum_api import parse_post_page

    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        return None
    try:
        return parse_post_page(payload)
    except Exception:
        return None


def _tags_to_cache(tags) -> dict:
    return {"tags": [{"name": tag.name, "count": tag.count} for tag in tags]}


def _tags_from_cache(payload):
    from lib.core.forum_api import parse_tags

    if not isinstance(payload, dict):
        return ()
    return parse_tags(payload.get("tags"))


__all__ = [
    "CommunityService",
    "friendly_error",
    "post_to_cache",
]