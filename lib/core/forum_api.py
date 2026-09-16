"""雪绒论坛主站（forum.fxxr.store）的 HTTP 客户端。

留言墙（`wall.fxxr.store`，见 `lib/core/forum.py`）只有一面墙；主站是带账号、帖子、
回复与点赞的完整社区，只有 JSON API、没有网页。契约见 `.oprate/论坛使用指南.html`，
本模块是它在代码里的事实源。

- 统一响应是 `{"ok": true, "data": ...}` 与 `{"ok": false, "error": {...}}`，失败一律抛
  `ForumApiError`，调用方只需要 `error.friendly()` 拿一句中文。
- 鉴权用 `Authorization: Bearer <token>`，token 有效期 30 天。
- 主站时间戳是**秒**（留言墙是毫秒，两套不能混用），`format_timestamp()` 是这里的口径。
- 和 `lib/core/forum.py` 一样不导入任何 GUI：请求函数可注入以便测试，线程与回主线程由
  `lib/core/forum_community.py` 负责。
- 图片接口（`/api/images`）没有接入：浏览帖子只渲染文字，不下载图片字节，打开一篇配图帖
  不该顺带拉几 MB 流量（正文里的图片按 `lib/core/forum_markdown.py` 的规则降级成占位符）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import math
import time

import requests

from lib.core.logger import get_logger

_logger = get_logger(__name__)

FORUM_API_BASE = "https://forum.fxxr.store/api"
FORUM_API_TIMEOUT = (5.0, 15.0)
FORUM_API_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
FORUM_API_HEADERS = {
    "User-Agent": "FlyingSnowVelvet-Community/1.0",
    "Accept": "application/json",
}

#: 分页：默认一页 20 条，服务端上限 50。
FORUM_API_PAGE_SIZE = 20
FORUM_API_MAX_PAGE_SIZE = 50
#: 关键词搜索用 `q`，服务端按标题与正文模糊匹配。
FORUM_API_SORTS = ("new", "hot", "active", "old")
FORUM_SORT_LABELS = {"new": "最新", "hot": "最热", "active": "最近回复", "old": "最早"}
FORUM_DEFAULT_SORT = "new"

#: 服务端限制（`GET /api` 的 `limits` 是权威，这里是打开面板前的本地兜底校验）。
FORUM_TITLE_MIN = 2
FORUM_TITLE_MAX = 120
FORUM_CONTENT_MAX = 20000
FORUM_REPLY_MAX = 10000
FORUM_TAG_MAX_COUNT = 5
FORUM_TAG_MAX_LENGTH = 20
FORUM_USERNAME_MIN = 3
FORUM_USERNAME_MAX = 24
FORUM_PASSWORD_MIN = 8
FORUM_DISPLAY_NAME_MAX = 32
FORUM_TAGS_LIMIT = 50


@dataclass(frozen=True, slots=True)
class ForumUser:
    """一个账号的公开资料；`post_count` / `reply_count` / `likes_given` 可能缺省。"""

    id: int = 0
    username: str = ""
    display_name: str = ""
    bio: str = ""
    role: str = "user"
    post_count: int = 0
    reply_count: int = 0
    likes_given: int = 0
    created_at: int = 0

    @property
    def label(self) -> str:
        """界面上显示的名字：优先显示名，没有就退回用户名。"""
        return self.display_name or self.username or "未知用户"


@dataclass(frozen=True, slots=True)
class ForumSession:
    """一次登录的结果：token 加上它代表的人与到期时间。"""

    token: str = ""
    user: ForumUser = field(default_factory=ForumUser)
    expires_at: int = 0
    token_type: str = "Bearer"

    def expired(self, *, now: int | None = None) -> bool:
        if self.expires_at <= 0:
            return False
        return int(now if now is not None else time.time()) >= self.expires_at


@dataclass(frozen=True, slots=True)
class ForumPost:
    """列表与详情共用的一篇帖子；`content` 是 Markdown 原文。"""

    id: int = 0
    title: str = ""
    content: str = ""
    excerpt: str = ""
    tags: tuple[str, ...] = ()
    author: ForumUser | None = None
    reply_count: int = 0
    like_count: int = 0
    view_count: int = 0
    is_pinned: bool = False
    is_locked: bool = False
    image_count: int = 0
    liked_by_me: bool = False
    created_at: int = 0
    updated_at: int = 0
    last_reply_at: int = 0


@dataclass(frozen=True, slots=True)
class ForumPostPage:
    posts: tuple[ForumPost, ...] = ()
    page: int = 1
    per_page: int = FORUM_API_PAGE_SIZE
    total: int = 0
    total_pages: int = 0
    has_more: bool = False
    sort: str = FORUM_DEFAULT_SORT


@dataclass(frozen=True, slots=True)
class ForumReply:
    """一条回复；`parent_id` 非空时是楼中楼，UI 用它算「回复 #N 楼」。"""

    id: int = 0
    post_id: int = 0
    parent_id: int | None = None
    content: str = ""
    author: ForumUser | None = None
    like_count: int = 0
    liked_by_me: bool = False
    image_count: int = 0
    created_at: int = 0
    updated_at: int = 0
    #: 只有 `/users/:name/replies` 会带：这条回复挂在哪个帖子上。
    post_title: str = ""


