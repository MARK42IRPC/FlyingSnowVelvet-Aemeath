"""雪绒论坛窗口：工作台式外壳 + 三列自适应卡片墙 + 底部发帖框。

数据来自 `lib/core/forum.py`（`GET /api/feed` 分页读取、`POST /api/messages` 发帖）。
窗口只负责呈现与交互：卡片宽度固定为列宽、高度随内容变化，卡片配色只改描边；
往下滚动到底部再请求更早的一页，发帖按钮受客户端 12 秒冷却约束。

字号与控件间距对齐工作台（页头 19/11、卡片 14/17/11、输入与按钮 14）；卡片正文再按字数
在 1~2 倍之间自适应，短句放大、长文回到基准字号。正文支持 `**粗体**`、`*斜体*`、
`__下划线__`、`~~删除线~~` 四种行内标记（解析与输入辅助都在 `forum_markup`），发帖框左侧
四个复选小按钮会在光标处自动加上标记，接着打的字就落在标记里。
卡片底纹由 `forum_texture` 按卡片信息内容哈希生成，这里只负责把它铺到卡片上；正文里写了
`[雪豹]` 这类效果令牌时，令牌被 `forum_markup` 洗掉，卡片底部另贴一张会自己走的 gif
（`forum_sticker`，帧由全局 `GIF_FRAME` 事件推进）。
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
    QScrollArea,
    QSizePolicy,
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
from lib.core.forum_api import ForumApiClient
from lib.core.forum_filter import (
    FORUM_REASON_BANNED_WORD,
    FORUM_REASON_LINK,
    FORUM_REASON_LONG_NUMBER,
    ForumViolation,
)
from lib.core.forum_colors import build_color_tokens, text_colors
from lib.core.forum_session import ForumSessionStore
from lib.core.logger import get_logger
from lib.script.ui.forum_style import (
    FORUM_CARD_RADIUS,
    forum_card_text_size,
    forum_card_text_color,
    forum_stylesheet,
    forum_texture_color,
)
from lib.script.ui.forum_wall import ForumWallMixin
#: 输入框颜色控件与字体助手随发帖框一起到 `forum_wall`，这里按原名重新导出，既有导入面不变。
from lib.script.ui.forum_color_control import (  # noqa: F401 - 既有导入面
    ForumColorControl,
    format_button_font,
)
from lib.script.ui.forum_markup import (
    FORMAT_BY_KEY,
    span_at_cursor,
    to_html,
    toggle,
)
from lib.script.ui.forum_texture import (
    CardTexture,
    card_texture,
    paint_card_texture,
)
from lib.script.ui.forum_sticker import ForumSticker, sticker_paths
from lib.script.ui.forum_text import MarkupText
from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.core.render.backends.qt.widgets.workbench_page import QtWorkbenchToolPage

#: 布局常量与页签元数据已抽到叶子模块 `forum_wall_layout`（批次 3 后续轮次，避免
#: `forum_wall` 与 `forum_window` 循环 import）；这里按原名重新导出，既有导入面不变。
from lib.script.ui.forum_wall_layout import (  # noqa: E402,F401 - 既有导入面
    CARD_MIN_WIDTH,
    COLUMN_COUNT,
    COLUMN_SPACING,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    FORUM_DEFAULT_PAGE,
    FORUM_HEADER_NOTICE,
    FORUM_NICKNAME_PLACEHOLDER,
    FORUM_PAGES,
    LOAD_OLDER_THRESHOLD_PX,
    MIN_WINDOW_WIDTH,
    NICKNAME_MAX_LENGTH,
    SCROLL_GAP,
    SCROLL_GUTTER,
    WALL_MARGIN,
)

logger = get_logger(__name__)


#: 违规原因码 → 给用户看的一句话。原因码来自 `lib/core/forum_filter`。
_VIOLATION_MESSAGES = {
    FORUM_REASON_LINK: "留言里不能带网址或链接，去掉之后再发吧",
    FORUM_REASON_LONG_NUMBER: "留言里不能带六位以上的数字（电话、账号一类）",
    FORUM_REASON_BANNED_WORD: "留言里有违规词，改掉之后再发吧",
}


def violation_message(violation: ForumViolation) -> str:
    """把违规判定翻译成状态栏文案；未知原因码给一句兜底说明。"""
    base = _VIOLATION_MESSAGES.get(
        violation.reason, "这条留言包含不允许的内容，改掉之后再发吧"
    )
    detail = str(violation.detail or "").strip()
    return f"{base}（{detail}）" if detail else base


class ForumCard(QFrame):
    """一条留言卡片：左上昵称、中间大字正文、右下日期；accent 决定描边颜色与底纹色调。"""

    def __init__(self, message: ForumMessage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = message
        self.texture: CardTexture = card_texture(message)
        #: 正文效果令牌对应的贴图控件，贴在卡片底部（`forum_sticker`）。
        self._stickers: list[ForumSticker] = []
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

        # 昵称行：昵称 + 右侧淡色小字的设备标识。标识是「同一台机器」的留言印记，
        # 匿名留言不摆这一格（`forum.device_tag_for_display()` 已经把匿名档洗成空串，
        # 卡片因此不会给匿名留一条空气缝）。
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(scale_px(6, min_abs=5))

        name = QLabel(message.nickname, self)
        name.setObjectName("ForumCardName")
        name.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        # 昵称按内容换行，长昵称不会把这张卡片撑得比同列其它卡片宽。
        name.setWordWrap(True)
        name_row.addWidget(name, 0, Qt.AlignLeft | Qt.AlignVCenter)

        device_tag = str(getattr(message, "device_tag", "") or "").strip()
        if device_tag:
            tag = QLabel(device_tag, self)
            tag.setObjectName("ForumCardDeviceTag")
            tag.setFont(get_ui_font(size=scale_px(9, min_abs=8)))
            tag.setToolTip("这条留言来自同一台设备（别人无法冒充）")
            name_row.addWidget(tag, 0, Qt.AlignLeft | Qt.AlignVCenter)
        name_row.addStretch(1)
        layout.addLayout(name_row)

        # 正文越短字号越大（1~2 倍，标记不计入字数），卡片不会因为一句话就空掉。
        # 富文本：成对标记渲染成粗体/斜体/下划线/删除线，原文先转义（用户写的 `<b>` 只当
        # 普通字符显示）；粗体由 `MarkupText` 加同色描边落实，细节见 `forum_text`。
        # 留言自带的颜色令牌（正文开头）优先；没写就跟随主题。
        message_color, message_outline = text_colors(message.content)
        self._content = MarkupText(
            to_html(message.content),
            font=get_ui_font(size=forum_card_text_size(message.content)),
            color=message_color or forum_card_text_color(),
            outline_color=message_outline or message_color or forum_card_text_color(),
            # 只有留言自己带了 `[outline=...]` 才整篇描边：没选描边色的卡片仍按老样子，
            # 描边只负责把加粗片段撑粗。
            outline_all=bool(message_outline),
            width_hint=CARD_MIN_WIDTH,
            parent=self,
        )
        layout.addWidget(self._content, 0)

        stamp = QLabel(format_relative_time(message.created_at), self)
        stamp.setObjectName("ForumCardMeta")
        stamp.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        layout.addWidget(stamp, 0, Qt.AlignRight)

        # 效果令牌（`[雪豹]`）在正文里已经洗掉，贴图补在卡片最底部、居中，作为这条留言的落款。
        for path in sticker_paths(message.content):
            sticker = ForumSticker(path, self)
            self._stickers.append(sticker)
            layout.addWidget(sticker, 0, Qt.AlignHCenter)

    def refresh_theme(self) -> None:
        """换主题时重刷正文颜色：富文本颜色写在字符格式里，刷新样式表碰不到。

        留言自带颜色令牌时保留它自己的颜色，只对跟随主题的卡片生效。
        """
        message_color, message_outline = text_colors(self.message.content)
        theme_color = forum_card_text_color()
        self._content.set_colors(
            message_color or theme_color,
            message_outline or message_color or theme_color,
            outline_all=bool(message_outline),
        )

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


class ForumWindow(ForumWallMixin, QtWorkbenchToolPage):
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
        #: 违规提示语音（懒创建；音频缺失时为 None 并静默跳过）。
        self._violation_sound = None
        # 回调来自 IO 线程，显式排队回 UI 线程，避免渲染中途重入。
        self._dispatch_requested.connect(self._run_dispatched, Qt.QueuedConnection)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        # 登录态与主站客户端：主论坛与账号页共用同一份，登录一次两边都认。
        self._session_store = ForumSessionStore.load()
        self._api = ForumApiClient()
        self._wall_subtitle = "雪绒留言墙 · 正在读取…"
        self._page = FORUM_DEFAULT_PAGE
        self._wall_loaded = False

        self._build_ui()
        self._session_store.subscribe(self._on_session_changed)
        self._on_session_changed(self._session_store.get())

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


    # ── 交互 ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """刷新当前子页面；页眉的刷新按钮走这里。"""
        if self._page == "board":
            self._board.refresh()
            return
        if self._page == "account":
            self._account.refresh()
            return
        self._wall_loaded = True
        self._wall_subtitle = "雪绒留言墙 · 正在读取…"
        self._set_subtitle(self._wall_subtitle)
        self._set_status("正在刷新最新留言…")
        self._service.refresh()

    def _select_accent(self, accent: str) -> None:
        self._selected_accent = accent if accent in FORUM_ACCENTS else FORUM_DEFAULT_ACCENT
        for name, button in self._accent_buttons.items():
            selected = name == self._selected_accent
            button.setChecked(selected)
            # UI 字体没有 U+2713，用形近的数学根号作勾选标记。
            button.setText("\u221a" if selected else "")

    def _toggle_format(self, key: str) -> None:
        """复选按钮：在光标/选区处加减一对标记，接着打的字自动落在标记里。"""
        fmt = FORMAT_BY_KEY.get(str(key or ""))
        if fmt is None:
            return
        caret = self._caret_position()
        selected = len(self._input.selectedText())
        text, start, end = toggle(self._input.text(), caret, caret + selected, fmt.marker)
        if len(text) > FORUM_MAX_CONTENT:
            self._set_status(f"加上标记会超过 {FORUM_MAX_CONTENT} 字上限，先删掉一些再试")
            self._sync_format_buttons()
            return
        self._input.setText(text)
        self._input.setSelection(start, end - start)
        self._input.setFocus()
        self._sync_format_buttons()

    def _sync_format_buttons(self) -> None:
        """按钮的复选状态跟着光标：亮着就表示光标处的字已经是这种格式。"""
        text = self._input.text()
        caret = self._caret_position()
        for key, button in self._format_buttons.items():
            checked = span_at_cursor(text, caret, FORMAT_BY_KEY[key].marker) is not None
            if button.isChecked() != checked:
                button.setChecked(checked)

    def _caret_position(self) -> int:
        """选区起点或光标位置。

        `QLineEdit.cursorPosition()` 在选中文字时指的是选区**末尾**，直接拿它算选区会把
        标记加错地方（实测会加成「正文****」）；`selectionStart()` 没有选区时返回 -1。
        """
        start = self._input.selectionStart()
        return start if start >= 0 else self._input.cursorPosition()

    def _on_send(self) -> None:
        if not self._send_button.isEnabled():
            return
        # 颜色令牌拼在正文最前面（渲染时整段洗掉），随留言一起发给服务端。
        tokens = build_color_tokens(
            self._text_color_picker.token_color(),
            self._outline_color_picker.token_color(),
        )
        content = f"{tokens}{self._input.text()}" if tokens else self._input.text()
        error = self._service.post(
            content,
            nickname=self._nickname.text(),
            accent=self._selected_accent,
        )
        if isinstance(error, ForumViolation):
            # 违规：提示 + 播一条爱弥斯口吻的语音，冷却照常开始（见 ForumService.post）。
            # 语音按违规类别挑（链接 / 数字 / 骂人 / 广告…），免得话术和问题对不上。
            self._set_status(violation_message(error), tone="warn")
            self._play_violation_sound(error.category)
            self._sync_composer_state()
            return
        if error:
            self._set_status(error)
            self._sync_composer_state()
            return
        self._input.clear()
        self._set_status("已发送，等待论坛确认…")
        self._sync_composer_state()

    def _play_violation_sound(self, category: str = "") -> None:
        """播一条该违规类别的提示语音；音频缺失时静默跳过，不影响拦截本身。"""
        try:
            from lib.script.voice.forum_violation import ForumViolationSound

            if self._violation_sound is None:
                self._violation_sound = ForumViolationSound()
            self._violation_sound.play(category)
        except Exception as exc:
            logger.debug("[ForumWindow] 违规提示语音播放失败: %s", exc)

    def _sync_composer_state(self) -> None:
        remaining = int(self._service.cooldown_remaining() + 0.999)
        has_content = bool(self._input.text().strip())
        self._send_button.setEnabled(remaining <= 0 and has_content)
        self._send_button.setText(f"发送（{remaining}s）" if remaining > 0 else "发送")
        self._counter.setText(f"{len(self._input.text())}/{FORUM_MAX_CONTENT}")
        self._sync_format_buttons()

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

    def _set_status(self, text: str, *, tone: str = "") -> None:
        """状态栏文案；``tone`` 为 ``warn`` 时用告警色，违规提示才不会看着像普通提示。"""
        self._status.setText(text)
        if self._status.property("tone") != tone:
            self._status.setProperty("tone", tone)
            style = self._status.style()
            if style is not None:
                style.unpolish(self._status)
                style.polish(self._status)

    def _set_subtitle(self, text: str) -> None:
        if self._page == "wall":
            # 留言墙的副标题由服务回调随时改，切页回来要能还原。
            self._wall_subtitle = text
        self._subtitle.setText(text)

    def refresh_workbench_theme(self) -> None:
        self.setStyleSheet(forum_stylesheet())
        # 正文颜色在富文本字符格式里，样式表刷不到，逐张卡片重刷。
        for card in self.findChildren(ForumCard):
            card.refresh_theme()

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
        for page in (self._board, self._account):
            page.cleanup()
        try:
            self._session_store.unsubscribe(self._on_session_changed)
        except Exception:
            pass
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
    "FORUM_DEFAULT_PAGE",
    "FORUM_PAGES",
    "ForumCard",
    "ForumWindow",
    "cleanup_forum_window",
    "get_forum_window",
    "open_forum_window",
]
