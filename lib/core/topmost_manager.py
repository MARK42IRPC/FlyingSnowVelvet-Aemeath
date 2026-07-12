"""置顶管理兼容入口。

旧代码仍可通过 get_topmost_manager() 使用 register()/enforce_*()。
实际层级管理已收敛到 lib.core.layer_manager.LayerManager。
"""

from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager


TOPMOST_PRIORITY_DEFAULT = int(Layer.PET_UI)
TOPMOST_PRIORITY_QR_DIALOG = int(Layer.DIALOG)
TOPMOST_PRIORITY_MAIN_PET = int(Layer.MAIN_PET)
TOPMOST_PRIORITY_OVERLAY = int(Layer.EFFECT)


def _priority_to_layer(priority: int) -> int:
    """兼容旧 priority 常量到新 layer。"""
    try:
        value = int(priority)
    except (TypeError, ValueError):
        return int(Layer.PET_UI)

    if value == TOPMOST_PRIORITY_MAIN_PET:
        return int(Layer.MAIN_PET)
    if value == TOPMOST_PRIORITY_OVERLAY:
        return int(Layer.EFFECT)
    if value == TOPMOST_PRIORITY_QR_DIALOG:
        return int(Layer.DIALOG)
    if value == TOPMOST_PRIORITY_DEFAULT:
        return int(Layer.PET_UI)
    return value


class TopmostManager:
    """LayerManager 的旧接口兼容包装。"""

    ENFORCE_INTERVAL: int = 30

    def register(self, widget, priority: int = TOPMOST_PRIORITY_DEFAULT) -> None:
        get_layer_manager().register(widget, _priority_to_layer(priority))

    def unregister(self, widget) -> None:
        get_layer_manager().unregister(widget)

    def enforce_on_frame(self) -> None:
        get_layer_manager().enforce_on_frame()

    def pause(self) -> None:
        get_layer_manager().pause()

    def resume(self) -> None:
        get_layer_manager().resume()

    def bring_to_front(self, widget) -> None:
        get_layer_manager().bring_to_front(widget)

    def enforce_now(self) -> None:
        get_layer_manager().enforce_now()

    def enforce_burst(self, delays_ms: tuple[int, ...] = (0, 16, 48, 96, 180)) -> None:
        get_layer_manager().enforce_burst(delays_ms)


_INSTANCE = None


def get_topmost_manager() -> TopmostManager:
    """返回旧接口兼容单例。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TopmostManager()
    return _INSTANCE
