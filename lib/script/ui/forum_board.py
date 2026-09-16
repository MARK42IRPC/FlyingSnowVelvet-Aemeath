"""主论坛页：帖子列表、帖子详情与回复。

列表只放「标题 + 摘要 + 作者 + 计数 + 时间」，点一行才进详情；详情页才显示正文块、
回复与楼层（需求 细节3），避免列表里堆一屏正文拖慢滚动。

- 排序四档与服务端 `sort` 取值一一对应：最新 / 最热 / 最近回复 / 最早。
- 发帖是同一个页面里的第三屏（工具栏「发帖」进入），标题、正文、标签都在这里填；
  发布成功后新帖直接插到列表最前面，不用等下一次刷新。
- 点赞、回复都需要登录：未登录时按钮变成「去登录」，点了由窗口切到账号页。
- 正文里的图片按需取字节：`CommunityService.load_thumbnail()` 先看磁盘缓存（`lib/core/forum_images.py`），
  列表行的缩略图与发帖页上传后的预览共用同一个控件（`ForumImageThumb`）。
- 发帖页与留言墙共用同一套行内标记辅助（`lib/script/ui/forum_markup.py` 的 `toggle()`），正文是多行
  编辑框，所以按钮打交道的对象是 `QTextCursor` 而不是 `QLineEdit`；「文字色 / 描边色」两个按钮把选中的
  一段包进颜色令牌（`lib/core/forum_colors.py` 的 `apply_color_tokens()`），一篇帖子因此可以有几段不同的颜色。
- 正文里的行内标记与颜色由 `forum_text.MarkupText` 逐段渲染：QLabel 的富文本能给文字上色，但给不出
  描边，而描边色是发帖页的一个按钮，所以带标记或带颜色的段落才换成它，纯文字段仍走 QLabel 的快路径。
- 翻页沿用留言墙的手感：滚到底自动加载下一页，也有一个明确的「加载更多」按钮兜底。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QTextCursor
from PyQt5.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_CONTENT_MAX,
    FORUM_DEFAULT_SORT,
    FORUM_IMAGES_PER_POST,
    FORUM_REPLY_MAX,
    FORUM_SORT_LABELS,
    FORUM_TAG_MAX_COUNT,
    FORUM_TITLE_MAX,
    FORUM_TITLE_MIN,
    ForumImage,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumTag,
    format_timestamp,
    image_markdown,
)
from lib.core.forum_colors import ForumTextRun, apply_color_tokens, iter_color_tokens
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
from lib.script.ui.forum_color_control import ForumColorControl, format_button_font
from lib.script.ui.forum_markup import (
    FORMAT_BY_KEY,
    FORUM_MARKUP_FORMATS,
    escape_text,
    span_at_cursor,
    to_html,
    toggle,
)
from lib.script.ui.forum_style import forum_card_text_color, forum_muted_text_color
from lib.script.ui.forum_text import MarkupText
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

logger = get_logger(__name__)

#: 距底部还有这么多像素就预读下一页。
LOAD_MORE_THRESHOLD_PX = scale_px(160, min_abs=110)
#: 标签条最多铺几个标签，再多会把工具栏挤成两行。
TAG_CHIP_LIMIT = 6
#: 列表里的摘要长度。
EXCERPT_LENGTH = 72
#: 四档排序的说法；键与服务端 `sort` 取值一致。
_SORT_TOOLTIPS = {
    "new": "按发布时间从新到旧",
    "hot": "按点赞与回复数加权排序",
    "active": "按最后一条回复的时间排序",
    "old": "按发布时间从旧到新",
}
#: 列表行缩略图与发帖页预览图的边长（正方形；图不裁切，按长边贴合进去）。
LIST_THUMB_SIZE = scale_px(54, min_abs=42)
COMPOSE_THUMB_SIZE = scale_px(68, min_abs=54)
#: 内存里最多留几张图的字节：磁盘缓存才是常态（`lib/core/forum_images.py`），
#: 这里只为了让来回切页时预览立刻就在。
THUMB_MEMORY_LIMIT = 128
#: 发帖页富文本正文的高度估算宽度（还没有真实列宽时用它量高）。
BODY_WIDTH_HINT = scale_px(420, min_abs=280)

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


def _clear_layout(layout) -> None:
    """把一个（嵌套）布局里的控件全部摘掉并排队销毁，更深一层的布局一样处理。

    `QLayout.takeAt()` 只把子布局从这一层摘下来：布局本身还攥着自己的控件不放，得连它
    一起清，否则那些控件会留在页面上（`_clear_detail_body()` 的正文配图行就是这么漏的）。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)
            child.deleteLater()


def _color_span_at(text, caret: int, kind: str) -> tuple[int, int] | None:
    """光标落在哪一段同类颜色令牌里，给出 `(开始令牌起点, 这一段结束的位置)`。

    颜色令牌是「从出现处生效、到同类下一个令牌为止」（见 `lib/core/forum_colors.py`），所以
    按顺序扫一遍同类令牌就够：光标在开始令牌之后、下一个同类令牌之前，就是被这一段包着。
    """
    body = str(text or "")
    opening: int | None = None
    for start, _end, token_kind, value in iter_color_tokens(body):
        if token_kind != kind:
            continue
        if opening is not None and opening <= caret < start:
            return opening, start
        opening = start if value else None
    if opening is not None and caret >= opening:
        return opening, len(body)
    return None


