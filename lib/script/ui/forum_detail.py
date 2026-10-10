"""主论坛页的「详情屏」：帖子正文块、正文配图与回复输入区。

批次 3 后续轮次从 `forum_board.ForumBoardPage` 切出。主论坛页是三屏并列（列表 / 详情 /
发帖），发帖屏已在第 56 节切到 `forum_composer`，本轮把详情屏的装配搬出来——
`_build_detail()` 搭「返回列表 + 标题 + 作者信息 + 点赞 + 正文 + 回复标题 + 回复区 +
回复输入框」这一整块骨架。详情的渲染（`_render_body` 一族）、回复行回调、服务协调与
调度仍留在 `ForumBoardPage` 本体。

切分保持逐行等价：`ForumDetailMixin` 的方法体与搬出前一致（缩进也未变），`ForumBoardPage`
只是多继承本 mixin。详情屏依赖的看板状态（`_replies_host` / `_empty_hint` / `_service` 等）
仍由 `ForumBoardPage.__init__` 建立，混入方法与之同源；`_empty_reply_hint()` 仍留在本体，
`_build_detail()` 按 `self` 解析调用它。

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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import FORUM_REPLY_MAX
from lib.core.render.backends.qt.widgets.forum_images import _font
from lib.script.ui.workbench_settings_layout import SmoothScrollArea


class ForumDetailMixin:
    """详情屏的装配；由 `ForumBoardPage` 混入。"""

    def _build_detail(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame(page)
        bar.setObjectName("ForumToolbar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
        )
        bar_layout.setSpacing(scale_px(8, min_abs=6))
        self._back_button = QPushButton("← 返回列表", bar)
        self._back_button.setObjectName("ForumGhostButton")
        self._back_button.setFont(_font(11))
        self._back_button.clicked.connect(self.show_list)
        bar_layout.addWidget(self._back_button, 0)
        self._detail_hint = QLabel("", bar)
        self._detail_hint.setObjectName("ForumHint")
        self._detail_hint.setFont(_font(11))
        bar_layout.addWidget(self._detail_hint, 1)
        layout.addWidget(bar, 0)

        self._detail_scroll = SmoothScrollArea(page)
        self._detail_scroll.setObjectName("ForumScroll")
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.NoFrame)
        self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._detail_scroll)
        host.setObjectName("ForumDetailHost")
        self._detail_host = host
        detail_layout = QVBoxLayout(host)
        detail_layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(12, min_abs=10),
            scale_px(26, min_abs=21),
            scale_px(12, min_abs=10),
        )
        detail_layout.setSpacing(scale_px(7, min_abs=5))

        self._detail_title = QLabel("", host)
        self._detail_title.setObjectName("ForumDetailTitle")
        self._detail_title.setFont(_font(17, bold=True))
        self._detail_title.setWordWrap(True)
        detail_layout.addWidget(self._detail_title)

        self._detail_meta = QLabel("", host)
        self._detail_meta.setObjectName("ForumPostMeta")
        self._detail_meta.setFont(_font(10))
        self._detail_meta.setWordWrap(True)
        detail_layout.addWidget(self._detail_meta)

        self._detail_actions = QHBoxLayout()
        self._detail_actions.setContentsMargins(0, 0, 0, 0)
        self._detail_actions.setSpacing(scale_px(8, min_abs=6))
        self._detail_like = QToolButton(host)
        self._detail_like.setObjectName("ForumLikeButton")
        self._detail_like.setCursor(Qt.PointingHandCursor)
        self._detail_like.setFont(_font(11))
        self._detail_like.clicked.connect(self._on_detail_like)
        self._detail_actions.addWidget(self._detail_like, 0)
        self._detail_login_hint = QLabel("登录后可以点赞与回复", host)
        self._detail_login_hint.setObjectName("ForumHint")
        self._detail_login_hint.setFont(_font(10))
        self._detail_actions.addWidget(self._detail_login_hint, 0)
        self._detail_actions.addStretch(1)
        detail_layout.addLayout(self._detail_actions)

        self._detail_body = QVBoxLayout()
        self._detail_body.setContentsMargins(0, 0, 0, 0)
        self._detail_body.setSpacing(scale_px(5, min_abs=4))
        detail_layout.addLayout(self._detail_body)

        self._replies_title = QLabel("回复", host)
        self._replies_title.setObjectName("ForumSectionTitle")
        self._replies_title.setFont(_font(13, bold=True))
        detail_layout.addWidget(self._replies_title)

        self._replies_host = QWidget(host)
        self._replies_layout = QVBoxLayout(self._replies_host)
        self._replies_layout.setContentsMargins(0, 0, 0, 0)
        self._replies_layout.setSpacing(scale_px(6, min_abs=5))
        detail_layout.addWidget(self._replies_host)
        # 空态提示**常驻**：它和回复行挤在同一个布局里，但 `_clear_reply_rows()` 会跳过它。
        # 早先它跟着行一起被删掉，`self._empty_hint` 就成了指向已销毁 QLabel 的野引用，
        # 下一次 `_sync_replies_state()`（或 `on_reply_posted()`）在 `setVisible` 上抛
        # 「wrapped C/C++ object of type QLabel has been deleted」，整个回调当场中断——
        # 用户看到的就是「回复列表不显示」（2026-09-16 线上日志实测，看第二篇帖子必现）。
        self._empty_hint = self._empty_reply_hint()
        self._empty_hint.setVisible(False)

        self._more_replies = QPushButton("加载更多回复", host)
        self._more_replies.setObjectName("ForumGhostButton")
        self._more_replies.setFont(_font(11))
        self._more_replies.setVisible(False)
        self._more_replies.clicked.connect(lambda: self._service.load_more_replies())
        detail_layout.addWidget(self._more_replies)

        composer = QFrame(host)
        composer.setObjectName("ForumReplyComposer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(
            scale_px(10, min_abs=8),
            scale_px(8, min_abs=6),
            scale_px(10, min_abs=8),
            scale_px(8, min_abs=6),
        )
        composer_layout.setSpacing(scale_px(8, min_abs=6))
        self._reply_target_label = QLabel("", composer)
        self._reply_target_label.setObjectName("ForumHint")
        self._reply_target_label.setFont(_font(10))
        self._reply_target_label.setVisible(False)
        composer_layout.addWidget(self._reply_target_label, 0)
        self._reply_input = QLineEdit(composer)
        self._reply_input.setObjectName("ForumReplyInput")
        self._reply_input.setPlaceholderText(f"写下你的回复…（最多 {FORUM_REPLY_MAX} 字）")
        self._reply_input.setMaxLength(FORUM_REPLY_MAX)
        self._reply_input.setFont(_font(12))
        self._reply_input.returnPressed.connect(self._on_send_reply)
        self._reply_input.textChanged.connect(lambda _text: self._sync_composer())
        composer_layout.addWidget(self._reply_input, 1)
        self._reply_send = QPushButton("回复", composer)
        self._reply_send.setObjectName("ForumSend")
        self._reply_send.setFont(_font(12))
        self._reply_send.clicked.connect(self._on_send_reply)
        composer_layout.addWidget(self._reply_send, 0)
        self._reply_login = QPushButton("去登录", composer)
        self._reply_login.setFont(_font(12))
        self._reply_login.setVisible(False)
        self._reply_login.clicked.connect(self.login_requested.emit)
        composer_layout.addWidget(self._reply_login, 0)
        detail_layout.addWidget(composer)
        detail_layout.addStretch(1)

        self._detail_scroll.setWidget(host)
        layout.addWidget(self._detail_scroll, 1)
        return page


__all__ = [
    "ForumDetailMixin",
]