@dataclass(frozen=True, slots=True)
class ForumReplyPage:
    replies: tuple[ForumReply, ...] = ()
    page: int = 1
    per_page: int = FORUM_API_PAGE_SIZE
    total: int = 0
    total_pages: int = 0
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class ForumTag:
    name: str = ""
    count: int = 0


@dataclass(frozen=True, slots=True)
class ForumLikeResult:
    """点赞切换的结果：服务端返回切换后的状态与总数，本地直接采信。"""

    target_type: str = ""
    target_id: int = 0
    liked: bool = False
    like_count: int = 0


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _as_text(value) -> str:
    return str(value or "").strip()


def _unwrap(payload, key: str):
    """服务端把新建 / 读取的单个对象再嵌一层键名（`{"post": {...}}`）。

    客户端只关心里面那个对象；顺手兼容摊平的写法，服务端换形状时不会整块功能报
    「论坛没有返回…」。
    """
    if isinstance(payload, dict):
        inner = payload.get(key)
        if isinstance(inner, dict):
            return inner
    return payload


def _as_tags(value) -> tuple[str, ...]:
    """标签可能是 `"a,b"` 或 `["a","b"]`；两种都按服务端语义转成小写元组。"""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return ()
    tags: list[str] = []
    for item in items:
        tag = _as_text(item).lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags[:FORUM_TAG_MAX_COUNT])


def parse_user(payload) -> ForumUser | None:
    """解析作者 / 用户资料；结构不对返回 None，调用方按「未知用户」显示。"""
    if not isinstance(payload, dict):
        return None
    username = _as_text(payload.get("username"))
    if not username:
        return None
    return ForumUser(
        id=_as_int(payload.get("id")),
        username=username,
        display_name=_as_text(payload.get("display_name")),
        bio=_as_text(payload.get("bio")),
        role=_as_text(payload.get("role")) or "user",
        post_count=_as_int(payload.get("post_count")),
        reply_count=_as_int(payload.get("reply_count")),
        likes_given=_as_int(payload.get("likes_given")),
        created_at=_as_int(payload.get("created_at")),
    )


def parse_session(payload) -> ForumSession:
    """解析注册 / 登录 / `me` 的 `data` 段（`me` 只有 user，没有 token）。"""
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    return ForumSession(
        token=_as_text(payload.get("token")),
        user=parse_user(payload.get("user")) or ForumUser(),
        expires_at=_as_int(payload.get("expires_at")),
        token_type=_as_text(payload.get("token_type")) or "Bearer",
    )


