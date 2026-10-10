"""账号页的界面装配：滚动外壳、登录 / 注册表单与四张卡片。

批次 3 后续轮次从 `forum_account.ForumAccountPage` 切出。账号页本体是「控制器 + 界面装配」
合在一起的一个 QWidget：这一轮只把**界面装配**那一半搬出来——`_build_ui()` 搭滚动外壳，
`_card()` / `_field()` 两个控件工厂，以及登录 / 注册、账号资料、最新帖子、本机数据、
服务器状态五张卡片的构造。会话同步、服务回调、交互入口与状态栏仍留在
`ForumAccountPage` 本体。

切分保持逐行等价：`ForumAccountSectionsMixin` 的方法体与搬出前一致（缩进也未变），
`ForumAccountPage` 只是多继承本 mixin。装配用到的模块级文案常量（`_FORM_HINT_*`）与
`_font()` 助手随之一并搬到这里；本体继续按原语义从本模块重新导出，`_FORM_HINT_*` 与
`_font` 的既有别名不变。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_DISPLAY_NAME_MAX,
    FORUM_PASSWORD_MIN,
    FORUM_USERNAME_MAX,
    FORUM_USERNAME_MIN,
)
from lib.core.forum_session import session_path
from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

#: 登录 / 注册两套表单提示；文案与搬出前逐字相同（`_FORM_HINT_*`）。
_FORM_HINT_LOGIN = (
    "用户名不区分大小写；账号不存在与密码错误统一按「登录失败」返回，不区分是哪一个。"
)
_FORM_HINT_REGISTER = (
    f"用户名 {FORUM_USERNAME_MIN}–{FORUM_USERNAME_MAX} 位，只能用字母、数字、下划线和中划线；"
    f"密码至少 {FORUM_PASSWORD_MIN} 位。注册成功会自动登录。"
    "一个 IP 只能注册一个账号，注册过就直接登录。"
)


def _font(size: int, *, bold: bool = False):
    font = get_ui_font(size=scale_px(size, min_abs=max(8, size - 2)))
    font.setBold(bold)
    return font


class ForumAccountSectionsMixin:
    """账号页的界面装配；由 `ForumAccountPage` 混入。"""

    def _build_ui(self) -> None:
        # 账号页也会长高（登录表单、资料、最新帖子、本机数据、服务器状态），所以本体放进
        # 滚动区：塞不下时整页滚动，而不是把卡片压扁——压扁会让「最新帖子」的行互相压字。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = SmoothScrollArea(self)
        self._scroll.setObjectName("ForumScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._scroll)
        host.setObjectName("ForumAccountHost")
        self._host = host
        root = QVBoxLayout(host)
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

        root.addWidget(self._build_activity_card(), 0)
        root.addWidget(self._build_data_card(), 0)
        root.addWidget(self._build_server_card(), 0)
        root.addStretch(1)

        self._status = QLabel("", self)
        self._status.setObjectName("ForumStatus")
        self._status.setFont(_font(11))
        self._status.setWordWrap(True)
        root.addWidget(self._status, 0)

        self._scroll.setWidget(host)
        outer.addWidget(self._scroll, 1)

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

        # 模式签：登录与注册共用下面这组输入框，切换只改提示与字段可见性。
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(scale_px(4, min_abs=3))
        self._mode_buttons: dict[str, QToolButton] = {}
        for mode, caption in (("login", "登录"), ("register", "注册新账号")):
            tab = QToolButton(card)
            tab.setObjectName("ForumModeTab")
            tab.setText(caption)
            tab.setCheckable(True)
            tab.setCursor(Qt.PointingHandCursor)
            tab.setFont(_font(12, bold=True))
            tab.clicked.connect(lambda _checked=False, target=mode: self.set_mode(target))
            self._mode_buttons[mode] = tab
            mode_row.addWidget(tab, 0)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self._username = self._field(card, "用户名")
        self._username.setMaxLength(FORUM_USERNAME_MAX)
        layout.addWidget(self._username)

        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(scale_px(6, min_abs=5))
        self._password = self._field(card, "密码", password=True)
        password_row.addWidget(self._password, 1)
        self._password_toggle = QToolButton(card)
        self._password_toggle.setObjectName("ForumLinkButton")
        self._password_toggle.setText("显示")
        self._password_toggle.setCursor(Qt.PointingHandCursor)
        self._password_toggle.setFont(_font(10))
        self._password_toggle.setToolTip("把密码显示成明文，方便核对输入")
        self._password_toggle.clicked.connect(self._toggle_password)
        password_row.addWidget(self._password_toggle, 0)
        layout.addLayout(password_row)

        self._display_name = self._field(card, f"显示名（可选，最多 {FORUM_DISPLAY_NAME_MAX} 字）")
        self._display_name.setMaxLength(FORUM_DISPLAY_NAME_MAX)
        layout.addWidget(self._display_name)

        self._form_hint = QLabel(_FORM_HINT_LOGIN, card)
        self._form_hint.setObjectName("ForumHint")
        self._form_hint.setFont(_font(10))
        self._form_hint.setWordWrap(True)
        layout.addWidget(self._form_hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._login_button = QPushButton("登录", card)
        self._login_button.setObjectName("ForumSend")
        self._login_button.setFont(_font(12))
        self._login_button.clicked.connect(self._on_login)
        buttons.addWidget(self._login_button, 0)
        self._register_button = QPushButton("注册新账号", card)
        self._register_button.setObjectName("ForumGhostButton")
        self._register_button.setFont(_font(12))
        self._register_button.setToolTip("注册成功后自动登录，并把 token 存进共享目录")
        self._register_button.clicked.connect(self._on_register)
        buttons.addWidget(self._register_button, 0)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        # 回车提交：走 `_on_submit()` 按当前模式分发；用户名回车先跳到密码。
        self._password.returnPressed.connect(self._on_submit)
        self._username.returnPressed.connect(self._focus_password)
        self._sync_mode()
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

    def _build_activity_card(self) -> QWidget:
        """「最新帖子」：登录后铺自己的最近几篇，点一行直接进主论坛详情。"""
        card, layout = self._card("最新帖子")
        self._activity_card = card
        self._activity_hint = QLabel("登录后这里会列出你最近发的帖子。", card)
        self._activity_hint.setObjectName("ForumHint")
        self._activity_hint.setFont(_font(10))
        self._activity_hint.setWordWrap(True)
        layout.addWidget(self._activity_hint)

        self._activity_host = QWidget(card)
        self._activity_layout = QVBoxLayout(self._activity_host)
        self._activity_layout.setContentsMargins(0, 0, 0, 0)
        self._activity_layout.setSpacing(scale_px(6, min_abs=5))
        layout.addWidget(self._activity_host)
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


__all__ = [
    "ForumAccountSectionsMixin",
    "_FORM_HINT_LOGIN",
    "_FORM_HINT_REGISTER",
    "_font",
]
