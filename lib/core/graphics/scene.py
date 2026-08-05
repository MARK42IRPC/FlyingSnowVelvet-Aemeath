"""Backend-neutral resource state and draw-batch construction."""
from __future__ import annotations

from dataclasses import dataclass

from lib.core.layer import Layer, draw_order_key, normalize_layer
from .commands import DrawBatch, DrawRequest, ResourceRevision, SpriteCommand
from .resources import ImageResource, RasterFrame
from .types import coerce_point


@dataclass(frozen=True, slots=True)
class _RegisteredResource:
    resource: ImageResource
    revision: int


class DrawScene:
    """Own resources, animation state, and draw requests without rendering."""

    def __init__(self) -> None:
        self._resources: dict[str, _RegisteredResource] = {}
        self._current_frames: dict[str, int] = {}
        self._active_requests: dict[str, DrawRequest] = {}
        self._resource_seq = 0
        self._request_seq = 0

    def register_resource(self, resource: ImageResource) -> None:
        if not isinstance(resource, ImageResource):
            raise TypeError("resource must be an ImageResource")
        self._resource_seq += 1
        self._resources[resource.resource_id] = _RegisteredResource(
            resource=resource,
            revision=self._resource_seq,
        )
        self._current_frames[resource.resource_id] = 0

    def unregister_resource(self, resource_id: str) -> None:
        self._resources.pop(resource_id, None)
        self._current_frames.pop(resource_id, None)
        self._active_requests.pop(resource_id, None)

    def has_resource(self, resource_id: str) -> bool:
        return resource_id in self._resources

    def get_frame_count(self, resource_id: str) -> int:
        registered = self._resources.get(resource_id)
        return len(registered.resource.frames) if registered is not None else 0

    def get_current_frame_index(self, resource_id: str) -> int:
        return self._current_frames.get(resource_id, 0)

    def next_frame(self, resource_id: str) -> tuple[RasterFrame, bool] | None:
        registered = self._resources.get(resource_id)
        if registered is None:
            return None
        frames = registered.resource.frames

        current = self._current_frames.get(resource_id, 0)
        loop_completed = current == len(frames) - 1
        current = (current + 1) % len(frames)
        self._current_frames[resource_id] = current
        return frames[current], loop_completed

    def get_frame(self, resource_id: str, frame_index: int = -1) -> RasterFrame | None:
        registered = self._resources.get(resource_id)
        if registered is None:
            return None
        frames = registered.resource.frames
        if frame_index == -1:
            frame_index = self._current_frames.get(resource_id, 0)
        if 0 <= frame_index < len(frames):
            return frames[frame_index]
        return None

    def reset_frame(self, resource_id: str) -> None:
        if resource_id in self._current_frames:
            self._current_frames[resource_id] = 0

    def add_draw_request(self, request: DrawRequest, clear_others: bool = False) -> None:
        if not isinstance(request, DrawRequest):
            raise TypeError("request must be a DrawRequest")
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

    def build_batch(self) -> DrawBatch:
        commands: list[SpriteCommand] = []
        for request in self.ordered_requests():
            registered = self._resources.get(request.resource_id)
            if registered is None:
                continue
            frame_index = request.frame_index
            if frame_index == -1:
                frame_index = self._current_frames.get(request.resource_id, 0)
            frames = registered.resource.frames
            if frame_index < 0 or frame_index >= len(frames):
                continue
            commands.append(SpriteCommand(
                resource_id=request.resource_id,
                resource_revision=registered.revision,
                frame_index=frame_index,
                frame=frames[frame_index],
                position=request.position,
                alpha=request.alpha,
                flipped=request.flipped,
                scale=request.scale,
                layer=request.layer,
                z=request.z,
                order=request.order,
            ))
        resource_revisions = tuple(
            ResourceRevision(resource_id, registered.revision)
            for resource_id, registered in self._resources.items()
        )
        return DrawBatch(tuple(commands), resource_revisions)

    def get_active_resource_ids(self) -> list[str]:
        return list(self._active_requests)

    def set_request_alpha(self, resource_id: str, alpha: float) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            try:
                request.alpha = max(0.0, min(1.0, float(alpha)))
            except (TypeError, ValueError):
                request.alpha = 1.0

    def set_request_flipped(self, resource_id: str, flipped: bool) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            request.flipped = bool(flipped)

    def set_request_position(self, resource_id: str, position: object) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            point = coerce_point(position) if position is not None else None
            if position is not None and point is None:
                raise TypeError(f"invalid draw request position: {position!r}")
            request.position = point

    def set_request_scale(self, resource_id: str, scale: float) -> None:
        request = self._active_requests.get(resource_id)
        if request is not None:
            try:
                request.scale = max(0.0, float(scale))
            except (TypeError, ValueError):
                request.scale = 1.0

    def cleanup(self) -> None:
        self._resources.clear()
        self._current_frames.clear()
        self._active_requests.clear()