def parse_post(payload) -> ForumPost:
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    images = payload.get("images")
    image_count = len(images) if isinstance(images, (list, tuple)) else 0
    return ForumPost(
        id=_as_int(payload.get("id")),
        title=_as_text(payload.get("title")),
        content=str(payload.get("content") or ""),
        excerpt=_as_text(payload.get("excerpt")),
        tags=_as_tags(payload.get("tags")),
        author=parse_user(payload.get("author")),
        reply_count=_as_int(payload.get("reply_count")),
        like_count=_as_int(payload.get("like_count")),
        view_count=_as_int(payload.get("view_count")),
        is_pinned=_as_bool(payload.get("is_pinned")),
        is_locked=_as_bool(payload.get("is_locked")),
        image_count=image_count,
        liked_by_me=_as_bool(payload.get("liked_by_me")),
        created_at=_as_int(payload.get("created_at")),
        updated_at=_as_int(payload.get("updated_at")),
        last_reply_at=_as_int(payload.get("last_reply_at")),
    )


def parse_post_page(payload) -> ForumPostPage:
    """解析帖子列表；单条结构不对时跳过那一条，不整页失败。"""
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    raw_posts = payload.get("posts")
    posts: list[ForumPost] = []
    if isinstance(raw_posts, (list, tuple)):
        for item in raw_posts:
            if not isinstance(item, dict):
                continue
            post = parse_post(item)
            if post.id:
                posts.append(post)
    per_page = _as_int(payload.get("per_page"), FORUM_API_PAGE_SIZE) or FORUM_API_PAGE_SIZE
    page = max(1, _as_int(payload.get("page"), 1))
    total = max(0, _as_int(payload.get("total")))
    total_pages = max(0, _as_int(payload.get("total_pages")))
    return ForumPostPage(
        posts=tuple(posts),
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        has_more=_as_bool(payload.get("has_more")) or page < total_pages,
        sort=_as_text(payload.get("sort")) or FORUM_DEFAULT_SORT,
    )


def parse_reply(payload) -> ForumReply:
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    parent = payload.get("parent_id")
    images = payload.get("images")
    return ForumReply(
        id=_as_int(payload.get("id")),
        post_id=_as_int(payload.get("post_id")),
        parent_id=None if parent in (None, "") else _as_int(parent),
        content=str(payload.get("content") or ""),
        author=parse_user(payload.get("author")),
        like_count=_as_int(payload.get("like_count")),
        liked_by_me=_as_bool(payload.get("liked_by_me")),
        image_count=len(images) if isinstance(images, (list, tuple)) else 0,
        created_at=_as_int(payload.get("created_at")),
        updated_at=_as_int(payload.get("updated_at")),
        post_title=_as_text(payload.get("post_title")),
    )


def parse_reply_page(payload) -> ForumReplyPage:
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    raw_replies = payload.get("replies")
    replies: list[ForumReply] = []
    if isinstance(raw_replies, (list, tuple)):
        for item in raw_replies:
            if not isinstance(item, dict):
                continue
            reply = parse_reply(item)
            if reply.id:
                replies.append(reply)
    per_page = _as_int(payload.get("per_page"), FORUM_API_PAGE_SIZE) or FORUM_API_PAGE_SIZE
    page = max(1, _as_int(payload.get("page"), 1))
    total = max(0, _as_int(payload.get("total")))
    total_pages = max(0, _as_int(payload.get("total_pages")))
    return ForumReplyPage(
        replies=tuple(replies),
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        has_more=_as_bool(payload.get("has_more")) or page < total_pages,
    )


def parse_tags(payload) -> tuple[ForumTag, ...]:
    """解析标签云；`count` 缺省按 0，按服务端给的顺序保留（服务端按帖子数降序）。"""
    if not isinstance(payload, (list, tuple)):
        return ()
    tags: list[ForumTag] = []
    for item in payload:
        if isinstance(item, dict):
            name = _as_text(item.get("name")).lower()
            count = _as_int(item.get("count"))
        elif isinstance(item, str):
            name = _as_text(item).lower()
            count = 0
        else:
            continue
        if name:
            tags.append(ForumTag(name=name, count=count))
    return tuple(tags)


