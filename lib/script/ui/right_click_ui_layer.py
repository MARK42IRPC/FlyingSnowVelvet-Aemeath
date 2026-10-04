"""右键 UI 的单一顶层窗口。

命令框、命令提示框与七个附属按钮原先各自是独立的置顶 QWidget：每个窗口都订阅
`FRAME` 并各自 `move()`，拖动桌宠时每帧要触发十次原生窗口移动与十次合成，右键
面板打开后的掉帧主要来自这里。这里改成“一个宿主窗口承载整组控件”：

  - 子控件不再注册 `LayerManager`，也不再订阅 `FRAME`，统一由本窗口一帧驱动一次；
  - 子控件按全局坐标计算目标位置（沿用原有锚点逻辑），由句柄换算成宿主本地坐标；
  - 宿主按所有可见子控件的全局矩形求并集，一次 `setGeometry()` 完成整组移动，并用
    同一并集设置窗口掩码，露出的空隙仍然点击穿透到桌宠/桌面。
"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QRegion
from PyQt5.QtWidgets import QWidget

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.layers import WindowLayer
from lib.core.render.layers import get_layer_manager
from lib.script.ui.render_bridge import (
    command_action_graph,
    move_widget_to_global,
    screen_rect_for_point,
    widget_global_rect,
)


class RightClickUiLayer(QWidget):
    """承载右键命令 UI 全部控件的单层宿主窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 显式给出初始尺寸，避免 Qt 在 show() 时按子控件自动调整宿主窗口。
        self.resize(1, 1)
        get_layer_manager().register(self, WindowLayer.PET_UI)

        self._event_center = get_event_center()
        self._members: list[QWidget] = []
        # 命令框附属按钮族的声明式锚点图（档位 2）：由本层一次解算，不再逐控件收发锚点事件。
        self._family_nodes: dict[str, QWidget] = {}
        self._family_graph = command_action_graph()
        self._visible = False
        self._region = QRegion()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE,
            self._on_clickthrough_toggle,
        )

    # ── 成员管理 ──────────────────────────────────────────────────────

    def adopt(self, *widgets: QWidget | None) -> list[QWidget]:
        """把右键控件收进本窗口，返回成功接管的控件（保持传入顺序）。"""
        adopted: list[QWidget] = []
        for widget in widgets:
            if widget is None or widget in self._members:
                continue
            was_visible = False
            try:
                was_visible = bool(widget.isVisible())
                widget.setParent(self)
                widget.setWindowFlags(Qt.Widget)
                widget.setAttribute(Qt.WA_TranslucentBackground)
            except Exception:
                continue
            widget._layer_host = self
            try:
                get_layer_manager().unregister(widget)
            except Exception:
                pass
            self._unsubscribe_frame(widget)
            self._members.append(widget)
            if was_visible:
                widget.show()
                widget.update()
            adopted.append(widget)
        return adopted

    def members(self) -> tuple[QWidget, ...]:
        return tuple(self._members)

    def register_family_node(self, node_id: str, widget) -> None:
        """登记按钮族里的一个节点控件；``node_id`` 见 ``visuals/anchor_graph.py``。"""
        if widget is not None:
            self._family_nodes[str(node_id)] = widget

    def family_nodes(self) -> dict[str, object]:
        return dict(self._family_nodes)

    @staticmethod
    def _unsubscribe_frame(widget: QWidget) -> None:
        """宿主统一驱动帧刷新，子控件不再各自响应 FRAME。"""
        handler = getattr(widget, "_on_frame", None)
        center = getattr(widget, "_event_center", None)
        if handler is None or center is None:
            return
        try:
            center.unsubscribe(EventType.FRAME, handler)
        except Exception:
            pass

    # ── 显示与隐藏 ────────────────────────────────────────────────────

    def show_layer(self) -> None:
        """显示整组控件：先同步几何，再一次性显示并申明置顶。"""
        self._visible = True
        # 先按锚点图解算一次整族落位，避免首次显示时按钮停在默认位置。
        self._resolve_family()
        self._sync_geometry()
        self.show()
        try:
            self.raise_()
        except Exception:
            pass

    def hide_layer(self) -> None:
        self._visible = False
        self.hide()

    def is_layer_visible(self) -> bool:
        return self._visible

    # ── 帧驱动 ────────────────────────────────────────────────────────

    def _on_frame(self, event=None) -> None:
        if not self._visible:
            return
        self._resolve_family()
        for member in self._members:
            update = getattr(member, "_update_position", None)
            if update is None:
                continue
            try:
                update()
            except RuntimeError:
                continue
        self._sync_geometry()

    def _family_rects(self) -> dict[str, object] | None:
        """按声明式锚点图一次解算按钮族矩形；命令框不在场时返回 ``None``。"""
        command = self._family_nodes.get("command_dialog")
        if command is None:
            return None
        graph = self._family_graph or command_action_graph()
        root_rect = widget_global_rect(command)
        sizes = {
            node_id: (widget.width(), widget.height())
            for node_id, widget in self._family_nodes.items()
            if node_id != graph.root_id
        }
        screen = screen_rect_for_point(point=root_rect.center, fallback_widget=self)
        return graph.resolve(root_rect, sizes=sizes, screen=screen)

    def family_rects(self) -> dict[str, object] | None:
        """按钮族当前的屏幕矩形（按声明式锚点图一次解算），供节点控件查询落位。"""
        return self._family_rects()

    def _resolve_family(self) -> None:
        """整族一次解算：命令框发一次全局锚点，八个按钮按图落位。"""
        command = self._family_nodes.get("command_dialog")
        if command is None:
            return
        root_rect = widget_global_rect(command)
        resolved = self._family_rects()
        if resolved is None:
            return
        for node_id, widget in self._family_nodes.items():
            if node_id == "command_dialog":
                continue
            rect = resolved.get(node_id)
            if rect is None:
                continue
            try:
                move_widget_to_global(widget, int(rect.x), int(rect.y))
            except RuntimeError:
                continue
        # 命令框发一次全局锚点：提示框、麦克风指示器等族外跟随者仍按既有协议跟随。
        self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
            "window_id": "command_dialog",
            "anchor_id": "all",
            "anchor_point": root_rect.top_left,
            "ui_id": "all",
        }))

    def _visible_member_rects(self) -> list[QRect]:
        rects: list[QRect] = []
        for member in self._members:
            try:
                if not member.isVisible():
                    continue
            except RuntimeError:
                continue
            position = getattr(member, "_layer_global_pos", None)
            if position is None:
                continue
            rects.append(QRect(position[0], position[1], member.width(), member.height()))
        return rects

    def _sync_geometry(self) -> None:
        """按可见成员的全局并集移动宿主，并把成员放回宿主本地坐标。"""
        rects = self._visible_member_rects()
        if not rects:
            return
        left = min(rect.x() for rect in rects)
        top = min(rect.y() for rect in rects)
        right = max(rect.x() + rect.width() for rect in rects)
        bottom = max(rect.y() + rect.height() for rect in rects)
        union = QRect(left, top, max(1, right - left), max(1, bottom - top))
        if self.geometry() != union:
            self.setGeometry(union)
        self._place_members(left, top)
        self._apply_mask()

    def _place_members(self, left: int, top: int) -> None:
        for member in self._members:
            position = getattr(member, "_layer_global_pos", None)
            if position is None:
                continue
            try:
                member.move(position[0] - left, position[1] - top)
            except RuntimeError:
                continue

    def _apply_mask(self) -> None:
        """只保留可见成员覆盖的区域，其余位置点击穿透。"""
        region = QRegion()
        for member in self._members:
            try:
                if not member.isVisible():
                    continue
                region = region.united(QRegion(member.geometry()))
            except RuntimeError:
                continue
        if region == self._region:
            return
        self._region = region
        try:
            self.setMask(region)
        except Exception:
            pass

    def _on_clickthrough_toggle(self, event) -> None:
        """穿透模式开启时整层（含子控件）都不接收鼠标事件。"""
        self.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            bool(event.data.get("enabled", False)),
        )

    def mask_region(self) -> QRegion:
        """返回当前命中区域（测试与诊断用）。"""
        return QRegion(self._region)

    def close_layer(self) -> None:
        self._visible = False
        for member in self._members:
            try:
                member.hide()
            except Exception:
                pass
        self.hide()
        self._dispose()

    def _dispose(self) -> None:
        """摘掉事件订阅，避免图层销毁后仍被投递回调。"""
        if getattr(self, '_disposed', False):
            return
        self._disposed = True
        for event_type, callback in (
            (EventType.FRAME, self._on_frame),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            try:
                self._event_center.unsubscribe(event_type, callback)
            except Exception:
                pass


__all__ = ["RightClickUiLayer"]
