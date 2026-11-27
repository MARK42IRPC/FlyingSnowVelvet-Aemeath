"""Product adapter for the announcement service and its reusable popup."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import requests

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
from lib.core.render.visuals.announcement_chrome import text_paragraph
from lib.core.render.visuals.announcement_visuals import ANNOUNCEMENT_SIZE
from lib.core.render.visuals.window_specs import (
    ANNOUNCEMENT_CLOSE,
    ANNOUNCEMENT_RETRY,
    ANNOUNCEMENT_SUPPRESS_FOREVER,
    ANNOUNCEMENT_SUPPRESS_TODAY,
    ANNOUNCEMENT_UPDATE,
    announcement_window_spec,
)
from lib.script.ui import render_bridge

__all__ = [
    "AnnouncementBlock",
    "AnnouncementPreferences",
    "is_announcement_suppressed",
    "load_announcement_preferences",
    "parse_announcement",
    "save_announcement_preferences",
    "set_announcement_forever_suppressed",
]


def announcement_to_html(document: AnnouncementDocument) -> str:
    """Render an announcement document as escaped, presentation-only HTML."""
    return text_paragraph(document.title, "h1") + _announcement_body_to_html(document)


def _announcement_body_to_html(document: AnnouncementDocument) -> str:
    return "".join(
        text_paragraph(block.text, "h2" if block.kind == "subtitle" else "p")
        for block in document.blocks
    )


_DEFAULT_TITLE = "桌宠公告"
_ACTION_ROW_IDS = ("channel", "today", "forever", "update", "error_close", "retry")


class _AnnouncementSignal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot=None) -> None:
        if slot is None:
            self._slots.clear()
        else:
            try:
                self._slots.remove(slot)
            except ValueError:
                pass

    def emit(self, *args) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class DesktopPetAnnouncementDialog:
    follows_workbench_theme = False

    def __init__(self, parent=None) -> None:
        self.suppress_today_requested = _AnnouncementSignal()
        self.suppress_forever_requested = _AnnouncementSignal()
        self.retry_requested = _AnnouncementSignal()
        self.update_requested = _AnnouncementSignal()
        self.dismissed = _AnnouncementSignal()
        self._requested_visible = False
        self._title = "桌宠公告"
        self._html = ""
        self._mode = "announcement"
        self._update_available = False
        self._spec = self._build_spec()
        self._window = render_bridge.create_spec_window(self._spec, on_semantic=self._on_semantic, parent=parent)
        self._header_label = self._window.find("header")
        self._source_label = self._window.find("source")
        self._channel_label = self._window.find("channel")
        self._minimize_button = self._window.find("minimize")
        self._close_button = self._window.find("close")
        self._body = self._window.find("body")
        self._today_button = self._window.find("today")
        self._forever_button = self._window.find("forever")
        self._update_button = self._window.find("update")
        self._retry_button = self._window.find("retry")
        self._error_close_button = self._window.find("error_close")
        self._set_action_mode("announcement")

    def _build_spec(self):
        width, height = ANNOUNCEMENT_SIZE.width, ANNOUNCEMENT_SIZE.height
        return announcement_window_spec(
            title=self._title, html=self._html, width=width, height=height,
        )

    def show_document(self, document: AnnouncementDocument) -> None:
        self.refresh_workbench_theme()
        self._title = document.title or "桌宠公告"
        self._html = _announcement_body_to_html(document)
        self._update_available = document.has_update()
        self._header_label.setText(self._title)
        self._window.set_html("body", self._html)
        self._set_action_mode("announcement")
        self._show_dialog()

    def show_loading(self) -> None:
        self.refresh_workbench_theme()
        self._update_available = False
        self._header_label.setText("桌宠公告")
        self._window.set_html(
            "body",
            text_paragraph("正在获取公告", "h2") + text_paragraph("正在连接公告服务器，请稍候。"),
        )
        self._set_action_mode("loading")
        self._show_dialog()

    def show_error(self) -> None:
        self.refresh_workbench_theme()
        self._update_available = False
        self._header_label.setText("桌宠公告")
        self._window.set_html(
            "body",
            text_paragraph("公告暂时无法加载", "h2") + text_paragraph("没有可用的本地公告，请稍后重试。"),
        )
        self._set_action_mode("error")
        self._show_dialog()

    def wants_visible(self) -> bool:
        return self._window.is_requested_visible()

    def refresh_workbench_theme(self) -> None:
        self._spec = self._build_spec()
        self._window.apply_theme(self._spec)

    def floating_stylesheet(self) -> str:
        return self._spec.stylesheet

    def hide_dialog(self) -> None:
        self._requested_visible = False
        self._window.hide_dialog()

    def cleanup(self) -> None:
        self._requested_visible = False
        self._window.cleanup()

    def move(self, *args) -> None:
        self._window.widget.move(*args)

    def raise_(self) -> None:
        self._window.widget.raise_()

    def activateWindow(self) -> None:
        self._window.widget.activateWindow()

    def deleteLater(self) -> None:
        self._window.cleanup()

    def minimumWidth(self) -> int:
        return self._window.widget.minimumWidth()

    def width(self) -> int:
        return self._window.widget.width()

    def height(self) -> int:
        return self._window.widget.height()

    def isVisible(self) -> bool:
        return self._window.widget.isVisible()

    def styleSheet(self) -> str:
        return self._window.widget.styleSheet()

    def _set_action_mode(self, mode: str) -> None:
        self._mode = str(mode)
        is_announcement = self._mode == "announcement"
        is_error = self._mode == "error"
        if self._channel_label is not None:
            self._channel_label.setVisible(is_announcement)
        for button, visible in (
            (self._today_button, is_announcement), (self._forever_button, is_announcement),
            (self._update_button, is_announcement and self._update_available),
            (self._error_close_button, is_error), (self._retry_button, is_error),
        ):
            if button is not None:
                button.setVisible(visible)

    def _show_dialog(self) -> None:
        self._requested_visible = True
        self._window.show_window()

    def _on_semantic(self, semantic: str) -> None:
        if semantic == ANNOUNCEMENT_SUPPRESS_TODAY:
            self.suppress_today_requested.emit()
        elif semantic == ANNOUNCEMENT_SUPPRESS_FOREVER:
            self.suppress_forever_requested.emit()
        elif semantic == ANNOUNCEMENT_UPDATE:
            self.update_requested.emit()
        elif semantic == ANNOUNCEMENT_RETRY:
            self.retry_requested.emit()
        elif semantic == ANNOUNCEMENT_CLOSE:
            self.dismissed.emit()
            self.hide_dialog()


class AnnouncementController:
    POLL_INTERVAL_MS = 100

    def __init__(self, application=None, *, event_center=None, update_opener: Callable | None = None,
                 on_update: Callable | None = None, **kwargs) -> None:
        from lib.core.render.backends.qt.widgets.ui_dispatch import UiDispatcher

        self._application = application
        self._closed = False
        self._dialog = None
        self._request_generation = 0
        self._dispatcher = UiDispatcher(application)
        self._event_center = event_center or get_event_center()
        self._update_opener = update_opener or on_update
        self._preferences = load_announcement_preferences(kwargs.get("state_path"))
        self._state_path = kwargs.get("state_path")
        self._tray_connected = False
        self._service = AnnouncementService(
            dispatch=self._dispatch,
            on_loading=self._on_loading,
            on_document=self._on_document,
            on_error=self._on_error,
            on_hide=self._on_hide,
            state_path=kwargs.get("state_path"),
            cache_path=kwargs.get("cache_path"),
            today_provider=kwargs.get("today_provider", date.today),
            submit_io=kwargs.get("submit_io") or (
                lambda func, *args: (kwargs.get("compute_hub") or get_compute_hub()).submit_io(func, *args)
            ),
            request_get=kwargs.get("request_get") or (
                lambda *args, **options: requests.get(*args, **options)
            ),
        )
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        if self._application is not None and hasattr(self._application, "announcement_requested"):
            self._application.announcement_requested.connect(self.open_from_tray)
            self._tray_connected = True


    def _dispatch(self, callback: Callable[[], None]) -> None:
        if not self._closed:
            self._dispatcher.post(callback)

    def dispatch(self, callback: Callable[[], None]) -> None:
        self._dispatch(callback)

    def start(self) -> bool:
        return self._service.start()

    def refresh(self) -> None:
        self._service.retry()

    def open_from_tray(self) -> None:
        if not self._closed:
            self._service.open_manual()

    def _on_config_updated(self, event) -> None:
        values = getattr(event, "data", None)
        if isinstance(values, dict):
            ui = values.get("values", {}).get("UI", {})
            if "workbench_light_theme" not in ui:
                return
        if self._dialog is not None:
            self._dialog.refresh_workbench_theme()

    def _ensure_dialog(self):
        if self._dialog is None:
            self._dialog = DesktopPetAnnouncementDialog(None)
            self._dialog.dismissed.connect(self._on_dismissed)
            self._dialog.suppress_today_requested.connect(self._suppress_today)
            self._dialog.suppress_forever_requested.connect(self._suppress_forever)
            self._dialog.retry_requested.connect(self.open_from_tray)
            self._dialog.update_requested.connect(self._open_update)
        return self._dialog

    def _on_loading(self) -> None:
        self._ensure_dialog().show_loading()

    def _on_document(self, document, manual: bool) -> None:
        dialog = self._ensure_dialog()
        if not manual and is_announcement_suppressed(self._preferences):
            return
        dialog.show_document(document)

    def _on_error(self, manual: bool) -> None:
        if manual and self._dialog is not None and self._dialog.wants_visible():
            self._dialog.show_error()

    def _on_hide(self) -> None:
        self._hide_dialog()

    def _open_update(self) -> None:
        self._hide_dialog()
        if self._update_opener is not None:
            self._update_opener()

    def _on_dismissed(self) -> None:
        self._service.dismiss()
        self._hide_dialog()

    def _hide_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.hide_dialog()

    def _suppress_today(self) -> None:
        self._service.suppress_today()
        self._preferences = load_announcement_preferences()
        self._on_dismissed()

    def _suppress_forever(self) -> None:
        self._service.suppress_forever()
        self._preferences = load_announcement_preferences()
        self._on_dismissed()

    def _request_update(self) -> None:
        self._on_dismissed()
        if self._update_opener is not None:
            self._update_opener()

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dispatcher.clear()
        self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        if self._tray_connected:
            self._application.announcement_requested.disconnect(self.open_from_tray)
            self._tray_connected = False
        self._service.cleanup()
        if self._dialog is not None:
            self._dialog.cleanup()
            self._dialog = None
