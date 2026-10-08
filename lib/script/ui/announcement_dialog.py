"""Remote desktop-pet announcement loading, persistence, and UI."""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from typing import Callable

import requests

from config.scale import scale_px
from lib.core.announcement import (
    AnnouncementBlock,
    AnnouncementDocument,
    AnnouncementPreferences,
    AnnouncementService,
    is_announcement_suppressed,
    load_announcement_preferences,
    parse_announcement,
    save_announcement_preferences,
    set_announcement_forever_suppressed,
)

#: 这些偏好的定义在 lib.core.announcement；窗口模块与测试一直从这里取，保持再导出。
__all__ = [
    "AnnouncementBlock",
    "AnnouncementPreferences",
    "is_announcement_suppressed",
    "load_announcement_preferences",
    "parse_announcement",
    "save_announcement_preferences",
    "set_announcement_forever_suppressed",
]
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import EventType, get_event_center
from lib.core.render.visuals.announcement_visuals import (
    ANNOUNCEMENT_SIZE,
    get_announcement_colors,
)
from lib.core.render.visuals.window_specs import (
    ANNOUNCEMENT_CLOSE,
    ANNOUNCEMENT_RETRY,
    ANNOUNCEMENT_SUPPRESS_FOREVER,
    ANNOUNCEMENT_SUPPRESS_TODAY,
    announcement_window_spec,
)
from lib.script.ui import render_bridge
from lib.script.ui.render_bridge import ui_font_family as get_ui_font_family


def announcement_to_html(document: AnnouncementDocument) -> str:
    """Render an announcement document as escaped, presentation-only HTML."""

    def with_breaks(value: str) -> str:
        return escape(value).replace("\n", "<br>")

    parts = [f'<h1>{with_breaks(document.title)}</h1>']
    for block in document.blocks:
        tag = "h2" if block.kind == "subtitle" else "p"
        parts.append(f"<{tag}>{with_breaks(block.text)}</{tag}>")
    return "".join(parts)


def _announcement_body_to_html(document: AnnouncementDocument) -> str:
    """Render only body blocks; the window header owns the document title."""

    def with_breaks(value: str) -> str:
        return escape(value).replace("\n", "<br>")

    parts: list[str] = []
    for block in document.blocks:
        tag = "h2" if block.kind == "subtitle" else "p"
        parts.append(f"<{tag}>{with_breaks(block.text)}</{tag}>")
    return "".join(parts)


_WIDTH = int(ANNOUNCEMENT_SIZE.width)
_HEIGHT = int(ANNOUNCEMENT_SIZE.height)
_BORDER = scale_px(1, min_abs=1)


def _color_name(key: str) -> str:
    color = get_announcement_colors()[key]
    return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"


