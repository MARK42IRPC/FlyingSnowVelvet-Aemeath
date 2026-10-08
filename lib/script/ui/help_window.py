"""帮助窗口：事件驱动的单例浮窗，按「标题 + 文本」展示一段说明。

工作台各配置分类的小标题右侧有一枚灰色问号，点它就发一条 `HELP_WINDOW_REQUEST`
（`lib/core/event/center.py`）事件；本模块订阅这个事件并开窗。事件驱动而不是直接
调用，是因为触发方（设置页）与展示方（浮窗）分属不同模块，只有事件这一条公开契约。

窗口本体不再是 `QWidget`：控件树、窗口标志、滚动视口、淡入淡出与层级注册都由
``lib/core/render/visuals/window_specs.py`` 的 ``help_window_spec()`` 描述，
``render_bridge.create_spec_window()`` 交给 Qt 宿主
（``lib/core/render/backends/qt/widgets/spec_host.py``）装配。本模块只做三件事：
订阅事件、收集「标题 + 正文」、把语义回调翻译成产品动作。

同时只允许存在一个帮助窗口：再次触发会先销毁已有窗口。
"""

from __future__ import annotations

from config.scale import scale_px
from lib.core.event.center import EventType, get_event_center
from lib.core.logger import get_logger
from lib.core.render.visuals.announcement_visuals import get_announcement_colors
from lib.core.render.visuals.window_specs import HELP_CLOSE, help_window_spec
from lib.script.ui import render_bridge

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
_HEADER_SIZE = scale_px(16, min_abs=13)
_SOURCE_SIZE = scale_px(9, min_abs=8)
_BODY_SIZE = scale_px(13, min_abs=11)
_CLOSE_SIZE_FONT = scale_px(16, min_abs=14)
_CLOSE_SIZE = scale_px(28, min_abs=24)
_ACCENT_HEIGHT = scale_px(32, min_abs=26)
_HEADER_SPACING = scale_px(10, min_abs=8)
_ROOT_SPACING = scale_px(12, min_abs=9)
_BODY_MARGIN = (
    scale_px(14, min_abs=11),
    scale_px(12, min_abs=10),
    scale_px(14, min_abs=11),
    scale_px(12, min_abs=10),
)
_ROOT_MARGIN = (
    _BORDER + scale_px(18, min_abs=14),
    _BORDER + scale_px(15, min_abs=12),
    _BORDER + scale_px(18, min_abs=14),
    _BORDER + scale_px(15, min_abs=12),
)


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


class DesktopPetHelpDialog:
    """紧凑的帮助浮窗：标题 + 可滚动正文 + 关闭。

    属性面沿用原 `QWidget` 用法（``_header_label`` / ``_body`` / ``_scroll`` /
    ``show_help()`` / ``hide_dialog()`` / ``cleanup()`` / ``wants_visible()``），
    调用方不需要知道自己拿到的不再是 `QWidget`。
    """

    #: 主题事件由控制器订阅，窗口自己不去重复订阅（与原浮窗语义一致）。
    follows_workbench_theme = False

    def __init__(self, parent=None) -> None:
        self._title = HELP_DEFAULT_TITLE
        self._text = ""
        self._spec = self._build_spec()
        self._window = render_bridge.create_spec_window(
            self._spec,
            on_semantic=self._on_semantic,
            dialog=False,
            parent=parent,
        )
        self._header_label = self._window.find("header")
        self._source_label = self._window.find("source")
        self._body = self._window.find("body")
        self._scroll = self._window.find("body_area")
        self._close_button = self._window.find("close")

    # ── 描述 ─────────────────────────────────────────────────────────

    def _build_spec(self):
        return help_window_spec(
            title=self._title,
            text=self._text,
            width=HELP_WINDOW_WIDTH,
            height=HELP_WINDOW_HEIGHT,
            border_width=_BORDER,
            header_height=_HEADER_HEIGHT,
            accent_width=_ACCENT_WIDTH,
            accent_height=_ACCENT_HEIGHT,
            close_size=_CLOSE_SIZE,
            close_size_font=_CLOSE_SIZE_FONT,
            root_margin=_ROOT_MARGIN,
            root_spacing=_ROOT_SPACING,
            header_spacing=_HEADER_SPACING,
            body_margin=_BODY_MARGIN,
            header_size=_HEADER_SIZE,
            source_size=_SOURCE_SIZE,
            body_size=_BODY_SIZE,
            stylesheet=_help_stylesheet(),
            empty_text=HELP_EMPTY_TEXT,
        )

    # ── 内容 ─────────────────────────────────────────────────────────

    def show_help(self, title: str, text: str) -> None:
        """换上新内容并淡入；同一窗口被复用时会整段替换，不留上一条的残影。"""

        self.refresh_workbench_theme()
        self._title = str(title or "").strip() or HELP_DEFAULT_TITLE
        self._text = str(text or "").strip()
        body = self._text or HELP_EMPTY_TEXT
        if self._header_label is not None:
            self._header_label.setText(self._title)
        if self._body is not None:
            self._body.setText(body)
        if self._scroll is not None:
            self._scroll.verticalScrollBar().setValue(0)
        self._window.show_window()

    def wants_visible(self) -> bool:
        return self._window.is_requested_visible()

    # ── 主题 ─────────────────────────────────────────────────────────

    def refresh_workbench_theme(self) -> None:
        self._spec = self._build_spec()
        self._window.apply_theme(self._spec)

    # ── 显示与隐藏 ───────────────────────────────────────────────────

    def hide_dialog(self) -> None:
        self._window.hide_dialog()

    def cleanup(self) -> None:
        self._window.cleanup()

    # ── 语义与生命周期 ───────────────────────────────────────────────

    def _on_semantic(self, semantic: str) -> None:
        if str(semantic) == HELP_CLOSE:
            self.hide_dialog()

    # ── 底层窗口的属性面（调用方按原 QWidget 用法）────────────────────

    def widget(self):
        """底层真实窗口，供诊断与需要 ``QWidget`` 的调用方取用。"""

        return self._window.widget

    def move(self, *args) -> None:
        self._window.widget.move(*args)

    def show(self) -> None:
        self._window.widget.show()

    def hide(self) -> None:
        self._window.widget.hide()

    def raise_(self) -> None:
        self._window.widget.raise_()

    def activateWindow(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._window.widget.activateWindow()

    def width(self) -> int:
        return self._window.widget.width()

    def height(self) -> int:
        return self._window.widget.height()


def _help_stylesheet() -> str:
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


class HelpWindowController:
    """订阅 `HELP_WINDOW_REQUEST`，保证全局只有一个帮助窗口。

    「只有一个」的做法是：每次请求都先 `_discard_dialog()` 丢掉手上那一只
    （`cleanup()` 会注销 `LayerManager` 并 `deleteLater()`），再按需新建。
    这样即便上一次的浮窗正处于淡出动画里，也不会留下一个半透明残影。

    控制器本身不再是 `QObject`：它只需要"把回调投递回 Qt 事件循环"这一条 Qt 事实，
    由 `render_bridge.create_ui_dispatcher()` 提供的宿主承接（与公告 / 论坛控制器同源）。
    """

    def __init__(self, parent=None) -> None:
        self._dialog: DesktopPetHelpDialog | None = None
        self._closed = False
        self._last_payload: tuple[str, str] | None = None
        self._dispatcher = render_bridge.create_ui_dispatcher(parent)
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
        self._dispatcher.post(callback)

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
        self._dispatcher.clear()
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
