"""统一二维码窗口基类。"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve, QEvent
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

from config.config import UI
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.graphics.application_visuals import (
    build_qr_panel_visual,
    decode_panel_image,
    qr_panel_size,
    resolve_qr_panel_layout,
)
from lib.core.graphics.image_loader import load_image_resource
from lib.core.graphics.resources import ImageResource
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.qt_bridge.screen import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager

class BaseQrDialog(QWidget):
    """标准二维码浮窗基类。"""

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
        window_flags: int | None = None,
    ) -> None:
        super().__init__()
        flags = window_flags if window_flags is not None else (
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(*qr_panel_size())
        get_layer_manager().register(self, Layer.DIALOG)

        self._visible = False
        self._title = str(title or "").strip()
        self._status = str(status or "").strip()
        self._action_text = str(action_text or "").strip()
        self._placeholder_text = str(placeholder_text or "").strip()
        self._qr_background = bool(qr_background)
        self._status_bold = bool(status_bold)
        self._status_font_size = status_font_size
        self._qr_resource: ImageResource | None = None
        self._draw_backend = QtDrawBackend()

        self._action_btn = QPushButton(self._action_text, self)
        self._action_btn.setFocusPolicy(Qt.NoFocus)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.setAttribute(Qt.WA_Hover, True)
        self._action_btn.installEventFilter(self)
        self._action_btn.clicked.connect(self._on_action_clicked)
        # The child remains the native input adapter; product pixels come from
        # build_qr_panel_visual() in the parent paint pass.
        self._action_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 0px; "
            "color: transparent; padding: 0px; }"
        )

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI["ui_fade_duration"])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._layout_controls()

    def _content_rects(self) -> tuple[QRect, QRect, QRect, QRect, QRect]:
        layout = resolve_qr_panel_layout((self.width(), self.height()))

        def _qrect(rect) -> QRect:
            return QRect(
                int(round(rect.x)),
                int(round(rect.y)),
                int(round(rect.width)),
                int(round(rect.height)),
            )

        return tuple(_qrect(rect) for rect in (
            layout.inner_rect,
            layout.title_rect,
            layout.qr_rect,
            layout.status_rect,
            layout.action_rect,
        ))

    def _layout_controls(self) -> None:
        *_, btn_rect = self._content_rects()
        self._action_btn.setGeometry(btn_rect)

    def _set_dialog_title(self, title: str | None) -> None:
        if title:
            self._title = str(title).strip()

    def _set_dialog_status(self, status: str | None) -> None:
        if status:
            self._status = str(status).strip()

    def _set_qr_pixmap_from_bytes(self, qr_png: bytes | None, *, clear_when_none: bool) -> None:
        if qr_png:
            resource = decode_panel_image(qr_png, resource_prefix="application-qr")
            if resource is not None:
                self._qr_resource = resource
                return
        if clear_when_none:
            self._qr_resource = None

    def _set_qr_pixmap_from_path(self, image_path: str | Path, *, clear_when_missing: bool = True) -> None:
        candidate = Path(image_path)
        if candidate.exists():
            resource = load_image_resource(candidate)
            if resource is not None:
                self._qr_resource = resource
                return
        if clear_when_missing:
            self._qr_resource = None

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

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _disconnect_fade_out_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass

    def _show_dialog(self) -> None:
        self._center_on_screen()
        if not self._visible:
            self._visible = True
            self._disconnect_fade_out_done()
            self.show()
            self._animate(1.0)
        get_layer_manager().enforce_now()
        self.update()

    def hide_dialog(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._disconnect_fade_out_done()
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)

    def _before_hide_widget(self) -> None:
        """子类可在真正 hide 前注入行为。"""

    def _on_fade_out_done(self) -> None:
        self._disconnect_fade_out_done()
        if not self._visible:
            self._before_hide_widget()
            self.hide()

    def _on_action_clicked(self) -> None:
        self.hide_dialog()

    def _action_visual_state(self) -> str:
        if not self._action_btn.isEnabled():
            return "disabled"
        if self._action_btn.isDown():
            return "pressed"
        if self._action_btn.underMouse():
            return "hover"
        return "normal"

    def eventFilter(self, watched, event) -> bool:
        if watched is self._action_btn and event.type() in {
            QEvent.Enter,
            QEvent.Leave,
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.EnabledChange,
        }:
            self.update()
        return super().eventFilter(watched, event)

    def _visual_status_text(self) -> str:
        return self._status

    def _build_panel_visual(self):
        return build_qr_panel_visual(
            self._title,
            self._visual_status_text(),
            self._placeholder_text,
            self._qr_resource,
            size=(self.width(), self.height()),
            layer=int(Layer.DIALOG),
            status_bold=self._status_bold,
            qr_background=self._qr_background,
            status_font_size=self._status_font_size,
            action_text=self._action_text,
            action_state=self._action_visual_state(),
            action_enabled=self._action_btn.isEnabled(),
        )

    def resizeEvent(self, event) -> None:
        self._layout_controls()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        visual = self._build_panel_visual()
        self._draw_backend.render(visual.batch, painter)
        painter.end()
