"""Decode and resize backend-neutral RGBA image resources."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageSequence

from .resources import ImageResource, RasterFrame


def _resource_id_for_path(path: Path) -> str:
    normalized = path.resolve().as_posix().casefold().encode("utf-8")
    digest = sha256(normalized).hexdigest()[:16]
    return f"image:{path.stem}:{digest}"


def decode_image_frames(path: str | Path) -> tuple[RasterFrame, ...]:
    """Decode a static image or animation into composited RGBA8888 frames."""
    resolved = Path(path)
    if not resolved.is_file():
        return ()

    frames: list[RasterFrame] = []
    try:
        with Image.open(resolved) as image:
            size = image.size
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            for frame in ImageSequence.Iterator(image):
                disposal = frame.info.get("disposal", 2)
                offset = frame.info.get("offset", (0, 0))
                frame_rgba = frame.convert("RGBA")
                if disposal == 2:
                    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                canvas.paste(frame_rgba, offset, frame_rgba)
                width, height = canvas.size
                frames.append(RasterFrame(
                    width=width,
                    height=height,
                    pixels=canvas.tobytes("raw", "RGBA"),
                    duration_ms=frame.info.get("duration", 0),
                ))
    except (OSError, ValueError):
        return ()
    return tuple(frames)


def load_image_resource(
    path: str | Path,
    *,
    resource_id: str | None = None,
) -> ImageResource | None:
    """Load a static image or animation without creating toolkit objects."""
    resolved = Path(path)
    frames = decode_image_frames(resolved)
    if not frames:
        return None
    return ImageResource(resource_id or _resource_id_for_path(resolved), frames)


def _resized_frame(
    frame: RasterFrame,
    size: tuple[int, int],
) -> RasterFrame:
    width, height = size
    image = Image.frombytes("RGBA", (frame.width, frame.height), frame.pixels)
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    return RasterFrame(
        width=width,
        height=height,
        pixels=resized.tobytes("raw", "RGBA"),
        duration_ms=frame.duration_ms,
    )


def resize_image_resource(
    resource: ImageResource,
    size: tuple[int, int],
    *,
    keep_aspect: bool = False,
) -> ImageResource:
    """Resize every frame and derive a stable transformed resource ID."""
    target_width = int(size[0])
    target_height = int(size[1])
    if target_width <= 0 or target_height <= 0:
        raise ValueError("image resource target dimensions must be positive")

    if keep_aspect:
        source_width, source_height = resource.size
        scale = min(target_width / source_width, target_height / source_height)
        target_width = max(1, int(round(source_width * scale)))
        target_height = max(1, int(round(source_height * scale)))

    target_size = (target_width, target_height)
    if all((frame.width, frame.height) == target_size for frame in resource.frames):
        return resource

    mode = "fit" if keep_aspect else "stretch"
    resource_id = (
        f"{resource.resource_id}@{target_width}x{target_height}:{mode}"
    )
    return ImageResource(
        resource_id,
        tuple(_resized_frame(frame, target_size) for frame in resource.frames),
    )


def resize_image_resource_to_width(
    resource: ImageResource,
    width: int,
) -> ImageResource:
    """Resize frames to a target width while preserving aspect ratio."""
    target_width = int(width)
    if target_width <= 0:
        raise ValueError("image resource target width must be positive")
    source_width, source_height = resource.size
    target_height = max(1, int(round(source_height * target_width / source_width)))
    return resize_image_resource(resource, (target_width, target_height))


def resize_image_resource_to_height(
    resource: ImageResource,
    height: int,
) -> ImageResource:
    """Resize frames to a target height while preserving aspect ratio."""
    target_height = int(height)
    if target_height <= 0:
        raise ValueError("image resource target height must be positive")
    source_width, source_height = resource.size
    target_width = max(1, int(round(source_width * target_height / source_height)))
    return resize_image_resource(resource, (target_width, target_height))