class _AnnouncementSignal:
    """``pyqtSignal()`` 的后端中立替身：只保留 ``connect`` / ``emit`` / ``disconnect``。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot=None) -> None:
        if slot is None:
            self._slots.clear()
            return
        try:
            self._slots.remove(slot)
        except ValueError:
            pass

    def emit(self, *args) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class DesktopPetAnnouncementDialog:
    """紧凑、可滚动的桌宠公告浮窗（按窗口描述装配）。

    本类不再是 ``QWidget`` 子类、也不再 ``import PyQt5``：它只收集公告状态、产出一棵
    ``WindowSpec``，并把语义回调翻译成产品动作。真实控件树由
    ``render_bridge.create_spec_window()`` 交给 Qt 宿主搭建。

    为兼容既有调用方与测试，保留原 ``QWidget`` 的属性面（``_header_label`` / ``_body`` /
    ``_today_button`` 等），它们现在指向描述宿主里对应 ``id`` 的真实控件。
    """

    #: 公告控制器已订阅主题事件并调用 refresh_workbench_theme()。
    follows_workbench_theme = False

    def __init__(self, parent=None) -> None:
        self.suppress_today_requested = _AnnouncementSignal()
        self.suppress_forever_requested = _AnnouncementSignal()
        self.retry_requested = _AnnouncementSignal()
        self.dismissed = _AnnouncementSignal()

        self._requested_visible = False
        self._title = "桌宠公告"
        self._html = ""
        self._mode = "announcement"
        self._spec = self._build_spec()
        self._window = render_bridge.create_spec_window(
            self._spec,
            on_semantic=self._on_semantic,
            parent=parent,
        )

        self._header_label = self._window.find("header")
        self._body = self._window.find("body")
        self._today_button = self._window.find("today")
        self._forever_button = self._window.find("forever")
        self._retry_button = self._window.find("retry")
        self._error_close_button = self._window.find("error_close")
        self._minimize_button = self._window.find("minimize")
        self._close_button = self._window.find("close")
        self._set_action_mode("announcement")

    # ── 描述 ─────────────────────────────────────────────────────────

    def _build_spec(self):
        return announcement_window_spec(
            title=self._title,
            html=self._html,
            document_stylesheet=self._document_stylesheet(),
            stylesheet=self._widget_stylesheet(),
            width=_WIDTH,
            height=_HEIGHT,
            border_width=_BORDER,
            root_margin=(
                _BORDER + scale_px(20, min_abs=16),
                _BORDER + scale_px(18, min_abs=14),
                _BORDER + scale_px(20, min_abs=16),
                _BORDER + scale_px(17, min_abs=14),
            ),
            root_spacing=scale_px(15, min_abs=11),
            header_height=0,
            header_spacing=scale_px(12, min_abs=9),
            accent_width=scale_px(3, min_abs=2),
            accent_height=scale_px(42, min_abs=36),
            header_size=scale_px(17, min_abs=14),
            source_size=scale_px(10, min_abs=9),
            channel_size=scale_px(9, min_abs=8),
            body_size=scale_px(13, min_abs=11),
            close_size=scale_px(30, min_abs=26),
            close_size_font=scale_px(17, min_abs=15),
            button_height=scale_px(34, min_abs=30),
            button_row_spacing=scale_px(9, min_abs=7),
            document_margin=scale_px(16, min_abs=12),
        )

    # ── 内容 ─────────────────────────────────────────────────────────

    def show_document(self, document: AnnouncementDocument) -> None:
        self.refresh_workbench_theme()
        self._title = document.title or "桌宠公告"
        self._html = _announcement_body_to_html(document)
        self._header_label.setText(self._title)
        self._body.setHtml(self._html)
        self._body.verticalScrollBar().setValue(0)
        self._set_action_mode("announcement")
        self._show_dialog()

    def show_loading(self) -> None:
        self.refresh_workbench_theme()
        self._header_label.setText("桌宠公告")
        self._body.setHtml(
            '<div class="status"><h2>正在获取公告</h2>'
            "<p>正在连接公告服务器，请稍候。</p></div>"
        )
        self._set_action_mode("loading")
        self._show_dialog()

    def show_error(self) -> None:
        self.refresh_workbench_theme()
        self._header_label.setText("桌宠公告")
        self._body.setHtml(
            '<div class="status"><h2>公告暂时无法加载</h2>'
            "<p>没有可用的本地公告，请稍后重试。</p></div>"
        )
        self._set_action_mode("error")
        self._show_dialog()

    def wants_visible(self) -> bool:
        return self._requested_visible

    # ── 主题 ─────────────────────────────────────────────────────────

    def refresh_workbench_theme(self) -> None:
        """Repolish the announcement when the workbench theme changes."""
        self._spec = self._build_spec()
        self._window.apply_theme(self._spec)

    def floating_stylesheet(self) -> str:
        return self._widget_stylesheet()

    # ── 显示与隐藏 ───────────────────────────────────────────────────

    def hide_dialog(self) -> None:
        if not self._requested_visible:
            return
        self._requested_visible = False
        self._window.hide_dialog()

    def cleanup(self) -> None:
        self._requested_visible = False
        self._window.cleanup()

    def _set_action_mode(self, mode: str) -> None:
        self._mode = str(mode)
        is_announcement = self._mode == "announcement"
        is_error = self._mode == "error"
        for button, visible in (
            (self._today_button, is_announcement),
            (self._forever_button, is_announcement),
            (self._error_close_button, is_error),
            (self._retry_button, is_error),
        ):
            if button is not None:
                button.setVisible(visible)

    def _show_dialog(self) -> None:
        self._requested_visible = True
        self._window.show_window()

    # ── 语义与生命周期 ───────────────────────────────────────────────

    def _on_semantic(self, semantic: str) -> None:
        key = str(semantic)
        if key == ANNOUNCEMENT_SUPPRESS_TODAY:
            self.suppress_today_requested.emit()
        elif key == ANNOUNCEMENT_SUPPRESS_FOREVER:
            self.suppress_forever_requested.emit()
        elif key == ANNOUNCEMENT_RETRY:
            self.retry_requested.emit()
        elif key == ANNOUNCEMENT_CLOSE:
            self._dismiss()

    def _dismiss(self) -> None:
        self.dismissed.emit()
        self.hide_dialog()

    # ── 底层窗口的属性面（调用方按原 QWidget 用法）────────────────────

    def widget(self):
        """底层真实窗口，供诊断与需要 ``QWidget`` 的调用方取用。"""

        return self._window.widget

    def findChild(self, *args, **kwargs):  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.findChild(*args, **kwargs)

    def show(self) -> None:
        self._window.widget.show()

    def hide(self) -> None:
        self._window.widget.hide()

    def move(self, *args) -> None:
        self._window.widget.move(*args)

    def raise_(self) -> None:
        self._window.widget.raise_()

    def activateWindow(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._window.widget.activateWindow()

    def deleteLater(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._window.widget.deleteLater()

    def minimumWidth(self) -> int:  # noqa: N802
        return self._window.widget.minimumWidth()

    def width(self) -> int:
        return self._window.widget.width()

    def height(self) -> int:
        return self._window.widget.height()

    def isVisible(self) -> bool:  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.isVisible()

    @staticmethod
    def _document_stylesheet() -> str:
        font_family = get_ui_font_family().replace("'", "\\'")
        return f"""
            body {{
                color: {_color_name("text_muted")};
                font-family: '{font_family}';
                font-size: {scale_px(13, min_abs=11)}px;
                line-height: 1.32;
                margin: 0;
            }}
            h1 {{
                color: {_color_name("text")};
                font-size: {scale_px(17, min_abs=14)}px;
                font-weight: 700;
                margin: 0 0 {scale_px(8, min_abs=6)}px 0;
            }}
            h2 {{
                color: {_color_name("cyan")};
                font-size: {scale_px(14, min_abs=12)}px;
                font-weight: 700;
                margin: {scale_px(12, min_abs=9)}px 0 {scale_px(5, min_abs=3)}px 0;
            }}
            p {{
                color: {_color_name("text_muted")};
                margin: 0 0 {scale_px(10, min_abs=7)}px 0;
                white-space: pre-wrap;
            }}
            .status {{ text-align: center; margin-top: {scale_px(76, min_abs=54)}px; }}
        """

    @staticmethod
    def _widget_stylesheet() -> str:
        canvas = _color_name("canvas")
        surface = _color_name("surface")
        surface_raised = _color_name("surface_raised")
        surface_hover = _color_name("surface_hover")
        border = _color_name("border")
        border_strong = _color_name("border_strong")
        text = _color_name("text")
        text_muted = _color_name("text_muted")
        text_dim = _color_name("text_dim")
        cyan = _color_name("cyan")
        pink = _color_name("pink")
        pink_hover = _color_name("pink_hover")
        font_family = get_ui_font_family().replace("'", "\\'")
        return f"""
            QWidget#DesktopPetAnnouncementDialog {{
                color: {text};
                font-family: '{font_family}';
            }}
            QFrame#AnnouncementHeaderAccent {{
                background: {pink};
                border: none;
                border-right: {scale_px(1, min_abs=1)}px solid {cyan};
            }}
            QLabel#AnnouncementHeader {{
                color: {text};
                font-size: {scale_px(18, min_abs=15)}px;
                font-weight: 700;
            }}
            QLabel#AnnouncementSource, QLabel#AnnouncementChannel {{
                color: {text_dim};
                font-weight: 500;
            }}
            QTextBrowser#AnnouncementBody {{
                background: {surface};
                color: {text_muted};
                border: {scale_px(1, min_abs=1)}px solid {border};
                border-top-color: {border_strong};
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: 0px;
            }}
            QToolButton#AnnouncementCloseButton,
            QToolButton#AnnouncementMinimizeButton {{
                background: transparent;
                color: {text_muted};
                border: {scale_px(1, min_abs=1)}px solid transparent;
                border-radius: {scale_px(4, min_abs=3)}px;
                font-size: {scale_px(17, min_abs=15)}px;
                font-weight: 500;
                padding: 0px;
            }}
            QToolButton#AnnouncementCloseButton:hover,
            QToolButton#AnnouncementMinimizeButton:hover {{
                background: {surface_hover};
                color: {text};
                border-color: {border};
            }}
            QPushButton {{
                background: {surface_raised};
                color: {text};
                border: {scale_px(1, min_abs=1)}px solid {border};
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: {scale_px(4, min_abs=3)}px {scale_px(13, min_abs=10)}px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {surface_hover}; border-color: {cyan}; }}
            QPushButton:pressed {{ background: {border_strong}; }}
            QPushButton#AnnouncementForeverButton {{ color: {text_muted}; }}
            QPushButton#AnnouncementTodayButton {{
                background: {pink};
                color: {canvas};
                border-color: {pink};
            }}
            QPushButton#AnnouncementTodayButton:hover {{
                background: {pink_hover};
                border-color: {pink_hover};
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


