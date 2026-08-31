"""ctypes adapter for the versioned DirectX offscreen ABI.

This module is deliberately not registered with ``BackendRouter``.  It is a
diagnostic backend used by Windows/WARP tests until the complete desktop
bundle exists.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from lib.core.graphics.commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    EllipseCommand,
    LineCommand,
    RectCommand,
    SpriteCommand,
    TextCommand,
    TransformPop,
    TransformPush,
)
from lib.core.graphics.resources import RasterFrame
from lib.core.graphics.types import Color, Rect


FSDX_ABI_VERSION = 9
FSDX_RUNTIME_FLAG_WARP = 0x00000001
FSDX_DRAW_FLAG_FLIPPED = 0x00000001
FSDX_DRAW_FLAG_HAS_FILL = 0x00000002
FSDX_DRAW_FLAG_HAS_STROKE = 0x00000004
FSDX_DRAW_FLAG_TEXT_BOLD = 0x00000008
FSDX_COMMAND_SPRITE = 1
FSDX_COMMAND_LINE = 2
FSDX_COMMAND_RECT = 3
FSDX_COMMAND_ELLIPSE = 4
FSDX_COMMAND_TEXT = 5
FSDX_COMMAND_CLIP_PUSH = 6
FSDX_COMMAND_CLIP_POP = 7
FSDX_COMMAND_TRANSFORM_PUSH = 8
FSDX_COMMAND_TRANSFORM_POP = 9
FSDX_STATUS_OK = 0
FSDX_STATUS_INVALID_ARGUMENT = 1
FSDX_STATUS_ABI_MISMATCH = 2
FSDX_STATUS_BUFFER_TOO_SMALL = 7
FSDX_STATUS_UNSUPPORTED = 8
FSDX_STATUS_DEVICE_LOST = 9


class _RuntimeDesc(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class _ResourceDesc(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("rgba_pixels", ctypes.c_void_p),
        ("rgba_size", ctypes.c_uint64),
    ]


class _DrawCommand(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("layer", ctypes.c_int32),
        ("z", ctypes.c_int32),
        ("order", ctypes.c_int32),
        ("text_length", ctypes.c_uint32),
        ("resource", ctypes.c_uint64),
        ("x0", ctypes.c_float),
        ("y0", ctypes.c_float),
        ("x1", ctypes.c_float),
        ("y1", ctypes.c_float),
        ("alpha", ctypes.c_float),
        ("stroke_width", ctypes.c_float),
        ("fill_rgba", ctypes.c_uint32),
        ("stroke_rgba", ctypes.c_uint32),
        ("m11", ctypes.c_float),
        ("m12", ctypes.c_float),
        ("m21", ctypes.c_float),
        ("m22", ctypes.c_float),
        ("dx", ctypes.c_float),
        ("dy", ctypes.c_float),
        ("payload_offset", ctypes.c_uint32),
        ("payload_size", ctypes.c_uint32),
    ]


class DxBridgeError(RuntimeError):
    """Raised when the optional native DX bridge cannot complete an operation."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def find_dx_library() -> Path | None:
    """Find a locally built DLL without importing any GUI toolkit."""
    configured = os.environ.get("FLYING_SNOW_DX_DLL", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    native_root = _repo_root() / "native" / "dx_backend"
    candidates.extend([
        native_root / "build" / "cmake" / "Release" / "flying_snow_dx.dll",
        native_root / "build" / "cmake" / "Debug" / "flying_snow_dx.dll",
        native_root / "build" / "Release" / "flying_snow_dx.dll",
        native_root / "build" / "Debug" / "flying_snow_dx.dll",
        native_root / "build" / "flying_snow_dx.dll",
        _repo_root() / "build" / "flying_snow_dx.dll",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _load_library(path: Path | None = None):
    library_path = path or find_dx_library()
    if library_path is None:
        raise DxBridgeError("flying_snow_dx.dll was not found; build native/dx_backend first")
    try:
        loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
        library = loader(str(library_path))
    except OSError as exc:
        raise DxBridgeError(f"failed to load DX library {library_path}: {exc}") from exc

    library.fsdx_get_abi_version.argtypes = []
    library.fsdx_get_abi_version.restype = ctypes.c_uint32
    library.fsdx_create_runtime.argtypes = [ctypes.POINTER(_RuntimeDesc), ctypes.POINTER(ctypes.c_uint64)]
    library.fsdx_create_runtime.restype = ctypes.c_int
    library.fsdx_destroy_runtime.argtypes = [ctypes.c_uint64]
    library.fsdx_destroy_runtime.restype = ctypes.c_int
    library.fsdx_recover_device.argtypes = [ctypes.c_uint64]
    library.fsdx_recover_device.restype = ctypes.c_int
    library.fsdx_get_device_generation.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.fsdx_get_device_generation.restype = ctypes.c_int
    library.fsdx_measure_text.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
        ctypes.c_float,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float),
    ]
    library.fsdx_measure_text.restype = ctypes.c_int
    library.fsdx_register_font_file.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
    ]
    library.fsdx_register_font_file.restype = ctypes.c_int
    library.fsdx_register_resource.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(_ResourceDesc),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.fsdx_register_resource.restype = ctypes.c_int
    library.fsdx_release_resource.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    library.fsdx_release_resource.restype = ctypes.c_int
    library.fsdx_submit_frame.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(_DrawCommand),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
    ]
    library.fsdx_submit_frame.restype = ctypes.c_int
    library.fsdx_readback_rgba.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.fsdx_readback_rgba.restype = ctypes.c_int
    library.fsdx_get_last_error.argtypes = []
    library.fsdx_get_last_error.restype = ctypes.c_char_p

    version = int(library.fsdx_get_abi_version())
    if version != FSDX_ABI_VERSION:
        raise DxBridgeError(f"unsupported DX ABI version: {version}")
    return library


