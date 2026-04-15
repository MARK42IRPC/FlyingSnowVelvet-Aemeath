"""统一二维码窗口基类。"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QCursor, QFontMetrics, QPainter, QPixmap
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

from config.config import UI, UI_THEME
from config.font_config import draw_mixed_text, get_digit_font, get_ui_font, wrap_mixed_text
from config.scale import scale_px, scale_style_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.topmost_manager import get_topmost_manager

_WIDTH = scale_px(320, min_abs=1)
_HEIGHT = scale_px(430, min_abs=1)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2
_TITLE_H = scale_px(36, min_abs=1)
_STATUS_H = scale_px(80, min_abs=1)
_QR_SIZE = scale_px(240, min_abs=1)
_STATUS_GAP = scale_px(8, min_abs=1)
_BTN_W = scale_px(132, min_abs=1)
_BTN_H = scale_px(30, min_abs=1)
_BTN_BOTTOM = scale_px(12, min_abs=1)

_C_BORDER = UI_THEME["border"]
_C_MID = UI_THEME["mid"]
_C_BG = UI_THEME["bg"]
_C_TEXT = UI_THEME["text"]


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
        self.setFixedSize(_WIDTH, _HEIGHT)
        get_topmost_manager().register(self)

        self._visible = False
        self._title = str(title or "").strip()
        self._status = str(status or "").strip()
        self._placeholder_text = str(placeholder_text or "").strip()
        self._qr_background = bool(qr_background)
        self._qr_pixmap: QPixmap | None = None

        self._title_font = get_ui_font()
        self._title_font.setBold(True)
        self._status_font = (
            get_ui_font(size=status_font_size)
            if status_font_size is not None
            else get_ui_font()
        )
        self._status_font.setBold(bool(status_bold))
        self._digit_font = get_digit_font()

        self._action_btn = QPushButton(str(action_text or "").strip(), self)
        self._action_btn.setFocusPolicy(Qt.NoFocus)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.setFont(get_ui_font())
        self._action_btn.clicked.connect(self._on_action_clicked)
        self._action_btn.setStyleSheet(scale_style_px(
            "QPushButton {"
            f"background: rgb({_C_BG.red()}, {_C_BG.green()}, {_C_BG.blue()});"
            f"border: 2px solid rgb({_C_BORDER.red()}, {_C_BORDER.green()}, {_C_BORDER.blue()});"
            f"color: rgb({_C_TEXT.red()}, {_C_TEXT.green()}, {_C_TEXT.blue()});"
            "font-weight: bold;"
            "padding: 2px 6px;"
            "}"
            "QPushButton:hover {background: rgb(255, 200, 210);}"
            "QPushButton:pressed {background: rgb(255, 170, 190);}"
        ))

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI["ui_fade_duration"])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._layout_controls()

    def _content_rects(self) -> tuple[QRect, QRect, QRect, QRect, QRect]:
        inner = self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER)
        title_rect = QRect(inner.x(), inner.y(), inner.width(), _TITLE_H)

        qr_x = inner.x() + (inner.width() - _QR_SIZE) // 2
        qr_y = title_rect.bottom() + scale_px(10, min_abs=1)
        qr_rect = QRect(qr_x, qr_y, _QR_SIZE, _QR_SIZE)

        btn_rect = QRect(
            inner.x() + (inner.width() - _BTN_W) // 2,
            inner.bottom() - _BTN_BOTTOM - _BTN_H + 1,
            _BTN_W,
            _BTN_H,
        )

        status_top = qr_rect.bottom() + scale_px(10, min_abs=1)
        status_bottom = btn_rect.y() - _STATUS_GAP
        status_h = max(scale_px(24, min_abs=1), min(_STATUS_H, status_bottom - status_top))
        status_rect = QRect(
            inner.x() + scale_px(10, min_abs=1),
            status_top,
            inner.width() - scale_px(20, min_abs=1),
            status_h,
        )
        return inner, title_rect, qr_rect, status_rect, btn_rect

    def _layout_controls(self) -> None:
        *_, btn_rect = self._content_rects()
        self._action_btn.setGeometry(btn_rect)

    def _draw_wrapped_mixed_text(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        align: int,
        *,
        font=None,
        digit_font=None,
    ) -> None:
        draw_font = font or self._status_font
        draw_digit_font = digit_font or self._digit_font
        lines = wrap_mixed_text(text, rect.width(), draw_font, draw_digit_font)
        if not lines:
            return

        fm_def = QFontMetrics(draw_font)
        fm_dig = QFontMetrics(draw_digit_font)
        line_h = max(fm_def.height(), fm_dig.height())
        total_h = line_h * len(lines)
        y = rect.y() + (rect.height() - total_h) // 2

        h_align = align & int(Qt.AlignLeft | Qt.AlignHCenter | Qt.AlignRight)
        if not h_align:
            h_align = int(Qt.AlignHCenter)

        for line in lines:
            line_rect = QRect(rect.x(), y, rect.width(), line_h)
            draw_mixed_text(
                painter,
                line_rect,
                line,
                draw_font,
                draw_digit_font,
                h_align | int(Qt.AlignVCenter),
            )
            y += line_h

    def _set_dialog_title(self, title: str | None) -> None:
        if title:
            self._title = str(title).strip()

    def _set_dialog_status(self, status: str | None) -> None:
        if status:
            self._status = str(status).strip()

    def _set_qr_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._qr_pixmap = None
            return
        self._qr_pixmap = pixmap

    def _set_qr_pixmap_from_bytes(self, qr_png: bytes | None, *, clear_when_none: bool) -> None:
        if qr_png:
            pix = QPixmap()
            if pix.loadFromData(qr_png, "PNG") or pix.loadFromData(qr_png):
                self._qr_pixmap = pix
                return
        if clear_when_none:
            self._qr_pixmap = None

    def _set_qr_pixmap_from_path(self, image_path: str | Path, *, clear_when_missing: bool = True) -> None:
        candidate = Path(image_path)
        if candidate.exists():
            pixmap = QPixmap(str(candidate))
            if not pixmap.isNull():
                self._qr_pixmap = pixmap
                return
        if clear_when_missing:
            self._qr_pixmap = None

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

    def _paint_status(self, painter: QPainter, status_rect: QRect) -> None:
        if not self._status:
            return
        painter.setPen(_C_TEXT)
        painter.setFont(self._status_font)
        self._draw_wrapped_mixed_text(
            painter,
            status_rect,
            self._status,
            Qt.AlignCenter | Qt.TextWordWrap,
        )

    def resizeEvent(self, event) -> None:
        self._layout_controls()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        painter.fillRect(self.rect(), _C_BORDER)
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), _C_MID)
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), _C_BG)

        _, title_rect, qr_rect, status_rect, _ = self._content_rects()
        painter.setPen(_C_TEXT)
        painter.setFont(self._title_font)
        painter.drawText(title_rect, Qt.AlignCenter, self._title)

        if self._qr_background:
            painter.fillRect(qr_rect, Qt.white)
        if self._qr_pixmap is not None and not self._qr_pixmap.isNull():
            scaled = self._qr_pixmap.scaled(
                qr_rect.width(),
                qr_rect.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            px = qr_rect.x() + (qr_rect.width() - scaled.width()) // 2
            py = qr_rect.y() + (qr_rect.height() - scaled.height()) // 2
            painter.drawPixmap(px, py, scaled)
        else:
            painter.setPen(_C_TEXT)
            painter.drawText(qr_rect, Qt.AlignCenter, self._placeholder_text)

        self._paint_status(painter, status_rect)
        painter.end()
