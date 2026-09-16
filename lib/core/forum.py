"""Backend-neutral client for the 雪绒论坛 message wall (wall.fxxr.store).

论坛只有两个接口：`GET /api/feed` 按 id 倒序分页读取，`POST /api/messages` 发帖。
服务端限流是同一 IP 6 秒 1 条、每小时 30 条；本模块在客户端额外加 12 秒冷却
（`FORUM_POST_COOLDOWN_SECS`），比服务端保守，避免用户点出 429。

服务端没有任何内容检查，所以发帖前先过一遍 `lib/core/forum_filter` 的本地过滤
（链接、六位以上数字串、违规词）；命中即拒绝，由窗口层提示并播语音、同时维持 12 秒冷却。
发出去的正文尾部会附上本机设备标识（`[sha-xxxxxxxx]`，见 `lib/core/device_identity`），
同设备的留言因此可以被认成同一个人；展示时由 `strip_device_tag()` 洗掉。

和公告服务一样，这里不导入任何 GUI 库：网络请求交给 `submit_io` 注入的线程池，
结果通过 `dispatch` 回到 UI 线程，请求函数也可注入以便测试。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
import json
import math
import re
import threading
import time

import requests

from lib.core.compute_hub import get_compute_hub
from lib.core.device_identity import get_device_tag
from lib.core.forum_filter import ForumViolation, check_content
from lib.core.logger import get_logger

_logger = get_logger(__name__)

FORUM_BASE_URL = "https://wall.fxxr.store"
FORUM_FEED_URL = f"{FORUM_BASE_URL}/api/feed"
FORUM_POST_URL = f"{FORUM_BASE_URL}/api/messages"
FORUM_REQUEST_TIMEOUT = (5.0, 12.0)
FORUM_PAGE_LIMIT = 60
FORUM_MAX_CONTENT = 200
#: 客户端冷却，比服务端的 6 秒限流更保守。
FORUM_POST_COOLDOWN_SECS = 12.0
#: 与 `/api/feed` 未填昵称时的返回一致：服务端把空昵称落成「匿名」。
FORUM_DEFAULT_NICKNAME = "匿名"
FORUM_ACCENTS = ("pink", "cyan", "blue", "snow")
FORUM_DEFAULT_ACCENT = "snow"
#: 昵称上限：服务端不限制，这里与窗口输入框一致，避免超长昵称把卡片撑开。
FORUM_MAX_NICKNAME = 12
#: 设备标识尾注：``[sha-123abcDE]``，八位十六进制，同设备恒定。
FORUM_DEVICE_TAG_PREFIX = "sha-"
FORUM_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FORUM_HEADERS = {
    "User-Agent": "FlyingSnowVelvet-Forum/1.0",
    "Accept": "application/json",
}


@dataclass(frozen=True, slots=True)
class ForumMessage:
    """一条论坛留言；字段与 `/api/feed` 返回一一对应。

    `content` 是**洗掉设备标识之后**的展示文本，`device_tag` 是单独取出来的标识
    （形如 ``sha-123abcDE``，没有则为空串）。两者都由 `parse_feed` / `parse_post_result`
    从服务端原文拆出来，UI 不需要再认识尾注格式。
    """

    id: int
    nickname: str
    content: str
    accent: str
    created_at: int
    device_tag: str = ""


@dataclass(frozen=True, slots=True)
class ForumPage:
    """一次读取结果；`mode` 为 `latest`（首屏/刷新）或 `older`（往上翻页）。"""

    messages: tuple[ForumMessage, ...]
    mode: str
    total: int


def normalize_accent(value, *, fallback: str = FORUM_DEFAULT_ACCENT) -> str:
    accent = str(value or "").strip().lower()
    return accent if accent in FORUM_ACCENTS else fallback


_DEVICE_TAG_RE = re.compile(
    r"\s*\[sha-[0-9a-fA-F]{8}\]\s*$",
)


def strip_device_tag(text) -> str:
    """洗掉正文末尾的设备标识尾注，返回用户真正看到的文字。

    标识由 `build_content_with_device_tag()` 加在结尾，等于给留言盖了一个「同设备」的戳。
    卡片展示的是洗掉之后的内容，标识另在昵称右侧以小字呈现（见 `device_tag_of()`）。
    """
    return _DEVICE_TAG_RE.sub("", str(text or "")).rstrip()


def device_tag_of(text) -> str:
    """取出正文里的设备标识（形如 ``sha-123abcDE``）；没有则返回空串。"""
    match = _DEVICE_TAG_RE.search(str(text or ""))
    if match is None:
        return ""
    return match.group(0).strip().strip("[]")


def device_tag_for_display(nickname, content) -> str:
    """卡片上要呈现的设备标识；昵称是「匿名」时是空串。

    标识的作用是「同一台机器的留言能对上」，而匿名留言正是为了不让人对上——正文里
    就算带着 ``[sha-xxxxxxxx]`` 尾注，匿名这一档也不把它摆到卡片上。
    """
    if normalize_nickname(nickname) == FORUM_DEFAULT_NICKNAME:
        return ""
    return device_tag_of(content)


def build_content_with_device_tag(content, *, tag: str | None = None) -> str:
    """在正文结尾附上本机设备标识，用于发送。

    已有尾注时不再叠加：用户手打一个 ``[sha-xxxxxxxx]`` 也不会变成两条。
    """
    text = str(content or "").strip()
    if device_tag_of(text):
        return text
    resolved = str(tag or "").strip() or get_device_tag()
    return f"{text}[{resolved}]"


def normalize_nickname(value, *, fallback: str = FORUM_DEFAULT_NICKNAME) -> str:
    """截断昵称到上限；空昵称落成「匿名」，与服务端行为一致。"""
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:FORUM_MAX_NICKNAME]


def parse_feed(payload) -> tuple[tuple[ForumMessage, ...], int]:
    """把 `/api/feed` 响应解析成留言元组与总条数。"""
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    if payload.get("ok") is False:
        raise ValueError(str(payload.get("error") or "论坛返回失败"))
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("论坛返回结构异常")

    messages: list[ForumMessage] = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        raw_content = str(item.get("content") or "").strip()
        if not raw_content:
            continue
        content = strip_device_tag(raw_content)
        if not content:
            continue
        try:
            message_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        try:
            created_at = int(item.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        nickname = normalize_nickname(item.get("nickname"))
        messages.append(
            ForumMessage(
                id=message_id,
                nickname=nickname,
                content=content,
                accent=normalize_accent(item.get("accent")),
                created_at=created_at,
                device_tag=device_tag_for_display(nickname, raw_content),
            )
        )

    try:
        total = max(0, int(payload.get("total")))
    except (TypeError, ValueError):
        total = len(messages)
    return tuple(messages), total


def parse_post_result(payload) -> ForumMessage:
    """把 `POST /api/messages` 的响应解析成刚发布的留言。"""
    if not isinstance(payload, dict):
        raise ValueError("论坛返回结构异常")
    if payload.get("ok") is False:
        raise ValueError(str(payload.get("error") or "发帖失败"))
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("论坛返回结构异常")
    raw_content = str(message.get("content") or "").strip()
    try:
        message_id = int(message.get("id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("论坛返回结构异常") from exc
    try:
        created_at = int(message.get("created_at") or 0)
    except (TypeError, ValueError):
        created_at = 0
    return ForumMessage(
        id=message_id,
        nickname=normalize_nickname(message.get("nickname")),
        content=strip_device_tag(raw_content),
        accent=normalize_accent(message.get("accent")),
        created_at=created_at,
        device_tag=device_tag_for_display(message.get("nickname"), raw_content),
    )


def format_relative_time(created_at_ms, *, now_ms: int | None = None) -> str:
    """把毫秒时间戳写成卡片右上角的相对时间。"""
    try:
        created = int(created_at_ms)
    except (TypeError, ValueError):
        return ""
    if created <= 0:
        return ""
    now = int(now_ms) if now_ms is not None else int(time.time() * 1000)
    seconds = max(0, now - created) // 1000
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
    return time.strftime("%Y-%m-%d", time.localtime(created / 1000.0))


class ForumService:
    """分页读取与发帖的协调器，线程与 UI 之间只通过 `dispatch` 传递回调。"""

    def __init__(
        self,
        *,
        dispatch: Callable[[Callable[[], None]], None],
        on_page: Callable[[ForumPage], None],
        on_error: Callable[[str], None],
        on_posted: Callable[[ForumMessage], None] | None = None,
        submit_io: Callable[..., Future] | None = None,
        request_get: Callable[..., object] | None = None,
        request_post: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        limit: int = FORUM_PAGE_LIMIT,
        cooldown_secs: float = FORUM_POST_COOLDOWN_SECS,
    ) -> None:
        self._dispatch = dispatch
        self._on_page = on_page
        self._on_error = on_error
        self._on_posted = on_posted
        self._submit_io = submit_io or get_compute_hub().submit_io
        self._request_get = request_get or requests.get
        self._request_post = request_post or requests.post
        self._clock = clock
        self._limit = max(1, min(int(limit), 100))
        self._cooldown_secs = max(0.0, float(cooldown_secs))

        self._generation = 0
        self._oldest_id: int | None = None
        self._total = 0
        self._older_in_flight = False
        self._reached_end = False
        self._last_post_at: float | None = None
        self._closed = False
        self._lock = threading.Lock()

    # ── 只读状态 ─────────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return self._total

    @property
    def oldest_id(self) -> int | None:
        return self._oldest_id

    @property
    def reached_end(self) -> bool:
        return self._reached_end

    @property
    def loading_older(self) -> bool:
        with self._lock:
            return self._older_in_flight

    def cooldown_remaining(self) -> float:
        with self._lock:
            last_post_at = self._last_post_at
        if last_post_at is None:
            return 0.0
        return max(0.0, self._cooldown_secs - (self._clock() - last_post_at))

    # ── 读取 ─────────────────────────────────────────────────────────

    def refresh(self) -> bool:
        """重新读取最新一页，替换当前列表。"""
        if self._closed:
            return False
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._older_in_flight = False
            self._reached_end = False
        return self._submit(self._load_worker, self._handle_page, generation, None, "latest")

    def load_older(self) -> bool:
        """按最早的 id 往前翻一页；已在翻页或已到底时忽略。"""
        if self._closed:
            return False
        with self._lock:
            if self._older_in_flight or self._reached_end:
                return False
            before = self._oldest_id
            if before is None:
                return False
            self._older_in_flight = True
            generation = self._generation
        return self._submit(self._load_worker, self._handle_page, generation, before, "older")

    def _load_worker(self, generation: int, before: int | None, mode: str):
        params: dict[str, object] = {"limit": self._limit}
        if before is not None:
            params["before"] = int(before)
        response = self._request_get(
            FORUM_FEED_URL,
            params=params,
            headers=dict(FORUM_HEADERS),
            timeout=FORUM_REQUEST_TIMEOUT,
            stream=True,
        )
        try:
            response.raise_for_status()
            messages, total = parse_feed(_read_json(response))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return generation, mode, messages, total

    def _handle_page(self, result) -> None:
        generation, mode, messages, total = result
        if self._closed:
            return
        with self._lock:
            if generation != self._generation:
                return
            self._total = total
            oldest = min((message.id for message in messages), default=None)
            if mode == "latest":
                self._oldest_id = oldest
            elif oldest is not None:
                self._oldest_id = (
                    oldest if self._oldest_id is None else min(self._oldest_id, oldest)
                )
            self._older_in_flight = False
            if len(messages) < self._limit:
                self._reached_end = True
        self._on_page(ForumPage(messages=tuple(messages), mode=mode, total=total))

    # ── 发帖 ─────────────────────────────────────────────────────────

    def post(
        self,
        content,
        *,
        nickname: str = FORUM_DEFAULT_NICKNAME,
        accent: str | None = None,
    ) -> ForumViolation | str | None:
        """提交一条留言。

        返回 `None` 表示已发出，`ForumViolation` 表示被本地过滤拦下（窗口层据此提示并播语音），
        其它字符串表示普通拒绝原因（内容为空、冷却中一类）。
        """
        if self._closed:
            return "论坛窗口已关闭"
        text = str(content or "").strip()
        if not text:
            return "内容不能为空"
        if len(text) > FORUM_MAX_CONTENT:
            return f"内容不能超过 {FORUM_MAX_CONTENT} 字"
        remaining = self.cooldown_remaining()
        if remaining > 0:
            return f"发帖冷却中，请等待 {int(math.ceil(remaining))} 秒"
        violation = check_content(text)
        if violation is not None:
            # 违规同样进冷却：否则用户可以连续试探过滤规则。
            with self._lock:
                self._last_post_at = self._clock()
            return violation
        with self._lock:
            self._last_post_at = self._clock()
        if not self._submit(
            self._post_worker,
            self._handle_posted,
            build_content_with_device_tag(text),
            normalize_nickname(nickname),
            normalize_accent(accent),
        ):
            return "发帖请求未能发出"
        return None

    def _post_worker(self, content: str, nickname: str, accent: str) -> ForumMessage:
        response = self._request_post(
            FORUM_POST_URL,
            json={"content": content, "nickname": nickname, "accent": accent},
            headers={**FORUM_HEADERS, "Content-Type": "application/json"},
            timeout=FORUM_REQUEST_TIMEOUT,
        )
        try:
            response.raise_for_status()
            return parse_post_result(_read_json(response))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _handle_posted(self, message: ForumMessage) -> None:
        if self._closed:
            return
        with self._lock:
            self._total += 1
            if self._oldest_id is None:
                self._oldest_id = message.id
        if self._on_posted is not None:
            self._on_posted(message)

    def _handle_error(self, message: str) -> None:
        if self._closed:
            return
        with self._lock:
            self._older_in_flight = False
        self._on_error(message)

    # ── 生命周期 ─────────────────────────────────────────────────────

    def cleanup(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._older_in_flight = False

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _submit(self, worker, handler, *args) -> bool:
        try:
            future = self._submit_io(worker, *args)
        except Exception as exc:
            _logger.warning("论坛请求未能提交: %s", exc)
            self._dispatch(lambda message=str(exc): self._handle_error(message))
            return False

        def complete(done: Future) -> None:
            try:
                result = done.result()
            except Exception as exc:
                self._dispatch(lambda message=_friendly_error(exc): self._handle_error(message))
                return
            self._dispatch(lambda result=result: handler(result))

        future.add_done_callback(complete)
        return True


def _friendly_error(error: Exception) -> str:
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status == 429:
        return "论坛限流：同一 IP 6 秒 1 条、每小时 30 条，请稍后再试"
    if status is not None:
        return f"论坛请求失败（HTTP {status}）"
    text = str(error).strip()
    return f"论坛请求失败：{text}" if text else "论坛请求失败"


def _read_json(response):
    payload = getattr(response, "content", None)
    if isinstance(payload, (bytes, bytearray)) and len(payload) > FORUM_MAX_RESPONSE_BYTES:
        raise ValueError("论坛返回内容过大")
    return response.json()


__all__ = [
    "FORUM_ACCENTS",
    "FORUM_BASE_URL",
    "FORUM_DEFAULT_ACCENT",
    "FORUM_DEFAULT_NICKNAME",
    "FORUM_DEVICE_TAG_PREFIX",
    "FORUM_FEED_URL",
    "FORUM_MAX_CONTENT",
    "FORUM_MAX_NICKNAME",
    "FORUM_PAGE_LIMIT",
    "FORUM_POST_COOLDOWN_SECS",
    "FORUM_POST_URL",
    "ForumMessage",
    "ForumPage",
    "ForumService",
    "build_content_with_device_tag",
    "device_tag_for_display",
    "device_tag_of",
    "format_relative_time",
    "normalize_accent",
    "normalize_nickname",
    "parse_feed",
    "parse_post_result",
    "strip_device_tag",
]
