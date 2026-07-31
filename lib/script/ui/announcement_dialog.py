"""Remote desktop-pet announcement loading, persistence, and UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import json
from pathlib import Path
import re
import threading
import time
from typing import Callable

import requests
from PyQt5.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.config import UI, UI_THEME
from config.font_config import get_ui_font, get_ui_font_family
from config.scale import scale_px, scale_style_px
from config.shared_storage_io import write_bytes_atomic
from config.user_storage_paths import get_user_cache_dir, get_user_state_dir
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.logger import get_logger
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager


ANNOUNCEMENT_URL = (
    "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/releases/download/RESC/"
    "%E5%85%AC%E5%91%8A.txt"
)
ANNOUNCEMENT_REQUEST_TIMEOUT = (5.0, 12.0)
ANNOUNCEMENT_MAX_BYTES = 1024 * 1024

_FIELD_START_RE = re.compile(
    r'^\s*(title|subtitle|text)\s*:\s*"(.*)$',
    re.IGNORECASE,
)
_logger = get_logger(__name__)


@dataclass(frozen=True)
class AnnouncementBlock:
    kind: str
    text: str


@dataclass(frozen=True)
class AnnouncementDocument:
    title: str
    blocks: tuple[AnnouncementBlock, ...]


@dataclass(frozen=True)
class AnnouncementPreferences:
    suppress_forever: bool = False
    suppress_date: str = ""


def parse_announcement(raw_text: str) -> AnnouncementDocument:
    """Parse ordered title/subtitle/text quoted blocks from the remote file."""
    raw = str(raw_text or "").lstrip("\ufeff")
    lines = raw.splitlines()
    fields: list[tuple[str, str]] = []
    index = 0

    while index < len(lines):
        match = _FIELD_START_RE.match(lines[index])
        if match is None:
            index += 1
            continue

        key = match.group(1).lower()
        remainder = match.group(2)
        inline = remainder.rstrip()
        if inline.endswith('"'):
            fields.append((key, inline[:-1].strip()))
            index += 1
            continue

        value_lines: list[str] = []
        if remainder:
            value_lines.append(remainder)
        index += 1
        while index < len(lines) and lines[index].strip() != '"':
            value_lines.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        fields.append((key, "\n".join(value_lines).strip()))

    title = next((value for key, value in fields if key == "title" and value), "")
    blocks = tuple(
        AnnouncementBlock(key, value)
        for key, value in fields
        if key in {"subtitle", "text"} and value
    )

    if not fields and raw.strip():
        blocks = (AnnouncementBlock("text", raw.strip()),)
    if not title:
        title = "桌宠公告"
    if not blocks and not raw.strip():
        raise ValueError("公告内容为空")
    return AnnouncementDocument(title=title, blocks=blocks)


def announcement_to_html(document: AnnouncementDocument) -> str:
    """Render an announcement document as escaped, presentation-only HTML."""

    def with_breaks(value: str) -> str:
        return escape(value).replace("\n", "<br>")

    parts = [f'<h1>{with_breaks(document.title)}</h1>']
    for block in document.blocks:
        tag = "h2" if block.kind == "subtitle" else "p"
        parts.append(f"<{tag}>{with_breaks(block.text)}</{tag}>")
    return "".join(parts)


def load_announcement_preferences(path: Path | None = None) -> AnnouncementPreferences:
    state_path = Path(path) if path is not None else get_user_state_dir("announcement.json")
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return AnnouncementPreferences()
    if not isinstance(payload, dict):
        return AnnouncementPreferences()
    suppress_forever = payload.get("suppress_forever", False)
    suppress_date = payload.get("suppress_date", "")
    return AnnouncementPreferences(
        suppress_forever=suppress_forever if isinstance(suppress_forever, bool) else False,
        suppress_date=suppress_date if isinstance(suppress_date, str) else "",
    )


def save_announcement_preferences(
    preferences: AnnouncementPreferences,
    path: Path | None = None,
) -> None:
    state_path = Path(path) if path is not None else get_user_state_dir("announcement.json")
    payload = {
        "schema_version": 1,
        "suppress_forever": bool(preferences.suppress_forever),
        "suppress_date": str(preferences.suppress_date or ""),
    }
    write_bytes_atomic(
        state_path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def set_announcement_forever_suppressed(
    suppressed: bool,
    path: Path | None = None,
) -> AnnouncementPreferences:
    """Update only the permanent suppression flag and preserve today's choice."""
    current = load_announcement_preferences(path)
    updated = AnnouncementPreferences(
        suppress_forever=bool(suppressed),
        suppress_date=current.suppress_date,
    )
    save_announcement_preferences(updated, path)
    return updated


