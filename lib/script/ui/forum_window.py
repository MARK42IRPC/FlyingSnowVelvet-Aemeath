"""雪绒论坛窗口：工作台式外壳 + 三列自适应卡片墙 + 底部发帖框。

数据来自 `lib/core/forum.py`（`GET /api/feed` 分页读取、`POST /api/messages` 发帖）。
窗口只负责呈现与交互：卡片宽度固定为列宽、高度随内容变化，卡片配色只改描边；
往下滚动到底部再请求更早的一页，发帖按钮受客户端 12 秒冷却约束。

字号与控件间距对齐工作台（页头 19/11、卡片 14/17/11、输入与按钮 14）；卡片正文再按字数
在 1~2 倍之间自适应，短句放大、长文回到基准字号。
卡片底纹由 `forum_texture` 按卡片信息内容哈希生成，这里只负责把它铺到卡片上。
窗口优先级跟随工作台窗口：普通窗口 + 无边框，既不置顶也不进 `LayerManager`——
注册进去的窗口会被 `stack_window()` 放进 `HWND_TOPMOST` 链，那正是「压住别的窗口」
的来源。
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.event.center import EventType, get_event_center
from lib.core.forum import (
    FORUM_ACCENTS,
    FORUM_DEFAULT_ACCENT,
    FORUM_MAX_CONTENT,
    ForumMessage,
    ForumPage,
    ForumService,
    format_relative_time,
)
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.script.ui.forum_style import (
    FORUM_ACCENT_LABELS,
    FORUM_CARD_RADIUS,
    forum_card_text_size,
    forum_stylesheet,
    forum_texture_color,
)
from lib.script.ui.forum_texture import (
    CardTexture,
    card_texture,
    paint_card_texture,
)
from lib.script.ui.workbench_components import create_window_button

COLUMN_COUNT = 3
WALL_MARGIN = scale_px(16, min_abs=13)
COLUMN_SPACING = scale_px(12, min_abs=10)
#: 窗口宽度取工作台的一半左右，三列必须仍能并排放下，所以卡片最小宽度随之下调。
CARD_MIN_WIDTH = scale_px(170, min_abs=150)
#: 最小宽度直接由三列网格推出，避免窗口窄到把卡片挤出行外。
MIN_WINDOW_WIDTH = (
    COLUMN_COUNT * CARD_MIN_WIDTH
    + (COLUMN_COUNT - 1) * COLUMN_SPACING
    + 2 * WALL_MARGIN
)
DEFAULT_WINDOW_WIDTH = max(MIN_WINDOW_WIDTH, scale_px(620, min_abs=580))
DEFAULT_WINDOW_HEIGHT = scale_px(800, min_abs=700)
LOAD_OLDER_THRESHOLD_PX = scale_px(140, min_abs=90)
NICKNAME_MAX_LENGTH = 24
FORUM_NICKNAME_PLACEHOLDER = "输入昵称…（未输入以匿名发送）"


class ForumCard(QFrame):
    """一条留言卡片：左上昵称、中间大字正文、右下日期；accent 决定描边颜色与底纹色调。"""

    def __init__(self, message: ForumMessage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = message
        self.texture: CardTexture = card_texture(message)
        self.setObjectName("ForumCard")
        self.setProperty("accent", message.accent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumWidth(CARD_MIN_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        layout.setSpacing(scale_px(9, min_abs=7))

        name = QLabel(message.nickname, self)
        name.setObjectName("ForumCardName")
        name.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        # 昵称按内容换行，长昵称不会把这张卡片撑得比同列其它卡片宽。
        name.setWordWrap(True)
        layout.addWidget(name, 0, Qt.AlignLeft)

        content = QLabel(message.content, self)
        content.setObjectName("ForumCardText")
        # 正文越短字号越大（1~2 倍），卡片不会因为一句话就空掉。
        content.setFont(get_ui_font(size=forum_card_text_size(message.content)))
        content.setWordWrap(True)
        content.setAlignment(Qt.AlignHCenter)
        content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.addWidget(content, 0)

        stamp = QLabel(format_relative_time(message.created_at), self)
        stamp.setObjectName("ForumCardMeta")
        stamp.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        layout.addWidget(stamp, 0, Qt.AlignRight)

    def paintEvent(self, event) -> None:
        """先按样式表画底与描边，再在最底层铺一层带卡片色调的几何底纹。"""
        super().paintEvent(event)
        texture = self.texture
        if texture is None:
            return
        border = scale_px(1, min_abs=1)
        painter = QPainter(self)
        try:
            paint_card_texture(
                painter,
                QRectF(self.rect()).adjusted(border, border, -border, -border),
                texture,
                forum_texture_color(accent=self.property("accent")),
                radius=FORUM_CARD_RADIUS,
            )
        finally:
            painter.end()


class ForumWindow(QtWorkbenchToolPage):
    """独立论坛窗口，也可作为工作台工具页内嵌。"""

    _dispatch_requested = pyqtSignal(object)

    def __init__(self, embedded: bool = False) -> None:
        super().__init__(embedded=embedded)
        self.setObjectName("ForumWindow")
        self.setWindowTitle("雪绒论坛")
        if not self._embedded:
            # 优先级跟工作台窗口一致：普通窗口 + 无边框，不置顶、不进 LayerManager。
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setMinimumSize(MIN_WINDOW_WIDTH, scale_px(680, min_abs=600))
            self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._messages: list[ForumMessage] = []
        self._column_layouts: list[QVBoxLayout] = []
        self._column_heights: list[int] = []
        self._scroll: QScrollArea | None = None
        self._selected_accent = FORUM_DEFAULT_ACCENT
        self._disposed = False
        # 回调来自 IO 线程，显式排队回 UI 线程，避免渲染中途重入。
        self._dispatch_requested.connect(self._run_dispatched, Qt.QueuedConnection)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)

        self._build_ui()

        self._service = ForumService(
            dispatch=self._dispatch,
            on_page=self._on_page,
            on_error=self._on_error,
            on_posted=self._on_posted,
        )
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._sync_composer_state)
        self._cooldown_timer.start()
        self._sync_composer_state()

    # ── 构建 ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(forum_stylesheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_wall(), 1)
        root.addWidget(self._build_composer())

        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(scale_px(18, min_abs=15), scale_px(18, min_abs=15))
        self._size_grip.raise_()
        self._size_grip.setVisible(not self._embedded)

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setObjectName("ForumHeader")
        header.setFixedHeight(scale_px(66, min_abs=58))
        self.set_drag_handle(header)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(scale_px(1, min_abs=1))
        title = QLabel("雪绒论坛", header)
        title.setObjectName("ForumTitle")
        title_font = get_ui_font(size=scale_px(19, min_abs=16))
        title_font.setBold(True)
        title.setFont(title_font)
        self._subtitle = QLabel("雪绒留言墙 · 正在读取…", header)
        self._subtitle.setObjectName("ForumSubtitle")
        self._subtitle.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        title_box.addWidget(title)
        title_box.addWidget(self._subtitle)

        self._refresh_button = QPushButton("刷新", header)
        self._refresh_button.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._refresh_button.clicked.connect(self.refresh)

        close_button = create_window_button(
            header, QStyle.SP_TitleBarCloseButton, "关闭论坛", self.fade_out, danger=True
        )

        layout = QHBoxLayout(header)
        layout.setContentsMargins(
            scale_px(18, min_abs=14),
            scale_px(10, min_abs=8),
            scale_px(12, min_abs=9),
            scale_px(10, min_abs=8),
        )
        layout.setSpacing(scale_px(10, min_abs=8))
        layout.addLayout(title_box, 1)
        layout.addWidget(self._refresh_button, 0)
        layout.addWidget(close_button, 0)
        return header

    def _build_wall(self) -> QWidget:
        wall = QWidget(self)
        wall_layout = QVBoxLayout(wall)
        wall_layout.setContentsMargins(
            WALL_MARGIN,
            scale_px(12, min_abs=10),
            WALL_MARGIN,
            scale_px(7, min_abs=6),
        )
        wall_layout.setSpacing(scale_px(10, min_abs=8))

        self._scroll = QScrollArea(wall)
        self._scroll.setObjectName("ForumScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._scroll)
        host.setObjectName("ForumColumnHost")
        self._host = host
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(COLUMN_SPACING)
        self._host_layout = host_layout
        for index in range(COLUMN_COUNT):
            column = QWidget(host)
            column.setObjectName("ForumColumn")
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(COLUMN_SPACING)
            column_layout.addStretch(1)
            host_layout.addWidget(column, 1)
            self._column_layouts.append(column_layout)
            self._column_heights.append(0)
        self._scroll.setWidget(host)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        self._scroll.viewport().installEventFilter(self)
        wall_layout.addWidget(self._scroll, 1)

        self._status = QLabel("正在连接雪绒论坛…", wall)
        self._status.setObjectName("ForumStatus")
        self._status.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        wall_layout.addWidget(self._status, 0)
        return wall

    def _build_composer(self) -> QWidget:
        composer = QFrame(self)
        composer.setObjectName("ForumComposer")
        layout = QVBoxLayout(composer)
        layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(11, min_abs=9),
            scale_px(16, min_abs=13),
            scale_px(11, min_abs=9),
        )
        layout.setSpacing(scale_px(9, min_abs=7))

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(scale_px(10, min_abs=8))

        accent_row = QHBoxLayout()
        accent_row.setContentsMargins(0, 0, 0, 0)
        accent_row.setSpacing(scale_px(5, min_abs=4))
        accent_label = QLabel("描边", composer)
        accent_label.setObjectName("ForumHint")
        accent_label.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        accent_row.addWidget(accent_label)
        self._accent_buttons: dict[str, QToolButton] = {}
        for accent in FORUM_ACCENTS:
            button = QToolButton(composer)
            button.setObjectName("ForumAccentSwatch")
            button.setProperty("accent", accent)
            button.setCheckable(True)
            button.setChecked(accent == self._selected_accent)
            button.setToolTip(f"{FORUM_ACCENT_LABELS[accent]}色描边")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, target=accent: self._select_accent(target)
            )
            self._accent_buttons[accent] = button
            accent_row.addWidget(button)
        self._select_accent(self._selected_accent)
        top_row.addLayout(accent_row, 0)

        self._nickname = QLineEdit(composer)
        self._nickname.setObjectName("ForumNickname")
        self._nickname.setPlaceholderText(FORUM_NICKNAME_PLACEHOLDER)
        self._nickname.setMaxLength(NICKNAME_MAX_LENGTH)
        self._nickname.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
        self._nickname.setToolTip(
            f"最多 {NICKNAME_MAX_LENGTH} 字；留空就以「匿名」发送，服务端会给空昵称补上这一档。"
        )
        # 小输入框：宽度刚好框住那句提示（再加样式表的左右内边距），不挤压正文输入框。
        self._nickname.setFixedWidth(max(
            scale_px(184, min_abs=164),
            self._nickname.fontMetrics().horizontalAdvance(FORUM_NICKNAME_PLACEHOLDER)
            + scale_px(26, min_abs=22),
        ))
        top_row.addWidget(self._nickname, 0)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(scale_px(10, min_abs=8))

        self._input = QLineEdit(composer)
        self._input.setObjectName("ForumInput")
        self._input.setPlaceholderText(f"说点什么…（最多 {FORUM_MAX_CONTENT} 字）")
        self._input.setMaxLength(FORUM_MAX_CONTENT)
        self._input.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._input.textChanged.connect(self._sync_composer_state)
        self._input.returnPressed.connect(self._on_send)
        bottom_row.addWidget(self._input, 1)

        self._counter = QLabel(f"0/{FORUM_MAX_CONTENT}", composer)
        self._counter.setObjectName("ForumHint")
        self._counter.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        bottom_row.addWidget(self._counter, 0)

        self._send_button = QPushButton("发送", composer)
        self._send_button.setObjectName("ForumSend")
        self._send_button.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._send_button.clicked.connect(self._on_send)
        bottom_row.addWidget(self._send_button, 0)
        layout.addLayout(bottom_row)
        return composer

    # ── 交互 ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._set_subtitle("雪绒留言墙 · 正在读取…")
        self._set_status("正在刷新最新留言…")
        self._service.refresh()

    def _select_accent(self, accent: str) -> None:
        self._selected_accent = accent if accent in FORUM_ACCENTS else FORUM_DEFAULT_ACCENT
        for name, button in self._accent_buttons.items():
            selected = name == self._selected_accent
            button.setChecked(selected)
            # UI 字体没有 U+2713，用形近的数学根号作勾选标记。
            button.setText("\u221a" if selected else "")

    def _on_send(self) -> None:
        if not self._send_button.isEnabled():
            return
        error = self._service.post(
            self._input.text(),
            nickname=self._nickname.text(),
            accent=self._selected_accent,
        )
        if error:
            self._set_status(error)
            self._sync_composer_state()
            return
        self._input.clear()
        self._set_status("已发送，等待论坛确认…")
        self._sync_composer_state()

    def _sync_composer_state(self) -> None:
        remaining = int(self._service.cooldown_remaining() + 0.999)
        has_content = bool(self._input.text().strip())
        self._send_button.setEnabled(remaining <= 0 and has_content)
        self._send_button.setText(f"发送（{remaining}s）" if remaining > 0 else "发送")
        self._counter.setText(f"{len(self._input.text())}/{FORUM_MAX_CONTENT}")

    def _on_scrolled(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        if value >= bar.maximum() - LOAD_OLDER_THRESHOLD_PX:
            self._request_older()

    def _on_scroll_range_changed(self, _minimum: int, maximum: int) -> None:
        if maximum <= LOAD_OLDER_THRESHOLD_PX:
            self._request_older()

    def _request_older(self) -> None:
        if self._service.reached_end or self._service.loading_older:
            return
        if self._service.load_older():
            self._set_status("正在加载更早的留言…")

    def eventFilter(self, watched, event):
        # 视口变高（或内容不足一屏）时继续补齐更早的留言。
        if (
            self._scroll is not None
            and watched is self._scroll.viewport()
            and event.type() == QEvent.Resize
        ):
            self._request_older()
        return super().eventFilter(watched, event)

    # ── 服务回调 ─────────────────────────────────────────────────────

    def _dispatch(self, callback) -> None:
        self._dispatch_requested.emit(callback)

    def _run_dispatched(self, callback) -> None:
        if self._disposed:
            return
        callback()

    def _on_page(self, page: ForumPage) -> None:
        if page.mode == "latest":
            self._messages = list(page.messages)
            self._render_messages()
        else:
            self._messages.extend(page.messages)
            self._add_cards(page.messages)
        if not self._messages:
            self._set_status("还没有留言，写下第一条吧")
        elif self._service.reached_end:
            self._set_status(f"已加载 {len(self._messages)} 条 · 没有更早的留言了")
        else:
            self._set_status(f"已加载 {len(self._messages)} 条 · 共 {page.total} 条")
        self._set_subtitle(f"雪绒留言墙 · 共 {page.total} 条留言")
        if page.mode == "latest":
            self._scroll.verticalScrollBar().setValue(0)

    def _on_error(self, message: str) -> None:
        self._set_status(message)

    def _on_posted(self, message: ForumMessage) -> None:
        self._messages.insert(0, message)
        self._render_messages()
        self._scroll.verticalScrollBar().setValue(0)
        self._set_status(f"已发布 · 共 {len(self._messages)} 条")
        self._set_subtitle(f"雪绒留言墙 · 共 {len(self._messages)} 条留言")
        self._sync_composer_state()

    # ── 渲染 ─────────────────────────────────────────────────────────

    def _clear_cards(self) -> None:
        for layout in self._column_layouts:
            while layout.count() > 1:
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        self._column_heights = [0 for _ in self._column_layouts]

    def _render_messages(self) -> None:
        self._clear_cards()
        self._add_cards(self._messages)

    def _add_cards(self, messages) -> None:
        for message in messages:
            card = ForumCard(message, self._host)
            height = max(1, int(card.sizeHint().height()))
            index = min(
                range(len(self._column_heights)),
                key=lambda position: self._column_heights[position],
            )
            layout = self._column_layouts[index]
            layout.insertWidget(layout.count() - 1, card)
            self._column_heights[index] += height + COLUMN_SPACING

    # ── 状态与生命周期 ───────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)

    def refresh_workbench_theme(self) -> None:
        self.setStyleSheet(forum_stylesheet())

    def _on_config_updated(self, event) -> None:
        values = (event.data or {}).get("values") or {}
        ui_values = values.get("UI")
        if not isinstance(ui_values, dict) or "workbench_light_theme" not in ui_values:
            return
        self.refresh_workbench_theme()

    def refresh_workbench_page(self) -> None:
        self.refresh()

    def _before_standalone_show(self) -> None:
        self._cooldown_timer.start()

    def _after_standalone_hide(self) -> None:
        if not self._embedded:
            self._cooldown_timer.stop()

    def show_centered(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen is not None else self.geometry()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + (geometry.height() - self.height()) // 2
        self.move(max(geometry.left(), x), max(geometry.top(), y))

    def cleanup(self) -> None:
        self._disposed = True
        self._cooldown_timer.stop()
        self._service.cleanup()
        try:
            self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        grip = getattr(self, "_size_grip", None)
        if grip is not None and grip.isVisible():
            grip.move(self.width() - grip.width(), self.height() - grip.height())
            grip.raise_()


_instance: ForumWindow | None = None


def get_forum_window() -> ForumWindow | None:
    return _instance


def open_forum_window() -> ForumWindow:
    """打开（或复用）论坛窗口并刷新最新留言。"""
    global _instance
    window = _instance
    if window is None:
        window = ForumWindow()
        _instance = window
    window.show_centered()
    window.fade_in()
    window.refresh()
    return window


def cleanup_forum_window() -> None:
    global _instance
    window, _instance = _instance, None
    if window is None:
        return
    window.cleanup()
    window.deleteLater()


__all__ = [
    "ForumCard",
    "ForumWindow",
    "cleanup_forum_window",
    "get_forum_window",
    "open_forum_window",
]
