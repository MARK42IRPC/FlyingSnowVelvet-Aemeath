"""统一绘制层级管理器。"""
from __future__ import annotations

import sys
import weakref
from dataclasses import dataclass

from PyQt5.QtCore import QTimer

from lib.core.layer import Layer, draw_order_key, layer_name, normalize_layer


@dataclass
class LayerWindow:
    """注册到 LayerManager 的窗口记录。"""

    layer: int
    z: int
    seq: int
    ref: weakref.ref
    name: str


class LayerManager:
    """集中管理项目全部顶层窗口的 z-order。"""

    ENFORCE_INTERVAL: int = 30
    _SWP_FLAGS: int = 0x0213
    _HWND_TOPMOST: int = -1

    def __init__(self) -> None:
        self._windows: list[LayerWindow] = []
        self._register_seq: int = 0
        self._counter: int = 0
        self._paused: bool = False

        self._set_window_pos_api = None
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes

            self._user32 = ctypes.windll.user32
            self._set_window_pos_api = self._user32.SetWindowPos
            self._set_window_pos_api.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            )
            self._set_window_pos_api.restype = wintypes.BOOL
        else:
            self._user32 = None

    def register(
        self,
        widget,
        layer=Layer.PET_UI,
        *,
        z: int = 0,
        name: str | None = None,
        replace: bool = True,
    ) -> None:
        """注册窗口层级；重复注册默认更新原记录。"""
        if widget is None:
            return

        layer_value = normalize_layer(layer)
        try:
            z_value = int(z)
        except (TypeError, ValueError):
            z_value = 0
        window_name = name or widget.__class__.__name__

        alive: list[LayerWindow] = []
        found = False
        for record in self._windows:
            current = record.ref()
            if current is None:
                continue
            if current is widget and replace:
                record.layer = layer_value
                record.z = z_value
                record.name = window_name
                found = True
            alive.append(record)

        if not found:
            self._register_seq += 1
            alive.append(LayerWindow(
                layer=layer_value,
                z=z_value,
                seq=self._register_seq,
                ref=weakref.ref(widget),
                name=window_name,
            ))

        self._windows = alive

    def unregister(self, widget) -> None:
        """注销窗口记录。"""
        if widget is None:
            return
        self._windows = [
            record for record in self._windows
            if (current := record.ref()) is not None and current is not widget
        ]

    def set_layer(self, widget, layer, *, z: int = 0, name: str | None = None) -> None:
        """更新窗口层级，未注册时自动注册。"""
        self.register(widget, layer, z=z, name=name, replace=True)

    def pause(self) -> None:
        """暂停强制置顶。"""
        self._paused = True

    def resume(self) -> None:
        """恢复强制置顶，并立即重申层级。"""
        self._paused = False
        self.enforce_now()

    def enforce_on_frame(self) -> None:
        """由 FRAME 事件驱动，按间隔重申全部窗口层级。"""
        if self._paused:
            return
        self._counter += 1
        if self._counter % self.ENFORCE_INTERVAL == 0:
            self.enforce_now()

    def enforce_now(self) -> None:
        """立即按 layer/z/生成顺序重申窗口，后生成的同级窗口位于上方。"""
        if self._paused:
            return
        self._enforce_all()

    def enforce_burst(self, delays_ms: tuple[int, ...] = (0, 16, 48, 96, 180)) -> None:
        """短时间内多次重申层级，抵消 Qt/Win32 异步 z-order 抖动。"""
        if self._paused:
            return
        for delay_ms in delays_ms:
            try:
                delay = max(0, int(delay_ms))
            except (TypeError, ValueError):
                delay = 0
            if delay == 0:
                self._enforce_all()
            else:
                QTimer.singleShot(delay, self._enforce_all)

    def bring_to_front(self, widget) -> None:
        """立即重申完整窗口链，避免单个窗口越过更高 layer。"""
        if widget is None or not widget.isVisible():
            return
        self._enforce_all()

    def raise_layer(self, layer) -> None:
        """重申完整窗口链，保证指定 layer 操作不会破坏全局顺序。"""
        if self._paused:
            return
        normalize_layer(layer)
        self._enforce_all()

    def snapshot(self) -> list[tuple[int, int, int, str, bool]]:
        """返回当前层级快照，便于调试。"""
        result: list[tuple[int, int, int, str, bool]] = []
        alive: list[LayerWindow] = []
        for record in self._sorted_records():
            widget = record.ref()
            if widget is None:
                continue
            alive.append(record)
            result.append((record.layer, record.z, record.seq, record.name, widget.isVisible()))
        self._windows = alive
        return result

    def describe_snapshot(self) -> str:
        """返回适合气泡/日志展示的层级快照文本。"""
        rows = self.snapshot()
        if not rows:
            return "当前没有已注册图层窗口"

        lines = ["图层快照："]
        for layer, z, seq, name, visible in rows:
            state = "显示" if visible else "隐藏"
            lines.append(f"{layer_name(layer)} z={z} #{seq} {state} {name}")
        return "\n".join(lines)

    def _sorted_records(self) -> list[LayerWindow]:
        return sorted(
            self._windows,
            key=lambda item: draw_order_key(item.layer, item.z, item.seq),
        )

    def _enforce_all(self) -> None:
        alive: list[LayerWindow] = []
        visible_widgets = []
        for record in self._sorted_records():
            widget = record.ref()
            if widget is None:
                continue
            alive.append(record)
            if widget.isVisible():
                visible_widgets.append(widget)
        self._windows = alive

        if self._set_window_pos_api is None:
            for widget in visible_widgets:
                widget.raise_()
            return

        insert_after = self._HWND_TOPMOST
        for widget in reversed(visible_widgets):
            hwnd = int(widget.winId())
            self._set_topmost(widget, insert_after=insert_after)
            insert_after = hwnd

    def _set_topmost(self, widget, *, insert_after=None) -> None:
        if self._set_window_pos_api is not None:
            target = self._HWND_TOPMOST if insert_after is None else int(insert_after)
            self._set_window_pos_api(
                int(widget.winId()),
                target,
                0, 0, 0, 0,
                self._SWP_FLAGS,
            )
        else:
            widget.raise_()


_INSTANCE: LayerManager | None = None


def get_layer_manager() -> LayerManager:
    """返回全局 LayerManager 单例。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = LayerManager()
    return _INSTANCE


def cleanup_layer_manager() -> None:
    """清理全局 LayerManager。"""
    global _INSTANCE
    _INSTANCE = None