class ForumImageThumb(QLabel):
    """一张图片的小预览：列表行与发帖页共用。

    给得出字节就直接画（发帖页刚上传的图本地就有），否则先摆一个占位空框，等页面把
    `CommunityService.load_thumbnail()` 取回来的字节交给 `set_data()`。描边与底色在样式表的
    `QLabel#ForumImageThumb` 里，尺寸由调用方给（列表行与发帖页两档）。

    `removable=True` 时点一下发 `clicked`（发帖页用它把这张图从帖子里摘掉）；列表行里不可移除，
    点击照常由父级（整行）接走，于是在列表里点缩略图与点这一行是同一件事。
    """

    clicked = pyqtSignal(str)

    def __init__(self, image_id, *, size: int, removable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.image_id = str(image_id or "")
        self._removable = bool(removable)
        self._data = b""
        self._size = max(8, int(size))
        self.setObjectName("ForumImageThumb")
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(_font(10))
        self.setText("图" if self.image_id else "?")
        self.setToolTip("点一下把这张图从帖子里摘掉" if self._removable else "帖子里的图片")
        if self._removable:
            self.setCursor(Qt.PointingHandCursor)

    def set_data(self, data) -> bool:
        """把字节画上去；解码不出来就保持占位框（少一张预览不该打断整页）。"""
        raw = bytes(data or b"")
        if not raw:
            return False
        if raw == self._data:
            return True
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw):
            return False
        self._data = raw
        self.setText("")
        self.setPixmap(
            pixmap.scaled(self._size, self._size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        return True

    def has_data(self) -> bool:
        return bool(self._data)

    def mouseReleaseEvent(self, event) -> None:
        if self._removable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.image_id)
            return
        super().mouseReleaseEvent(event)


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
        # 拉到了图片列表就铺缩略图，只在「接口只给了张数、没给图」时才退回文字提示。
        if post.image_count and not post.images and "【图片" not in body:
            body = f"{body}　【图片 ×{post.image_count}】"
        self._excerpt = QLabel(body or "（没有正文）", self)
        self._excerpt.setObjectName("ForumPostExcerpt")
        self._excerpt.setFont(_font(11))
        self._excerpt.setWordWrap(True)
        text_box.addWidget(self._excerpt)

        self._thumbs: list[ForumImageThumb] = []
        if post.images:
            strip = QHBoxLayout()
            strip.setContentsMargins(0, scale_px(2, min_abs=1), 0, 0)
            strip.setSpacing(scale_px(5, min_abs=4))
            for image in post.images[:FORUM_IMAGES_PER_POST]:
                thumb = ForumImageThumb(image.id, size=LIST_THUMB_SIZE, parent=self)
                self._thumbs.append(thumb)
                strip.addWidget(thumb, 0)
            strip.addStretch(1)
            text_box.addLayout(strip)

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
        #: 发帖页已经传好的图片（顺序就是要挂到帖子上的顺序）。
        self._compose_images: list[ForumImage] = []
        #: 正在传的那张图的本地字节：上传成功时用它先把预览画出来，不必再回站里取一次。
        self._pending_image_bytes = b""
        self._uploading_image = False
        #: 正文改成自己的那一刻 `_set_body_text()` 会举起这个旗子，`on_body_changed` 据此判断
        #: 「这次改动是我做的」，不把颜色区段的坐标当成过期。
        self._body_writing = False
        #: 最近一次上色的区段：拖颜色滑条时按它重刷。
        self._color_spans: dict[str, tuple[int, int]] = {}
        #: 图片字节的内存副本（磁盘缓存另有 `lib/core/forum_images.py`）；取不回来的记在
        #: `_thumb_failed` 里，免得每次刷新都再要一遍。
        self._thumb_data: dict[str, bytes] = {}
        self._thumb_failed: set[str] = set()
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
        self._stack.addWidget(self._build_composer())
        root.addWidget(self._stack, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName("ForumToolbar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
        )
        outer.setSpacing(scale_px(6, min_abs=5))

        # 第一行：排序 + 发帖 / 刷新。排序按钮点一下就是一次请求，所以不放下拉菜单，
        # 四档平铺能在窄窗口里一眼看全。
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(scale_px(6, min_abs=5))
        sort_label = QLabel("排序", bar)
        sort_label.setObjectName("ForumTagLabel")
        sort_label.setFont(_font(11))
        top.addWidget(sort_label, 0)

        self._sort_buttons: dict[str, QToolButton] = {}
        for sort, label in FORUM_SORT_LABELS.items():
            button = QToolButton(bar)
            button.setObjectName("ForumSortButton")
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_font(11))
            button.setToolTip(_SORT_TOOLTIPS.get(sort, label))
            button.clicked.connect(lambda _checked=False, target=sort: self.set_sort(target))
            self._sort_buttons[sort] = button
            top.addWidget(button, 0)
        self._sync_sort_buttons()

        top.addStretch(1)
        self._compose_button = QPushButton("发帖", bar)
        self._compose_button.setObjectName("ForumSend")
        self._compose_button.setFont(_font(11))
        self._compose_button.setToolTip("发布一篇新帖（需要登录）")
        self._compose_button.clicked.connect(self.open_composer)
        top.addWidget(self._compose_button, 0)

        self._refresh_button = QPushButton("刷新", bar)
        self._refresh_button.setObjectName("ForumGhostButton")
        self._refresh_button.setFont(_font(11))
        self._refresh_button.setToolTip("重新读取第一页并刷新标签云")
        self._refresh_button.clicked.connect(self.refresh)
        top.addWidget(self._refresh_button, 0)
        outer.addLayout(top)

        # 第二行：标签 + 搜索。标签多起来会换行，搜索框因此单独占一行右侧。
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(scale_px(6, min_abs=5))
        tag_label = QLabel("标签", bar)
        tag_label.setObjectName("ForumTagLabel")
        tag_label.setFont(_font(11))
        bottom.addWidget(tag_label, 0)

        self._tag_row = QHBoxLayout()
        self._tag_row.setContentsMargins(0, 0, 0, 0)
        self._tag_row.setSpacing(scale_px(5, min_abs=4))
        bottom.addLayout(self._tag_row, 0)

        bottom.addStretch(1)
        self._search = QLineEdit(bar)
        self._search.setObjectName("ForumSearchInput")
        self._search.setPlaceholderText("搜索帖子标题或正文…")
        self._search.setFont(_font(11))
        self._search.setFixedWidth(scale_px(180, min_abs=150))
        self._search.returnPressed.connect(self._on_search)
        bottom.addWidget(self._search, 0)

        self._search_button = QPushButton("搜索", bar)
        self._search_button.setObjectName("ForumGhostButton")
        self._search_button.setFont(_font(11))
        self._search_button.clicked.connect(self._on_search)
        bottom.addWidget(self._search_button, 0)
        outer.addLayout(bottom)
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
    def _build_composer(self) -> QWidget:
        """发帖页：标题 + 正文 + 标签，发布成功就回到列表。"""
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
        self._compose_back = QPushButton("← 返回列表", bar)
        self._compose_back.setObjectName("ForumGhostButton")
        self._compose_back.setFont(_font(11))
        self._compose_back.clicked.connect(self.show_list)
        bar_layout.addWidget(self._compose_back, 0)
        self._compose_hint = QLabel("新主题", bar)
        self._compose_hint.setObjectName("ForumHint")
        self._compose_hint.setFont(_font(11))
        bar_layout.addWidget(self._compose_hint, 1)
        layout.addWidget(bar, 0)

        scroll = SmoothScrollArea(page)
        scroll.setObjectName("ForumScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget(scroll)
        host.setObjectName("ForumComposerHost")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(10, min_abs=8),
            scale_px(16 + 10, min_abs=13 + 8),
            scale_px(10, min_abs=8),
        )
        host_layout.setSpacing(scale_px(8, min_abs=6))

        card = QFrame(host)
        card.setObjectName("ForumThreadComposer")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        card_layout.setSpacing(scale_px(7, min_abs=5))

        self._thread_title = QLineEdit(card)
        self._thread_title.setObjectName("ForumField")
        self._thread_title.setPlaceholderText(
            f"标题（{FORUM_TITLE_MIN}–{FORUM_TITLE_MAX} 字）"
        )
        self._thread_title.setMaxLength(FORUM_TITLE_MAX)
        self._thread_title.setFont(_font(12))
        card_layout.addWidget(self._thread_title)

        card_layout.addWidget(self._build_compose_tools(card), 0)
        card_layout.addWidget(self._build_compose_colors(card), 0)

        self._thread_body = QPlainTextEdit(card)
        self._thread_body.setObjectName("ForumThreadBody")
        self._thread_body.setPlaceholderText(
            "正文（支持 Markdown：标题、列表、引用、代码块；上面的按钮会在光标处加标记，"
            "也能给选中的一段选文字色与描边色）"
        )
        self._thread_body.setFont(_font(12))
        self._thread_body.setMinimumHeight(scale_px(150, min_abs=110))
        self._thread_body.textChanged.connect(self._on_body_changed)
        # 光标 / 选区一动就重算按钮的复选状态：亮着就表示「光标处的字就是这种格式 / 这个颜色」。
        self._thread_body.cursorPositionChanged.connect(self._sync_format_buttons)
        self._thread_body.selectionChanged.connect(self._sync_format_buttons)
        card_layout.addWidget(self._thread_body, 1)

        self._image_strip = QWidget(card)
        self._image_strip.setObjectName("ForumImageStrip")
        self._image_row = QHBoxLayout(self._image_strip)
        self._image_row.setContentsMargins(0, 0, 0, 0)
        self._image_row.setSpacing(scale_px(6, min_abs=5))
        self._image_strip.setVisible(False)
        card_layout.addWidget(self._image_strip, 0)

        self._thread_counter = QLabel(f"0 / {FORUM_CONTENT_MAX}", card)
        self._thread_counter.setObjectName("ForumThreadCounter")
        self._thread_counter.setFont(_font(10))
        card_layout.addWidget(self._thread_counter, 0, Qt.AlignRight)

        self._thread_tags = QLineEdit(card)
        self._thread_tags.setObjectName("ForumField")
        self._thread_tags.setPlaceholderText(
            f"标签（可选，最多 {FORUM_TAG_MAX_COUNT} 个，用逗号隔开）"
        )
        self._thread_tags.setFont(_font(12))
        card_layout.addWidget(self._thread_tags)

        self._thread_error = QLabel("", card)
        self._thread_error.setObjectName("ForumFieldError")
        self._thread_error.setFont(_font(11))
        self._thread_error.setWordWrap(True)
        self._thread_error.setVisible(False)
        card_layout.addWidget(self._thread_error)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._compose_send = QPushButton("发布", card)
        self._compose_send.setObjectName("ForumSend")
        self._compose_send.setFont(_font(12))
        self._compose_send.clicked.connect(self._on_publish_thread)
        buttons.addWidget(self._compose_send, 0)
        self._compose_cancel = QPushButton("取消", card)
        self._compose_cancel.setObjectName("ForumGhostButton")
        self._compose_cancel.setFont(_font(12))
        self._compose_cancel.clicked.connect(self.show_list)
        buttons.addWidget(self._compose_cancel, 0)
        self._compose_login = QPushButton("去登录", card)
        self._compose_login.setFont(_font(12))
        self._compose_login.setVisible(False)
        self._compose_login.clicked.connect(self.login_requested.emit)
        buttons.addWidget(self._compose_login, 0)
        buttons.addStretch(1)
        card_layout.addLayout(buttons)

        host_layout.addWidget(card, 0)
        host_layout.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self._sync_thread_counter()
        self._sync_compose_images()
        return page

    def _build_compose_tools(self, card: QWidget) -> QWidget:
        """正文上方的工具栏：行内标记 + 文字色 / 描边色 + 添加图片。

        四个字母按钮与留言墙是同一套（`forum_markup.toggle()`），只是打交道的对象换成了
        `QPlainTextEdit` 的光标；「文字色 / 描边色」两个按钮既是开关也是应用动作
        （勾上就把选中的一段上成当前颜色，再点一下取消），对应的滑条见 `_build_compose_colors()`。
        """
        bar = QWidget(card)
        bar.setObjectName("ForumComposeTools")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(5, min_abs=4))

        self._format_buttons: dict[str, QToolButton] = {}
        for fmt in FORUM_MARKUP_FORMATS:
            button = QToolButton(bar)
            button.setObjectName("ForumFormatButton")
            button.setText(fmt.button)
            button.setCheckable(True)
            button.setToolTip(
                f"{fmt.label}：选中文字后点一下，用 {fmt.marker} 把选区包起来；没有选中时先点亮按钮"
                f"再输入，打的字就自动是{fmt.label}。再点一下取消。"
            )
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(format_button_font(fmt.key))
            button.clicked.connect(lambda _checked=False, key=fmt.key: self._toggle_body_format(key))
            self._format_buttons[fmt.key] = button
            row.addWidget(button, 0)

        self._color_buttons: dict[str, QToolButton] = {}
        for kind, label, tip in (
            ("color", "文字色", "把选中的一段（或接下来打的字）改成选定的文字色；再点一下取消"),
            ("outline", "描边色", "给选中的一段（或接下来打的字）加一圈描边色；再点一下取消"),
        ):
            button = QToolButton(bar)
            button.setObjectName("ForumColorButton")
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_font(11))
            button.clicked.connect(lambda _checked=False, key=kind: self._toggle_body_color(key))
            self._color_buttons[kind] = button
            row.addWidget(button, 0)

        row.addStretch(1)
        self._image_button = QPushButton("添加图片", bar)
        self._image_button.setObjectName("ForumGhostButton")
        self._image_button.setFont(_font(11))
        self._image_button.clicked.connect(self._on_add_image)
        row.addWidget(self._image_button, 0)
        return bar

    def _build_compose_colors(self, card: QWidget) -> QWidget:
        """颜色滑条那一行：勾了哪一档就铺开哪一档，两个都没勾就整行收起。

        这里的取色控件不带自己的复选框（`show_toggle=False`）：开关就是工具栏上那两个按钮，
        控件只负责「选颜色」，滑条因此常驻。
        """
        host = QWidget(card)
        host.setObjectName("ForumComposeColors")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(8, min_abs=6))
        self._color_pickers: dict[str, ForumColorControl] = {}
        for kind, label in (("color", "文字颜色"), ("outline", "描边颜色")):
            picker = ForumColorControl(host, label, show_toggle=False)
            picker.colorChanged.connect(lambda _color, key=kind: self._reapply_body_color(key))
            self._color_pickers[kind] = picker
            row.addWidget(picker, 0)
        row.addStretch(1)
        host.setVisible(False)
        self._color_host = host
        return host

    # ── 发帖页：行内标记、颜色与图片 ─────────────────────────────────

    def _body_text(self) -> str:
        return self._thread_body.toPlainText()

    def _body_caret(self) -> int:
        """光标位置；选中一段时给选区起点（`QTextCursor.position()` 指的是选区末尾）。"""
        cursor = self._thread_body.textCursor()
        return cursor.selectionStart() if cursor.hasSelection() else cursor.position()

    def _body_selection(self) -> tuple[int, int]:
        cursor = self._thread_body.textCursor()
        return cursor.selectionStart(), cursor.selectionEnd()

    def _set_body_text(self, text: str, start: int, stop: int) -> None:
        """换掉正文并摆好光标 / 选区；`_body_writing` 让 `_on_body_changed()` 知道是自己改的。"""
        self._body_writing = True
        try:
            self._thread_body.setPlainText(text)
        finally:
            self._body_writing = False
        size = len(text)
        cursor = self._thread_body.textCursor()
        cursor.setPosition(max(0, min(int(start), size)))
        if stop > start:
            cursor.setPosition(max(0, min(int(stop), size)), QTextCursor.KeepAnchor)
        self._thread_body.setTextCursor(cursor)
        self._thread_body.setFocus()
        self._sync_format_buttons()

    def _on_body_changed(self) -> None:
        self._sync_thread_counter()
        if not self._body_writing:
            # 正文被人手改了，之前记下的颜色区段坐标就不再可信，别再拿它去改颜色。
            self._color_spans.clear()

    def _toggle_body_format(self, key: str) -> None:
        """复选按钮：在光标 / 选区处加减一对标记，接着打的字自动落在标记里。"""
        fmt = FORMAT_BY_KEY.get(str(key or ""))
        if fmt is None:
            return
        caret, end = self._body_selection()
        text, start, stop = toggle(self._body_text(), caret, end, fmt.marker)
        if len(text) > FORUM_CONTENT_MAX:
            self._compose_error(f"加上标记会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            self._sync_format_buttons()
            return
        self._set_body_text(text, start, stop)

    def _toggle_body_color(self, kind: str) -> None:
        """「文字色 / 描边色」按钮：勾上就是把选中的一段上成当前颜色，再点一下取消。"""
        picker = self._color_pickers.get(kind)
        if picker is None:
            return
        body = self._body_text()
        start, end = self._body_selection()
        if _color_span_at(body, start, kind) is not None:
            value = ""  # 光标已经在这一段里了：这一下就是取消
        else:
            value = picker.color()
        text, new_start, new_stop = apply_color_tokens(body, start, end, **{kind: value})
        if len(text) > FORUM_CONTENT_MAX:
            self._compose_error(f"加颜色令牌会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            self._sync_format_buttons()
            return
        if value:
            self._color_spans[kind] = (new_start, new_stop)
        else:
            self._color_spans.pop(kind, None)
        self._set_body_text(text, new_start, new_stop)
        self._sync_color_host()

    def _reapply_body_color(self, kind: str) -> None:
        """拖动滑条时，把刚上过色的那一段换成新颜色（换色是重刷，不会越点越长）。"""
        button = self._color_buttons.get(kind)
        picker = self._color_pickers.get(kind)
        span = self._color_spans.get(kind)
        if span is None or picker is None or button is None or not button.isChecked():
            return
        body = self._body_text()
        text, start, stop = apply_color_tokens(body, span[0], span[1], **{kind: picker.color()})
        if text == body:
            return
        self._color_spans[kind] = (start, stop)
        self._set_body_text(text, start, stop)

    def _sync_format_buttons(self) -> None:
        """按钮的复选状态跟着光标：亮着就表示光标处的字已经是这种格式 / 这个颜色。"""
        if not hasattr(self, "_format_buttons"):
            return
        text = self._body_text()
        caret = self._body_caret()
        for key, button in self._format_buttons.items():
            checked = span_at_cursor(text, caret, FORMAT_BY_KEY[key].marker) is not None
            if button.isChecked() != checked:
                button.setChecked(checked)
        for kind, button in self._color_buttons.items():
            checked = _color_span_at(text, caret, kind) is not None
            if button.isChecked() != checked:
                button.setChecked(checked)

    def _sync_color_host(self) -> None:
        """颜色滑条跟着两个按钮走：勾了哪一档铺开哪一档，两个都没勾就整行收起。"""
        shown = False
        for kind, picker in self._color_pickers.items():
            visible = self._color_buttons[kind].isChecked()
            picker.setVisible(visible)
            shown = shown or visible
        self._color_host.setVisible(shown)

    def _insert_body_text(self, text: str) -> None:
        """在光标处插一段文字（图片 Markdown 走这里），超过了字数上限就只在错误行说明。"""
        body = self._body_text()
        caret, end = self._body_selection()
        merged = body[:caret] + text + body[end:]
        if len(merged) > FORUM_CONTENT_MAX:
            self._compose_error(f"插进来会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            return
        stop = caret + len(text)
        self._set_body_text(merged, stop, stop)

    def _on_add_image(self) -> None:
        """选一张图并上传；本地能拦下的问题（图上够了）就不必再走一趟网络。"""
        if len(self._compose_images) >= FORUM_IMAGES_PER_POST:
            self._compose_error(f"一个帖子最多挂 {FORUM_IMAGES_PER_POST} 张图，先摘掉一张再传")
            return
        path, _selected = QFileDialog.getOpenFileName(
            self, "选一张图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp);;所有文件 (*)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self._compose_error(f"这个文件读不出来：{exc}")
            return
        self._pending_image_bytes = data
        error = self._service.upload_image(data, name=Path(path).name)
        if error:
            self._pending_image_bytes = b""
            self._compose_error(error)
            self._sync_composer()
            return
        self._uploading_image = True
        self._compose_error("")
        self._sync_composer()

    def _remove_compose_image(self, image_id: str) -> None:
        """把一张已经传上去的图从这篇帖子里摘掉（图库那边不动）。"""
        ident = str(image_id or "")
        self._compose_images = [image for image in self._compose_images if image.id != ident]
        self._sync_compose_images()

    def _sync_compose_images(self) -> None:
        """铺开已上传图片的预览条；一张都没有就整条收起。"""
        while self._image_row.count():
            item = self._image_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for image in self._compose_images:
            thumb = ForumImageThumb(
                image.id, size=COMPOSE_THUMB_SIZE, removable=True, parent=self._image_strip
            )
            thumb.set_data(self._thumb_data.get(image.id, b""))
            thumb.clicked.connect(self._remove_compose_image)
            self._image_row.addWidget(thumb, 0)
        self._image_row.addStretch(1)
        self._image_strip.setVisible(bool(self._compose_images))
        self._sync_composer()

    # ── 对外 ─────────────────────────────────────────────────────────

    def subtitle(self) -> str:
        """页眉副标题：详情页报帖子标题，列表页报帖子总数。"""
        post = self._service.current_post
        if self._stack.currentIndex() == 2:
            return "主论坛 · 发布新帖"
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

    def open_composer(self) -> None:
        """进入发帖页；没登录就先把话说清楚，别让人写完才发现发不出去。"""
        self._compose_error("" if self._session.logged_in() else "登录后才能发帖。")
        self._stack.setCurrentIndex(2)
        if self._session.logged_in():
            self._thread_title.setFocus()
        self.subtitle_changed.emit()

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
            self._add_row(post)
        count = len(self._rows)
        if count == 0:
            self._list_hint.setText("没有找到帖子；换个关键字或标签再试")
        else:
            prefix = "缓存内容 · " if cached else ""
            total = page.total or count
            self._list_hint.setText(f"{prefix}已显示 {count} / {total} 篇帖子")
        self._more_button.setVisible(self._service.has_more_posts and not cached)
        self.subtitle_changed.emit()

    def on_thread_posted(self, post: ForumPost) -> None:
        """新帖发出去：清空表单、把它插到列表最前面并退出发帖页。"""
        self._thread_title.clear()
        self._thread_body.clear()
        self._thread_tags.clear()
        self._compose_images = []
        self._sync_compose_images()
        self._compose_error("")
        self._add_row(post, top=True)
        self._stack.setCurrentIndex(0)
        self._list_hint.setText(f"已发布《{post.title}》")
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
        self._floor_by_id[reply.id] = floor
        row = ForumReplyRow(reply, floor, parent=self._replies_host)
        row.like_clicked.connect(self._on_reply_like)
        row.reply_clicked.connect(self._set_reply_target)
        self._reply_rows[reply.id] = row
        self._replies_layout.addWidget(row)
        self._sync_replies_state()
        bar = self._detail_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def on_image_uploaded(self, image) -> None:
        """上传成功：挂进这篇帖子，顺手把图片 Markdown 插在光标处。"""
        self._uploading_image = False
        data = self._pending_image_bytes
        self._pending_image_bytes = b""
        if image is None or not getattr(image, "id", ""):
            self._sync_composer()
            return
        if len(self._compose_images) < FORUM_IMAGES_PER_POST:
            self._compose_images.append(image)
            if data:
                self._remember_thumb(image.id, data)
        self._compose_error("")
        self._insert_body_text(image_markdown(image))
        self._sync_compose_images()

    def on_image_error(self, message: str) -> None:
        """上传失败：原因写在发帖页的错误行上，图不挂进帖子。"""
        self._uploading_image = False
        self._pending_image_bytes = b""
        self._compose_error(message)
        self._sync_composer()

    def on_thumbnail(self, image_id, data) -> None:
        """某张图的字节到了（取不回来时给空串）：铺到页面上，失败的记下来别再要。"""
        ident = str(image_id or "")
        if not ident:
            return
        if data:
            self._remember_thumb(ident, data)
        else:
            self._thumb_failed.add(ident)
        self._refresh_thumbs()

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
        # 空态只看「有没有行」：有行就收起来，没行才铺出来（两个方向都要动，
        # 只管「显示」的话，从空帖切到有回复的帖子时空态会一直挂在那儿）。
        self._empty_reply_hint().setVisible(not self._reply_rows)

    def _sync_session(self, session) -> None:
        logged_in = is_logged_in(session)
        self._reply_login.setVisible(not logged_in)
        self._reply_send.setVisible(logged_in)
        self._reply_input.setVisible(logged_in)
        self._detail_login_hint.setVisible(not logged_in)
        self._sync_composer()

    def _add_row(self, post: ForumPost, *, top: bool = False):
        """把一篇帖子摆进列表；`top=True` 时插到最前面（刚发布的新帖）。"""
        if not post.id or post.id in self._rows:
            return None
        row = ForumPostRow(post, self._list_host)
        row.activated.connect(self.open_post)
        row.like_clicked.connect(self._on_row_like)
        self._rows[post.id] = row
        if top:
            self._list_layout.insertWidget(0, row)
        else:
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        self._refresh_thumbs()
        return row

    def _on_publish_thread(self) -> None:
        error = self._service.post_thread(
            self._thread_title.text(),
            self._thread_body.toPlainText(),
            tags=self._thread_tags.text(),
            images=[image.id for image in self._compose_images],
        )
        self._compose_error(error)

    def _compose_error(self, text: str) -> None:
        message = str(text or "").strip()
        self._thread_error.setText(message)
        self._thread_error.setVisible(bool(message))

    def _sync_thread_counter(self) -> None:
        length = len(self._thread_body.toPlainText())
        over = length > FORUM_CONTENT_MAX
        self._thread_counter.setText(
            f"{length} / {FORUM_CONTENT_MAX}" if not over else f"超出 {length - FORUM_CONTENT_MAX} 字"
        )
        if self._thread_counter.property("tone") == ("warn" if over else ""):
            return
        self._thread_counter.setProperty("tone", "warn" if over else "")
        style = self._thread_counter.style()
        if style is not None:
            style.unpolish(self._thread_counter)
            style.polish(self._thread_counter)

    def _sync_composer(self) -> None:
        logged_in = self._session.logged_in()
        has_text = bool(self._reply_input.text().strip())
        self._reply_send.setEnabled(logged_in and has_text)
        self._compose_send.setEnabled(logged_in)
        self._compose_send.setToolTip("" if logged_in else "登录后才能发帖")
        self._compose_login.setVisible(not logged_in)
        self._compose_button.setToolTip(
            "发布一篇新帖" if logged_in else "发布一篇新帖（需要登录）"
        )
        count = len(self._compose_images)
        full = count >= FORUM_IMAGES_PER_POST
        self._image_button.setEnabled(logged_in and not self._uploading_image and not full)
        if self._uploading_image:
            self._image_button.setText("正在上传…")
        else:
            suffix = f"（{count}/{FORUM_IMAGES_PER_POST}）" if count else ""
            self._image_button.setText(f"添加图片{suffix}")
        if not logged_in:
            tip = "登录后才能上传图片"
        elif full:
            tip = f"一个帖子最多挂 {FORUM_IMAGES_PER_POST} 张图"
        else:
            tip = "上传一张图片（PNG / JPEG / GIF / WebP），正文里会插入它的 Markdown"
        self._image_button.setToolTip(tip)

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
                self._add_body_label(block.text, "ForumPostHeading", size, bold=True, block=block)
            elif block.kind == BLOCK_QUOTE:
                self._add_body_label(
                    f"“{block.text}”",
                    "ForumPostQuote",
                    12,
                    block=block,
                    prefix="“",
                    suffix="”",
                    color=forum_muted_text_color(),
                )
            elif block.kind == BLOCK_CODE:
                self._add_body_label(block.text, "ForumPostCode", 11)
            elif block.kind == BLOCK_LIST:
                marker = f"{block.marker} " if block.marker else ""
                indent = "　" * max(0, int(block.level))
                prefix = f"{indent}{marker}"
                self._add_body_label(
                    f"{prefix}{block.text}",
                    "ForumPostText",
                    _BLOCK_FONT_DEFAULT,
                    block=block,
                    prefix=prefix,
                )
            else:
                self._add_body_label(block.text, "ForumPostText", _BLOCK_FONT_DEFAULT, block=block)
        self._add_body_images(post)

    def _add_body_label(
        self,
        text: str,
        name: str,
        size: int,
        *,
        bold: bool = False,
        block=None,
        prefix: str = "",
        suffix: str = "",
        color: str = "",
    ) -> None:
        """正文的一段。

        纯文字段用 QLabel（样式表管字体与颜色，最省事）；带行内标记或颜色令牌的段换成
        `MarkupText`——QLabel 的富文本能给文字上色，但给不出**描边**，而描边色是发帖页的
        一个按钮，漏掉它就成了「发了带描边的帖子，看起来却没有描边」。两条路径的字体、字号
        与颜色取自同一处，观感一致。
        """
        if block is not None and self._block_needs_rich(block):
            self._add_body_rich(
                block, name, size, bold=bold, prefix=prefix, suffix=suffix, color=color
            )
            return
        label = QLabel(text, self._detail_host)
        label.setObjectName(name)
        label.setFont(_font(size, bold=bold))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_body.addWidget(label)

    @staticmethod
    def _block_needs_rich(block) -> bool:
        """这一段光靠 QLabel 显示不出原样吗（有颜色令牌，或有行内标记）。"""
        if block.kind == BLOCK_CODE:
            # 代码块里的 `**` 就是要原样显示，别当成格式解释。
            return False
        if any(run.color or run.outline for run in block.runs):
            return True
        html = to_html(block.raw)
        return bool(html) and html != escape_text(block.text)

    def _add_body_rich(
        self, block, name: str, size: int, *, bold: bool, prefix: str, suffix: str, color: str
    ) -> None:
        """带标记 / 带颜色的段：交给 `MarkupText` 逐段着色（连描边一起）。

        前缀（列表的项目符号与缩进、引用的书名号）不进 `block.runs`，所以要自己补一段没有
        颜色的 run，段的边界才对得上——`MarkupText` 是按累计字符数定位的。
        """
        runs = block.runs
        if prefix or suffix:
            runs = (ForumTextRun(prefix),) + runs + (ForumTextRun(suffix),)
        widget = MarkupText(
            escape_text(prefix) + to_html(block.raw) + escape_text(suffix),
            font=_font(size, bold=bold),
            color=color or forum_card_text_color(),
            width_hint=BODY_WIDTH_HINT,
            runs=runs,
            align=Qt.AlignLeft,
            object_name="ForumBodyText",
            parent=self._detail_host,
        )
        widget.setProperty("forumBlock", name)
        self._detail_body.addWidget(widget)

    def _add_body_images(self, post: ForumPost) -> None:
        """正文底下铺这一帖的配图；正文里那句「【图片】」占位当图注留着。"""
        if not post.images:
            return
        strip = QHBoxLayout()
        strip.setContentsMargins(0, scale_px(3, min_abs=2), 0, 0)
        strip.setSpacing(scale_px(6, min_abs=5))
        for image in post.images[:FORUM_IMAGES_PER_POST]:
            strip.addWidget(
                ForumImageThumb(image.id, size=LIST_THUMB_SIZE, parent=self._detail_host), 0
            )
        strip.addStretch(1)
        self._detail_body.addLayout(strip)
        self._refresh_thumbs()

    def _clear_detail_body(self) -> None:
        while self._detail_body.count():
            item = self._detail_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            # 正文配图那一行是**嵌套布局**（`_add_body_images()` 走的是 `addLayout()`）：
            # 只 takeAt 不删它，里面那几张缩略图就一直挂在详情页上（换帖之后是残影）。
            layout = item.layout()
            if layout is not None:
                _clear_layout(layout)
                layout.deleteLater()

    def _clear_rows(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

    def _clear_reply_rows(self) -> None:
        hint = self._live_empty_hint()
        while self._replies_layout.count():
            item = self._replies_layout.takeAt(0)
            widget = item.widget()
            if widget is None or widget is hint:
                continue
            if sip.isdeleted(widget):
                continue
            widget.setParent(None)
            widget.deleteLater()
        if hint is not None:
            # 取出来再放回去：空态始终排在回复行前面，但它自己不跟着被销毁。
            self._replies_layout.insertWidget(0, hint)
        self._reply_rows.clear()
        self._floor_by_id.clear()

    def _live_empty_hint(self) -> QLabel | None:
        """常驻的空态标签；底下的 C++ 对象已经不在了就当它没有（`sip.isdeleted`）。"""
        hint = getattr(self, "_empty_hint", None)
        if hint is None or sip.isdeleted(hint):
            return None
        return hint

    def _empty_reply_hint(self) -> QLabel:
        """「还没有人回复」的空态；常驻同一个标签，不重复创建、也不跟着回复行被删。

        `self._empty_hint` 只是 Python 侧的引用：控件在别处被销毁之后这个引用还在，继续用
        就会在 `setVisible()` 上抛「wrapped C/C++ object of type QLabel has been deleted」，
        整个 `on_replies` / `on_reply_posted` 回调当场中断——用户看到的就是「回复列表不显示」
        （2026-09-16 线上日志实测）。所以每次都先确认底下的 C++ 对象还活着，死了就重建一个、
        并把这件事写进日志：回复列表绝不能因为一个提示标签而整片空掉。
        """
        hint = self._live_empty_hint()
        if hint is not None:
            return hint
        if getattr(self, "_empty_hint", None) is not None:
            logger.warning("[ForumBoard] 空态提示已被销毁，重建一个（回复列表不该因此中断）")
        hint = QLabel("还没有人回复，来做第一个吧", self._replies_host)
        hint.setObjectName("ForumEmptyHint")
        hint.setFont(_font(11))
        self._replies_layout.addWidget(hint)
        self._empty_hint = hint
        return hint

    def _remember_thumb(self, image_id: str, data: bytes) -> None:
        """记一份图片字节（内存里只留最近这些张，磁盘缓存才是常态）。"""
        while len(self._thumb_data) >= THUMB_MEMORY_LIMIT:
            self._thumb_data.pop(next(iter(self._thumb_data)), None)
        self._thumb_data[image_id] = bytes(data)
        self._thumb_failed.discard(image_id)

    def _refresh_thumbs(self) -> None:
        """把手上有的字节铺到页面上每一张缩略图；还没有的去服务层取（同一张只跑一趟）。

        刻意按控件树现查现铺（`findChildren`），不另记一张「图 id → 控件」的表：行被清掉时
        Qt 会把子控件从树上摘掉，这里就不可能捏着一个已经销毁的控件——列表反复重排也不会。
        """
        for thumb in self.findChildren(ForumImageThumb):
            ident = thumb.image_id
            if not ident or thumb.has_data():
                continue
            data = self._thumb_data.get(ident)
            if data:
                thumb.set_data(data)
                continue
            if ident in self._thumb_failed:
                continue
            self._service.load_thumbnail(ident)

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