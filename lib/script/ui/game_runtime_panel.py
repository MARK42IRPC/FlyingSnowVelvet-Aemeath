"""游戏运行时宿主窗口与其几何助手。

本模块从 `game_runtime.py` 切出 `GameRuntimePanel`（可缩放、等比、带自绘边框的
游戏承载窗口）以及它用的两个纯几何函数 `centered_aspect_rect` /
`aspect_resize_geometry`。面板只被 `GameRuntime` 构造并驱动，不反向依赖控制器。

行级等价：类体与两个函数与前一份逐字节相同。`game_runtime.py` 反向 `from ... import`
这三个名字，`tests/test_game_runtime_geometry.py` 的既有导入面不变。

本模块 `import PyQt5`，按 34.2 节规则登记 `frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.render.layers import Layer
from lib.script.gemes.MAIN.game_packages import GamePackageManifest
from lib.script.ui.render_bridge import (
    create_component_layer,
    create_component_layer_request,
    screen_rect_for_point as get_screen_geometry_for_point,
    ui_font as get_ui_font,
)


def centered_aspect_rect(
    container: QRect,
    aspect_width: int,
    aspect_height: int,
    inset: int = 0,
) -> QRect:
    inner = container.adjusted(inset, inset, -inset, -inset)
    if inner.width() * aspect_height <= inner.height() * aspect_width:
        width = max(1, inner.width())
        height = max(1, width * aspect_height // aspect_width)
    else:
        height = max(1, inner.height())
        width = max(1, height * aspect_width // aspect_height)
    x = inner.x() + (inner.width() - width) // 2
    y = inner.y() + (inner.height() - height) // 2
    return QRect(x, y, width, height)


def aspect_resize_geometry(
    start: QRect,
    edges: set[str],
    delta: QPoint,
    minimum_width: int,
    aspect_width: int,
    aspect_height: int,
) -> QRect:
    width_delta = 0
    height_delta = 0
    if "left" in edges:
        width_delta = -delta.x()
    elif "right" in edges:
        width_delta = delta.x()
    if "top" in edges:
        height_delta = -delta.y()
    elif "bottom" in edges:
        height_delta = delta.y()
    width_from_height = round(height_delta * aspect_width / aspect_height)
    effective_delta = width_delta if abs(width_delta) >= abs(width_from_height) else width_from_height
    width = max(int(minimum_width), int(start.width() + effective_delta))
    height = max(1, round(width * aspect_height / aspect_width))
    x = start.right() - width + 1 if "left" in edges else start.x()
    y = start.bottom() - height + 1 if "top" in edges else start.y()
    return QRect(x, y, width, height)


class GameRuntimePanel(QWidget):
    """Resizable game runtime host window."""

    _DEFAULT_WIDTH = 1000
    _DEFAULT_HEIGHT = 800
    _MINIMUM_WIDTH = 600
    _MINIMUM_HEIGHT = 480
    _ASPECT_WIDTH = 10
    _ASPECT_HEIGHT = 8

    _PADDING = scale_px(8, min_abs=1)
    _LAYER = scale_px(4, min_abs=1)
    _BORDER = _LAYER * 2
    _RESIZE_MARGIN = scale_px(12, min_abs=1)
    _C_BORDER = QColor(25, 16, 58)
    _C_MID = QColor(145, 122, 232)
    _C_BG = QColor(59, 43, 118)

    def __init__(self) -> None:
        super().__init__()
        # 普通窗口 + 无边框：优先级与工作台窗口一致，既不置顶、也不进 LayerManager。
        # 游戏窗口因此不会把粒子/特效覆盖层压在下面，也不会被强制重申层级打断。
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)

        self._render_core = create_component_layer()
        request = create_component_layer_request(
            "game_runtime_panel_shell", self._paint_panel_layer, Layer.PANEL
        )
        if request is not None:
            self._render_core.register_item(request)

        self._font = get_ui_font()
        self._font.setBold(True)
        self._game_widget: QWidget | None = None
        self._manifest: GamePackageManifest | None = None
        self._active_game_name = ""
        self._close_callback = None
        self._drag_origin: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_start_geometry: QRect | None = None
        self._fading_out = False
        self._allow_hide_once = False
        self._fullscreen_active = False
        self._normal_geometry = QRect()
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(UI.get("ui_fade_duration", 180))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)
        self.setWindowOpacity(0.0)
        self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
        self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
        self.hide()
        self._refresh_size()

    def configure_game(self, manifest: GamePackageManifest, widget: QWidget, close_callback) -> None:
        self._manifest = manifest
        self._active_game_name = manifest.name
        self._close_callback = close_callback
        if self._game_widget is not None:
            try:
                if hasattr(self._game_widget, "deactivate"):
                    self._game_widget.deactivate()
            except Exception:
                pass
            self._game_widget.setParent(None)
            self._game_widget.deleteLater()
        self._game_widget = widget
        self._game_widget.setParent(self)
        self._game_widget.show()
        if hasattr(widget, "set_close_callback"):
            widget.set_close_callback(close_callback)
        if hasattr(widget, "set_fullscreen_callback"):
            widget.set_fullscreen_callback(self.toggle_fullscreen)
        self.setMinimumSize(
            max(320, int(manifest.minimum_width)),
            max(240, int(manifest.minimum_height)),
        )
        self.resize(
            max(self.minimumWidth(), int(manifest.default_width)),
            max(self.minimumHeight(), int(manifest.default_height)),
        )
        self._refresh_size()
        self.update()

    def get_game_middle_third_rect_global(self) -> QRect:
        if not self.isVisible() or self._game_widget is None:
            return QRect()
        local_rect = self._game_widget.geometry()
        third_w = max(1, local_rect.width() // 3)
        middle_x = local_rect.x() + third_w
        middle_rect = QRect(middle_x, local_rect.y(), third_w, local_rect.height())
        return QRect(self.mapToGlobal(middle_rect.topLeft()), middle_rect.size())

    def move_to_screen_center(self) -> None:
        if self._fullscreen_active:
            return
        screen = get_screen_geometry_for_point(fallback_widget=self)
        x = int(screen.x) + (int(screen.width) - self.width()) // 2
        y = int(screen.y) + (int(screen.height) - self.height()) // 2
        self.move(x, y)

    def activate(self) -> None:
        if self._game_widget is None:
            return
        if hasattr(self._game_widget, "reset_game"):
            try:
                self._game_widget.reset_game(start_running=False)
            except TypeError:
                self._game_widget.reset_game()
        elif hasattr(self._game_widget, "on_runtime_activated"):
            self._game_widget.on_runtime_activated()
        self._game_widget.show()
        self.activateWindow()
        self._game_widget.setFocus(Qt.ActiveWindowFocusReason)

    def deactivate(self) -> None:
        if self._game_widget is not None and hasattr(self._game_widget, "deactivate"):
            try:
                self._game_widget.deactivate()
            except Exception:
                pass
        self.exit_fullscreen()

    def fade_in(self) -> None:
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self.setWindowOpacity(0.0)
        self.show()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(apply_ui_opacity(1.0))
        self._opacity_anim.start()

    def fade_out(self) -> None:
        if self._fading_out or not self.isVisible():
            return
        self._fading_out = True
        self._opacity_anim.stop()
        current_opacity = self.windowOpacity()
        self._opacity_anim.setStartValue(max(0.0, min(1.0, float(current_opacity))))
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.start()

    def hide(self) -> None:
        if self._allow_hide_once or self._fading_out or not self.isVisible():
            super().hide()
            return
        self.fade_out()

    def toggle_fullscreen(self) -> None:
        if self._fullscreen_active:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self) -> None:
        if self._fullscreen_active:
            return
        self._normal_geometry = QRect(self.geometry())
        self._fullscreen_active = True
        self._drag_origin = None
        self._resize_origin = None
        self._resize_start_geometry = None
        self._resize_edges.clear()
        self.setCursor(Qt.ArrowCursor)
        self.showFullScreen()
        self._refresh_size()
        self.update()
        if self._game_widget is not None:
            self._game_widget.setFocus(Qt.ActiveWindowFocusReason)

    def exit_fullscreen(self) -> None:
        if not self._fullscreen_active:
            return
        restore_geometry = QRect(self._normal_geometry)
        self._fullscreen_active = False
        self.showNormal()
        if restore_geometry.isValid():
            self.setGeometry(restore_geometry)
        else:
            self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
            self.move_to_screen_center()
        self._refresh_size()
        self.update()
        if self._game_widget is not None:
            QTimer.singleShot(0, lambda: self._game_widget.setFocus(Qt.ActiveWindowFocusReason))

    def _current_aspect_width(self) -> int:
        return max(1, int(self._manifest.aspect_width if self._manifest is not None else self._ASPECT_WIDTH))

    def _current_aspect_height(self) -> int:
        return max(1, int(self._manifest.aspect_height if self._manifest is not None else self._ASPECT_HEIGHT))

    def _current_minimum_width(self) -> int:
        return max(320, int(self._manifest.minimum_width if self._manifest is not None else self._MINIMUM_WIDTH))

    def _refresh_size(self) -> None:
        if self._game_widget is None:
            return
        inset = 0 if self._fullscreen_active else self._BORDER
        self._game_widget.setGeometry(
            centered_aspect_rect(
                self.rect(),
                self._current_aspect_width(),
                self._current_aspect_height(),
                inset,
            )
        )

    def _hit_test_edges(self, pos) -> set[str]:
        if self._fullscreen_active:
            return set()
        edges: set[str] = set()
        if pos.x() <= self._RESIZE_MARGIN:
            edges.add("left")
        elif pos.x() >= self.width() - self._RESIZE_MARGIN:
            edges.add("right")
        if pos.y() <= self._RESIZE_MARGIN:
            edges.add("top")
        elif pos.y() >= self.height() - self._RESIZE_MARGIN:
            edges.add("bottom")
        return edges

    def _update_cursor(self, edges: set[str]) -> None:
        if edges in ({"left", "top"}, {"right", "bottom"}):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in ({"right", "top"}, {"left", "bottom"}):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "left" in edges or "right" in edges:
            self.setCursor(Qt.SizeHorCursor)
        elif "top" in edges or "bottom" in edges:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def _on_opacity_anim_finished(self) -> None:
        if not self._fading_out:
            return
        self._fading_out = False
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False
            self.setWindowOpacity(apply_ui_opacity(1.0))

    def mousePressEvent(self, event) -> None:
        if self._fullscreen_active:
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        edges = self._hit_test_edges(event.pos())
        if edges:
            self._resize_edges = edges
            self._resize_origin = event.globalPos()
            self._resize_start_geometry = self.geometry()
        else:
            self._drag_origin = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._resize_origin is not None and self._resize_start_geometry is not None and self._resize_edges:
            delta = event.globalPos() - self._resize_origin
            geom = aspect_resize_geometry(
                self._resize_start_geometry,
                self._resize_edges,
                delta,
                self._current_minimum_width(),
                self._current_aspect_width(),
                self._current_aspect_height(),
            )
            self.setGeometry(geom)
            event.accept()
            return
        if self._drag_origin is not None:
            self.move(event.globalPos() - self._drag_origin)
            event.accept()
            return
        self._update_cursor(self._hit_test_edges(event.pos()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_origin = None
        self._resize_start_geometry = None
        self._resize_edges.clear()
        self._update_cursor(self._hit_test_edges(event.pos()))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._drag_origin is None and self._resize_origin is None:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        self._refresh_size()
        super().resizeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._render_core.render(painter, self.rect())
        painter.end()

    def _paint_panel_layer(self, painter: QPainter, _target_rect) -> None:
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self._fullscreen_active:
            painter.fillRect(self.rect(), Qt.black)
            return
        painter.fillRect(self.rect(), self._C_BORDER)
        painter.fillRect(
            self.rect().adjusted(self._LAYER, self._LAYER, -self._LAYER, -self._LAYER),
            self._C_MID,
        )
        content = self.rect().adjusted(self._BORDER, self._BORDER, -self._BORDER, -self._BORDER)
        painter.fillRect(content, self._C_BG)
        painter.fillRect(
            content.adjusted(scale_px(3, min_abs=1), scale_px(3, min_abs=1), -scale_px(3, min_abs=1), -scale_px(3, min_abs=1)),
            QColor(88, 68, 166),
        )
