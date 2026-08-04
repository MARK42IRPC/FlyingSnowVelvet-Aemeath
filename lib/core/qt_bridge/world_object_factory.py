"""Qt factory boundary for desktop world-object widgets."""
from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Callable

from lib.core.graphics.types import Point
from lib.core.qt_bridge.window import to_qpoint


@lru_cache(maxsize=None)
def _resolve_world_object_type(module_name: str, class_name: str) -> Callable[..., object]:
    candidate = getattr(import_module(module_name), class_name)
    if not callable(candidate):
        raise TypeError(f"world object type is not callable: {module_name}.{class_name}")
    return candidate


def create_world_object(
    module_name: str,
    class_name: str,
    *,
    position: Point | object,
    **kwargs,
) -> object:
    """Create a Qt world-object widget from backend-neutral construction data."""
    object_type = _resolve_world_object_type(module_name, class_name)
    return object_type(position=to_qpoint(position), **kwargs)
