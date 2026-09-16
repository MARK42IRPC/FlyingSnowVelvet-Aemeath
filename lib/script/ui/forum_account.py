"""账号页：登录、注册、退出，以及登录数据与浏览缓存的清理入口。

登录数据（token）落在共享目录的 secrets 下，覆盖安装与切版本都不会清掉；这里把路径
直接写在界面上，用户能自己确认「东西存在哪」。注册成功即自动登录：服务端在注册响应里
就返回 token，`CommunityService` 拿到就落盘并通知两个子页面。

「清理登录数据」删本地 token（并在有 token 时顺便让服务端作废它），「清理浏览缓存」
清的是 `<用户根>/cache/forum/` 的小快照 —— 启动时也会清一次，所以按钮旁边同时显示
当前占用，方便确认确实清了。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_DISPLAY_NAME_MAX,
    FORUM_PASSWORD_MIN,
    FORUM_USERNAME_MAX,
    FORUM_USERNAME_MIN,
    ForumUser,
    format_date,
    format_expiry,
    validate_password,
    validate_username,
)
from lib.core.forum_community import CommunityService
from lib.core.forum_session import ForumSessionStore, is_logged_in, session_path
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font

logger = get_logger(__name__)

_FORM_HINT = (
    f"用户名 {FORUM_USERNAME_MIN}–{FORUM_USERNAME_MAX} 位，只能用字母、数字、下划线和中划线；"
    f"密码至少 {FORUM_PASSWORD_MIN} 位。注册成功会自动登录。"
)


def _require_password(password) -> str:
    """登录只要求「填了密码」：老账号的密码规则由服务端说了算，本地不拦。"""
    return "" if str(password or "") else "请输入密码"


def _font(size: int, *, bold: bool = False):
    font = get_ui_font(size=scale_px(size, min_abs=max(8, size - 2)))
    font.setBold(bold)
    return font


class ForumAccountPage(QWidget):
    """账号子页面：未登录显示表单，登录后显示资料与退出入口。"""

    _dispatch_requested = pyqtSignal(object)
    #: 登录态变化后窗口要重写副标题与导航条小字。
    subtitle_changed = pyqtSignal()

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

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(14, min_abs=11),
            scale_px(16 + 10, min_abs=13 + 8),
            scale_px(14, min_abs=11),
        )
        root.setSpacing(scale_px(10, min_abs=8))

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ForumAccountStack")
        self._stack.addWidget(self._build_login_card())
        self._stack.addWidget(self._build_profile_card())
        root.addWidget(self._stack, 0)

        root.addWidget(self._build_data_card(), 0)
        root.addWidget(self._build_server_card(), 0)
        root.addStretch(1)

        self._status = QLabel("", self)
        self._status.setObjectName("ForumStatus")
        self._status.setFont(_font(11))
        self._status.setWordWrap(True)
        root.addWidget(self._status, 0)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(self)
        card.setObjectName("ForumAccountCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        layout.setSpacing(scale_px(7, min_abs=5))
        label = QLabel(title, card)
        label.setObjectName("ForumSectionTitle")
        label.setFont(_font(13, bold=True))
        layout.addWidget(label)
        return card, layout

    def _field(self, parent: QWidget, placeholder: str, *, password: bool = False) -> QLineEdit:
        field = QLineEdit(parent)
        field.setObjectName("ForumField")
        field.setPlaceholderText(placeholder)
        field.setFont(_font(12))
        if password:
            field.setEchoMode(QLineEdit.Password)
            field.setToolTip("只在本机与服务端之间传输，不写进任何日志")
        return field

    def _build_login_card(self) -> QWidget:
        card, layout = self._card("登录 / 注册")
        self._login_card = card

        self._username = self._field(card, "用户名")
        self._username.setMaxLength(FORUM_USERNAME_MAX)
        layout.addWidget(self._username)

        self._password = self._field(card, "密码", password=True)
        layout.addWidget(self._password)

        self._display_name = self._field(card, f"显示名（可选，最多 {FORUM_DISPLAY_NAME_MAX} 字）")
        self._display_name.setMaxLength(FORUM_DISPLAY_NAME_MAX)
        layout.addWidget(self._display_name)

        hint = QLabel(_FORM_HINT, card)
        hint.setObjectName("ForumHint")
        hint.setFont(_font(10))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._login_button = QPushButton("登录", card)
        self._login_button.setObjectName("ForumSend")
        self._login_button.setFont(_font(12))
        self._login_button.clicked.connect(self._on_login)
        buttons.addWidget(self._login_button, 0)
        self._register_button = QPushButton("注册新账号", card)
        self._register_button.setFont(_font(12))
        self._register_button.setToolTip("注册成功后自动登录，并把 token 存进共享目录")
        self._register_button.clicked.connect(self._on_register)
        buttons.addWidget(self._register_button, 0)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        # 回车直接登录；密码框回车也走同一条路。
        self._password.returnPressed.connect(self._on_login)
        self._username.returnPressed.connect(lambda: self._password.setFocus())
        return card

    def _build_profile_card(self) -> QWidget:
        card, layout = self._card("账号资料")
        self._profile_card = card

        self._profile_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("username", "用户名"),
            ("display_name", "显示名"),
            ("role", "身份"),
            ("counts", "发帖 / 回复"),
            ("created_at", "注册时间"),
            ("expires_at", "登录有效期"),
            ("bio", "简介"),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(scale_px(8, min_abs=6))
            caption_label = QLabel(caption, card)
            caption_label.setObjectName("ForumFieldLabel")
            caption_label.setFont(_font(11))
            caption_label.setFixedWidth(scale_px(74, min_abs=64))
            row.addWidget(caption_label, 0)
            value = QLabel("-", card)
            value.setObjectName("ForumFieldValue")
            value.setFont(_font(11))
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(value, 1)
            self._profile_labels[key] = value
            layout.addLayout(row)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._refresh_button = QPushButton("刷新资料", card)
        self._refresh_button.setFont(_font(12))
        self._refresh_button.setToolTip("向论坛确认 token 是否还有效，并拉一次最新资料")
        self._refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self._refresh_button, 0)
        self._logout_button = QPushButton("退出登录", card)
        self._logout_button.setFont(_font(12))
        self._logout_button.clicked.connect(self._on_logout)
        buttons.addWidget(self._logout_button, 0)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    def _build_data_card(self) -> QWidget:
        card, layout = self._card("本机数据")
        self._data_card = card

        path_hint = QLabel(f"登录数据保存在 {session_path()}，覆盖安装不会清掉。", card)
        path_hint.setObjectName("ForumHint")
        path_hint.setFont(_font(10))
        path_hint.setWordWrap(True)
        layout.addWidget(path_hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._forget_button = QPushButton("清理登录数据", card)
        self._forget_button.setObjectName("ForumDangerButton")
        self._forget_button.setFont(_font(12))
        self._forget_button.setToolTip("删掉本机的 token，并用它让服务端也作废这个会话")
        self._forget_button.clicked.connect(self._on_forget)
        buttons.addWidget(self._forget_button, 0)
        self._cache_button = QPushButton("清理浏览缓存", card)
        self._cache_button.setFont(_font(12))
        self._cache_button.setToolTip("清掉帖子列表快照；桌宠每次启动也会自动清一次")
        self._cache_button.clicked.connect(self._on_clear_cache)
        buttons.addWidget(self._cache_button, 0)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._cache_label = QLabel("", card)
        self._cache_label.setObjectName("ForumHint")
        self._cache_label.setFont(_font(10))
        layout.addWidget(self._cache_label)
        return card

    def _build_server_card(self) -> QWidget:
        card, layout = self._card("服务器状态")
        self._server_card = card
        self._server_label = QLabel("还没查询过；点下面的按钮问一次。", card)
        self._server_label.setObjectName("ForumFieldValue")
        self._server_label.setFont(_font(11))
        self._server_label.setWordWrap(True)
        layout.addWidget(self._server_label)
        self._health_button = QPushButton("检查论坛状态", card)
        self._health_button.setFont(_font(12))
        self._health_button.clicked.connect(self._on_check_health)
        layout.addWidget(self._health_button, 0)
        return card

    # ── 对外 ─────────────────────────────────────────────────────────

    def subtitle(self) -> str:
        session = self._session.get()
        if session is None:
            return "账号页 · 未登录"
        return f"账号页 · 已登录 {session.user.label}"

    def needs_initial_load(self) -> bool:
        return not self._loaded

    def refresh(self) -> None:
        self._loaded = True
        if self._session.logged_in():
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

    def _sync_session(self, session) -> None:
        logged_in = is_logged_in(session)
        self._stack.setCurrentIndex(1 if logged_in else 0)
        self._login_button.setEnabled(not logged_in)
        self._register_button.setEnabled(not logged_in)
        if logged_in:
            self._password.clear()
            self._fill_profile(session.user, expires_at=session.expires_at)
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