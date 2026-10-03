"""Qt 控件窗口宿主：把后端中立的控件描述渲染成一个真实顶层窗口。

这是控件层"描述 + 后端渲染"结构里的后端那一半，属于 Qt 后端自有件（档位 D）。
它只做 Qt 事实：窗口标志、透明度动画、绘制批次执行、指针事件翻译、剪贴板与
z-order 注册。控件该画什么、什么时候画、点下去意味着什么，全部由注入的回调与
``lib/core/render/visuals/controls.py`` 的描述对象决定。

因此产品控件（``lib/script/ui/*.py``）不必再继承 ``QWidget``：控件的状态与绘制数据
留在描述层，真实窗口由本模块持有。换后端时替换的是这一层，不是控件。
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from lib.core.render.backends.qt.widgets.anchors import get_anchor_point
from lib.core.render.visuals.controls import (
    BUTTON_LEFT,
    BUTTON_MIDDLE,
    BUTTON_NONE,
    BUTTON_RIGHT,
    PointerClick,
    PointerEvent,
)
from lib.core.render.visuals.types import Point, Rect
from lib.core.render.layers import get_layer_manager

_QPAINT_TO_CORE = {
    Qt.LeftButton: BUTTON_LEFT,
    Qt.RightButton: BUTTON_RIGHT,
    Qt.MiddleButton: BUTTON_MIDDLE,
}


class QtControlHost(QWidget):
    """一个渲染单个产品控件的顶层 Qt 窗口。

    ``draw_backend`` 与 ``presentation_host`` 由组合入口注入（见 ``render_bridge``）：
    本模块属于档位 D，不得静态引用档位 A 的绘制实现。

    注入的回调：

    - ``paint_batch``：返回当前要绘制的 ``DrawBatch``（``None`` 表示跳过本帧）。
    - ``auto_hide_ms`` / ``on_auto_hide``：可选的自动隐藏计时（说明书面板用）；
      不给就不创建定时器，`start_auto_hide()` 成为空操作。
    - ``on_pointer``：把中立指针事件交给控件，返回它要执行的产品意图。
    - ``on_fade_out_finished``：淡出动画正常结束时回调（被打断时不回调）。
    """

    def __init__(
        self,
        *,
        draw_backend,
        presentation_host,
        paint_batch: Callable[[], object | None],
        on_pointer: Callable[[PointerEvent], PointerClick] | None = None,
        on_hide_requested: Callable[[], None] | None = None,
        on_fade_out_finished: Callable[[], None] | None = None,
        on_hover_changed: Callable[[bool], None] | None = None,
        on_pointer_release: Callable[[], None] | None = None,
        on_pointer_move: Callable[[PointerEvent], None] | None = None,
        capture_on_press: bool = False,
        layer=None,
        fade_duration_ms: int = 200,
        fade_out_duration_ms: int = 200,
        auto_hide_ms: int | None = None,
        on_auto_hide: Callable[[], None] | None = None,
        transparent_for_mouse: bool = False,
        show_without_activating: bool = False,
        pointing_cursor: bool = False,
    ) -> None:
        super().__init__()
        self._auto_hide = None
        if auto_hide_ms and on_auto_hide is not None:
            self._auto_hide = QTimer(self)
            self._auto_hide.setSingleShot(True)
            self._auto_hide.setInterval(max(0, int(auto_hide_ms)))
            self._auto_hide.timeout.connect(on_auto_hide)
        self._paint_batch = paint_batch
        self._on_pointer = on_pointer
        self._on_hide_requested = on_hide_requested
        self._on_fade_out_finished = on_fade_out_finished
        self._on_hover_changed = on_hover_changed
        self._on_pointer_release = on_pointer_release
        self._on_pointer_move = on_pointer_move
        self._capture_on_press = bool(capture_on_press)
        self._awaiting_fade_out = False
        self._fade_duration_ms = max(0, int(fade_duration_ms))
        self._fade_out_duration_ms = max(0, int(fade_out_duration_ms))
        self._draw_backend = draw_backend
        self._presentation = presentation_host
        self._closing = False

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        if show_without_activating:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        if transparent_for_mouse:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setCursor(Qt.PointingHandCursor if pointing_cursor else Qt.ArrowCursor)
        if layer is not None:
            get_layer_manager().register(self, layer)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_animation_finished)

        #: 供说明书（tooltip）查找的说明字段；由产品控件写入。
        self._description = ""

    # ── 绘制 ───────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        batch = self._paint_batch()
        if batch is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        self._draw_backend.render(
            batch,
            painter,
            Rect(0, 0, self.width(), self.height()),
        )
        painter.end()

    # ── 指针 ───────────────────────────────────────────────────────
    def pointer_event(self, event) -> PointerEvent:
        """把一个 Qt 鼠标事件翻译成中立指针事件。"""
        local = event.pos() if hasattr(event, "pos") else QPoint(0, 0)
        global_pos = self.mapToGlobal(local)
        return PointerEvent(
            button=_QPAINT_TO_CORE.get(event.button(), BUTTON_NONE),
            local=Point(local.x(), local.y()),
            screen=Point(global_pos.x(), global_pos.y()),
        )

    def mousePressEvent(self, event) -> None:
        if self._on_pointer is None:
            return
        if self._capture_on_press:
            try:
                self.grabMouse()
            except RuntimeError:
                pass
        intent = self._on_pointer(self.pointer_event(event))
        if intent.copy_text is not None:
            QApplication.clipboard().setText(intent.copy_text)
        if intent.hide:
            if self._on_hide_requested is not None:
                self._on_hide_requested()
            else:
                self.hide()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._on_pointer_move is not None:
            self._on_pointer_move(self.pointer_event(event))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._capture_on_press:
            try:
                if self.mouseGrabber() is self:
                    self.releaseMouse()
            except RuntimeError:
                pass
        if self._on_pointer_release is not None:
            self._on_pointer_release()
        event.accept()

    def enterEvent(self, event) -> None:
        if self._on_hover_changed is not None:
            self._on_hover_changed(True)

    def leaveEvent(self, event) -> None:
        if self._on_hover_changed is not None:
            self._on_hover_changed(False)

    # ── 尺寸、位置与命令 ───────────────────────────────────────────
    def apply_size(self, width: int, height: int) -> None:
        width, height = max(1, int(width)), max(1, int(height))
        if self.width() != width or self.height() != height:
            self.setFixedSize(width, height)

    def move_to(self, x: int, y: int) -> None:
        self._presentation.move_widget_to_global(self, int(x), int(y))

    def geometry_rect(self) -> Rect:
        """窗口在屏幕坐标系中的矩形（核心类型，不是 ``QRect``）。"""
        return self._presentation.widget_global_rect(self)

    def global_point(self, local: Point) -> Point:
        return self._presentation.widget_global_point(self, local)

    def local_anchor(self, anchor_id: str) -> QPoint:
        """锚点在窗口本地坐标中的位置；事件总线上的锚点载荷要求整数 ``QPoint``。"""
        point = get_anchor_point(self, anchor_id)
        return QPoint(int(point.x()), int(point.y()))

    def set_clickthrough(self, enabled: bool) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))

    # ── 命中测试：光标下的产品说明书 ───────────────────────────────
    def description_at(self, global_pos, *, restricted_names=()) -> str:
        """返回光标下那个控件声明的 ``_description``；没有就返回空串。

        这是纯 Qt 事实（``widgetAt`` / ``parent()`` 链 / ``topLevelWidgets`` 兜底），
        但**产品策略不在这里**：哪些窗口算受限面板由调用方以名字传入，本方法只负责
        "受限面板只有在真正激活时才放行"这条 Qt 语义。说明书面板本身永远被跳过。

        - 传入的 ``global_pos`` 是原始 Qt 坐标对象（面板需要把同一个位置再交给 Qt）。
        - 子控件若有 ``_description`` 优先；没有就沿 ``parent()`` 链向上找。
        """
        restricted = {str(name) for name in restricted_names if name}
        widget = QApplication.widgetAt(global_pos)

        # 无焦点时 widgetAt 可能返回 None，手动从顶层窗口做命中测试
        if widget is None:
            for top in reversed(QApplication.topLevelWidgets()):
                if top is self or not top.isVisible():
                    continue
                local = top.mapFromGlobal(global_pos)
                hit = top.childAt(local)
                if hit is not None:
                    widget = hit
                    break
                if top.rect().contains(local):
                    widget = top
                    break

        if widget is None:
            return ""

        window = widget.window()
        if self._is_restricted_window(window, restricted) and not self._window_is_active(window):
            return ""

        # 先尝试 parent() 链（widget 本身 → 父级 → 祖父级 …）
        current = widget
        while current is not None:
            if current is self:
                break
            description = getattr(current, "_description", None)
            if description:
                return str(description)
            try:
                current = current.parent()
            except Exception:
                current = None

        # parent() 链断裂时（PyQt5 有时返回 C++ 包装而非 Python 实例），
        # 回退到遍历顶层窗口，直接在包含光标的那个窗口上查找 _description
        for top in QApplication.topLevelWidgets():
            if top is self or not top.isVisible():
                continue
            local = top.mapFromGlobal(global_pos)
            if top.rect().contains(local):
                description = getattr(top, "_description", None)
                if description:
                    return str(description)

        return ""

    @staticmethod
    def _is_restricted_window(window, restricted_names) -> bool:
        if window is None:
            return False
        if window.__class__.__name__ in restricted_names:
            return True
        return str(window.objectName() or "") in restricted_names

    @staticmethod
    def _window_is_active(window) -> bool:
        if window is None or not window.isVisible():
            return False
        active = QApplication.activeWindow()
        return bool(window.isActiveWindow() or active is window)

    # ── 透明度动画 ─────────────────────────────────────────────────
    def fade_to(self, target: float, *, duration_ms: int | None = None, fade_out: bool = False) -> None:
        self._anim.stop()
        value = max(0.0, min(1.0, float(target)))
        self._anim.setStartValue(float(self._opacity.opacity()))
        self._anim.setEndValue(value)
        if duration_ms is None:
            duration_ms = self._fade_out_duration_ms if fade_out else self._fade_duration_ms
        self._anim.setDuration(max(0, int(duration_ms)))
        self._awaiting_fade_out = bool(fade_out) and self._on_fade_out_finished is not None
        self._anim.start()

    def stop_animation(self) -> None:
        self._awaiting_fade_out = False
        self._anim.stop()

    @property
    def opacity(self) -> float:
        return float(self._opacity.opacity())

    def set_opacity(self, value: float) -> None:
        self._opacity.setOpacity(max(0.0, min(1.0, float(value))))

    def _on_animation_finished(self) -> None:
        """淡出动画真正播完时通知控件；被新动画打断（``stop_animation``）不触发。"""
        if not self._awaiting_fade_out:
            return
        self._awaiting_fade_out = False
        if self._on_fade_out_finished is not None and not self._closing:
            self._on_fade_out_finished()

    # ── 生命周期 ───────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        self._closing = True
        try:
            get_layer_manager().unregister(self)
        except Exception:
            pass
        super().closeEvent(event)

    def start_auto_hide(self, duration_ms: int | None = None) -> None:
        """（重新）启动自动隐藏计时；未配置计时器的宿主是空操作。"""
        if self._auto_hide is None:
            return
        if duration_ms is not None:
            self._auto_hide.setInterval(max(0, int(duration_ms)))
        self._auto_hide.start()

    def stop_auto_hide(self) -> None:
        if self._auto_hide is not None:
            self._auto_hide.stop()

    def cleanup(self) -> None:
        self._closing = True
        try:
            if self._auto_hide is not None:
                self._auto_hide.stop()
        except Exception:
            pass
        try:
            self._anim.stop()
        except Exception:
            pass
        try:
            get_layer_manager().unregister(self)
        except Exception:
            pass
        self.hide()
        self.close()


__all__ = ["QtControlHost"]