def parse_like_result(payload) -> ForumLikeResult:
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    return ForumLikeResult(
        target_type=_as_text(payload.get("target_type")),
        target_id=_as_int(payload.get("target_id")),
        liked=_as_bool(payload.get("liked")),
        like_count=_as_int(payload.get("like_count")),
    )


def format_timestamp(created_at, *, now: int | None = None) -> str:
    """把主站的**秒**级时间戳写成人话；留言墙的 `format_relative_time` 是毫秒口径。"""
    try:
        stamp = int(created_at)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    current = int(now if now is not None else time.time())
    seconds = max(0, current - stamp)
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 30:
        return f"{days} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def format_date(stamp, *, with_time: bool = False) -> str:
    """绝对时间：账号页的注册时间用日期，日志用日期加时分。"""
    value = _as_int(stamp)
    if value <= 0:
        return ""
    pattern = "%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d"
    return time.strftime(pattern, time.localtime(value))


def format_expiry(expires_at, *, now: int | None = None) -> str:
    """token 到期时间：写成日期加剩余天数，账号页直接显示。"""
    stamp = _as_int(expires_at)
    if stamp <= 0:
        return "未知"
    current = int(now if now is not None else time.time())
    days = max(0, math.ceil((stamp - current) / 86400.0))
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
    return f"{date}（还有 {days} 天）"


class ForumApiError(Exception):
    """一次失败的请求：`status` 是 HTTP 状态（网络层失败为 0），`code` 是服务端错误码。"""

    def __init__(self, status: int, code: str, message: str, details=None) -> None:
        super().__init__(message or code or f"HTTP {status}")
        self.status = int(status or 0)
        self.code = _as_text(code)
        self.message = _as_text(message)
        self.details = details if isinstance(details, dict) else {}

    def friendly(self) -> str:
        """给用户看的一句话；服务端英文原因只在没有更合适的中文时补在后面。"""
        status = self.status
        if status == 0:
            return f"网络连接失败：{self.message or '请检查网络后重试'}"
        if status == 400:
            base = "内容不符合论坛要求"
        elif status == 401:
            return "登录状态已失效，请重新登录"
        elif status == 403:
            return "没有权限：可能不是作者本人、帖子已锁定或账号被封禁"
        elif status == 404:
            return "内容不存在或已被删除"
        elif status == 405:
            return "论坛接口不接受这个请求方法"
        elif status == 409:
            return "用户名已存在，或新密码和旧密码相同"
        elif status == 413:
            return "内容太大，论坛不接受"
        elif status == 415:
            return "请求格式不对（论坛只收 JSON）"
        elif status == 429:
            return "操作太频繁，等一分钟再试"
        elif status >= 500:
            return "论坛服务端出错了，稍后再试"
        else:
            base = f"论坛请求失败（HTTP {status}）"
        detail = self.message
        return f"{base}：{detail}" if detail else base

    def is_auth_error(self) -> bool:
        """token 失效一类：调用方据此清掉本地登录态。"""
        return self.status == 401 or self.code == "unauthorized"


