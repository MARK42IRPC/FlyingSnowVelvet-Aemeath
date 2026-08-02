"""Backend-neutral state for resource-based drawing."""
from __future__ import annotations

from collections.abc import Iterable

from lib.core.layer import Layer, draw_order_key, normalize_layer
from .commands import DrawRequest


class DrawScene:
    """Own resources, animation state, and draw requests without rendering."""

    def __init__(self) -> None:
        self._resources: dict[str, list[object]] = {}
        self._current_frames: dict[str, int] = {}
        self._active_requests: dict[str, DrawRequest] = {}
        self._request_seq = 0

    def register_resource(self, resource_id: str, frames: Iterable[object]) -> None:
        self._resources[resource_id] = list(frames)
        self._current_frames[resource_id] = 0

    def unregister_resource(self, resource_id: str) -> None:
        self._resources.pop(resource_id, None)
        self._current_frames.pop(resource_id, None)
        self._active_requests.pop(resource_id, None)

    def has_resource(self, resource_id: str) -> bool:
        return resource_id in self._resources

    def get_frame_count(self, resource_id: str) -> int:
        return len(self._resources.get(resource_id, []))

    def get_current_frame_index(self, resource_id: str) -> int:
        return self._current_frames.get(resource_id, 0)

    def next_frame(self, resource_id: str) -> tuple[object, bool] | None:
        frames = self._resources.get(resource_id, [])
        if not frames:
            return None

        current = self._current_frames.get(resource_id, 0)
        loop_completed = current == len(frames) - 1
        current = (current + 1) % len(frames)
        self._current_frames[resource_id] = current
        return frames[current], loop_completed

    def get_frame(self, resource_id: str, frame_index: int = -1) -> object | None:
        frames = self._resources.get(resource_id, [])
        if not frames:
            return None
        if frame_index == -1:
            frame_index = self._current_frames.get(resource_id, 0)
        if 0 <= frame_index < len(frames):
            return frames[frame_index]
        return None

    def reset_frame(self, resource_id: str) -> None:
        if resource_id in self._current_frames:
            self._current_frames[resource_id] = 0

    def add_draw_request(self, request: DrawRequest, clear_others: bool = False) -> None:
        if clear_others:
            self._active_requests.clear()

        request.layer = normalize_layer(request.layer, Layer.MAIN_PET)
        try:
            request.z = int(request.z)
        except (TypeError, ValueError):
            request.z = 0

        existing = self._active_requests.get(request.resource_id)
        if existing is not None:
            request.order = existing.order
        else:
            self._request_seq += 1
            request.order = self._request_seq
        self._active_requests[request.resource_id] = request

    def remove_draw_request(self, resource_id: str) -> None:
        self._active_requests.pop(resource_id, None)

    def clear_all_requests(self) -> None:
        self._active_requests.clear()

    def ordered_requests(self) -> list[DrawRequest]:
        return sorted(
            self._active_requests.values(),
            key=lambda item: draw_order_key(item.layer, item.z, item.order, Layer.MAIN_PET),
        )

    def get_active_resource_ids(self) -> list[str]:
        return list(self._active_requests)

    def set_request_alpha(self, resource_id: str, alpha: float) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            request.alpha = max(0.0, min(1.0, alpha))

    def set_request_flipped(self, resource_id: str, flipped: bool) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            request.flipped = bool(flipped)

    def set_request_position(self, resource_id: str, position: object) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            request.position = position

    def set_request_scale(self, resource_id: str, scale: float) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            request.scale = scale

    def cleanup(self) -> None:
        self._resources.clear()
        self._current_frames.clear()
        self._active_requests.clear()
