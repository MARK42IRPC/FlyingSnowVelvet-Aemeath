"""音响队列面板的自绘小件：布局常量、队列删除按钮与立即播放按钮（档位 D 宿主）。

批次 3 后续轮次从 `playlist_panel` 切出，本轮按《render 层边界契约》第 3 节从
`lib/script/ui/playlist_queue_buttons.py` 下沉到档位 D。**不依赖面板状态**的那一组：
尺寸常量（`_WIDTH` / `_ROW_H` / `_PAGE_SIZE` 等）与两个继承 `SpeakerActionButtonMixin` 的按钮。

宿主不得 import `lib.script`：按钮交互外观混入与动作按钮文字色经 `tier_d_seams` 注入：

- `playlist_queue_buttons:action_button_mixin` → 产品侧 `speaker_menu_style.SpeakerActionButtonMixin`；
- `playlist_queue_buttons:action_text_color` → 产品侧 `speaker_menu_style._C_ACTION_TEXT`（核心 `Color`）；
- 点击粒子经 `tier_d_seams.hook('click_particle')`（与同族宿主一致）。

产品侧 `lib/script/ui/playlist_queue_buttons.py` 只做再导出，既有导入面不变。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PyQt5.QtGui import QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.layers import WindowLayer, get_layer_manager

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


def _action_button_mixin():
    """产品侧 `SpeakerActionButtonMixin`；未注入时给出同形的空实现。"""
    hook = tier_d_seams.hook("playlist_queue_buttons:action_button_mixin")
    if hook is not None:
        return hook()
    return _NullActionButtonMixin


def _action_text_color():
    """动作按钮文字色（核心 `Color`）；未注入时用黑。"""
    hook = tier_d_seams.hook("playlist_queue_buttons:action_text_color")
    if hook is not None:
        return hook()
    from lib.core.render.visuals.palette import COLORS

    return COLORS['black']


def _publish_click_particle(widget, event) -> None:
    hook = tier_d_seams.hook("click_particle")
    if hook is not None:
        hook(widget, event)


class _NullActionButtonMixin:
    """缺省交互外观混入（隔离构造时使用；正常运行时由产品垫片注入真实混入）。"""

    _hovered = False
    _pressed = False

    def _init_speaker_action_button_state(self) -> None:
        self._hovered = False
        self._pressed = False
        self._label_font = tier_d_seams.ui_font()
        self._label_font.setBold(True)

    def _paint_action_button_shell(self, painter):
        return self.rect()

    def _begin_action_press(self) -> None:
        self._pressed = True
        self.update()

    def _finish_action_press(self, *, commit: bool) -> bool:
        was_pressed = getattr(self, '_pressed', False)
        self._pressed = False
        self.update()
        return was_pressed and commit

    def _cancel_action_press(self) -> None:
        self._finish_action_press(commit=False)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class _QueueRemoveButton(_action_button_mixin(), QWidget):
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
        _publish_click_particle(self, event)
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
        pen = QPen(tier_d_seams.painter_color(_action_text_color()))
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
        p.setBrush(tier_d_seams.painter_color(_action_text_color()))
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
