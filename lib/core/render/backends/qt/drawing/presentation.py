"""Qt implementation of the backend-neutral presentation geometry host.

负责把 QWidget 的宿主事实翻译成核心 `Rect` / `Point`：控件落在哪块屏、窗口该被
夹到哪里、宿主分层时子控件怎么换算回屏幕坐标。共享算法在
`lib/core/render/visuals/screen.py`，这里只做 Qt 取值与类型转换。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication

from lib.core.render.visuals.screen import clamp_rect_position as clamp_core_rect_position
from lib.core.render.visuals.types import Point, Rect, coerce_point


def _to_qpoint(value: object) -> QPoint | None:
    if value is None:
        return None
    if isinstance(value, QPoint):
        return value
    point = coerce_point(value)
    if point is None:
        return None
    return QPoint(int(round(point.x)), int(round(point.y)))


def _core_rect(rect: QRect) -> Rect:
    return Rect(rect.x(), rect.y(), max(0, rect.width()), max(0, rect.height()))


class QtPresentationHost:
    """Resolve screen geometry and window placement through Qt."""

    def screen_rect_for_widget(self, widget=None, point=None) -> Rect | None:
        screen = None
        qt_point = _to_qpoint(point)
        if qt_point is not None:
            screen = QApplication.screenAt(qt_point)

        if screen is None and widget is not None:
            handle = None
            try:
                handle = widget.windowHandle()
            except Exception:
                handle = None
            if handle is not None:
                screen = handle.screen()
            if screen is None:
                try:
                    screen = widget.screen()
                except Exception:
                    screen = None

        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return _core_rect(screen.geometry())

    def clamp_position(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        widget=None,
        point=None,
    ) -> tuple[int, int] | None:
        screen = self.screen_rect_for_widget(widget, point=point)
        if screen is None:
            return None
        clamped_x, clamped_y, _ = clamp_core_rect_position(
            x, y, width, height, screen
        )
        return clamped_x, clamped_y

    def widget_global_rect(self, widget) -> Rect:
        try:
            origin = widget.mapToGlobal(QPoint(0, 0))
        except Exception:
            return Rect(widget.x(), widget.y(), widget.width(), widget.height())
        return Rect(origin.x(), origin.y(), widget.width(), widget.height())

    def widget_global_point(self, widget, point) -> Point:
        core = coerce_point(point) or Point()
        rect = self.widget_global_rect(widget)
        return Point(rect.x + core.x, rect.y + core.y)

    def move_widget_to_global(self, widget, x: int, y: int) -> None:
        """按屏幕坐标移动控件，宿主分层时换算成宿主本地坐标。

        右键 UI 合并为一层后，命令框与附属按钮都是宿主窗口的子控件，`move()` 使用
        的是父窗口本地坐标。这里统一接收全局目标位置，记录到 `_layer_global_pos`
        （宿主据此计算自己的几何与命中区域），再换算成本地坐标后移动；Qt 下相同
        坐标的 `move()` 不会触发原生窗口操作，拖动时每帧只有宿主窗口移动一次。
        """
        x = int(x)
        y = int(y)
        try:
            widget._layer_global_pos = (x, y)
        except Exception:
            pass
        host = getattr(widget, "_layer_host", None)
        if host is not None:
            try:
                origin = host.mapToGlobal(QPoint(0, 0))
            except Exception:
                origin = None
            if origin is not None:
                x -= origin.x()
                y -= origin.y()
        if widget.x() != x or widget.y() != y:
            widget.move(x, y)


def create_qt_presentation_host() -> QtPresentationHost:
    return QtPresentationHost()


__all__ = ["QtPresentationHost", "create_qt_presentation_host"]
