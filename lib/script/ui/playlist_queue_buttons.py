"""音响队列面板的自绘小件：布局常量、队列删除按钮与立即播放按钮。

批次 3 后续轮次从 `playlist_panel` 切出。队列面板（`PlaylistPanel`）是一个跟随音响的
置顶浮窗，里面夹着两个自绘的小按钮（删除 `x` 与立即播放 `▶`）和一整套尺寸常量。这一轮把
**不依赖面板状态**的那一组先搬出来：尺寸常量（`_WIDTH` / `_ROW_H` / `_PAGE_SIZE` 等）与两个
继承 `SpeakerActionButtonMixin` 的按钮。

切分保持逐行等价：搬出的类与常量与搬出前一致（缩进也未变）。`playlist_panel` 按原名重新
导出这些名字，`PlaylistPanel` 本体与外部导入面（含 `preloader` 只用的 `init/ cleanup/
get_playlist_panel`）不变。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PyQt5.QtGui import QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.layers import WindowLayer, get_layer_manager
from lib.script.ui import render_bridge
from lib.script.ui.speaker_menu_style import (
    SpeakerActionButtonMixin,
    _C_ACTION_TEXT,
)

_WIDTH     = scale_px(240, min_abs=1)  # 固定宽度（xp），与 UI 说明一致
_ROW_H     = scale_px(20, min_abs=1)   # 每行高度（px），与命令提示框保持一致
_LAYER     = scale_px(2, min_abs=1)
_BORDER    = _LAYER * 2  # 单侧边框总厚度（2px 黑 + 2px 青）
_PAD_X     = scale_px(6, min_abs=1)    # 文字水平内边距（px）
_GAP       = scale_px(6, min_abs=1)    # 与音响右边缘的水平间距（px）
_PAGE_SIZE = 7     # 每页最多条目数
_REMOVE_BTN_W = scale_px(20, min_abs=1)
_REMOVE_BTN_H = scale_px(20, min_abs=1)
_AUTO_HIDE_MOUSE_DISTANCE = UI.get('auto_hide_mouse_distance', scale_px(300, min_abs=1))


class _QueueRemoveButton(SpeakerActionButtonMixin, QWidget):
    """队列删除按钮（x）。"""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._pressed = False
        self._visible = False
        self._description = ''
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_REMOVE_BTN_W, _REMOVE_BTN_H)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, WindowLayer.PET_UI)
        self._init_speaker_action_button_state()
        self.setFocusPolicy(Qt.NoFocus)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        ec = get_event_center()
        ec.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def show_btn(self) -> None:
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)

    def hide_btn(self) -> None:
        self._cancel_action_press()
        if not self._visible:
            return
        self._visible = False
        self._anim.finished.connect(self._on_fade_done)
        self._animate(0.0)

    def _on_fade_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_done)
        except (RuntimeError, TypeError):
            pass
        if not self._visible:
            self.hide()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self._begin_action_press()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            commit = self.rect().contains(event.pos())
            if self._finish_action_press(commit=commit):
                if self._callback:
                    self._callback()
        event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        content = self._paint_action_button_shell(p)

        p.setRenderHint(QPainter.Antialiasing, True)
        icon = content.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
        pen = QPen(render_bridge.painter_color(_C_ACTION_TEXT))
        pen.setWidth(_LAYER)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(icon.topLeft(), icon.bottomRight())
        p.drawLine(icon.topRight(), icon.bottomLeft())
        p.end()


class _QueuePlayNowButton(_QueueRemoveButton):
    """队列立即播放按钮（▶）。"""

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        content = self._paint_action_button_shell(p)

        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(render_bridge.painter_color(_C_ACTION_TEXT))
        cx = content.center().x()
        cy = content.center().y()
        half_w = max(3, content.width() // 4)
        half_h = max(3, content.height() // 4)
        p.drawPolygon(QPolygonF([
            QPointF(cx - half_w, cy - half_h),
            QPointF(cx - half_w, cy + half_h),
            QPointF(cx + half_w, cy),
        ]))
        p.end()


__all__ = [
    "_AUTO_HIDE_MOUSE_DISTANCE",
    "_BORDER",
    "_GAP",
    "_LAYER",
    "_PAD_X",
    "_PAGE_SIZE",
    "_QueuePlayNowButton",
    "_QueueRemoveButton",
    "_REMOVE_BTN_H",
    "_REMOVE_BTN_W",
    "_ROW_H",
    "_WIDTH",
]
