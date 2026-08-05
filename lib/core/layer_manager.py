"""统一绘制层级管理器。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lib.core.desktop_backend import get_deferred_call, get_layer_window_host_factory
from lib.core.layer import Layer, draw_order_key, layer_name, normalize_layer
from lib.core.window_host import (
    LayerWindowHost,
    LayerWindowHostFactory,
    create_passive_layer_window_host,
)


@dataclass
class LayerWindow:
    """注册到 LayerManager 的窗口记录。"""

    layer: int
    z: int
    seq: int
    host: LayerWindowHost
    name: str


class LayerManager:
    """集中管理项目全部顶层窗口的 z-order。"""

    def __init__(
        self,
        *,
        defer: Callable[[int, Callable[[], None]], None] | None = None,
        host_factory: LayerWindowHostFactory | None = None,
    ) -> None:
        self._windows: list[LayerWindow] = []
        self._register_seq: int = 0
        self._dirty: bool = False
        self._paused: bool = False
        self._defer = defer
        self._host_factory = host_factory or create_passive_layer_window_host

    def register(
        self,
        window: object,
        layer=Layer.PET_UI,
        *,
        z: int = 0,
        name: str | None = None,
        replace: bool = True,
    ) -> None:
        """注册窗口层级；重复注册默认更新原记录。"""
        if window is None:
            return

        host = self._host_factory(window)
        identity = int(host.identity)
        layer_value = normalize_layer(layer)
        try:
            z_value = int(z)
        except (TypeError, ValueError):
            z_value = 0
        window_name = name or type(window).__name__

        alive: list[LayerWindow] = []
        found = False
        for record in self._windows:
            if not record.host.is_alive():
                continue
            if record.host.identity == identity and replace:
                record.layer = layer_value
                record.z = z_value
                record.host = host
                record.name = window_name
                found = True
            alive.append(record)

        if not found:
            self._register_seq += 1
            alive.append(LayerWindow(
                layer=layer_value,
                z=z_value,
                seq=self._register_seq,
                host=host,
                name=window_name,
            ))

        self._windows = alive
        self._dirty = True

    def unregister(self, window: object) -> None:
        """注销窗口记录。"""
        if window is None:
            return
        identity = int(self._host_factory(window).identity)
        remaining = [
            record for record in self._windows
            if record.host.is_alive() and record.host.identity != identity
        ]
        if len(remaining) != len(self._windows):
            self._dirty = True
        self._windows = remaining

    def set_layer(self, window: object, layer, *, z: int = 0, name: str | None = None) -> None:
        """更新窗口层级，未注册时自动注册。"""
        self.register(window, layer, z=z, name=name, replace=True)

    def pause(self) -> None:
        """暂停强制置顶。"""
        self._paused = True

    def resume(self) -> None:
        """恢复强制置顶，并立即重申层级。"""
        self._paused = False
        self.enforce_now()

    def enforce_on_frame(self) -> None:
        """在下一帧提交待处理的窗口层级变化。"""
        if self._paused or not self._dirty:
            return
        self.enforce_now()

    def enforce_now(self) -> None:
        """立即按 layer/z/生成顺序重申窗口，后生成的同级窗口位于上方。"""
        if self._paused:
            return
        self._enforce_all()
        self._dirty = False

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
                self.enforce_now()
            else:
                self._defer_call(delay, self.enforce_now)

    def _defer_call(self, delay_ms: int, callback: Callable[[], None]) -> None:
        defer = self._defer or get_deferred_call()
        if defer is None:
            callback()
            return
        defer(delay_ms, callback)

    def bring_to_front(self, window: object) -> None:
        """立即重申完整窗口链，避免单个窗口越过更高 layer。"""
        if window is None:
            return
        identity = int(self._host_factory(window).identity)
        host = next(
            (
                record.host
                for record in self._windows
                if record.host.is_alive() and record.host.identity == identity
            ),
            None,
        )
        if host is None or not host.is_visible():
            return
        self.enforce_now()

    def raise_layer(self, layer) -> None:
        """重申完整窗口链，保证指定 layer 操作不会破坏全局顺序。"""
        if self._paused:
            return
        normalize_layer(layer)
        self.enforce_now()

    def snapshot(self) -> list[tuple[int, int, int, str, bool]]:
        """返回当前层级快照，便于调试。"""
        result: list[tuple[int, int, int, str, bool]] = []
        alive: list[LayerWindow] = []
        for record in self._sorted_records():
            if not record.host.is_alive():
                continue
            alive.append(record)
            result.append((
                record.layer,
                record.z,
                record.seq,
                record.name,
                record.host.is_visible(),
            ))
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
        visible_hosts: list[LayerWindowHost] = []
        for record in self._sorted_records():
            if not record.host.is_alive():
                continue
            alive.append(record)
            if record.host.is_visible():
                visible_hosts.append(record.host)
        self._windows = alive

        insert_after: int | None = None
        for host in reversed(visible_hosts):
            insert_after = host.stack_window(insert_after)
            if insert_after is None:
                for fallback_host in visible_hosts:
                    fallback_host.raise_window()
                return


_INSTANCE: LayerManager | None = None


def get_layer_manager() -> LayerManager:
    """返回全局 LayerManager 单例。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = LayerManager(host_factory=get_layer_window_host_factory())
    return _INSTANCE


def cleanup_layer_manager() -> None:
    """清理全局 LayerManager。"""
    global _INSTANCE
    _INSTANCE = None
