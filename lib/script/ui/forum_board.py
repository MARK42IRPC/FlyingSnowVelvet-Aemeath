"""主论坛页：帖子列表、帖子详情与回复。

列表只放「标题 + 摘要 + 作者 + 计数 + 时间」，点一行才进详情；详情页才显示正文块、
回复与楼层（需求 细节3），避免列表里堆一屏正文拖慢滚动。

- 排序只暴露两档：`new`（最新）与 `hot`（最热，服务端按点赞与回复数加权）。
- 点赞、回复都需要登录：未登录时按钮变成「去登录」，点了由窗口切到账号页。
- 正文不下载图片：`lib/core/forum_markdown.py` 把 Markdown 降级成块，图片降级成占位符。
- 翻页沿用留言墙的手感：滚到底自动加载下一页，也有一个明确的「加载更多」按钮兜底。
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_DEFAULT_SORT,
    FORUM_REPLY_MAX,
    FORUM_SORT_LABELS,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumTag,
    format_timestamp,
)
from lib.core.forum_community import CommunityService
from lib.core.forum_markdown import (
    BLOCK_CODE,
    BLOCK_DIVIDER,
    BLOCK_HEADING,
    BLOCK_LIST,
    BLOCK_QUOTE,
    plain_text,
    render_blocks,
)
from lib.core.forum_session import ForumSessionStore, is_logged_in
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

logger = get_logger(__name__)

#: 距底部还有这么多像素就预读下一页。
LOAD_MORE_THRESHOLD_PX = scale_px(160, min_abs=110)
#: 标签条最多铺几个标签，再多会把工具栏挤成两行。
TAG_CHIP_LIMIT = 6
#: 列表里的摘要长度。
EXCERPT_LENGTH = 72
#: 详情页正文块的字号：列表、引用、代码各一档，其余用默认。
_BLOCK_FONT_DEFAULT = 13
_HEADING_FONT_SIZES = {1: 18, 2: 16, 3: 15, 4: 14, 5: 14, 6: 13}


def _font(size: int, *, bold: bool = False):
    font = get_ui_font(size=scale_px(size, min_abs=max(8, size - 2)))
    font.setBold(bold)
    return font


def _like_mark(liked: bool) -> str:
    """实心 / 空心小心形；UI 字体没有 U+2665 时靠系统字体回退。"""
    return "\u2665" if liked else "\u2661"


def _apply_like_state(button: QToolButton, liked: bool, count: int, prefix: str = "") -> None:
    button.setChecked(bool(liked))
    text = f"{_like_mark(liked)} {prefix}{max(0, int(count))}".strip()
    button.setText(text)
    button.setProperty("liked", "yes" if liked else "no")
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)


class ForumPostRow(QFrame):
    """列表里的一行帖子；整行可点，点赞按钮单独吃掉自己的点击。"""

    activated = pyqtSignal(int)
    like_clicked = pyqtSignal(int, bool)

    def __init__(self, post: ForumPost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.post = post
        self.setObjectName("ForumPostRow")
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
        )
        root.setSpacing(scale_px(10, min_abs=8))

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(scale_px(3, min_abs=2))

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(scale_px(6, min_abs=5))
        if post.is_pinned:
            title_row.addWidget(self._badge("置顶", "pinned"), 0)
        if post.is_locked:
            title_row.addWidget(self._badge("已锁定", "locked"), 0)
        self._title = QLabel(post.title or "（无标题）", self)
        self._title.setObjectName("ForumPostTitle")
        self._title.setFont(_font(14, bold=True))
        self._title.setWordWrap(True)
        title_row.addWidget(self._title, 1)
        text_box.addLayout(title_row)

        body = post.excerpt.strip() or plain_text(post.content, limit=EXCERPT_LENGTH)
        if post.image_count and "【图片" not in body:
            body = f"{body}　【图片 ×{post.image_count}】"
        self._excerpt = QLabel(body or "（没有正文）", self)
        self._excerpt.setObjectName("ForumPostExcerpt")
        self._excerpt.setFont(_font(11))
        self._excerpt.setWordWrap(True)
        text_box.addWidget(self._excerpt)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(scale_px(8, min_abs=6))
        author = post.author.label if post.author is not None else "未知用户"
        stamp = format_timestamp(post.last_reply_at or post.created_at)
        self._meta = QLabel(" · ".join(part for part in (author, stamp) if part), self)
        self._meta.setObjectName("ForumPostMeta")
        self._meta.setFont(_font(10))
        meta_row.addWidget(self._meta, 0)
        for tag in post.tags:
            chip = QLabel(f"#{tag}", self)
            chip.setObjectName("ForumPostTag")
            chip.setFont(_font(10))
            meta_row.addWidget(chip, 0)
        meta_row.addStretch(1)
        counts = QLabel(
            f"回复 {post.reply_count} · 点赞 {post.like_count} · 浏览 {post.view_count}",
            self,
        )
        counts.setObjectName("ForumPostMeta")
        counts.setFont(_font(10))
        meta_row.addWidget(counts, 0)
        text_box.addLayout(meta_row)
        root.addLayout(text_box, 1)

        self._like = QToolButton(self)
        self._like.setObjectName("ForumLikeButton")
        self._like.setCursor(Qt.PointingHandCursor)
        self._like.setFont(_font(11))
        self._like.setToolTip("点赞这篇帖子（需要登录）")
        self._like.clicked.connect(self._on_like)
        root.addWidget(self._like, 0)
        self.set_like(post.liked_by_me, post.like_count)

    def _badge(self, text: str, state: str) -> QLabel:
        badge = QLabel(text, self)
        badge.setObjectName("ForumBadge")
        badge.setProperty("state", state)
        badge.setFont(_font(10, bold=True))
        return badge

    def set_like(self, liked: bool, count: int) -> None:
        _apply_like_state(self._like, liked, count)

    def set_like_busy(self, busy: bool) -> None:
        self._like.setEnabled(not busy)

    def _on_like(self) -> None:
        self.like_clicked.emit(self.post.id, not self.post.liked_by_me)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._hits_like(event.pos()):
            self.activated.emit(self.post.id)
        super().mouseReleaseEvent(event)

    def _hits_like(self, point) -> bool:
        """点赞按钮自己处理点击，行不能再跟着进详情。"""
        if not self._like.isVisible():
            return False
        return self._like.geometry().contains(point)


class ForumReplyRow(QFrame):
    """一条回复：楼层号、作者、时间、正文，外带点赞与「回复这一层」。"""

    like_clicked = pyqtSignal(int, bool)
    reply_clicked = pyqtSignal(int)

    def __init__(
        self,
        reply: ForumReply,
        floor: int,
        *,
        parent_floor: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.reply = reply
        self.floor = int(floor)
        self.setObjectName("ForumReplyRow")

        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(8, min_abs=6),
            scale_px(12, min_abs=10),
            scale_px(8, min_abs=6),
        )
        root.setSpacing(scale_px(4, min_abs=3))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(scale_px(7, min_abs=5))
        floor_label = QLabel(f"{self.floor} 楼", self)
        floor_label.setObjectName("ForumFloor")
        floor_label.setFont(_font(10, bold=True))
        head.addWidget(floor_label, 0)

        author = reply.author.label if reply.author is not None else "未知用户"
        prefix = f"回复 {parent_floor} 楼 · " if parent_floor else ""
        meta = QLabel(f"{prefix}{author} · {format_timestamp(reply.created_at)}", self)
        meta.setObjectName("ForumReplyMeta")
        meta.setFont(_font(10))
        head.addWidget(meta, 0)
        head.addStretch(1)

        self._reply_button = QToolButton(self)
        self._reply_button.setObjectName("ForumLinkButton")
        self._reply_button.setText("回复")
        self._reply_button.setCursor(Qt.PointingHandCursor)
        self._reply_button.setFont(_font(10))
        self._reply_button.setToolTip(f"回复 {floor} 楼")
        self._reply_button.clicked.connect(lambda: self.reply_clicked.emit(self.reply.id))
        head.addWidget(self._reply_button, 0)

        self._like = QToolButton(self)
        self._like.setObjectName("ForumLikeButton")
        self._like.setCursor(Qt.PointingHandCursor)
        self._like.setFont(_font(10))
        self._like.clicked.connect(
            lambda: self.like_clicked.emit(self.reply.id, not self.reply.liked_by_me)
        )
        head.addWidget(self._like, 0)
        root.addLayout(head)

        body = plain_text(reply.content, limit=2000) or "（空回复）"
        if reply.image_count:
            body = f"{body}　【图片 ×{reply.image_count}】"
        text = QLabel(body, self)
        text.setObjectName("ForumReplyText")
        text.setFont(_font(12))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(text)

        self.set_like(reply.liked_by_me, reply.like_count)

    def set_like(self, liked: bool, count: int) -> None:
        _apply_like_state(self._like, liked, count)

    def set_like_busy(self, busy: bool) -> None:
        self._like.setEnabled(not busy)

class ForumBoardPage(QWidget):
    """主论坛子页面；自己管网络协调器，窗口只负责切页与生命周期。"""

    _dispatch_requested = pyqtSignal(object)
    login_requested = pyqtSignal()
    #: 副标题依赖的数据变了（帖子总数 / 当前帖子），通知窗口重算。
    subtitle_changed = pyqtSignal()

    def __init__(
        self,
        *,
        session: ForumSessionStore,
        api=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ForumBoardPage")
        self._session = session
        self._rows: dict[int, ForumPostRow] = {}
        self._reply_rows: dict[int, ForumReplyRow] = {}
        self._floor_by_id: dict[int, int] = {}
        self._tags: tuple[ForumTag, ...] = ()
        self._active_tag: str | None = None
        self._reply_target: tuple[int, int] | None = None
        self._sort = FORUM_DEFAULT_SORT
        self._disposed = False
        #: 首次进入时才自动读第一页；来回切页签不再重复请求。
        self._loaded = False

        self._dispatch_requested.connect(self._run_dispatched, Qt.QueuedConnection)
        self._service = CommunityService(
            dispatch=self._dispatch,
            listener=self,
            client=api,
            session=session,
        )

        self._build_ui()
        self._service.restore_snapshot()
        # 账号页退出登录时这边也要收起回复框：订阅会话，而不是只等自己的服务回调。
        session.subscribe(self._on_session_changed)
        self._sync_session(session.get())

    # ── 界面 ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar(), 0)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ForumBoardStack")
        self._stack.addWidget(self._build_list())
        self._stack.addWidget(self._build_detail())
        root.addWidget(self._stack, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName("ForumToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
        )
        layout.setSpacing(scale_px(8, min_abs=6))

        sort_label = QLabel("排序", bar)
        sort_label.setObjectName("ForumHint")
        sort_label.setFont(_font(11))
        layout.addWidget(sort_label, 0)

        self._sort_buttons: dict[str, QToolButton] = {}
        for sort, label in FORUM_SORT_LABELS.items():
            button = QToolButton(bar)
            button.setObjectName("ForumSortButton")
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_font(11))
            button.setToolTip(
                "按发布时间从新到旧" if sort == "new" else "按点赞与回复数排序"
            )
            button.clicked.connect(lambda _checked=False, target=sort: self.set_sort(target))
            self._sort_buttons[sort] = button
            layout.addWidget(button, 0)
        self._sync_sort_buttons()

        self._tag_row = QHBoxLayout()
        self._tag_row.setContentsMargins(0, 0, 0, 0)
        self._tag_row.setSpacing(scale_px(5, min_abs=4))
        layout.addLayout(self._tag_row, 0)

        layout.addStretch(1)
        self._search = QLineEdit(bar)
        self._search.setObjectName("ForumSearchInput")
        self._search.setPlaceholderText("搜索帖子标题或正文…")
        self._search.setFont(_font(11))
        self._search.setFixedWidth(scale_px(180, min_abs=150))
        self._search.returnPressed.connect(self._on_search)
        layout.addWidget(self._search, 0)

        self._search_button = QPushButton("搜索", bar)
        self._search_button.setObjectName("ForumGhostButton")
        self._search_button.setFont(_font(11))
        self._search_button.clicked.connect(self._on_search)
        layout.addWidget(self._search_button, 0)

        self._refresh_button = QPushButton("刷新", bar)
        self._refresh_button.setFont(_font(11))
        self._refresh_button.setToolTip("重新读取第一页并刷新标签云")
        self._refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self._refresh_button, 0)
        return bar

    def _build_list(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
        )
        layout.setSpacing(scale_px(8, min_abs=6))

        self._list_scroll = SmoothScrollArea(page)
        self._list_scroll.setObjectName("ForumScroll")
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.NoFrame)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._list_scroll)
        host.setObjectName("ForumBoardList")
        self._list_host = host
        self._list_layout = QVBoxLayout(host)
        self._list_layout.setContentsMargins(0, 0, scale_px(10, min_abs=8), 0)
        self._list_layout.setSpacing(scale_px(8, min_abs=6))
        self._list_layout.addStretch(1)
        self._list_scroll.setWidget(host)
        self._list_scroll.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)
        layout.addWidget(self._list_scroll, 1)

        self._list_hint = QLabel("正在读取帖子…", page)
        self._list_hint.setObjectName("ForumStatus")
        self._list_hint.setFont(_font(11))
        layout.addWidget(self._list_hint, 0)

        self._more_button = QPushButton("加载更多", page)
        self._more_button.setObjectName("ForumGhostButton")
        self._more_button.setFont(_font(11))
        self._more_button.setVisible(False)
        self._more_button.clicked.connect(lambda: self._service.load_more_posts())
        layout.addWidget(self._more_button, 0)
        return page

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
    # ── 对外 ─────────────────────────────────────────────────────────

    def subtitle(self) -> str:
        """页眉副标题：详情页报帖子标题，列表页报帖子总数。"""
        post = self._service.current_post
        if self._stack.currentIndex() == 1 and post is not None:
            return f"主论坛 · {post.title}"
        total = self._service.post_total
        suffix = f" · 共 {total} 篇帖子" if total else ""
        return f"主论坛{suffix}"

    def needs_initial_load(self) -> bool:
        """窗口判断「第一次打开这一页要不要拉数据」。"""
        return not self._loaded

    def refresh(self) -> None:
        self._loaded = True
        self._service.refresh_posts()

    def show_list(self) -> None:
        self._service.close_post()
        self._clear_reply_rows()
        self._stack.setCurrentIndex(0)
        self.subtitle_changed.emit()

    def set_sort(self, sort: str) -> None:
        if sort == self._sort:
            return
        self._sort = sort
        self._sync_sort_buttons()
        self._service.load_posts(sort=sort, tag=self._active_tag or "", query=self._search.text())

    def apply_tag(self, tag: str | None) -> None:
        self._active_tag = tag or None
        self._sync_tag_chips()
        self._service.load_posts(sort=self._sort, tag=tag or "", query=self._search.text())

    def open_post(self, post_id) -> None:
        """进入详情：先摆好骨架再请求，点击的反馈立刻可见。"""
        if not post_id:
            return
        self._detail_title.setText("正在读取帖子…")
        self._detail_meta.setText("")
        self._detail_hint.setText("")
        self._clear_detail_body()
        self._clear_reply_rows()
        self._stack.setCurrentIndex(1)
        self._service.open_post(int(post_id))

    def cleanup(self) -> None:
        self._disposed = True
        try:
            self._session.unsubscribe(self._on_session_changed)
        except Exception:
            pass
        self._service.cleanup()

    # ── 服务回调（都在 UI 线程） ─────────────────────────────────────

    def on_posts(self, page: ForumPostPage, append: bool, *, cached: bool = False) -> None:
        if not append:
            self._clear_rows()
        for post in page.posts:
            if post.id in self._rows:
                continue
            row = ForumPostRow(post, self._list_host)
            row.activated.connect(self.open_post)
            row.like_clicked.connect(self._on_row_like)
            self._rows[post.id] = row
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        count = len(self._rows)
        if count == 0:
            self._list_hint.setText("没有找到帖子；换个关键字或标签再试")
        else:
            prefix = "缓存内容 · " if cached else ""
            total = page.total or count
            self._list_hint.setText(f"{prefix}已显示 {count} / {total} 篇帖子")
        self._more_button.setVisible(self._service.has_more_posts and not cached)
        self.subtitle_changed.emit()

    def on_post(self, post: ForumPost) -> None:
        self._detail_title.setText(post.title or "（无标题）")
        author = post.author.label if post.author is not None else "未知用户"
        parts = [author, format_timestamp(post.created_at)]
        if post.tags:
            parts.append(" ".join(f"#{tag}" for tag in post.tags))
        parts.append(f"浏览 {post.view_count}")
        if post.is_locked:
            parts.append("已锁定，不能回复")
        self._detail_meta.setText(" · ".join(part for part in parts if part))
        self._set_detail_like(post.liked_by_me, post.like_count)
        self._render_body(post)
        self._clear_reply_rows()
        self._clear_reply_target()
        self._stack.setCurrentIndex(1)
        self.subtitle_changed.emit()

    def on_replies(self, page: ForumReplyPage, append: bool) -> None:
        if not append:
            self._clear_reply_rows()
        start = max(0, (page.page - 1) * page.per_page)
        for index, reply in enumerate(page.replies):
            if reply.id in self._reply_rows:
                continue
            floor = start + index + 1
            self._floor_by_id[reply.id] = floor
            parent_floor = self._floor_by_id.get(reply.parent_id) if reply.parent_id else None
            row = ForumReplyRow(reply, floor, parent_floor=parent_floor, parent=self._replies_host)
            row.like_clicked.connect(self._on_reply_like)
            row.reply_clicked.connect(self._set_reply_target)
            self._reply_rows[reply.id] = row
            self._replies_layout.addWidget(row)
        self._sync_replies_state()

    def on_reply_posted(self, reply: ForumReply, floor: int) -> None:
        self._reply_input.clear()
        self._clear_reply_target()
        self._empty_reply_hint().setVisible(False)
        self._floor_by_id[reply.id] = floor
        row = ForumReplyRow(reply, floor, parent=self._replies_host)
        row.like_clicked.connect(self._on_reply_like)
        row.reply_clicked.connect(self._set_reply_target)
        self._reply_rows[reply.id] = row
        self._replies_layout.addWidget(row)
        self._sync_replies_state()
        bar = self._detail_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def on_likes(self, kind: str, target_id: int, liked: bool, count: int) -> None:
        if kind == "reply":
            row = self._reply_rows.get(target_id)
            if row is not None:
                row.set_like(liked, count)
                row.set_like_busy(False)
            return
        row = self._rows.get(target_id)
        if row is not None:
            row.set_like(liked, count)
            row.set_like_busy(False)
        post = self._service.current_post
        if post is not None and post.id == target_id:
            self._set_detail_like(liked, count)

    def on_tags(self, tags) -> None:
        self._tags = tuple(tags)[:TAG_CHIP_LIMIT]
        self._sync_tag_chips()

    def on_session(self, session) -> None:
        self._sync_session(session)

    def _on_session_changed(self, session) -> None:
        """会话对象的变化（登录 / 退出 / 被踢下线）统一走这里。"""
        self._sync_session(session)

    def on_account(self, _user) -> None:
        """账号资料由账号页展示，这边不需要额外处理。"""

    def on_status(self, text: str, tone: str = "") -> None:
        self._list_hint.setText(text)
        self._detail_hint.setText(text)
        if tone == "warn":
            self._mark_warn(self._list_hint)
            self._mark_warn(self._detail_hint)

    def on_error(self, message: str) -> None:
        self.on_status(message, "warn")

    # ── 交互 ─────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        self._service.load_posts(
            sort=self._sort,
            tag=self._active_tag or "",
            query=self._search.text(),
        )

    def _on_row_like(self, post_id: int, liked: bool) -> None:
        row = self._rows.get(post_id)
        if row is not None:
            row.set_like_busy(True)
        post = self._service.current_post
        if post is not None and post.id == post_id:
            self._set_detail_like(liked, post.like_count)
        if not self._service.toggle_like("post", post_id, liked=liked) and row is not None:
            row.set_like_busy(False)

    def _on_reply_like(self, reply_id: int, liked: bool) -> None:
        row = self._reply_rows.get(reply_id)
        if row is not None:
            row.set_like_busy(True)
        if not self._service.toggle_like("reply", reply_id, liked=liked) and row is not None:
            row.set_like_busy(False)

    def _on_detail_like(self) -> None:
        post = self._service.current_post
        if post is None:
            return
        self._on_row_like(post.id, not post.liked_by_me)

    def _set_detail_like(self, liked: bool, count: int) -> None:
        _apply_like_state(self._detail_like, liked, count, prefix="点赞 ")

    def _set_reply_target(self, reply_id: int) -> None:
        floor = self._floor_by_id.get(int(reply_id))
        if floor is None:
            return
        self._reply_target = (int(reply_id), floor)
        self._reply_target_label.setText(f"正在回复 {floor} 楼")
        self._reply_target_label.setToolTip("再点一次「回复」可以换一层；发完自动清掉")
        self._reply_target_label.setVisible(True)
        self._reply_input.setFocus()

    def _clear_reply_target(self) -> None:
        self._reply_target = None
        self._reply_target_label.setVisible(False)
        self._reply_target_label.setText("")

    def _on_send_reply(self) -> None:
        post = self._service.current_post
        if post is None:
            return
        parent_id = self._reply_target[0] if self._reply_target else None
        error = self._service.post_reply(post.id, self._reply_input.text(), parent_id=parent_id)
        if error:
            self._detail_hint.setText(error)

    def _on_list_scrolled(self, value: int) -> None:
        bar = self._list_scroll.verticalScrollBar()
        if value >= bar.maximum() - LOAD_MORE_THRESHOLD_PX:
            self._service.load_more_posts()

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _sync_sort_buttons(self) -> None:
        for sort, button in self._sort_buttons.items():
            button.setChecked(sort == self._sort)

    def _sync_tag_chips(self) -> None:
        while self._tag_row.count():
            item = self._tag_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._tag_row.addWidget(self._tag_chip("全部", None), 0)
        for tag in self._tags:
            self._tag_row.addWidget(self._tag_chip(f"#{tag.name}", tag.name), 0)

    def _tag_chip(self, text: str, tag: str | None) -> QToolButton:
        chip = QToolButton(self)
        chip.setObjectName("ForumTagChip")
        chip.setText(text)
        chip.setCheckable(True)
        chip.setChecked((tag or None) == self._active_tag)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setFont(_font(10))
        chip.clicked.connect(lambda _checked=False, target=tag: self.apply_tag(target))
        return chip

    def _sync_replies_state(self) -> None:
        total = self._service.replies_total or len(self._reply_rows)
        self._replies_title.setText(f"回复（{total}）")
        self._more_replies.setVisible(self._service.has_more_replies)
        if not self._reply_rows:
            self._empty_reply_hint().setVisible(True)

    def _sync_session(self, session) -> None:
        logged_in = is_logged_in(session)
        self._reply_login.setVisible(not logged_in)
        self._reply_send.setVisible(logged_in)
        self._reply_input.setVisible(logged_in)
        self._detail_login_hint.setVisible(not logged_in)
        self._sync_composer()

    def _sync_composer(self) -> None:
        logged_in = self._session.logged_in()
        has_text = bool(self._reply_input.text().strip())
        self._reply_send.setEnabled(logged_in and has_text)

    def _render_body(self, post: ForumPost) -> None:
        self._clear_detail_body()
        blocks = render_blocks(post.content)
        if not blocks:
            self._add_body_label("（这篇帖子没有正文）", "ForumPostText", 12)
            return
        for block in blocks:
            if block.kind == BLOCK_DIVIDER:
                line = QFrame(self._detail_host)
                line.setObjectName("ForumDivider")
                line.setFrameShape(QFrame.HLine)
                line.setFixedHeight(scale_px(1, min_abs=1))
                self._detail_body.addWidget(line)
            elif block.kind == BLOCK_HEADING:
                size = _HEADING_FONT_SIZES.get(int(block.level or 1), 14)
                self._add_body_label(block.text, "ForumPostHeading", size, bold=True)
            elif block.kind == BLOCK_QUOTE:
                self._add_body_label(f"“{block.text}”", "ForumPostQuote", 12)
            elif block.kind == BLOCK_CODE:
                self._add_body_label(block.text, "ForumPostCode", 11)
            elif block.kind == BLOCK_LIST:
                marker = f"{block.marker} " if block.marker else ""
                indent = "　" * max(0, int(block.level))
                self._add_body_label(
                    f"{indent}{marker}{block.text}", "ForumPostText", _BLOCK_FONT_DEFAULT
                )
            else:
                self._add_body_label(block.text, "ForumPostText", _BLOCK_FONT_DEFAULT)

    def _add_body_label(self, text: str, name: str, size: int, *, bold: bool = False) -> None:
        label = QLabel(text, self._detail_host)
        label.setObjectName(name)
        label.setFont(_font(size, bold=bold))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_body.addWidget(label)

    def _clear_detail_body(self) -> None:
        while self._detail_body.count():
            item = self._detail_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _clear_rows(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

    def _clear_reply_rows(self) -> None:
        while self._replies_layout.count():
            item = self._replies_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._reply_rows.clear()
        self._floor_by_id.clear()

    def _empty_reply_hint(self) -> QLabel:
        """「还没有人回复」的空态；复用同一个标签，不重复创建。"""
        hint = getattr(self, "_empty_hint", None)
        if hint is not None:
            return hint
        hint = QLabel("还没有人回复，来做第一个吧", self._replies_host)
        hint.setObjectName("ForumEmptyHint")
        hint.setFont(_font(11))
        self._replies_layout.addWidget(hint)
        self._empty_hint = hint
        return hint

    def _mark_warn(self, label: QLabel) -> None:
        if label.property("tone") == "warn":
            return
        label.setProperty("tone", "warn")
        style = label.style()
        if style is not None:
            style.unpolish(label)
            style.polish(label)

    def _dispatch(self, callback) -> None:
        self._dispatch_requested.emit(callback)

    def _run_dispatched(self, callback) -> None:
        if self._disposed:
            return
        try:
            callback()
        except Exception as exc:
            logger.warning("[ForumBoard] 回调执行失败: %s", exc)


__all__ = [
    "ForumBoardPage",
    "ForumPostRow",
    "ForumReplyRow",
]