class ForumApiClient:
    """主站客户端：一个实例持有当前 token，方法一一对应端点。"""

    def __init__(
        self,
        *,
        token: str = "",
        request: Callable[..., object] | None = None,
        timeout=FORUM_API_TIMEOUT,
        base_url: str = FORUM_API_BASE,
    ) -> None:
        self.token = str(token or "")
        self._request = request or requests.request
        self._timeout = timeout
        self._base_url = str(base_url or FORUM_API_BASE).rstrip("/")

    # ── 传输层 ───────────────────────────────────────────────────────

    def _call(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        params=None,
        authorized: bool = True,
    ) -> object:
        """发一次请求并返回 `data` 段；失败抛 `ForumApiError`。

        `authorized=True` 时带上当前 token（没登录就不带，服务端会把 `liked_by_me`
        之类按匿名处理）。服务端返回非 JSON（例如 Cloudflare 的错误页）也当成协议错误。
        """
        url = f"{self._base_url}{path}"
        headers = dict(FORUM_API_HEADERS)
        if authorized and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._request(
                method,
                url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except Exception as exc:
            raise ForumApiError(0, "network", str(exc)) from exc
        try:
            status = _as_int(getattr(response, "status_code", 0))
            payload = _read_json(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if not isinstance(payload, dict):
            raise ForumApiError(status, "bad_response", "论坛返回的不是 JSON 对象")
        if payload.get("ok") is False:
            error = payload.get("error")
            error = error if isinstance(error, dict) else {}
            raise ForumApiError(
                status,
                _as_text(error.get("code")),
                _as_text(error.get("message")),
                error.get("details"),
            )
        if status >= 400:
            raise ForumApiError(status, "", "", None)
        data = payload.get("data")
        return data if isinstance(data, dict) else (data if isinstance(data, list) else {})

    # ── 账号 ─────────────────────────────────────────────────────────

    def register(self, username, password, display_name=None) -> ForumSession:
        body = {"username": _as_text(username), "password": str(password or "")}
        if _as_text(display_name):
            body["display_name"] = _as_text(display_name)
        session = parse_session(self._call("POST", "/auth/register", json_body=body, authorized=False))
        self._remember(session)
        return session

    def login(self, username, password) -> ForumSession:
        session = parse_session(
            self._call(
                "POST",
                "/auth/login",
                json_body={"username": _as_text(username), "password": str(password or "")},
                authorized=False,
            )
        )
        self._remember(session)
        return session

    def logout(self) -> None:
        """让服务端作废当前 token；失败也照样忘掉本地 token（调用方负责）。"""
        self._call("POST", "/auth/logout", json_body={})

    def me(self) -> ForumUser:
        data = self._call("GET", "/auth/me")
        user = parse_user(data.get("user")) if isinstance(data, dict) else None
        if user is None:
            raise ForumApiError(0, "bad_response", "论坛没有返回账号资料")
        return user

    def health(self) -> dict:
        data = self._call("GET", "/health", authorized=False)
        return data if isinstance(data, dict) else {}

    # ── 帖子 ─────────────────────────────────────────────────────────

    def list_posts(
        self,
        *,
        page: int = 1,
        per_page: int = FORUM_API_PAGE_SIZE,
        sort: str = FORUM_DEFAULT_SORT,
        tag: str | None = None,
        author: str | None = None,
        query: str | None = None,
    ) -> ForumPostPage:
        params: dict[str, object] = {
            "page": max(1, _as_int(page, 1)),
            "per_page": _clamp_per_page(per_page),
            "sort": _normalize_sort(sort),
        }
        for key, value in (("tag", tag), ("author", author), ("q", query)):
            text = _as_text(value)
            if text:
                params[key] = text
        return parse_post_page(self._call("GET", "/posts", params=params))

    def get_post(self, post_id) -> ForumPost:
        """详情；服务端顺手把 `view_count` 加 1，本地不重复计。"""
        data = self._call("GET", f"/posts/{_as_int(post_id)}")
        post = parse_post(_unwrap(data, "post"))
        if not post.id:
            raise ForumApiError(0, "bad_response", "论坛没有返回这篇帖子")
        return post

    def create_post(self, title, content, *, tags=None) -> ForumPost:
        """发一篇新帖；只发标签不传图片（图片接口没接入，见模块开头说明）。"""
        body: dict[str, object] = {
            "title": _as_text(title),
            "content": str(content or ""),
        }
        tag_list = list(_as_tags(tags))
        if tag_list:
            body["tags"] = tag_list
        data = self._call("POST", "/posts", json_body=body)
        post = parse_post(_unwrap(data, "post"))
        if not post.id:
            raise ForumApiError(0, "bad_response", "论坛没有返回这篇帖子")
        return post

    def tags(self, limit: int = FORUM_TAGS_LIMIT) -> tuple[ForumTag, ...]:
        data = self._call("GET", "/tags", params={"limit": max(1, _as_int(limit, FORUM_TAGS_LIMIT))}, authorized=False)
        return parse_tags(data)

    # ── 用户资料 ─────────────────────────────────────────────────────

    def user_profile(self, username) -> ForumUser:
        """公开资料；不需要 token，用户名大小写不敏感。"""
        name = _require_username(username)
        user = parse_user(_unwrap(self._call("GET", f"/users/{name}"), "user"))
        if user is None:
            raise ForumApiError(0, "bad_response", "论坛没有返回这位用户的资料")
        return user

    def user_posts(
        self,
        username,
        *,
        page: int = 1,
        per_page: int = FORUM_API_PAGE_SIZE,
    ) -> ForumPostPage:
        """某个人发的帖子；服务端只认分页参数。"""
        name = _require_username(username)
        params = {
            "page": max(1, _as_int(page, 1)),
            "per_page": _clamp_per_page(per_page),
        }
        return parse_post_page(self._call("GET", f"/users/{name}/posts", params=params))

    def user_replies(
        self,
        username,
        *,
        page: int = 1,
        per_page: int = FORUM_API_PAGE_SIZE,
    ) -> ForumReplyPage:
        """某个人发的回复；每条额外带 `post_title`，方便点回原帖。"""
        name = _require_username(username)
        params = {
            "page": max(1, _as_int(page, 1)),
            "per_page": _clamp_per_page(per_page),
        }
        return parse_reply_page(self._call("GET", f"/users/{name}/replies", params=params))

    # ── 回复 ─────────────────────────────────────────────────────────

    def list_replies(
        self,
        post_id,
        *,
        page: int = 1,
        per_page: int = FORUM_API_PAGE_SIZE,
    ) -> ForumReplyPage:
        params = {
            "page": max(1, _as_int(page, 1)),
            "per_page": _clamp_per_page(per_page),
        }
        return parse_reply_page(
            self._call("GET", f"/posts/{_as_int(post_id)}/replies", params=params)
        )

    def create_reply(self, post_id, content, *, parent_id=None) -> ForumReply:
        body: dict[str, object] = {"content": str(content or "")}
        if parent_id not in (None, ""):
            body["parent_id"] = _as_int(parent_id)
        data = self._call("POST", f"/posts/{_as_int(post_id)}/replies", json_body=body)
        reply = parse_reply(_unwrap(data, "reply"))
        if not reply.id:
            raise ForumApiError(0, "bad_response", "论坛没有返回这条回复")
        return reply

    # ── 点赞 ─────────────────────────────────────────────────────────

    def like_post(self, post_id, *, liked: bool = True) -> ForumLikeResult:
        method = "POST" if liked else "DELETE"
        return parse_like_result(self._call(method, f"/posts/{_as_int(post_id)}/like"))

    def like_reply(self, reply_id, *, liked: bool = True) -> ForumLikeResult:
        method = "POST" if liked else "DELETE"
        return parse_like_result(self._call(method, f"/replies/{_as_int(reply_id)}/like"))

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _remember(self, session: ForumSession) -> None:
        self.token = session.token


def _read_json(response):
    payload = getattr(response, "content", None)
    if isinstance(payload, (bytes, bytearray)) and len(payload) > FORUM_API_MAX_RESPONSE_BYTES:
        raise ForumApiError(0, "too_large", "论坛返回内容过大")
    try:
        return response.json()
    except Exception as exc:
        raise ForumApiError(_as_int(getattr(response, "status_code", 0)), "bad_response", "论坛返回的不是 JSON") from exc


def _require_username(username) -> str:
    name = _as_text(username)
    if not name:
        raise ForumApiError(0, "bad_request", "缺少用户名")
    return name


def _clamp_per_page(value) -> int:
    return max(1, min(_as_int(value, FORUM_API_PAGE_SIZE) or FORUM_API_PAGE_SIZE, FORUM_API_MAX_PAGE_SIZE))


def _normalize_sort(value) -> str:
    sort = _as_text(value).lower()
    return sort if sort in FORUM_API_SORTS else FORUM_DEFAULT_SORT


def validate_username(username) -> str:
    """注册 / 登录前的本地校验，返回空串表示通过。"""
    text = _as_text(username)
    if len(text) < FORUM_USERNAME_MIN:
        return f"用户名至少 {FORUM_USERNAME_MIN} 个字符"
    if len(text) > FORUM_USERNAME_MAX:
        return f"用户名最多 {FORUM_USERNAME_MAX} 个字符"
    for char in text:
        if not (char.isascii() and (char.isalnum() or char in "_-")):
            return "用户名只能用字母、数字、下划线和中划线"
    return ""


def validate_password(password) -> str:
    text = str(password or "")
    if len(text) < FORUM_PASSWORD_MIN:
        return f"密码至少 {FORUM_PASSWORD_MIN} 位"
    return ""


def validate_content(content) -> str:
    """帖子正文规则：不能空、不超过 `FORUM_CONTENT_MAX`；返回空串表示可用。"""
    text = str(content or "").strip()
    if not text:
        return "正文不能为空"
    if len(text) > FORUM_CONTENT_MAX:
        return f"正文不能超过 {FORUM_CONTENT_MAX} 字"
    return ""


def validate_tags(value) -> str:
    """标签在服务端是可选字段，这里只拦「数量」与「单个长度」两条硬规则。"""
    if isinstance(value, str):
        items = str(value).split(",")
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return ""
    items = [item.strip() for item in items if item.strip()]
    if len(items) > FORUM_TAG_MAX_COUNT:
        return f"最多 {FORUM_TAG_MAX_COUNT} 个标签"
    for item in items:
        if len(item) > FORUM_TAG_MAX_LENGTH:
            return f"每个标签最多 {FORUM_TAG_MAX_LENGTH} 个字"
    return ""


def validate_reply(content) -> str:
    text = str(content or "").strip()
    if not text:
        return "回复不能为空"
    if len(text) > FORUM_REPLY_MAX:
        return f"回复不能超过 {FORUM_REPLY_MAX} 字"
    return ""


def validate_title(title) -> str:
    text = str(title or "").strip()
    if len(text) < FORUM_TITLE_MIN:
        return f"标题至少 {FORUM_TITLE_MIN} 个字"
    if len(text) > FORUM_TITLE_MAX:
        return f"标题最多 {FORUM_TITLE_MAX} 个字"
    return ""


__all__ = [
    "FORUM_API_BASE",
    "FORUM_API_HEADERS",
    "FORUM_API_MAX_PAGE_SIZE",
    "FORUM_API_MAX_RESPONSE_BYTES",
    "FORUM_API_PAGE_SIZE",
    "FORUM_API_SORTS",
    "FORUM_API_TIMEOUT",
    "FORUM_CONTENT_MAX",
    "FORUM_DEFAULT_SORT",
    "FORUM_DISPLAY_NAME_MAX",
    "FORUM_PASSWORD_MIN",
    "FORUM_REPLY_MAX",
    "FORUM_SORT_LABELS",
    "FORUM_TAG_MAX_COUNT",
    "FORUM_TAG_MAX_LENGTH",
    "FORUM_TAGS_LIMIT",
    "FORUM_TITLE_MAX",
    "FORUM_TITLE_MIN",
    "FORUM_USERNAME_MAX",
    "FORUM_USERNAME_MIN",
    "ForumApiClient",
    "ForumApiError",
    "ForumLikeResult",
    "ForumPost",
    "ForumPostPage",
    "ForumReply",
    "ForumReplyPage",
    "ForumSession",
    "ForumTag",
    "ForumUser",
    "format_date",
    "format_expiry",
    "format_timestamp",
    "parse_like_result",
    "parse_post",
    "parse_post_page",
    "parse_reply",
    "parse_reply_page",
    "parse_session",
    "parse_tags",
    "parse_user",
    "validate_content",
    "validate_password",
    "validate_tags",
    "validate_reply",
    "validate_title",
    "validate_username",
]