def is_announcement_suppressed(
    preferences: AnnouncementPreferences,
    current_date: date | None = None,
) -> bool:
    today = current_date or date.today()
    return bool(
        preferences.suppress_forever
        or preferences.suppress_date == today.isoformat()
    )


_WIDTH = scale_px(520, min_abs=460)
_HEIGHT = scale_px(440, min_abs=380)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


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

        self._header_label = QLabel("桌宠公告", self)
        self._header_label.setObjectName("AnnouncementHeader")
        header_font = get_ui_font(size=scale_px(17, min_abs=14))
        header_font.setBold(True)
        self._header_label.setFont(header_font)

        self._source_label = QLabel("FLYING SNOW VELVET", self)
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
        header_row.setSpacing(scale_px(8, min_abs=6))
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
        button_row.addStretch(1)
        button_row.addWidget(self._error_close_button)
        button_row.addWidget(self._retry_button)
        button_row.addWidget(self._today_button)
        button_row.addWidget(self._forever_button)

        content = QVBoxLayout(self)
        content.setContentsMargins(
            _BORDER + scale_px(17, min_abs=14),
            _BORDER + scale_px(14, min_abs=12),
            _BORDER + scale_px(17, min_abs=14),
            _BORDER + scale_px(14, min_abs=12),
        )
        content.setSpacing(scale_px(12, min_abs=9))
        content.addLayout(header_row)
        content.addWidget(self._body, 1)
        content.addLayout(button_row)

        self.setStyleSheet(self._widget_stylesheet())

        self._opacity_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_animation.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._opacity_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_animation.finished.connect(self._on_animation_finished)

    def show_document(self, document: AnnouncementDocument) -> None:
        self._body.document().setDefaultStyleSheet(self._document_stylesheet())
        self._body.setHtml(announcement_to_html(document))
        self._body.verticalScrollBar().setValue(0)
        self._set_action_mode("announcement")
        self._show_dialog()

    def show_loading(self) -> None:
        self._body.setHtml(
            '<div class="status"><h1>正在获取公告</h1>'
            '<p>正在连接公告服务器，请稍候。</p></div>'
        )
        self._set_action_mode("loading")
        self._show_dialog()

    def show_error(self) -> None:
        self._body.setHtml(
            '<div class="status"><h1>公告暂时无法加载</h1>'
            '<p>没有可用的本地公告，请稍后重试。</p></div>'
        )
        self._set_action_mode("error")
        self._show_dialog()

    def wants_visible(self) -> bool:
        return self._requested_visible

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
        painter.fillRect(self.rect(), UI_THEME["border"])
        painter.fillRect(
            self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER),
            UI_THEME["mid"],
        )
        painter.fillRect(
            self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER),
            UI_THEME["bg"],
        )

    @staticmethod
    def _document_stylesheet() -> str:
        font_family = get_ui_font_family().replace("'", "\\'")
        return f"""
            body {{
                color: #101820;
                font-family: '{font_family}';
                font-size: {scale_px(13, min_abs=11)}px;
                line-height: 1.55;
                margin: 0;
            }}
            h1 {{
                color: #234c80;
                font-size: {scale_px(19, min_abs=16)}px;
                font-weight: 700;
                margin: 0 0 {scale_px(14, min_abs=10)}px 0;
            }}
            h2 {{
                color: #234c80;
                font-size: {scale_px(15, min_abs=13)}px;
                font-weight: 700;
                margin: {scale_px(16, min_abs=12)}px 0 {scale_px(6, min_abs=5)}px 0;
            }}
            p {{
                color: #101820;
                margin: 0 0 {scale_px(11, min_abs=8)}px 0;
                white-space: pre-wrap;
            }}
            .status {{ text-align: center; margin-top: {scale_px(70, min_abs=50)}px; }}
        """

    @staticmethod
    def _widget_stylesheet() -> str:
        border = UI_THEME["border"].name()
        cyan = UI_THEME["mid"].name()
        deep_cyan = UI_THEME["deep_cyan"].name()
        pink = UI_THEME["bg"].name()
        deep_pink = UI_THEME["deep_pink"].name()
        font_family = get_ui_font_family().replace("'", "\\'")
        return scale_style_px(
            f"""
            QWidget#DesktopPetAnnouncementDialog {{
                color: {border};
                font-family: '{font_family}';
            }}
            QLabel#AnnouncementHeader {{ color: {border}; }}
            QLabel#AnnouncementSource {{ color: #234c80; font-weight: 600; }}
            QTextBrowser#AnnouncementBody {{
                background: #fff8fb;
                color: #101820;
                border: 2px solid {border};
                border-top-color: {deep_cyan};
                border-bottom-color: {deep_pink};
                padding: 0px;
            }}
            QToolButton#AnnouncementCloseButton {{
                background: transparent;
                color: {border};
                border: 2px solid transparent;
                font-size: 20px;
                font-weight: bold;
                padding: 0px;
            }}
            QToolButton#AnnouncementCloseButton:hover {{
                background: {deep_pink};
                border-color: {border};
            }}
            QPushButton {{
                background: #fff8fb;
                color: {border};
                border: 2px solid {border};
                padding: 4px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {cyan}; }}
            QPushButton:pressed {{ background: {pink}; }}
            QPushButton#AnnouncementTodayButton {{ background: {cyan}; }}
            QPushButton#AnnouncementTodayButton:hover {{ background: {deep_cyan}; }}
            QPushButton#AnnouncementForeverButton {{ background: {pink}; }}
            QPushButton#AnnouncementForeverButton:hover {{ background: {deep_pink}; }}
            QScrollBar:vertical {{
                background: #fff8fb;
                width: 10px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {deep_cyan};
                min-height: 28px;
                border: 1px solid {border};
            }}
            QScrollBar::handle:vertical:hover {{ background: {deep_pink}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )


class AnnouncementController(QObject):
    """Own the announcement request, window, cache, and suppression state."""

    _download_succeeded = pyqtSignal(int, bool, str)
    _download_failed = pyqtSignal(int, bool, str)

    def __init__(
        self,
        parent=None,
        *,
        state_path: Path | None = None,
        cache_path: Path | None = None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(parent)
        self._state_path = Path(state_path) if state_path is not None else get_user_state_dir(
            "announcement.json"
        )
        self._cache_path = Path(cache_path) if cache_path is not None else get_user_cache_dir(
            "announcement.txt"
        )
        self._today_provider = today_provider
        self._preferences = load_announcement_preferences(self._state_path)
        self._dialog: DesktopPetAnnouncementDialog | None = None
        self._current_document: AnnouncementDocument | None = None
        self._manual_waiting = False
        self._request_id = 0
        self._active_request_id = 0
        self._futures = {}
        self._closed = False
        self._response_lock = threading.Lock()
        self._active_responses = {}

        self._download_succeeded.connect(self._on_download_succeeded)
        self._download_failed.connect(self._on_download_failed)

    def start(self) -> bool:
        """Start the non-blocking startup fetch unless automatic display is suppressed."""
        if self._closed or self._is_suppressed():
            return False
        return self._request_download(manual=False)

    def open_from_tray(self) -> None:
        """Fetch and show the latest announcement for every manual open."""
        if self._closed:
            return
        dialog = self._ensure_dialog()
        dialog.show_loading()
        self._manual_waiting = True
        self._request_download(manual=True)

    def cleanup(self) -> None:
        if self._closed:
            return
        self._manual_waiting = False
        with self._response_lock:
            self._closed = True
            futures = tuple(self._futures.values())
            responses = tuple(self._active_responses.values())
            self._futures.clear()
            self._active_responses.clear()
        for future in futures:
            future.cancel()
        for response in responses:
            try:
                response.close()
            except Exception:
                pass
        if self._dialog is not None:
            self._dialog.cleanup()
            self._dialog = None

    def _request_download(self, *, manual: bool) -> bool:
        if self._closed:
            return False

        self._request_id += 1
        request_id = self._request_id
        with self._response_lock:
            self._active_request_id = request_id
            stale_futures = tuple(self._futures.values())
            stale_responses = tuple(self._active_responses.values())
            self._active_responses.clear()
        for future in stale_futures:
            future.cancel()
        for response in stale_responses:
            try:
                response.close()
            except Exception:
                pass
        try:
            future = get_compute_hub().submit_io(
                self._download_worker,
                request_id,
                manual,
            )
        except Exception as exc:
            self._on_download_failed(request_id, manual, str(exc))
            return False
        with self._response_lock:
            if not self._closed:
                self._futures[request_id] = future
        try:
            future.add_done_callback(
                lambda completed, current_id=request_id: self._discard_future(
                    current_id,
                    completed,
                )
            )
        except (AttributeError, TypeError):
            pass
        return True

    def _download_worker(self, request_id: int, manual: bool) -> None:
        response = None
        try:
            if not self._request_is_current(request_id):
                return
            response = requests.get(
                ANNOUNCEMENT_URL,
                params={"_": str(time.time_ns())},
                headers={
                    "User-Agent": "FlyingSnowVelvet-Announcement/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                timeout=ANNOUNCEMENT_REQUEST_TIMEOUT,
                stream=True,
            )
            with self._response_lock:
                if self._closed or request_id != self._active_request_id:
                    response.close()
                    return
                self._active_responses[request_id] = response
            response.raise_for_status()

            payload = bytearray()
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if not self._request_is_current(request_id):
                    return
                if not chunk:
                    continue
                payload.extend(chunk)
                if len(payload) > ANNOUNCEMENT_MAX_BYTES:
                    raise ValueError("公告文件超过 1 MiB 限制")
            raw_text = self._decode_payload(bytes(payload))
            parse_announcement(raw_text)
            self._download_succeeded.emit(request_id, manual, raw_text)
        except Exception as exc:
            if not self._closed:
                self._download_failed.emit(request_id, manual, str(exc))
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            with self._response_lock:
                if self._active_responses.get(request_id) is response:
                    self._active_responses.pop(request_id, None)

    def _on_download_succeeded(self, request_id: int, manual: bool, raw_text: str) -> None:
        if self._closed or request_id != self._active_request_id:
            return
        try:
            document = parse_announcement(raw_text)
        except ValueError as exc:
            self._on_download_failed(request_id, manual, str(exc))
            return

        self._current_document = document
        try:
            write_bytes_atomic(self._cache_path, raw_text.encode("utf-8"))
        except OSError as exc:
            _logger.warning("缓存桌宠公告失败: %s", exc)

        manual_display = bool(manual or self._manual_waiting)
        self._manual_waiting = False
        if manual_display:
            if self._dialog is not None and self._dialog.wants_visible():
                self._dialog.show_document(document)
            return
        if not self._is_suppressed():
            self._ensure_dialog().show_document(document)

    def _on_download_failed(self, request_id: int, manual: bool, message: str) -> None:
        if self._closed or request_id != self._active_request_id:
            return
        _logger.warning("下载桌宠公告失败: %s", message)

        document = self._current_document or self._load_cached_document()
        if document is not None:
            self._current_document = document

        manual_display = bool(manual or self._manual_waiting)
        self._manual_waiting = False
        if manual_display:
            if self._dialog is None or not self._dialog.wants_visible():
                return
            if document is None:
                self._dialog.show_error()
            else:
                self._dialog.show_document(document)
            return
        if document is not None and not self._is_suppressed():
            self._ensure_dialog().show_document(document)

    def _ensure_dialog(self) -> DesktopPetAnnouncementDialog:
        if self._dialog is None:
            self._dialog = DesktopPetAnnouncementDialog()
            self._dialog.suppress_today_requested.connect(self._suppress_today)
            self._dialog.suppress_forever_requested.connect(self._suppress_forever)
            self._dialog.retry_requested.connect(self._retry_from_dialog)
            self._dialog.dismissed.connect(self._on_dialog_dismissed)
        return self._dialog

    def _suppress_today(self) -> None:
        self._preferences = AnnouncementPreferences(
            suppress_forever=False,
            suppress_date=self._today_provider().isoformat(),
        )
        self._save_preferences()
        if self._dialog is not None:
            self._dialog.hide_dialog()

    def _suppress_forever(self) -> None:
        self._preferences = AnnouncementPreferences(
            suppress_forever=True,
            suppress_date=self._preferences.suppress_date,
        )
        self._save_preferences()
        if self._dialog is not None:
            self._dialog.hide_dialog()

    def _retry_from_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.show_loading()
        self._manual_waiting = True
        self._request_download(manual=True)

    def _on_dialog_dismissed(self) -> None:
        self._manual_waiting = False

    def _request_is_current(self, request_id: int) -> bool:
        with self._response_lock:
            return not self._closed and request_id == self._active_request_id

    def _discard_future(self, request_id: int, future) -> None:
        with self._response_lock:
            if self._futures.get(request_id) is future:
                self._futures.pop(request_id, None)

    def _save_preferences(self) -> None:
        try:
            save_announcement_preferences(self._preferences, self._state_path)
        except OSError as exc:
            _logger.warning("保存桌宠公告显示偏好失败: %s", exc)

    def _is_suppressed(self) -> bool:
        self._preferences = load_announcement_preferences(self._state_path)
        return is_announcement_suppressed(self._preferences, self._today_provider())

    def _load_cached_document(self) -> AnnouncementDocument | None:
        try:
            raw_text = self._cache_path.read_text(encoding="utf-8")
            return parse_announcement(raw_text)
        except (OSError, UnicodeError, ValueError):
            return None

    @staticmethod
    def _decode_payload(payload: bytes) -> str:
        if not payload:
            raise ValueError("公告内容为空")
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")
