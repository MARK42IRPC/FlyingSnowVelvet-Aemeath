"""网易云二维码登录面板（当前屏幕居中显示）。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect, QPushButton
from PyQt5.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve, QTimer
from PyQt5.QtGui import QPainter, QPixmap, QFontMetrics, QCursor

from config.config import UI, UI_THEME
from config.font_config import get_ui_font, get_digit_font, draw_mixed_text, wrap_mixed_text
from config.scale import scale_px, scale_style_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.anchor_utils import apply_ui_opacity


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

_C_BORDER = UI_THEME['border']
_C_MID = UI_THEME['mid']
_C_BG = UI_THEME['bg']
_C_TEXT = UI_THEME['text']


class CloudMusicLoginDialog(QWidget):
    """显示网易云扫码登录二维码的独立浮窗。"""

    def __init__(self) -> None:
        super().__init__()
        flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if hasattr(Qt, 'WindowDoesNotAcceptFocus'):
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(_WIDTH, _HEIGHT)
        get_topmost_manager().register(self)

        self._visible: bool = False
        self._title: str = '音乐扫码登录'
        self._status: str = '请使用音乐App扫码登录'
        self._qr_pixmap: QPixmap | None = None
        self._refresh_left: int | None = None
        self._allow_hide_once: bool = False
        self._allow_close_once: bool = False

        self._title_font = get_ui_font()
        self._title_font.setBold(True)
        self._status_font = get_ui_font()
        self._status_font.setBold(True)
        self._digit_font = get_digit_font()

        self._cancel_btn = QPushButton('退出扫码', self)
        self._cancel_btn.setFocusPolicy(Qt.NoFocus)
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setFont(get_ui_font())
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self._cancel_btn.setStyleSheet(scale_style_px(
            "QPushButton {"
            f"background: rgb({_C_BG.red()}, {_C_BG.green()}, {_C_BG.blue()});"
            f"border: 2px solid rgb({_C_BORDER.red()}, {_C_BORDER.green()}, {_C_BORDER.blue()});"
            f"color: rgb({_C_TEXT.red()}, {_C_TEXT.green()}, {_C_TEXT.blue()});"
            "font-weight: bold;"
            "padding: 2px 6px;"
            "}"
            "QPushButton:hover {"
            "background: rgb(255, 200, 210);"
            "}"
            "QPushButton:pressed {"
            "background: rgb(255, 170, 190);"
            "}"
        ))

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.subscribe(EventType.MUSIC_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
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
        self._cancel_btn.setGeometry(btn_rect)

    def _draw_wrapped_mixed_text(self, painter: QPainter, rect: QRect, text: str, align: int) -> None:
        """在给定区域内绘制自动换行混排文本（数字使用拉海洛字体）。"""
        lines = wrap_mixed_text(text, rect.width(), self._status_font, self._digit_font)
        if not lines:
            return

        fm_def = QFontMetrics(self._status_font)
        fm_dig = QFontMetrics(self._digit_font)
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
                self._status_font,
                self._digit_font,
                h_align | int(Qt.AlignVCenter),
            )
            y += line_h

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

    def show_dialog(self, qr_png: bytes | None, status: str = '', title: str = '') -> None:
        if qr_png:
            pix = QPixmap()
            if pix.loadFromData(qr_png, 'PNG') or pix.loadFromData(qr_png):
                self._qr_pixmap = pix
        elif not self._visible:
            self._qr_pixmap = None
        if title:
            self._title = title
        if status:
            self._status = status
        self._center_on_screen()
        if not self._visible:
            self._visible = True
            try:
                self._anim.finished.disconnect(self._on_fade_out_done)
            except (RuntimeError, TypeError):
                pass
            self.show()
            self._animate(1.0)
        self.update()

    def hide_dialog(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_fade_out_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        if not self._visible:
            self._allow_hide_once = True
            self.hide()

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
            qr_png=event.data.get('qr_png'),
            status=event.data.get('status', '请使用音乐App扫码登录'),
            title=event.data.get('title', '音乐扫码登录'),
        )

    def _on_qr_status(self, event: Event) -> None:
        self._status = str(event.data.get('status', self._status))
        refresh_left = event.data.get('refresh_left')
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _on_cancel_clicked(self) -> None:
        self._event_center.publish(Event(EventType.MUSIC_LOGIN_CANCEL_REQUEST, {}))

    def resizeEvent(self, event) -> None:
        self._layout_controls()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        painter.fillRect(self.rect(), _C_BORDER)
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), _C_MID)
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), _C_BG)

        inner, title_rect, qr_rect, status_rect, _ = self._content_rects()
        painter.setPen(_C_TEXT)
        painter.setFont(self._title_font)
        painter.drawText(title_rect, Qt.AlignCenter, self._title)

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
            painter.drawText(qr_rect, Qt.AlignCenter, '二维码加载中...')

        painter.setPen(_C_TEXT)
        painter.setFont(self._status_font)
        if self._refresh_left is not None and '等待扫码' in self._status:
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
                f'二维码将于 {self._refresh_left}s 后刷新',
                Qt.AlignCenter,
            )
        else:
            self._draw_wrapped_mixed_text(
                painter,
                status_rect,
                self._status,
                Qt.AlignCenter | Qt.TextWordWrap,
            )

        painter.end()

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_SHOW, self._on_qr_show)
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_STATUS, self._on_qr_status)
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_QR_HIDE, self._on_qr_hide)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)


_instance: 'CloudMusicLoginDialog | None' = None


def get_cloudmusic_login_dialog() -> 'CloudMusicLoginDialog | None':
    return _instance


def init_cloudmusic_login_dialog() -> 'CloudMusicLoginDialog':
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