class DxOffscreenTarget:
    """Native WARP/D3D11 render target with deterministic RGBA readback."""

    def __init__(self, width: int, height: int, *, warp: bool = True, library=None) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._library = library or _load_library()
        self._runtime = ctypes.c_uint64()
        desc = _RuntimeDesc(
            FSDX_ABI_VERSION,
            ctypes.sizeof(_RuntimeDesc),
            self.width,
            self.height,
            FSDX_RUNTIME_FLAG_WARP if warp else 0,
        )
        self._call(
            self._library.fsdx_create_runtime,
            ctypes.byref(desc),
            ctypes.byref(self._runtime),
        )
        self._resource_handles: dict[tuple[str, int, int], int] = {}
        self._resource_revisions: dict[str, int] = {}
        self._closed = False
        try:
            self._register_default_fonts()
        except Exception:
            # Font registration is part of runtime initialization.  Do not
            # leave a native runtime alive when the visual contract cannot be
            # established.
            self._library.fsdx_destroy_runtime(self._runtime)
            self._runtime = ctypes.c_uint64()
            raise

    def _register_default_fonts(self) -> None:
        from config import font_config

        for path in (font_config._HARMONY_PATH, font_config._LAHAI_ROI_PATH):
            if not os.path.isfile(path):
                raise DxBridgeError(f"required DX font file is missing: {path}")
            encoded = os.fspath(path).encode("utf-8")
            buffer = (ctypes.c_uint8 * len(encoded)).from_buffer_copy(encoded)
            self._call(
                self._library.fsdx_register_font_file,
                self._runtime,
                buffer,
                len(encoded),
            )

    def _error_text(self) -> str:
        raw = self._library.fsdx_get_last_error()
        return raw.decode("utf-8", "replace") if raw else "unknown DX error"

    def _call(self, function, *args) -> None:
        status = int(function(*args))
        if status != FSDX_STATUS_OK:
            raise DxBridgeError(f"DX call failed ({status}): {self._error_text()}")

    def _call_render_with_recovery(self, function, *args) -> None:
        status = int(function(*args))
        if status == FSDX_STATUS_DEVICE_LOST:
            self.recover_device()
            status = int(function(*args))
        if status != FSDX_STATUS_OK:
            raise DxBridgeError(f"DX call failed ({status}): {self._error_text()}")

    @property
    def device_generation(self) -> int:
        generation = ctypes.c_uint64()
        self._call(
            self._library.fsdx_get_device_generation,
            self._runtime,
            ctypes.byref(generation),
        )
        return int(generation.value)

    def measure_text(self, text: str, font) -> tuple[float, float]:
        """Measure one unwrapped string through this runtime's DirectWrite factory."""
        value = str(text or "")
        family = str(getattr(font, "family", "") or "Segoe UI")
        text_bytes = value.encode("utf-8")
        family_bytes = family.encode("utf-8")
        text_buffer = (
            (ctypes.c_uint8 * len(text_bytes)).from_buffer_copy(text_bytes)
            if text_bytes else None
        )
        family_buffer = (
            (ctypes.c_uint8 * len(family_bytes)).from_buffer_copy(family_bytes)
            if family_bytes else None
        )
        width = ctypes.c_float()
        height = ctypes.c_float()
        flags = FSDX_DRAW_FLAG_TEXT_BOLD if bool(getattr(font, "bold", False)) else 0
        self._call(
            self._library.fsdx_measure_text,
            self._runtime,
            text_buffer,
            len(text_bytes),
            family_buffer,
            len(family_bytes),
            float(getattr(font, "pixel_size", 12)),
            flags,
            ctypes.byref(width),
            ctypes.byref(height),
        )
        return float(width.value), float(height.value)

    def recover_device(self) -> None:
        self._call(self._library.fsdx_recover_device, self._runtime)

    def _register_frame(self, command: SpriteCommand) -> int:
        key = (command.resource_id, command.resource_revision, command.frame_index)
        cached = self._resource_handles.get(key)
        if cached is not None:
            return cached
        pixels = (ctypes.c_uint8 * len(command.frame.pixels)).from_buffer_copy(command.frame.pixels)
        desc = _ResourceDesc(
            FSDX_ABI_VERSION,
            ctypes.sizeof(_ResourceDesc),
            command.frame.width,
            command.frame.height,
            ctypes.cast(pixels, ctypes.c_void_p),
            len(command.frame.pixels),
        )
        handle = ctypes.c_uint64()
        self._call_render_with_recovery(
            self._library.fsdx_register_resource,
            self._runtime,
            ctypes.byref(desc),
            ctypes.byref(handle),
        )
        self._resource_handles[key] = int(handle.value)
        self._resource_revisions[command.resource_id] = command.resource_revision
        return int(handle.value)

    def _release_stale_resources(self, batch: DrawBatch) -> None:
        active = {item.resource_id: item.revision for item in batch.resource_revisions}
        for resource_id, revision in tuple(self._resource_revisions.items()):
            if active.get(resource_id) == revision:
                continue
            for key, handle in tuple(self._resource_handles.items()):
                if key[0] != resource_id:
                    continue
                self._library.fsdx_release_resource(self._runtime, handle)
                self._resource_handles.pop(key, None)
            self._resource_revisions.pop(resource_id, None)

    @staticmethod
    def _target_rect(command: SpriteCommand, viewport: Rect | None) -> tuple[int, int, int, int]:
        if command.target_size is not None:
            width = max(1, int(round(command.target_size.width)))
            height = max(1, int(round(command.target_size.height)))
        else:
            width = max(1, int(round(command.frame.width * command.scale)))
            height = max(1, int(round(command.frame.height * command.scale)))
        position = command.position
        x = 0 if position is None else int(round(position.x))
        y = 0 if position is None else int(round(position.y))
        return x, y, width, height

    @staticmethod
    def _pack_color(color: Color | None) -> int:
        if color is None:
            return 0
        return (
            int(color.red)
            | (int(color.green) << 8)
            | (int(color.blue) << 16)
            | (int(color.alpha) << 24)
        )

    def _native_command(
        self,
        command: (
            SpriteCommand
            | TextCommand
            | LineCommand
            | RectCommand
            | EllipseCommand
            | ClipPush
            | ClipPop
            | TransformPush
            | TransformPop
        ),
        viewport: Rect | None,
        payload: bytearray,
    ) -> _DrawCommand:
        native = _DrawCommand()
        native.abi_version = FSDX_ABI_VERSION
        native.struct_size = ctypes.sizeof(_DrawCommand)

        if isinstance(command, SpriteCommand):
            native.layer = int(command.layer)
            native.z = int(command.z)
            native.order = int(command.order)
            native.alpha = float(command.alpha)
            x, y, width, height = self._target_rect(command, viewport)
            native.type = FSDX_COMMAND_SPRITE
            native.flags = FSDX_DRAW_FLAG_FLIPPED if command.flipped else 0
            native.resource = self._register_frame(command)
            native.x0 = float(x)
            native.y0 = float(y)
            native.x1 = float(width)
            native.y1 = float(height)
            return native

        if isinstance(command, LineCommand):
            native.layer = int(command.layer)
            native.z = int(command.z)
            native.order = int(command.order)
            native.alpha = float(command.alpha)
            native.type = FSDX_COMMAND_LINE
            native.x0 = float(command.start.x)
            native.y0 = float(command.start.y)
            native.x1 = float(command.end.x)
            native.y1 = float(command.end.y)
            native.stroke_width = float(command.width)
            native.stroke_rgba = self._pack_color(command.color)
            return native

        if isinstance(command, TextCommand):
            try:
                text_bytes = command.text.encode("utf-8")
                family_bytes = command.font.family.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise DxBridgeError(f"text command contains invalid Unicode: {exc}") from exc
            command_payload_size = len(text_bytes) + len(family_bytes)
            if len(text_bytes) > 0x7FFFFFFF or command_payload_size > 0xFFFFFFFF:
                raise DxBridgeError("text command payload exceeds the DX ABI limit")
            if len(payload) + command_payload_size > 0xFFFFFFFF:
                raise DxBridgeError("frame text payload exceeds the DX ABI limit")
            native.type = FSDX_COMMAND_TEXT
            native.layer = int(command.layer)
            native.z = int(command.z)
            native.order = int(command.order)
            native.alpha = float(command.alpha)
            native.flags = FSDX_DRAW_FLAG_TEXT_BOLD if command.font.bold else 0
            native.x0 = float(command.rect.x)
            native.y0 = float(command.rect.y)
            native.x1 = float(command.rect.width)
            native.y1 = float(command.rect.height)
            native.stroke_width = float(command.font.pixel_size)
            native.fill_rgba = self._pack_color(command.color)
            native.stroke_rgba = int(command.alignment)
            native.text_length = len(text_bytes)
            native.payload_offset = len(payload)
            native.payload_size = command_payload_size
            payload.extend(text_bytes)
            payload.extend(family_bytes)
            return native

        if isinstance(command, (EllipseCommand, RectCommand)):
            native.type = (
                FSDX_COMMAND_ELLIPSE
                if isinstance(command, EllipseCommand)
                else FSDX_COMMAND_RECT
            )
            native.layer = int(command.layer)
            native.z = int(command.z)
            native.order = int(command.order)
            native.alpha = float(command.alpha)
            native.x0 = float(command.rect.x)
            native.y0 = float(command.rect.y)
            native.x1 = float(command.rect.width)
            native.y1 = float(command.rect.height)
            native.stroke_width = float(command.stroke_width)
            if command.fill is not None:
                native.flags |= FSDX_DRAW_FLAG_HAS_FILL
                native.fill_rgba = self._pack_color(command.fill)
            if command.stroke is not None and command.stroke_width > 0.0:
                native.flags |= FSDX_DRAW_FLAG_HAS_STROKE
                native.stroke_rgba = self._pack_color(command.stroke)
            return native

        if isinstance(command, ClipPush):
            native.type = FSDX_COMMAND_CLIP_PUSH
            native.x0 = float(command.rect.x)
            native.y0 = float(command.rect.y)
            native.x1 = float(command.rect.width)
            native.y1 = float(command.rect.height)
            return native

        if isinstance(command, ClipPop):
            native.type = FSDX_COMMAND_CLIP_POP
            return native

        if isinstance(command, TransformPush):
            native.type = FSDX_COMMAND_TRANSFORM_PUSH
            (
                native.m11,
                native.m12,
                native.m21,
                native.m22,
                native.dx,
                native.dy,
            ) = command.matrix
            return native

        if isinstance(command, TransformPop):
            native.type = FSDX_COMMAND_TRANSFORM_POP
            return native

        raise DxBridgeError(f"unsupported DX draw command: {type(command).__name__}")

    def render_batch(self, batch: DrawBatch, viewport: Rect | None = None) -> None:
        commands = list(batch.commands)
        self._release_stale_resources(batch)
        native_commands = (_DrawCommand * len(commands))()
        payload = bytearray()
        for index, command in enumerate(commands):
            native_commands[index] = self._native_command(command, viewport, payload)
        command_pointer = native_commands if commands else None
        native_payload = (
            (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
            if payload
            else None
        )
        self._call_render_with_recovery(
            self._library.fsdx_submit_frame,
            self._runtime,
            command_pointer,
            len(commands),
            native_payload,
            len(payload),
        )

    def readback_rgba(self) -> bytes:
        size = self.width * self.height * 4
        output = (ctypes.c_uint8 * size)()
        written = ctypes.c_uint64()
        self._call_render_with_recovery(
            self._library.fsdx_readback_rgba,
            self._runtime,
            output,
            size,
            ctypes.byref(written),
        )
        if int(written.value) != size:
            raise DxBridgeError(f"unexpected DX readback size: {written.value}")
        return bytes(output)

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in tuple(self._resource_handles.values()):
            self._library.fsdx_release_resource(self._runtime, handle)
        self._resource_handles.clear()
        self._resource_revisions.clear()
        self._library.fsdx_destroy_runtime(self._runtime)

    close = cleanup

    def __enter__(self) -> "DxOffscreenTarget":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.cleanup()


class DxOffscreenBackend:
    """DrawBackend-compatible adapter around a ``DxOffscreenTarget``."""

    def __init__(self, target: DxOffscreenTarget | None = None) -> None:
        self.target = target

    def render(self, batch: DrawBatch, target: object, viewport: Rect | None = None) -> None:
        if not isinstance(target, DxOffscreenTarget):
            raise TypeError("DX offscreen backend requires a DxOffscreenTarget")
        target.render_batch(batch, viewport)

    def cleanup(self) -> None:
        if self.target is not None:
            self.target.cleanup()
            self.target = None


__all__ = [
    "DxBridgeError",
    "DxOffscreenBackend",
    "DxOffscreenTarget",
    "find_dx_library",
]
