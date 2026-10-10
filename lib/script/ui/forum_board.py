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

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal  # noqa: F401
from PyQt5.QtGui import QPixmap, QTextCursor  # noqa: F401
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,  # noqa: F401
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_DEFAULT_SORT,
    FORUM_IMAGES_PER_POST,
    FORUM_SORT_LABELS,
    ForumImage,
    ForumPost,
    ForumPostPage,
    ForumReply,
    ForumReplyPage,
    ForumTag,
    format_timestamp,
    image_markdown,
)
from lib.core.forum_community import CommunityService
from lib.core.forum_session import ForumSessionStore, is_logged_in
from lib.core.logger import get_logger
from lib.script.ui.forum_composer import (
    FORUM_SIZE_STEPS as FORUM_SIZE_STEPS,  # noqa: F401 - 既有导入面
    ForumComposerMixin as _ForumComposerMixin,
)
from lib.script.ui.forum_detail import ForumDetailMixin as _ForumDetailMixin
from lib.script.ui.forum_body import ForumBodyMixin as _ForumBodyMixin

from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

#: 图片视图与列表行，以及它们共用的排版辅助，已按档位 D 下沉到
#: `lib/core/render/backends/qt/widgets/forum_images.py`；这里按原语义重新导出，
#: 页面代码与外部（含测试）的既有导入路径都不变。
from lib.core.render.backends.qt.widgets import forum_images

# 这些名字按原语义重新导出（`forum_board.ForumPostRow` 等既有导入面不变）。
from lib.core.render.backends.qt.widgets.forum_images import (  # noqa: F401
    BODY_WIDTH_HINT,
    COMPOSE_THUMB_SIZE,
    FORUM_IMAGE_MAX_PIXELS,
    LIST_THUMB_SIZE,
    THUMB_MEMORY_LIMIT,
    ForumDetailImage,
    ForumImageThumb,
    ForumImageView,
    ForumPostRow,
    ForumReplyRow,
    _apply_like_state,
    _block_alignment,
    _clear_layout,
    _color_span_at,
    _font,
)

# 档位 D 的字体装配点：控件自己的字号仍由产品面的 `render_bridge.ui_font` 提供，
# 与下沉前逐字段同源；`forum_images` 因此不必反向 import 产品面。
forum_images.configure_font_factory(get_ui_font)

logger = get_logger(__name__)

#: 距底部还有这么多像素就预读下一页。
LOAD_MORE_THRESHOLD_PX = scale_px(160, min_abs=110)
#: 标签条最多铺几个标签，再多会把工具栏挤成两行。
TAG_CHIP_LIMIT = 6
#: 四档排序的说法；键与服务端 `sort` 取值一致。
_SORT_TOOLTIPS = {
    "new": "按发布时间从新到旧",
    "hot": "按点赞与回复数加权排序",
    "active": "按最后一条回复的时间排序",
    "old": "按发布时间从旧到新",
}
#: 详情页正文块的字号：列表、引用、代码各一档，其余用默认。
_BLOCK_FONT_DEFAULT = 13
#: 发帖页字号下拉的档位已随发帖屏一同下沉到
#: `lib/script/ui/forum_composer.py`；这里按原名重新导出，既有导入面不变。
_HEADING_FONT_SIZES = {1: 18, 2: 16, 3: 15, 4: 14, 5: 14, 6: 13}


class ForumBoardPage(_ForumComposerMixin, _ForumDetailMixin, _ForumBodyMixin, QWidget):
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
        for thumb in self.findChildren(ForumImageView):
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
