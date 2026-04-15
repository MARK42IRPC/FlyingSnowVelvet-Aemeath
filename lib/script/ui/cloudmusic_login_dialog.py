"""网易云二维码登录面板（当前屏幕居中显示）。"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import QPainter

from config.config import UI_THEME
from config.scale import scale_px
from lib.core.event.center import Event, EventType, get_event_center
from lib.script.ui.qr_dialog_base import BaseQrDialog


class CloudMusicLoginDialog(BaseQrDialog):
    """显示网易云扫码登录二维码的独立浮窗。"""

    def __init__(self) -> None:
        flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        super().__init__(
            title="音乐扫码登录",
            status="请使用音乐App扫码登录",
            action_text="退出扫码",
            placeholder_text="二维码加载中...",
            qr_background=True,
            window_flags=flags,
        )
        self._refresh_left: int | None = None
        self._allow_hide_once = False
        self._allow_close_once = False

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def show_dialog(self, qr_png: bytes | None, status: str = "", title: str = "") -> None:
        self._set_qr_pixmap_from_bytes(qr_png, clear_when_none=not self._visible)
        self._set_dialog_title(title)
        self._set_dialog_status(status)
        self._show_dialog()

    def _before_hide_widget(self) -> None:
        self._allow_hide_once = True

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._allow_hide_once:
            self._allow_hide_once = False
            return
        if self._visible:
            QTimer.singleShot(0, self._restore_if_needed)

    def closeEvent(self, event) -> None:
        if self._allow_close_once:
            self._allow_close_once = False
            super().closeEvent(event)
            return
        if self._visible:
            event.ignore()
            QTimer.singleShot(0, self._restore_if_needed)
            return
        super().closeEvent(event)

    def _restore_if_needed(self) -> None:
        if not self._visible:
            return
        try:
            self.show()
            self.raise_()
        except Exception:
            return

    def _on_qr_show(self, event: Event) -> None:
        self.show_dialog(
            qr_png=event.data.get("qr_png"),
            status=event.data.get("status", "请使用音乐App扫码登录"),
            title=event.data.get("title", "音乐扫码登录"),
        )

    def _on_qr_status(self, event: Event) -> None:
        self._status = str(event.data.get("status", self._status))
        refresh_left = event.data.get("refresh_left")
        if refresh_left is None:
            self._refresh_left = None
        else:
            try:
                self._refresh_left = max(0, int(refresh_left))
            except Exception:
                self._refresh_left = None
        if self._visible:
            self.update()

    def _on_qr_hide(self, event: Event) -> None:
        self.hide_dialog()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get("enabled", False))

    def _on_action_clicked(self) -> None:
        self._event_center.publish(Event(EventType.MUSIC_LOGIN_CANCEL_REQUEST, {}))

    def _paint_status(self, painter: QPainter, status_rect: QRect) -> None:
        painter.setPen(UI_THEME["text"])
        if self._refresh_left is not None and "等待扫码" in self._status:
            top_h = max(scale_px(24, min_abs=1), status_rect.height() - scale_px(24, min_abs=1))
            status_main_rect = QRect(
                status_rect.x(),
                status_rect.y(),
                status_rect.width(),
                top_h,
            )
            countdown_rect = QRect(
                status_rect.x(),
                status_main_rect.bottom() + scale_px(1, min_abs=1),
                status_rect.width(),
                max(scale_px(20, min_abs=1), status_rect.height() - top_h),
            )
            self._draw_wrapped_mixed_text(
                painter,
                status_main_rect,
                self._status,
                Qt.AlignCenter | Qt.TextWordWrap,
            )
            self._draw_wrapped_mixed_text(
                painter,
                countdown_rect,
                f"二维码将于 {self._refresh_left}s 后刷新",
                Qt.AlignCenter,
            )
            return
        super()._paint_status(painter, status_rect)

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)


_instance: "CloudMusicLoginDialog | None" = None


def get_cloudmusic_login_dialog() -> "CloudMusicLoginDialog | None":
    return _instance


def init_cloudmusic_login_dialog() -> "CloudMusicLoginDialog":
    global _instance
    if _instance is None:
        _instance = CloudMusicLoginDialog()
    return _instance


def cleanup_cloudmusic_login_dialog() -> None:
    global _instance
    if _instance is not None:
        try:
            _instance.cleanup()
            _instance._allow_close_once = True
            _instance.close()
        except Exception:
            pass
        _instance = None
