"""雪绒社区的登录态：token 的落盘位置，以及两个子页面共享的会话对象。

token 是密钥，所以落在共享根目录的 secrets 下（`<用户根>/user/secrets/forum/session.json`），
而不是随版本覆盖的安装目录：桌宠更新、覆盖安装、切换发行包都不会把它清掉，用户只需要
登录一次。写盘走 `config.shared_storage_io.write_shared_text()`，共享目录暂时不可写时
会落到待同步缓存里，下一次启动补写。

`ForumSessionStore` 是当前登录态的**唯一持有者**：留言墙以外的两个子页面（主论坛与账号页）
都订阅它，登录、退出、被服务端踢下线只需要改这一处，另一页自动跟着刷新。
"""

from __future__ import annotations

from collections.abc import Callable
import json
import threading
import time

from config.shared_storage_io import read_text_best_effort, write_shared_text
from config.user_storage_paths import get_user_secrets_dir
from lib.core.forum_api import ForumSession, ForumUser, parse_user
from lib.core.logger import get_logger
from lib.core.secret_files import harden_secret_path

_logger = get_logger(__name__)

#: 文件格式版本；结构变化时自增，旧文件按版本不符忽略。
FORUM_SESSION_VERSION = 1
FORUM_SESSION_FILE = "session.json"
#: 会话文件上限：里面只有 token 与一份账号资料，正常不到 1 KiB。
FORUM_SESSION_MAX_BYTES = 16 * 1024


def session_path():
    """会话文件路径：`<用户根>/user/secrets/forum/session.json`。"""
    return get_user_secrets_dir("forum") / FORUM_SESSION_FILE


def save_session(session: ForumSession) -> bool:
    """把会话写进共享目录；过期或不带 token 的会话不落盘（等于没登录）。"""
    if session is None or not session.token or session.expired():
        return False
    payload = {
        "version": FORUM_SESSION_VERSION,
        "token": session.token,
        "token_type": session.token_type,
        "expires_at": int(session.expires_at),
        "saved_at": int(time.time()),
        "user": {
            "id": session.user.id,
            "username": session.user.username,
            "display_name": session.user.display_name,
            "role": session.user.role,
        },
    }
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        _logger.warning("[ForumSession] 登录态无法序列化: %s", exc)
        return False
    path = session_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.debug("[ForumSession] 会话目录不可用 %s: %s", path.parent, exc)
    saved = bool(write_shared_text(path, text))
    if saved:
        harden_secret_path(path)
    return saved


def load_session(*, now: int | None = None) -> ForumSession | None:
    """读取本地会话；文件缺失、损坏、过期或结构不全都返回 None。"""
    path = session_path()
    try:
        raw = read_text_best_effort(path)
    except OSError:
        return None
    except Exception as exc:
        _logger.debug("[ForumSession] 读取登录态失败 %s: %s", path, exc)
        return None
    if not raw or len(raw) > FORUM_SESSION_MAX_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("version") != FORUM_SESSION_VERSION:
        return None
    token = str(payload.get("token") or "").strip()
    if not token:
        return None
    try:
        expires_at = int(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    session = ForumSession(
        token=token,
        user=parse_user(payload.get("user")) or ForumUser(),
        expires_at=expires_at,
        token_type=str(payload.get("token_type") or "Bearer"),
    )
    if session.expired(now=now):
        return None
    return session


def clear_session() -> bool:
    """删掉本地登录态；文件本来就不存在时也算成功。"""
    path = session_path()
    removed = False
    try:
        path.unlink()
        removed = True
    except OSError:
        pass
    try:
        from config.shared_storage_io import remove_pending_syncs

        remove_pending_syncs(path)
    except Exception:
        pass
    return removed


def is_logged_in(session: ForumSession | None) -> bool:
    """会话是否可用：要有 token、且没过期；空会话（``token=""``）算未登录。"""
    return session is not None and bool(session.token) and not session.expired()


class ForumSessionStore:
    """当前登录态的唯一持有者，带订阅通知。

    约定：状态只在 UI 线程改（登录 / 退出都从界面触发），所以监听者在
    `set()` / `clear()` 里**同步**收到通知；网络线程只调用 `token()` / `get()` 读快照。
    """

    def __init__(self, session: ForumSession | None = None) -> None:
        self._lock = threading.Lock()
        self._session = session
        self._listeners: list[Callable[[ForumSession | None], None]] = []

    @classmethod
    def load(cls) -> "ForumSessionStore":
        """从共享目录恢复登录态；没有就返回空 store（不阻塞、不校验）。"""
        return cls(load_session())

    # ── 读取 ─────────────────────────────────────────────────────────

    def get(self) -> ForumSession | None:
        with self._lock:
            return self._session

    def token(self) -> str:
        with self._lock:
            session = self._session
        return session.token if session is not None else ""

    def user(self) -> ForumUser | None:
        session = self.get()
        return session.user if session is not None else None

    def logged_in(self) -> bool:
        return is_logged_in(self.get())

    # ── 订阅 ─────────────────────────────────────────────────────────

    def subscribe(self, listener: Callable[[ForumSession | None], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[ForumSession | None], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    # ── 写入 ─────────────────────────────────────────────────────────

    def set(self, session: ForumSession | None, *, persist: bool = True) -> None:
        """换掉当前会话；`persist=False` 只改内存（例如只是切页面时的临时状态）。

        「没有 token 的会话」等同于未登录：本地文件也要清掉，否则磁盘上会留一份
        永远用不上的旧 token。
        """
        with self._lock:
            self._session = session
            listeners = tuple(self._listeners)
        if session is None or not session.token:
            if persist:
                clear_session()
        elif persist:
            save_session(session)
        for listener in listeners:
            self._notify(listener, session)

    def clear(self, *, persist: bool = True) -> None:
        self.set(None, persist=persist)

    def _notify(self, listener, session: ForumSession | None) -> None:
        try:
            listener(session)
        except Exception as exc:
            _logger.warning("[ForumSession] 登录态通知失败: %s", exc)


__all__ = [
    "FORUM_SESSION_FILE",
    "FORUM_SESSION_MAX_BYTES",
    "FORUM_SESSION_VERSION",
    "ForumSessionStore",
    "is_logged_in",
    "clear_session",
    "load_session",
    "save_session",
    "session_path",
]
