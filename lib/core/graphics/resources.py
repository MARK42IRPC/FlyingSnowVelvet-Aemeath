"""Backend-neutral raster resource contracts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RasterFrame:
    """One tightly packed RGBA8888 frame owned by Python memory."""

    width: int
    height: int
    pixels: bytes
    duration_ms: int = 0

    def __post_init__(self) -> None:
        width = int(self.width)
        height = int(self.height)
        duration_ms = max(0, int(self.duration_ms))
        pixels = bytes(self.pixels)
        if width <= 0 or height <= 0:
            raise ValueError("raster frame dimensions must be positive")
        expected_size = width * height * 4
        if len(pixels) != expected_size:
            raise ValueError(
                f"RGBA8888 frame requires {expected_size} bytes, got {len(pixels)}"
            )
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "pixels", pixels)
        object.__setattr__(self, "duration_ms", duration_ms)

    @property
    def stride(self) -> int:
        return self.width * 4


@dataclass(frozen=True, slots=True)
class ImageResource:
    """A stable resource identifier and its immutable raster frames."""

    resource_id: str
    frames: tuple[RasterFrame, ...]

    def __post_init__(self) -> None:
        resource_id = str(self.resource_id or "").strip()
        frames = tuple(self.frames)
        if not resource_id:
            raise ValueError("image resource id must not be empty")
        if not frames:
            raise ValueError(f"image resource '{resource_id}' must contain at least one frame")
        if any(not isinstance(frame, RasterFrame) for frame in frames):
            raise TypeError("image resource frames must be RasterFrame values")
        object.__setattr__(self, "resource_id", resource_id)
        object.__setattr__(self, "frames", frames)

    @property
    def size(self) -> tuple[int, int]:
        """Return the logical size of the first frame."""
        frame = self.frames[0]
        return frame.width, frame.height
