"""Backend-neutral draw facade configured by the desktop composition root."""
from __future__ import annotations

from lib.core.desktop_backend import get_draw_backend_factory
from lib.core.graphics.backend import DrawBackend
from lib.core.graphics.commands import DrawRequest
from lib.core.graphics.scene import DrawScene


class DrawCore:
    """Compatibility facade over a backend-neutral draw scene."""

    def __init__(self, backend: DrawBackend | None = None) -> None:
        self._scene = DrawScene()
        self._backend = backend if backend is not None else self._create_default_backend()

    @property
    def _active_requests(self) -> dict[str, DrawRequest]:
        """Compatibility view used by existing ordering tests."""
        return self._scene._active_requests

    def register_resource(self, resource_id: str, frames: list[object]) -> None:
        self._scene.register_resource(resource_id, frames)

    def unregister_resource(self, resource_id: str) -> None:
        self._scene.unregister_resource(resource_id)

    def has_resource(self, resource_id: str) -> bool:
        return self._scene.has_resource(resource_id)

    def get_frame_count(self, resource_id: str) -> int:
        return self._scene.get_frame_count(resource_id)

    def get_current_frame_index(self, resource_id: str) -> int:
        return self._scene.get_current_frame_index(resource_id)

    def next_frame(self, resource_id: str) -> tuple[object, bool] | None:
        return self._scene.next_frame(resource_id)

    def get_frame(self, resource_id: str, frame_index: int = -1) -> object | None:
        return self._scene.get_frame(resource_id, frame_index)

    def reset_frame(self, resource_id: str) -> None:
        self._scene.reset_frame(resource_id)

    def add_draw_request(self, request: DrawRequest, clear_others: bool = False) -> None:
        self._scene.add_draw_request(request, clear_others)

    def remove_draw_request(self, resource_id: str) -> None:
        self._scene.remove_draw_request(resource_id)

    def clear_all_requests(self) -> None:
        self._scene.clear_all_requests()

    def render(self, painter: object, target_rect: object | None = None) -> None:
        self._backend.render(self._scene, painter, target_rect)

    def get_active_resource_ids(self) -> list[str]:
        return self._scene.get_active_resource_ids()

    def set_request_alpha(self, resource_id: str, alpha: float) -> None:
        self._scene.set_request_alpha(resource_id, alpha)

    def set_request_flipped(self, resource_id: str, flipped: bool) -> None:
        self._scene.set_request_flipped(resource_id, flipped)

    def set_request_position(self, resource_id: str, position: object) -> None:
        self._scene.set_request_position(resource_id, position)

    def set_request_scale(self, resource_id: str, scale: float) -> None:
        self._scene.set_request_scale(resource_id, scale)

    def cleanup(self) -> None:
        self._scene.cleanup()
        self._backend.cleanup()

    @staticmethod
    def _create_default_backend() -> DrawBackend:
        factory = get_draw_backend_factory()
        return factory() if factory is not None else _NullDrawBackend()


class _NullDrawBackend:
    """No-op backend used when core code runs without a desktop host."""

    def render(self, scene: DrawScene, painter: object, target_rect: object | None = None) -> None:
        return None

    def cleanup(self) -> None:
        return None


_draw_core: DrawCore | None = None


def get_draw_core() -> DrawCore:
    global _draw_core
    if _draw_core is None:
        _draw_core = DrawCore()
    return _draw_core


def cleanup_draw_core() -> None:
    global _draw_core
    if _draw_core is not None:
        _draw_core.cleanup()
        _draw_core = None
