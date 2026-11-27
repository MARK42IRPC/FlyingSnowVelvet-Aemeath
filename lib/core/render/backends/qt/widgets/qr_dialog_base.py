"""Qt 扫码浮窗：公告外壳内挂载共用的二维码绘制内容。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import QSize, QTimer, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget

from config.font_config import FONT
from config.scale import scale_px
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.spec_host import QtSpecWidget, build_spec_window
from lib.core.render.visuals.announcement_chrome import (
    announcement_panel_body,
    announcement_shell_spec,
)
from lib.core.render.visuals.application_visuals import (
    build_qr_image_batch,
    decode_panel_image,
    qr_image_side,
    qr_panel_size,
)
from lib.core.render.visuals.types import Rect
from lib.core.render.visuals.window_spec import ALIGN_HCENTER, ALIGN_VCENTER, ButtonSpec, LabelSpec


_ACTION = "qr_action"
_CLOSE = "qr_close"
_MINIMIZE = "qr_minimize"


class _QrImage(QWidget):
    """Render the shared QR image or placeholder in the announcement body."""

    def __init__(self, parent: QWidget, placeholder: str, background: bool) -> None:
        super().__init__(parent)
        self.setObjectName("QrImage")
        self._side = qr_image_side()
        self.setFixedSize(self._side, self._side)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resource = None
        self._placeholder = placeholder
        self._background = background
        self._draw_backend = tier_d_seams.create_draw_backend()

    def sizeHint(self) -> QSize:
        return QSize(self._side, self._side)

    def paintEvent(self, event) -> None:
        side = min(self.width(), self.height())
        if side <= 0:
            return
        batch = build_qr_image_batch(
            Rect((self.width() - side) // 2, (self.height() - side) // 2, side, side),
            self._placeholder,
            self.resource,
            qr_background=self._background,
        )
        painter = QPainter(self)
        try:
            self._draw_backend.render(batch, painter)
        finally:
            painter.end()


class BaseQrDialog(QtSpecWidget):
    """Reusable non-modal QR window with a product-owned action callback."""

    def __init__(
        self,
        *,
        title: str,
        status: str,
        action_text: str,
        placeholder_text: str,
        qr_background: bool = True,
        status_font_size: int | None = None,
        status_bold: bool = True,
        window_flags: int | str | None = None,
    ) -> None:
        super().__init__()
        self._title = str(title or "").strip()
        self._status = str(status or "").strip()
        self._avoid_activation = window_flags == "login"
        action_text = str(action_text or "").strip()
        body = announcement_panel_body((LabelSpec(
            self._status,
            id="status",
            font_size=status_font_size or int(FONT.get("ui_size", 12)),
            object_name="AnnouncementStatus",
            bold=status_bold, word_wrap=True, plain_text=True,
            align=ALIGN_HCENTER, valign=ALIGN_VCENTER, stretch=1,
        ),))
        # Fit the unchanged scan field and two lines of status inside the compact QR window.
        padding = scale_px(5, min_abs=1)
        body = replace(body, margin=(padding,) * 4, spacing=scale_px(4, min_abs=1))
        width, height = qr_panel_size()
        spec = announcement_shell_spec(
            object_name=type(self).__name__,
            title=self._title,
            body=body,
            actions=(ButtonSpec(action_text, _ACTION, id="action"),) if action_text else (),
            width=width,
            height=height,
            close_action=_CLOSE,
            close_tooltip=action_text or "关闭",
            minimize_action=_MINIMIZE,
            non_closing_actions=(_ACTION, _CLOSE, _MINIMIZE),
        )
        window = build_spec_window(
            spec,
            widget=self,
            font_factory=tier_d_seams.ui_font,
            presentation_host=tier_d_seams.presentation_host(),
            on_semantic=self._on_semantic,
        )
        if self._avoid_activation:
            self.setWindowFlags(
                Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        elif window_flags is not None:
            self.setWindowFlags(Qt.WindowFlags(int(window_flags)))
        self.setFocusPolicy(Qt.NoFocus)
        for node_id in ("minimize", "close", "action"):
            button = window.find(node_id)
            if button is not None:
                button.setFocusPolicy(Qt.NoFocus)
        self._header_label = window.find("header")
        self._header_label.setTextFormat(Qt.PlainText)
        self._status_label = window.find("status")
        body_widget = window.find("body")
        self._qr_image = _QrImage(body_widget, str(placeholder_text or "").strip(), qr_background)
        body_widget.layout().insertWidget(0, self._qr_image, stretch=0, alignment=Qt.AlignHCenter)

        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.hide_dialog)

    @property
    def _visible(self) -> bool:
        return self._spec_window.is_requested_visible()

    def _set_dialog_title(self, title: str | None) -> None:
        if title:
            self._title = str(title).strip()
            self.setWindowTitle(self._title)
            self._header_label.setText(self._title)

    def _set_dialog_status(self, status: str | None) -> None:
        if status:
            self._status = str(status).strip()

    def _refresh_status(self) -> None:
        self._status_label.setText(self._visual_status_text())

    def _set_qr_pixmap_from_bytes(self, qr_png: bytes | None, *, clear_when_none: bool) -> None:
        resource = decode_panel_image(qr_png, resource_prefix="application-qr")
        if resource is not None or clear_when_none:
            self._qr_image.resource = resource
            self._qr_image.update()

    def _set_qr_pixmap_from_path(self, image_path: str | Path) -> None:
        try:
            qr_png = Path(image_path).read_bytes()
        except OSError:
            qr_png = None
        self._set_qr_pixmap_from_bytes(qr_png, clear_when_none=True)

    def _show_dialog(self) -> None:
        self._refresh_status()
        self._spec_window.center_on()
        self._spec_window.show_window(activate=not self._avoid_activation)

    def hide_dialog(self) -> None:
        self._auto_close_timer.stop()
        self._spec_window.hide_dialog(immediate=not self.isVisible())

    def set_clickthrough(self, enabled: bool) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))

    def restore_soon(self) -> None:
        QTimer.singleShot(0, self._restore_if_needed)

    def _restore_if_needed(self) -> None:
        if not sip.isdeleted(self) and self._visible and not self.isVisible():
            self._spec_window.show_window(activate=False)

    def closeEvent(self, event) -> None:
        if self._visible:
            event.ignore()
            self.hide_dialog()
            return
        super().closeEvent(event)

    def _on_semantic(self, semantic: str) -> None:
        if semantic == _MINIMIZE:
            self.hide_dialog()
        elif semantic in (_ACTION, _CLOSE):
            self._on_action_clicked()

    def _on_action_clicked(self) -> None:
        self.hide_dialog()

    def _visual_status_text(self) -> str:
        return self._status

    def cleanup(self) -> None:
        self._auto_close_timer.stop()
        self._spec_window.cleanup()


__all__ = ["BaseQrDialog"]