class AnnouncementController:
    """Backend-neutral announcement view adapter over the announcement service."""

    def __init__(
        self,
        parent=None,
        *,
        state_path: Path | None = None,
        cache_path: Path | None = None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self._dialog: DesktopPetAnnouncementDialog | None = None
        self._closed = False
        self._dispatcher = render_bridge.create_ui_dispatcher(parent)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        self._service = AnnouncementService(
            dispatch=self._dispatch,
            on_loading=self._show_loading,
            on_document=self._show_document,
            on_error=self._show_error,
            on_hide=self._hide_dialog,
            state_path=state_path,
            cache_path=cache_path,
            today_provider=today_provider,
            submit_io=lambda func, *args: get_compute_hub().submit_io(func, *args),
            request_get=lambda *args, **kwargs: requests.get(*args, **kwargs),
        )

    def _dispatch(self, callback: Callable[[], None]) -> None:
        self._dispatcher.post(callback)

    def _on_config_updated(self, event) -> None:
        if self._dialog is not None:
            self._dialog.refresh_workbench_theme()

    def start(self) -> bool:
        return self._service.start()

    def open_from_tray(self) -> None:
        self._service.open_manual()

    def _ensure_dialog(self) -> DesktopPetAnnouncementDialog:
        if self._dialog is None:
            self._dialog = DesktopPetAnnouncementDialog()
            self._dialog.suppress_today_requested.connect(self._service.suppress_today)
            self._dialog.suppress_forever_requested.connect(self._service.suppress_forever)
            self._dialog.retry_requested.connect(self._service.retry)
            self._dialog.dismissed.connect(self._service.dismiss)
        return self._dialog

    def _show_loading(self) -> None:
        self._ensure_dialog().show_loading()

    def _show_document(self, document, manual: bool) -> None:
        if manual:
            if self._dialog is not None and self._dialog.wants_visible():
                self._dialog.show_document(document)
            return
        self._ensure_dialog().show_document(document)

    def _show_error(self, manual: bool) -> None:
        if manual and self._dialog is not None and self._dialog.wants_visible():
            self._dialog.show_error()

    def _hide_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.hide_dialog()

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._service.cleanup()
        self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        self._dispatcher.clear()
        if self._dialog is not None:
            self._dialog.cleanup()
            self._dialog = None
