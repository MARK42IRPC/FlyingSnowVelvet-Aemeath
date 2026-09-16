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
from PyQt5.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
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
    FORUM_MAX_NICKNAME,
    ForumMessage,
    ForumPage,
    ForumService,
    format_relative_time,
)
from lib.core.forum_filter import (
    FORUM_REASON_BANNED_WORD,
    FORUM_REASON_LINK,
    FORUM_REASON_LONG_NUMBER,
    ForumViolation,
)
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.script.ui.forum_style import (
    FORUM_ACCENT_LABELS,
    FORUM_CARD_RADIUS,
    FORUM_SCROLLBAR_WIDTH,
    forum_card_text_size,
    forum_card_text_color,
    forum_stylesheet,
    forum_texture_color,
)
from lib.script.ui.forum_color_picker import (
    MAX_LIGHTNESS,
    MIN_LIGHTNESS,
    ForumColorSlider,
    color_to_hsl,
    hsl_color,
    hue_gradient_stops,
)
from lib.script.ui.forum_markup import (
    FORMAT_BY_KEY,
    FORUM_MARKUP_FORMATS,
    build_color_tokens,
    span_at_cursor,
    text_colors,
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
from lib.script.ui.workbench_components import create_window_button
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

COLUMN_COUNT = 3
WALL_MARGIN = scale_px(16, min_abs=13)
COLUMN_SPACING = scale_px(12, min_abs=10)
#: 卡片右侧与滚动条之间的空隙：卡片贴着条子会把滚动条读成「卡片右边框」，最后一列像被压住。
SCROLL_GAP = scale_px(10, min_abs=8)
#: 滚动条连同它前面的空隙占掉的横向空间；滚动条一出现，视口就少这么多宽度。
SCROLL_GUTTER = FORUM_SCROLLBAR_WIDTH + SCROLL_GAP
#: 窗口宽度取工作台的一半左右，三列必须仍能并排放下，所以卡片最小宽度随之下调。
CARD_MIN_WIDTH = scale_px(170, min_abs=150)
#: 最小宽度由三列网格 + 滚动条占位推出，避免窗口窄到把卡片挤出行外（滚动条一出现就少一个
#: `SCROLL_GUTTER`，不预先留出来的话三列会被挤到卡片最小宽度以下）。
MIN_WINDOW_WIDTH = (
    COLUMN_COUNT * CARD_MIN_WIDTH
    + (COLUMN_COUNT - 1) * COLUMN_SPACING
    + 2 * WALL_MARGIN
    + SCROLL_GUTTER
)
DEFAULT_WINDOW_WIDTH = max(MIN_WINDOW_WIDTH, scale_px(620, min_abs=580))
DEFAULT_WINDOW_HEIGHT = scale_px(800, min_abs=700)
LOAD_OLDER_THRESHOLD_PX = scale_px(140, min_abs=90)
#: 昵称上限：核心层 `FORUM_MAX_NICKNAME` 是唯一事实源，这里只做别名。
NICKNAME_MAX_LENGTH = FORUM_MAX_NICKNAME
FORUM_NICKNAME_PLACEHOLDER = "输入昵称…（未输入以匿名发送）"

logger = get_logger(__name__)

#: 页眉小字：留言来自社区，不是官方口径。
FORUM_HEADER_NOTICE = "内容来自社区，不一定来自官方，请仔细甄别"


def _format_button_font(key: str):
    """按钮字符自己就是效果示例：B 加粗、I 斜体、U 下划线、S 删除线。"""
    font = get_ui_font(size=scale_px(12, min_abs=10))
    font.setBold(key == "bold")
    font.setItalic(key == "italic")
    font.setUnderline(key == "underline")
    font.setStrikeOut(key == "strike")
    return font


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


class ForumColorToggle(QCheckBox):
    """论坛取色开关：勾选后在方框里补一个对勾。

    样式表一旦给 `::indicator` 画了背景，Qt 就不再画原生的勾选标记，方框会变成一个实心
    色块——看上去分不清是「已勾选」还是「只是个色块」。这里按 `QStyle` 给出的 indicator
    矩形自己描一个对勾，不依赖字体字形，12px 的小方框里也能画准。
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        if rect.width() <= 2 or rect.height() <= 2:
            return
        pen = QPen(QColor(0, 0, 0, 170))
        pen.setWidthF(max(1.4, rect.height() * 0.16))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        path = QPainterPath()
        path.moveTo(rect.x() + rect.width() * 0.26, rect.y() + rect.height() * 0.52)
        path.lineTo(rect.x() + rect.width() * 0.44, rect.y() + rect.height() * 0.70)
        path.lineTo(rect.x() + rect.width() * 0.76, rect.y() + rect.height() * 0.30)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class ForumColorControl(QWidget):
    """一组「色相 + 明度」滑条：勾选后才给正文或描边选颜色。

    每条颜色控件自带一个复选框：不勾选就收起两条滑条、直接用主题默认色，卡片上也就不写
    颜色令牌；勾选后才铺开滑条自选。默认的留言因此不会平白多出一串颜色令牌，想改色的
    人也有一个明确的入口。

    两个滑条都是 0.0–1.0 的比例，真正换算成颜色的是 `forum_color_picker.hsl_color()`；
    色相条铺整圈彩虹，明度条铺当前色相的暗→亮渐变，拖哪一条都能立刻从滑条本身看出结果。
    控件对外暴露 `color()`（当前色）、`token_color()`（写进正文的颜色）、`is_enabled()`
    与 `colorChanged`，调用方不用认识 HSL。
    """

    colorChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, label: str = "颜色") -> None:
        super().__init__(parent)
        self.setObjectName("ForumColorControl")
        self._hue = 0.0
        self._lightness = 0.85
        self._enabled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(3, min_abs=2))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(scale_px(5, min_abs=4))
        # 复选框自己带标题：文字也是可点区域，比一个光秃秃的小方框好按。
        self._toggle = ForumColorToggle(label, self)
        self._toggle.setObjectName("ForumColorToggle")
        self._toggle.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setToolTip(f"勾选后才能自定义{label}；不勾选就跟着主题的默认色走。")
        self._toggle.toggled.connect(self._on_toggled)
        self._preview = QFrame(self)
        self._preview.setObjectName("ForumColorPreview")
        self._preview.setFixedSize(scale_px(22, min_abs=19), scale_px(12, min_abs=10))
        header.addWidget(self._toggle, 0)
        header.addWidget(self._preview, 0)
        header.addStretch(1)
        layout.addLayout(header)

        self._hue_slider = ForumColorSlider(
            tooltip=f"{label}：拖动选择色相",
            gradient_stops=hue_gradient_stops(),
            parent=self,
        )
        # 高度留给 ForumColorSlider 自己按共享滑条（音乐进度条）定，这里只定宽度：
        # 两个颜色控件并排，各自给一条能看出渐变的宽度。
        self._hue_slider.setMinimumWidth(scale_px(170, min_abs=140))
        layout.addWidget(self._hue_slider)

        self._lightness_slider = ForumColorSlider(
            tooltip=f"{label}：拖动选择明度",
            parent=self,
        )
        self._lightness_slider.setMinimumWidth(scale_px(170, min_abs=140))
        layout.addWidget(self._lightness_slider)

        self._hue_slider.valueChanged.connect(self._on_hue_changed)
        self._lightness_slider.valueChanged.connect(self._on_lightness_changed)
        self._apply_enabled()
        self._sync_preview()

    # ── 对外 ─────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """复选框是否勾选；没勾选时滑条收起，用的就是主题默认色。"""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._toggle.setChecked(bool(enabled))

    def color(self) -> str:
        """当前颜色：没勾选时是主题默认色，勾选后是滑条上的颜色。"""
        return self._slider_color() if self._enabled else self._default_color()

    def token_color(self) -> str:
        """写进正文令牌的颜色；没勾选时返回空串，卡片于是用默认色。"""
        return self._slider_color() if self._enabled else ""

    def set_color(self, color: str | None) -> None:
        """按颜色反推两个滑条的位置并勾选这条颜色；传 None 表示回到默认色。"""
        if color:
            hue, lightness = color_to_hsl(color)
            self._hue = max(0.0, min(1.0, hue / 360.0))
            span = max(1e-6, MAX_LIGHTNESS - MIN_LIGHTNESS)
            self._lightness = max(
                0.0, min(1.0, (lightness - MIN_LIGHTNESS) / span)
            )
        else:
            self._hue, self._lightness = 0.0, 0.85
        # 先摆好滑条再切复选框：切换会发 colorChanged，那一刻颜色应该已经是新的。
        self._hue_slider.set_ratio(self._hue)
        self._lightness_slider.set_ratio(self._lightness)
        self._toggle.setChecked(bool(color))
        self._sync_preview()

    # ── 内部 ─────────────────────────────────────────────────────────

    def _on_toggled(self, checked: bool) -> None:
        self._enabled = bool(checked)
        self._apply_enabled()
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _apply_enabled(self) -> None:
        """没勾选就把两条滑条收起来，控件只剩标题与预览色块。"""
        for slider in (self._hue_slider, self._lightness_slider):
            slider.setVisible(self._enabled)

    @staticmethod
    def _default_color() -> str:
        return forum_card_text_color()

    def _slider_color(self) -> str:
        return hsl_color(self._hue * 360.0, self._lightness).name()

    def _on_hue_changed(self, ratio: float) -> None:
        self._hue = float(ratio)
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _on_lightness_changed(self, ratio: float) -> None:
        self._lightness = float(ratio)
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _sync_preview(self) -> None:
        color = self.color()
        self._preview.setStyleSheet(
            f"background: {color}; border: 1px solid rgba(0, 0, 0, 0.35);"
        )
        # 明度条按当前色相重铺渐变，拖色相时它跟着变。
        stops = tuple(
            (index / 6.0, hsl_color(self._hue * 360.0, MIN_LIGHTNESS + (MAX_LIGHTNESS - MIN_LIGHTNESS) * index / 6.0).name())
            for index in range(7)
        )
        self._lightness_slider.set_gradient_stops(stops)


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
        # 匿名留言没有标识，这一格就直接不占位（不给匿名也留一条空气缝）。
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
        #: 违规提示语音（懒创建；音频缺失时为 None 并静默跳过）。
        self._violation_sound = None
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
        # 三行页眉：标题、副标题、社区内容提示；比原来高一行小字。
        header.setFixedHeight(scale_px(82, min_abs=72))
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

        # 页眉小字：留言是社区内容，不是官方公告，标出来免得被当成官方口径。
        self._notice = QLabel(FORUM_HEADER_NOTICE, header)
        self._notice.setObjectName("ForumHeaderNotice")
        self._notice.setFont(get_ui_font(size=scale_px(9, min_abs=8)))
        title_box.addWidget(self._notice)

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

        # 平滑滚动：复用设置页那套把离散滚轮步进转成短动画的容器，
        # 留言墙的滚动手感与工作台一致，不再是一格一格地跳。
        self._scroll = SmoothScrollArea(wall)
        self._scroll.setObjectName("ForumScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._scroll)
        host.setObjectName("ForumColumnHost")
        self._host = host
        host_layout = QHBoxLayout(host)
        # 只在右侧留出滚动条的空隙：视口宽度不含滚动条，卡片会正好顶在条子上。
        host_layout.setContentsMargins(0, 0, SCROLL_GAP, 0)
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
        accent_label = QLabel("卡片描边", composer)
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

        # 正文颜色与描边颜色：各一组「色相 + 明度」滑条，外加一枚跟随主题的重置。
        # 原有的「卡片描边」档位仍在左边，管的是卡片边框；这两组管的是卡里的字。
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(scale_px(8, min_abs=6))
        self._text_color_picker = ForumColorControl(composer, "文字颜色")
        self._outline_color_picker = ForumColorControl(composer, "描边颜色")
        color_row.addWidget(self._text_color_picker, 0)
        color_row.addWidget(self._outline_color_picker, 0)
        color_row.addStretch(1)
        # 颜色控件单独占一行：挤进昵称那一行会把两个滑条压到看不出渐变，
        # 「拖哪个位置是什么颜色」当场就看不出来了。
        layout.addLayout(color_row)

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

        format_row = QHBoxLayout()
        format_row.setContentsMargins(0, 0, 0, 0)
        format_row.setSpacing(scale_px(5, min_abs=4))
        self._format_buttons: dict[str, QToolButton] = {}
        for fmt in FORUM_MARKUP_FORMATS:
            button = QToolButton(composer)
            button.setObjectName("ForumFormatButton")
            button.setText(fmt.button)
            button.setCheckable(True)
            button.setToolTip(
                f"{fmt.label}：选中文字后点一下，用 {fmt.marker} 把选区包起来；没有选中时"
                f"先点亮按钮再输入，打的字就自动是{fmt.label}。再点一下取消。"
            )
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_format_button_font(fmt.key))
            button.clicked.connect(
                lambda _checked=False, key=fmt.key: self._toggle_format(key)
            )
            self._format_buttons[fmt.key] = button
            format_row.addWidget(button)
        bottom_row.addLayout(format_row, 0)

        self._input = QLineEdit(composer)
        self._input.setObjectName("ForumInput")
        self._input.setPlaceholderText(f"说点什么…（最多 {FORUM_MAX_CONTENT} 字）")
        self._input.setMaxLength(FORUM_MAX_CONTENT)
        self._input.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._input.setToolTip(
            "支持 **粗体**、*斜体*、__下划线__、~~删除线~~；左边四个小按钮会在光标处自动加标记。"
        )
        self._input.textChanged.connect(self._sync_composer_state)
        # 光标/选区一变就重算按钮的复选状态，按钮始终表示「光标处是不是这种格式」。
        self._input.cursorPositionChanged.connect(self._sync_format_buttons)
        self._input.selectionChanged.connect(self._sync_format_buttons)
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

        # 输入框里实时预览选中的字色与描边色：选色控件改一下，输入框立刻变。
        self._text_color_picker.colorChanged.connect(lambda _c: self._sync_input_colors())
        self._outline_color_picker.colorChanged.connect(lambda _c: self._sync_input_colors())
        self._sync_input_colors()
        return composer

    def _sync_input_colors(self) -> None:
        """把选中的文字色 / 描边色刷到输入框上，选色结果当场可见。

        没勾选的颜色不写规则：清空局部样式表之后，输入框回落到样式表里的默认色，
        「没启用就用默认色」在输入框这儿也要成立。
        """
        if not hasattr(self, "_input"):
            return
        rules: list[str] = []
        text_color = self._text_color_picker.token_color()
        outline_color = self._outline_color_picker.token_color()
        if text_color:
            rules.append(f"QLineEdit#ForumInput {{ color: {text_color}; }}")
        if outline_color:
            rules.append(f"QLineEdit#ForumInput:focus {{ border-color: {outline_color}; }}")
        self._input.setStyleSheet("".join(rules))

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
            self._set_status(violation_message(error), tone="warn")
            self._play_violation_sound()
            self._sync_composer_state()
            return
        if error:
            self._set_status(error)
            self._sync_composer_state()
            return
        self._input.clear()
        self._set_status("已发送，等待论坛确认…")
        self._sync_composer_state()

    def _play_violation_sound(self) -> None:
        """播一条随机的违规提示语音；音频缺失时静默跳过，不影响拦截本身。"""
        try:
            from lib.script.voice.forum_violation import ForumViolationSound

            if self._violation_sound is None:
                self._violation_sound = ForumViolationSound()
            self._violation_sound.play()
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
