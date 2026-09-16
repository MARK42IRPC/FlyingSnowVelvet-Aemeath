"""帮助窗口：事件驱动的单例浮窗，按「标题 + 文本」展示一段说明。

工作台各配置分类的小标题右侧有一枚灰色问号，点它就发一条 `HELP_WINDOW_REQUEST`
（`lib/core/event/center.py`）事件；本模块订阅这个事件并开窗。事件驱动而不是直接
调用，是因为触发方（设置页）与展示方（浮窗）分属不同模块，只有事件这一条公开契约。

同时只允许存在一个帮助窗口：再次触发会先销毁已有窗口。窗口本体是无边框
`Qt.Tool` 浮窗，外壳沿用公告窗口那一套（无边框 + 可拖标题区 + 淡入淡出 +
`Layer.DIALOG`），但正文用普通 `QLabel` 而不是富文本浏览器——帮助内容是纯文本，
不需要链接，段落式排版在窄窗口里更好读。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import EventType, get_event_center
from lib.core.graphics.announcement_visuals import get_announcement_colors
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.screen import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.ui.workbench_floating import WorkbenchFloatingWindow
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

logger = get_logger(__name__)

#: 帮助窗口比公告窄一些：它只有一段说明，没有分页和底部按钮行。
HELP_WINDOW_WIDTH = scale_px(440, min_abs=380)
HELP_WINDOW_HEIGHT = scale_px(380, min_abs=320)
#: 正文为空时的兜底文案，免得窗口开出来一片空白。
HELP_EMPTY_TEXT = "这一项暂时还没有补充说明。"
HELP_DEFAULT_TITLE = "帮助"

_BORDER = scale_px(1, min_abs=1)
_HEADER_HEIGHT = scale_px(56, min_abs=48)
_ACCENT_WIDTH = scale_px(3, min_abs=2)


def _color_name(key: str) -> str:
    color = get_announcement_colors()[key]
    return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"


def parse_help_payload(data) -> tuple[str, str]:
    """从事件负载里取出 `(标题, 文本)`，两者都做去空白与兜底。

    负载可能是 `Event` 的 `data` 字典，也可能是别的后端直接给的普通映射，
    所以这里不假定类型，只按映射取值：取不到标题就用默认标题，取不到正文就留空，
    由窗口层补一句兜底说明。
    """
    payload = data if isinstance(data, dict) else {}
    title = str(payload.get("title") or "").strip() or HELP_DEFAULT_TITLE
    text = str(payload.get("text") or "").strip()
    return title, text


class DesktopPetHelpDialog(WorkbenchFloatingWindow):
    """紧凑的帮助浮窗：标题 + 可滚动正文 + 关闭。"""

    # 主题事件由控制器订阅，窗口自己不去重复订阅。
    follows_workbench_theme = False

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DesktopPetHelpDialog")
        self.setWindowTitle("帮助")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(HELP_WINDOW_WIDTH, HELP_WINDOW_HEIGHT)
        get_layer_manager().register(self, Layer.DIALOG, name="DesktopPetHelpDialog")

        self._requested_visible = False
        self._closing_animation = False

        self._header_accent = QFrame(self)
        self._header_accent.setObjectName("HelpHeaderAccent")
        self._header_accent.setFixedSize(_ACCENT_WIDTH, scale_px(32, min_abs=26))

        self._header_label = QLabel(HELP_DEFAULT_TITLE, self)
        self._header_label.setObjectName("HelpHeader")
        header_font = get_ui_font(size=scale_px(16, min_abs=13))
        header_font.setBold(True)
        self._header_label.setFont(header_font)

        self._source_label = QLabel("HELP  /  FSV", self)
        self._source_label.setObjectName("HelpSource")
        self._source_label.setFont(get_ui_font(size=scale_px(9, min_abs=8)))

        self._close_button = QToolButton(self)
        self._close_button.setObjectName("HelpCloseButton")
        self._close_button.setText("×")
        self._close_button.setToolTip("关闭帮助")
        self._close_button.setAccessibleName("关闭帮助")
        self._close_button.setFixedSize(scale_px(28, min_abs=24), scale_px(28, min_abs=24))
        self._close_button.clicked.connect(self.hide_dialog)

        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(0)
        header_text.addWidget(self._header_label)
        header_text.addWidget(self._source_label)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(scale_px(10, min_abs=8))
        header_row.addWidget(self._header_accent, 0, Qt.AlignVCenter)
        header_row.addLayout(header_text, 1)
        header_row.addWidget(self._close_button, 0, Qt.AlignTop)

        self._header = QWidget(self)
        self._header.setFixedHeight(_HEADER_HEIGHT)
        self._header.setLayout(header_row)
        self.attach_floating_drag_handle(self._header, self._header_label, self._source_label)

        self._body = QLabel(self)
        self._body.setObjectName("HelpBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.PlainText)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._body.setFont(get_ui_font(size=scale_px(13, min_abs=11)))

        # 帮助正文可能比窗口高，复用设置页那套平滑滚动，滚动手感与工作台一致。
        self._scroll = SmoothScrollArea(self)
        self._scroll.setObjectName("HelpScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(self._scroll)
        holder.setObjectName("HelpScrollHost")
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        holder_layout.setSpacing(0)
        holder_layout.addWidget(self._body, 0)
        holder_layout.addStretch(1)
        self._scroll.setWidget(holder)

        content = QVBoxLayout(self)
        content.setContentsMargins(
            _BORDER + scale_px(18, min_abs=14),
            _BORDER + scale_px(15, min_abs=12),
            _BORDER + scale_px(18, min_abs=14),
            _BORDER + scale_px(15, min_abs=12),
        )
        content.setSpacing(scale_px(12, min_abs=9))
        content.addWidget(self._header)
        content.addWidget(self._scroll, 1)

        self.install_floating_chrome()

        self._opacity_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_animation.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._opacity_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_animation.finished.connect(self._on_animation_finished)

    # ── 内容 ─────────────────────────────────────────────────────────

    def show_help(self, title: str, text: str) -> None:
        """换上新内容并淡入；同一窗口被复用时会整段替换，不留上一条的残影。"""
        self.refresh_workbench_theme()
        self._header_label.setText(str(title or "").strip() or HELP_DEFAULT_TITLE)
        body = str(text or "").strip()
        self._body.setText(body if body else HELP_EMPTY_TEXT)
        self._scroll.verticalScrollBar().setValue(0)
        self._show_dialog()

    def wants_visible(self) -> bool:
        return self._requested_visible

    # ── 主题 ─────────────────────────────────────────────────────────

    def refresh_workbench_theme(self) -> None:
        self.refresh_floating_theme()

    def floating_stylesheet(self) -> str:
        return self._widget_stylesheet()

    # ── 显示与隐藏 ───────────────────────────────────────────────────

    def hide_dialog(self) -> None:
        if not self._requested_visible:
            return
        self._requested_visible = False
        self._closing_animation = True
        self._animate_to(0.0)

    def cleanup(self) -> None:
        self._opacity_animation.stop()
        self._requested_visible = False
        self._closing_animation = False
        self.cleanup_floating_chrome()
        self.hide()
        get_layer_manager().unregister(self)
        self.deleteLater()

    def _show_dialog(self) -> None:
        self._center_on_screen()
        was_visible = self._requested_visible
        self._requested_visible = True
        self._closing_animation = False
        if not was_visible:
            self.setWindowOpacity(0.0)
            self.show()
        get_layer_manager().bring_to_front(self)
        self.raise_()
        self.activateWindow()
        self._animate_to(apply_ui_opacity(1.0))

    def _animate_to(self, target: float) -> None:
        self._opacity_animation.stop()
        self._opacity_animation.setStartValue(float(self.windowOpacity()))
        self._opacity_animation.setEndValue(float(target))
        self._opacity_animation.start()

    def _on_animation_finished(self) -> None:
        if self._closing_animation and not self._requested_visible:
            self._closing_animation = False
            self.hide()

    def _center_on_screen(self) -> None:
        cursor_pos = QCursor.pos()
        screen = get_screen_geometry_for_point(point=cursor_pos, fallback_widget=self)
        target_x = screen.x() + (screen.width() - self.width()) // 2
        target_y = screen.y() + (screen.height() - self.height()) // 2
        x, y, _ = clamp_rect_position(
            target_x,
            target_y,
            self.width(),
            self.height(),
            point=cursor_pos,
            fallback_widget=self,
        )
        self.move(x, y)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(_color_name("border_strong")))
        painter.fillRect(
            self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER),
            QColor(_color_name("canvas")),
        )
        painter.end()

    @staticmethod
    def _widget_stylesheet() -> str:
        canvas = _color_name("canvas")
        surface = _color_name("surface")
        surface_hover = _color_name("surface_hover")
        border = _color_name("border")
        border_strong = _color_name("border_strong")
        text = _color_name("text")
        text_muted = _color_name("text_muted")
        text_dim = _color_name("text_dim")
        cyan = _color_name("cyan")
        pink = _color_name("pink")
        return f"""
            QWidget#DesktopPetHelpDialog {{
                color: {text};
            }}
            QFrame#HelpHeaderAccent {{
                background: {cyan};
                border: none;
                border-right: {scale_px(1, min_abs=1)}px solid {pink};
            }}
            QLabel#HelpHeader {{
                color: {text};
                font-weight: 700;
            }}
            QLabel#HelpSource {{
                color: {text_dim};
                font-weight: 500;
            }}
            QToolButton#HelpCloseButton {{
                background: transparent;
                color: {text_muted};
                border: {scale_px(1, min_abs=1)}px solid transparent;
                border-radius: {scale_px(4, min_abs=3)}px;
                font-size: {scale_px(16, min_abs=14)}px;
                font-weight: 500;
                padding: 0px;
            }}
            QToolButton#HelpCloseButton:hover {{
                background: {surface_hover};
                color: {text};
                border-color: {border};
            }}
            QScrollArea#HelpScroll, QWidget#HelpScrollHost {{
                background: {canvas};
                border: none;
            }}
            QLabel#HelpBody {{
                background: transparent;
                color: {text_muted};
                border: none;
                padding: 0px;
            }}
            QScrollBar:vertical {{
                background: {surface};
                width: {scale_px(8, min_abs=7)}px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {border_strong};
                min-height: {scale_px(28, min_abs=22)}px;
                border: none;
                border-radius: {scale_px(3, min_abs=2)}px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {cyan}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """


class HelpWindowController(QObject):
    """订阅 `HELP_WINDOW_REQUEST`，保证全局只有一个帮助窗口。

    「只有一个」的做法是：每次请求都先 `_discard_dialog()` 丢掉手上那一只
    （`cleanup()` 会注销 `LayerManager` 并 `deleteLater()`），再按需新建。
    这样即便上一次的浮窗正处于淡出动画里，也不会留下一个半透明残影。
    """

    _dispatch_requested = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dialog: DesktopPetHelpDialog | None = None
        self._closed = False
        self._last_payload: tuple[str, str] | None = None
        self._dispatch_requested.connect(self._run_dispatched, Qt.QueuedConnection)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        self._event_center.subscribe(EventType.HELP_WINDOW_REQUEST, self._on_help_requested)

    # ── 事件 ─────────────────────────────────────────────────────────

    def _on_help_requested(self, event) -> None:
        title, text = parse_help_payload(getattr(event, "data", None))
        self.show_help(title, text)

    def _on_config_updated(self, event) -> None:
        if self._dialog is not None:
            self._dialog.refresh_workbench_theme()

    def _dispatch(self, callback) -> None:
        self._dispatch_requested.emit(callback)

    def _run_dispatched(self, callback) -> None:
        callback()

    # ── 对外 ─────────────────────────────────────────────────────────

    def show_help(self, title: str, text: str) -> DesktopPetHelpDialog:
        """销毁已有窗口并新建一个，返回新建的那一只。"""
        if self._closed:
            return self._discard_dialog() or self._ensure_dialog(title, text)
        self._discard_dialog()
        return self._ensure_dialog(title, text)

    def get_dialog(self) -> DesktopPetHelpDialog | None:
        return self._dialog

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._event_center.unsubscribe(EventType.HELP_WINDOW_REQUEST, self._on_help_requested)
        except Exception:
            pass
        try:
            self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except Exception:
            pass
        self._discard_dialog()

    # ── 内部 ─────────────────────────────────────────────────────────

    def _ensure_dialog(self, title: str, text: str) -> DesktopPetHelpDialog:
        dialog = DesktopPetHelpDialog()
        self._dialog = dialog
        self._last_payload = (title, text)
        dialog.show_help(title, text)
        return dialog

    def _discard_dialog(self) -> DesktopPetHelpDialog | None:
        dialog, self._dialog = self._dialog, None
        if dialog is None:
            return None
        try:
            dialog.cleanup()
        except Exception as exc:
            logger.debug("帮助窗口清理失败: %s", exc)
        return dialog


__all__ = [
    "DesktopPetHelpDialog",
    "HELP_DEFAULT_TITLE",
    "HELP_EMPTY_TEXT",
    "HELP_WINDOW_HEIGHT",
    "HELP_WINDOW_WIDTH",
    "HelpWindowController",
    "parse_help_payload",
]
