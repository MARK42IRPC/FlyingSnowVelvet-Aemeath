"""账号页：登录、注册、退出，以及登录数据与浏览缓存的清理入口。

登录数据（token）落在共享目录的 secrets 下，覆盖安装与切版本都不会清掉；这里把路径
直接写在界面上，用户能自己确认「东西存在哪」。注册成功即自动登录：服务端在注册响应里
就返回 token，`CommunityService` 拿到就落盘并通知两个子页面。

登录与注册共用一组输入框，顶部两个模式签只切换「显示名字段 + 提示文案 + 回车提交动作」；
注册模式下额外提醒服务端的一条硬规则：**同一个 IP 只能注册一个账号**。

「清理登录数据」删本地 token（并在有 token 时顺便让服务端作废它），「清理浏览缓存」
清的是 `<用户根>/cache/forum/` 的小快照 —— 启动时也会清一次，所以按钮旁边同时显示
当前占用，方便确认确实清了。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QLineEdit,
    QWidget,
)

from lib.core.forum_api import (
    ForumUser,
    format_date,
    format_expiry,
    validate_password,
    validate_username,
)
from lib.core.forum_community import CommunityService
from lib.core.forum_session import ForumSessionStore, is_logged_in
from lib.core.logger import get_logger
from lib.script.ui.forum_account_sections import (
    ForumAccountSectionsMixin,
    _FORM_HINT_LOGIN,
    _FORM_HINT_REGISTER,
)
from lib.script.ui.forum_board import ForumPostRow

logger = get_logger(__name__)

#: 「最新帖子」卡里最多铺几条。
_ACTIVITY_ROW_LIMIT = 3


def _require_password(password) -> str:
    """登录只要求「填了密码」：老账号的密码规则由服务端说了算，本地不拦。"""
    return "" if str(password or "") else "请输入密码"


class ForumAccountPage(ForumAccountSectionsMixin, QWidget):
    """账号子页面：未登录显示表单，登录后显示资料与退出入口。"""

    _dispatch_requested = pyqtSignal(object)
    #: 登录态变化后窗口要重写副标题与导航条小字。
    subtitle_changed = pyqtSignal()
    #: 「最新帖子」里点了某一行：窗口切到主论坛并打开这篇帖子。
    post_requested = pyqtSignal(int)

    def __init__(
        self,
        *,
        session: ForumSessionStore,
        api=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ForumAccountPage")
        self._session = session
        self._disposed = False
        #: 首次进入才自动查一次；之后靠刷新按钮。
        self._loaded = False
        #: 登录表单当前模式：`login` / `register`（见 `set_mode()`）。
        self._mode = "login"

        self._dispatch_requested.connect(self._run_dispatched, Qt.QueuedConnection)
        self._service = CommunityService(
            dispatch=self._dispatch,
            listener=self,
            client=api,
            session=session,
        )
        self._build_ui()
        self._sync_session(session.get())
        self._sync_cache_label()
        self._session.subscribe(self._on_session_changed)

    # ── 界面 ─────────────────────────────────────────────────────────


    # ── 对外 ─────────────────────────────────────────────────────────

    def subtitle(self) -> str:
        session = self._session.get()
        if session is None:
            return "账号页 · 未登录"
        return f"账号页 · 已登录 {session.user.label}"

    def set_mode(self, mode: str) -> None:
        """切换登录 / 注册；同一个表单，只改提示与字段。"""
        target = "register" if str(mode).strip() == "register" else "login"
        if target == self._mode:
            return
        self._mode = target
        self._sync_mode()

    def needs_initial_load(self) -> bool:
        return not self._loaded

    def refresh(self) -> None:
        """登录时刷新资料与「最新帖子」；未登录就问一次服务器状态。"""
        self._loaded = True
        if self._session.logged_in():
            # 先问一次「我最近发了什么」，再让服务端确认 token 还有效。
            self._service.load_user()
            self._service.refresh_account()
        else:
            self._on_check_health()

    def cleanup(self) -> None:
        self._disposed = True
        try:
            self._session.unsubscribe(self._on_session_changed)
        except Exception:
            pass
        self._service.cleanup()

    # ── 服务回调 ─────────────────────────────────────────────────────

    def on_session(self, session) -> None:
        self._sync_session(session)

    def on_session_error(self, action: str, message: str) -> None:
        """登录 / 注册被服务端拒了。

        注册撞上「一个 IP 只能注册一个账号」时直接切回登录模式：这张表单再点几次也不会
        成功，不如把用户送到能用的那一边（状态栏里已经有服务端原文）。
        """
        if action == "register" and "IP" in str(message):
            self.set_mode("login")

    def on_user_activity(self, user: ForumUser, posts, replies) -> None:
        """自己的资料与动态：资料顺手刷新，「最新帖子」铺最近几篇。"""
        if user.username:
            self._fill_profile(user)
        self._clear_activity_rows()
        recent = tuple(posts.posts)[:_ACTIVITY_ROW_LIMIT]
        for post in recent:
            row = ForumPostRow(post, self._activity_host)
            row.activated.connect(self.post_requested.emit)
            self._activity_layout.addWidget(row)
        total_posts = posts.total or len(posts.posts)
        total_replies = replies.total or len(replies.replies)
        if recent:
            self._activity_hint.setText(
                f"共 {total_posts} 篇帖子、{total_replies} 条回复；下面是最近 {len(recent)} 篇，点一行进详情。"
            )
        else:
            self._activity_hint.setText(
                f"还没有发过帖子；已经回复过 {total_replies} 条。"
            )

    def on_account(self, user: ForumUser) -> None:
        self._fill_profile(user)
        self._set_status(f"资料已更新：{user.label}")

    def on_health(self, payload) -> None:
        if not isinstance(payload, dict):
            self._server_label.setText("论坛没有返回可读的状态")
            return
        status = str(payload.get("status") or "unknown")
        service = str(payload.get("service") or "fxxr-forum")
        latency = payload.get("latency_ms")
        db = payload.get("db") if isinstance(payload.get("db"), dict) else {}
        parts = [f"{service}：{status}"]
        if latency is not None:
            parts.append(f"延迟 {latency} ms")
        if db:
            parts.append(f"用户 {db.get('users', '?')} · 帖子 {db.get('posts', '?')}")
        self._server_label.setText(" · ".join(parts))

    def on_status(self, text: str, tone: str = "") -> None:
        self._set_status(text, tone=tone)

    def on_error(self, message: str) -> None:
        self._set_status(message, tone="warn")

    # ── 交互 ─────────────────────────────────────────────────────────

    def _on_login(self) -> None:
        # 本地先校验一遍：明显不合规的输入不必等一次往返（服务端仍会再校验）。
        error = validate_username(self._username.text()) or _require_password(self._password.text())
        if error:
            self._set_status(error, tone="warn")
            return
        error = self._service.login(self._username.text(), self._password.text())
        if error:
            self._set_status(error, tone="warn")

    def _on_register(self) -> None:
        error = validate_username(self._username.text()) or validate_password(self._password.text())
        if error:
            self._set_status(error, tone="warn")
            return
        error = self._service.register(
            self._username.text(),
            self._password.text(),
            self._display_name.text(),
        )
        if error:
            self._set_status(error, tone="warn")

    def _toggle_password(self) -> None:
        shown = self._password.echoMode() == QLineEdit.Normal
        self._password.setEchoMode(QLineEdit.Password if shown else QLineEdit.Normal)
        self._password_toggle.setText("显示" if shown else "隐藏")

    def _on_logout(self) -> None:
        if self._service.logout():
            self._set_status("正在退出登录…")
        else:
            self._set_status("本来就没有登录")

    def _on_forget(self) -> None:
        self._service.logout(forget=True)
        self._sync_cache_label()

    def _on_clear_cache(self) -> None:
        report = self._service.clear_cache()
        freed = report.bytes / 1024.0
        message = f"已清理浏览缓存：{report.files} 个文件、约 {freed:.1f} KB"
        if report.errors:
            message = f"{message}（{len(report.errors)} 个文件没删掉）"
        self._set_status(message)
        self._sync_cache_label()

    def _on_check_health(self) -> None:
        self._server_label.setText("正在检查…")
        self._service.check_health()

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _on_session_changed(self, session) -> None:
        """store 的通知：两个子页面都会收到，这里只管自己。"""
        self._sync_session(session)

    def _sync_mode(self) -> None:
        """模式签与字段可见性对齐；两种模式共用用户名与密码。"""
        registering = self._mode == "register"
        for mode, tab in self._mode_buttons.items():
            tab.setChecked(mode == self._mode)
        self._display_name.setVisible(registering)
        self._form_hint.setText(_FORM_HINT_REGISTER if registering else _FORM_HINT_LOGIN)
        self._login_button.setVisible(not registering)
        self._register_button.setVisible(registering)
        self._register_button.setObjectName("ForumSend" if registering else "ForumGhostButton")
        style = self._register_button.style()
        if style is not None:
            style.unpolish(self._register_button)
            style.polish(self._register_button)
    def _focus_password(self) -> None:
        self._password.setFocus()

    def _on_submit(self) -> None:
        """回车提交：按当前模式走登录或注册。"""
        if self._mode == "register":
            self._on_register()
        else:
            self._on_login()

    def _clear_activity_rows(self) -> None:
        while self._activity_layout.count():
            item = self._activity_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _sync_session(self, session) -> None:
        logged_in = is_logged_in(session)
        self._stack.setCurrentIndex(1 if logged_in else 0)
        self._login_button.setEnabled(not logged_in)
        self._register_button.setEnabled(not logged_in)
        if logged_in:
            self._password.clear()
            self._fill_profile(session.user, expires_at=session.expires_at)
            self._service.load_user()
        self._sync_cache_label()
        self.subtitle_changed.emit()

    def _fill_profile(self, user: ForumUser, *, expires_at: int | None = None) -> None:
        session = self._session.get()
        expiry = expires_at if expires_at is not None else (session.expires_at if session else 0)
        values = {
            "username": user.username or "-",
            "display_name": user.display_name or "（没设显示名）",
            "role": "管理员" if user.role == "admin" else "普通用户",
            "counts": f"{user.post_count} / {user.reply_count}",
            "created_at": format_date(user.created_at) or "未知",
            "expires_at": format_expiry(expiry),
            "bio": user.bio or "（还没写简介）",
        }
        for key, label in self._profile_labels.items():
            label.setText(values.get(key, "-"))

    def _sync_cache_label(self) -> None:
        stats = self._service.cache_summary()
        size = stats.get("bytes", 0) / 1024.0
        self._cache_label.setText(
            f"当前缓存：{stats.get('files', 0)} 个文件、约 {size:.1f} KB；桌宠启动时会自动清一次。"
        )

    def _set_status(self, text: str, *, tone: str = "") -> None:
        self._status.setText(text)
        if self._status.property("tone") != tone:
            self._status.setProperty("tone", tone)
            style = self._status.style()
            if style is not None:
                style.unpolish(self._status)
                style.polish(self._status)

    def _dispatch(self, callback) -> None:
        self._dispatch_requested.emit(callback)

    def _run_dispatched(self, callback) -> None:
        if self._disposed:
            return
        try:
            callback()
        except Exception as exc:
            logger.warning("[ForumAccount] 回调执行失败: %s", exc)


__all__ = ["ForumAccountPage"]
