"""元宝二维码登录面板（居中浮窗）。"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer

from lib.core.event.center import Event, EventType, get_event_center
from lib.script.ui.qr_dialog_base import BaseQrDialog


class YuanbaoLoginDialog(BaseQrDialog):
    """显示元宝扫码登录二维码的独立浮窗。"""

    def __init__(self) -> None:
        super().__init__(
            title="元宝扫码登录",
            status="请使用微信扫码登录元宝",
            action_text="关闭窗口",
            placeholder_text="二维码准备中...",
            qr_background=True,
        )
        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.hide_dialog)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.YUANBAO_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.subscribe(EventType.YUANBAO_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.subscribe(EventType.YUANBAO_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def show_dialog(self, qr_png: bytes | None, status: str = "", title: str = "") -> None:
        self._auto_close_timer.stop()
        self._set_qr_pixmap_from_bytes(qr_png, clear_when_none=True)
        self._set_dialog_title(title)
        self._set_dialog_status(status)
        self._show_dialog()

    def hide_dialog(self) -> None:
        self._auto_close_timer.stop()
        super().hide_dialog()

    def _on_qr_show(self, event: Event) -> None:
        self.show_dialog(
            qr_png=event.data.get("qr_png"),
            status=event.data.get("status", "请使用微信扫码登录元宝"),
            title=event.data.get("title", "元宝扫码登录"),
        )

    def _on_qr_status(self, event: Event) -> None:
        self._set_qr_pixmap_from_bytes(event.data.get("qr_png"), clear_when_none=True)
        logged_in = bool(event.data.get("logged_in"))
        self._status = str(event.data.get("status", self._status))
        if logged_in:
            self._status = "元宝登录成功，即将自动关闭…"
            self._event_center.publish(Event(EventType.INFORMATION, {
                "text": "元宝登录成功，已自动关闭二维码窗口。",
                "min": 10,
                "max": 120,
                "particle": False,
            }))
            if not self._visible:
                self.show_dialog(qr_png=event.data.get("qr_png"), status=self._status, title=self._title)
            self._auto_close_timer.start(1200)
        if self._visible:
            self.update()

    def _on_qr_hide(self, event: Event) -> None:
        if self._auto_close_timer.isActive():
            return
        self.hide_dialog()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get("enabled", False))

    def _on_action_clicked(self) -> None:
        self._event_center.publish(Event(EventType.YUANBAO_LOGIN_QR_HIDE, {}))

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.YUANBAO_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.unsubscribe(EventType.YUANBAO_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.unsubscribe(EventType.YUANBAO_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)


_instance: "YuanbaoLoginDialog | None" = None


def get_yuanbao_login_dialog() -> "YuanbaoLoginDialog | None":
    return _instance


def init_yuanbao_login_dialog() -> "YuanbaoLoginDialog":
    global _instance
    if _instance is None:
        _instance = YuanbaoLoginDialog()
    return _instance


def cleanup_yuanbao_login_dialog() -> None:
    global _instance
    if _instance is not None:
        try:
            _instance.cleanup()
            _instance.close()
        except Exception:
            pass
        _instance = None
