"""Remote desktop-pet announcement loading, persistence, and UI."""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from typing import Callable

import requests
from PyQt5.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.config import UI
from lib.core.qt_bridge.font import get_ui_font, get_ui_font_family
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
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
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import EventType, get_event_center
from lib.core.graphics.announcement_visuals import ANNOUNCEMENT_SIZE, get_announcement_colors
from lib.core.qt_bridge.screen import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager


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


class DesktopPetAnnouncementDialog(QWidget):
    """Compact, scroll-ready desktop-pet announcement window."""

    suppress_today_requested = pyqtSignal()
    suppress_forever_requested = pyqtSignal()
    retry_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DesktopPetAnnouncementDialog")
        self.setWindowTitle("桌宠公告")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_WIDTH, _HEIGHT)
        get_layer_manager().register(self, Layer.DIALOG, name="DesktopPetAnnouncementDialog")

        self._requested_visible = False
        self._closing_animation = False

        self._header_accent = QFrame(self)
        self._header_accent.setObjectName("AnnouncementHeaderAccent")
        self._header_accent.setFixedSize(
            scale_px(3, min_abs=2),
            scale_px(42, min_abs=36),
        )

        self._header_label = QLabel("桌宠公告", self)
        self._header_label.setObjectName("AnnouncementHeader")
        header_font = get_ui_font(size=scale_px(17, min_abs=14))
        header_font.setBold(True)
        self._header_label.setFont(header_font)

        self._source_label = QLabel("SYSTEM BROADCAST  /  FSV", self)
        self._source_label.setObjectName("AnnouncementSource")
        self._source_label.setFont(get_ui_font(size=scale_px(10, min_abs=9)))

        self._close_button = QToolButton(self)
        self._close_button.setObjectName("AnnouncementCloseButton")
        self._close_button.setText("×")
        self._close_button.setToolTip("关闭公告")
        self._close_button.setAccessibleName("关闭公告")
        self._close_button.setFixedSize(
            scale_px(30, min_abs=26),
            scale_px(30, min_abs=26),
        )
        self._close_button.clicked.connect(self._dismiss)

        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(0)
        header_text.addWidget(self._header_label)
        header_text.addWidget(self._source_label)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(scale_px(12, min_abs=9))
        header_row.addWidget(self._header_accent, 0, Qt.AlignVCenter)
        header_row.addLayout(header_text, 1)
        header_row.addWidget(self._close_button, 0, Qt.AlignTop)

        self._body = QTextBrowser(self)
        self._body.setObjectName("AnnouncementBody")
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._body.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
        self._body.document().setDocumentMargin(scale_px(16, min_abs=12))
        self._body.document().setDefaultStyleSheet(self._document_stylesheet())

        self._today_button = QPushButton("今日不再显示", self)
        self._today_button.setObjectName("AnnouncementTodayButton")
        self._today_button.clicked.connect(self.suppress_today_requested.emit)
        self._forever_button = QPushButton("永远不再显示", self)
        self._forever_button.setObjectName("AnnouncementForeverButton")
        self._forever_button.clicked.connect(self.suppress_forever_requested.emit)
        self._error_close_button = QPushButton("关闭", self)
        self._error_close_button.clicked.connect(self._dismiss)
        self._retry_button = QPushButton("重新加载", self)
        self._retry_button.setObjectName("AnnouncementRetryButton")
        self._retry_button.clicked.connect(self.retry_requested.emit)

        self._channel_label = QLabel("REMOTE CHANNEL  /  01", self)
        self._channel_label.setObjectName("AnnouncementChannel")
        self._channel_label.setFont(get_ui_font(size=scale_px(9, min_abs=8)))

        for button in (
            self._today_button,
            self._forever_button,
            self._error_close_button,
            self._retry_button,
        ):
            button.setFont(get_ui_font())
            button.setMinimumHeight(scale_px(34, min_abs=30))

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(scale_px(9, min_abs=7))
        button_row.addWidget(self._channel_label)
        button_row.addStretch(1)
        button_row.addWidget(self._error_close_button)
        button_row.addWidget(self._retry_button)
        button_row.addWidget(self._today_button)
        button_row.addWidget(self._forever_button)

        content = QVBoxLayout(self)
        content.setContentsMargins(
            _BORDER + scale_px(20, min_abs=16),
            _BORDER + scale_px(18, min_abs=14),
            _BORDER + scale_px(20, min_abs=16),
            _BORDER + scale_px(17, min_abs=14),
        )
        content.setSpacing(scale_px(15, min_abs=11))
        content.addLayout(header_row)
        content.addWidget(self._body, 1)
        content.addLayout(button_row)

        self.setStyleSheet(self._widget_stylesheet())

        self._opacity_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_animation.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._opacity_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_animation.finished.connect(self._on_animation_finished)

    def show_document(self, document: AnnouncementDocument) -> None:
        self.refresh_workbench_theme()
        self._body.document().setDefaultStyleSheet(self._document_stylesheet())
        self._header_label.setText(document.title or "桌宠公告")
        self._body.setHtml(_announcement_body_to_html(document))
        self._body.verticalScrollBar().setValue(0)
        self._set_action_mode("announcement")
        self._show_dialog()

    def show_loading(self) -> None:
        self.refresh_workbench_theme()
        self._header_label.setText("桌宠公告")
        self._body.setHtml(
            '<div class="status"><h2>正在获取公告</h2>'
            '<p>正在连接公告服务器，请稍候。</p></div>'
        )
        self._set_action_mode("loading")
        self._show_dialog()

    def show_error(self) -> None:
        self.refresh_workbench_theme()
        self._header_label.setText("桌宠公告")
        self._body.setHtml(
            '<div class="status"><h2>公告暂时无法加载</h2>'
            '<p>没有可用的本地公告，请稍后重试。</p></div>'
        )
        self._set_action_mode("error")
        self._show_dialog()

    def wants_visible(self) -> bool:
        return self._requested_visible

    def refresh_workbench_theme(self) -> None:
        """Repolish the announcement when the workbench theme changes."""
        self.setStyleSheet(self._widget_stylesheet())
        self._body.document().setDefaultStyleSheet(self._document_stylesheet())
        self.update()

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
        self.hide()
        get_layer_manager().unregister(self)
        self.deleteLater()

    def _set_action_mode(self, mode: str) -> None:
        is_announcement = mode == "announcement"
        is_error = mode == "error"
        self._today_button.setVisible(is_announcement)
        self._forever_button.setVisible(is_announcement)
        self._error_close_button.setVisible(is_error)
        self._retry_button.setVisible(is_error)

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

    def _dismiss(self) -> None:
        self.dismissed.emit()
        self.hide_dialog()

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
            QToolButton#AnnouncementCloseButton {{
                background: transparent;
                color: {text_muted};
                border: {scale_px(1, min_abs=1)}px solid transparent;
                border-radius: {scale_px(4, min_abs=3)}px;
                font-size: {scale_px(19, min_abs=17)}px;
                font-weight: 500;
                padding: 0px;
            }}
            QToolButton#AnnouncementCloseButton:hover {{
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


class AnnouncementController(QObject):
    """Qt view adapter over the backend-neutral announcement service."""

    _dispatch_requested = pyqtSignal(object)

    def __init__(
        self,
        parent=None,
        *,
        state_path: Path | None = None,
        cache_path: Path | None = None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(parent)
        self._dialog: DesktopPetAnnouncementDialog | None = None
        self._closed = False
        self._dispatch_requested.connect(lambda callback: callback())
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
        self._dispatch_requested.emit(callback)

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
        if self._dialog is not None:
            self._dialog.cleanup()
            self._dialog = None
