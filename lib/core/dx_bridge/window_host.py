"""Win32/DirectComposition window host for the diagnostic DirectX runtime."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

from lib.core.graphics.commands import DrawBatch
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import (
    Key,
    KeyboardInput,
    KeyModifier,
    MouseButton,
    MouseButtons,
    MouseInput,
)

from .offscreen import (
    FSDX_ABI_VERSION,
    DxBridgeError,
    DxOffscreenTarget,
    _DrawCommand,
)


FSDX_WINDOW_FLAG_TOPMOST = 0x00000001
FSDX_WINDOW_FLAG_TOOL = 0x00000002
FSDX_WINDOW_FLAG_NO_ACTIVATE = 0x00000004
FSDX_WINDOW_FLAG_CLICKTHROUGH = 0x00000008
FSDX_WINDOW_STATE_VISIBLE = 0x00000001
FSDX_WINDOW_STATE_CLICKTHROUGH = 0x00000002
FSDX_WINDOW_STATE_ACTIVE = 0x00000004
FSDX_WINDOW_STATE_CAPTURED = 0x00000008
FSDX_EVENT_FLAG_AUTO_REPEAT = 0x00000001
FSDX_EVENT_FLAG_TEXT_FIRST = 0x00000002
FSDX_EVENT_FLAG_TEXT_LAST = 0x00000004
FSDX_EVENT_POINTER_ENTER = 1
FSDX_EVENT_POINTER_LEAVE = 2
FSDX_EVENT_POINTER_PRESS = 3
FSDX_EVENT_POINTER_MOVE = 4
FSDX_EVENT_POINTER_RELEASE = 5
FSDX_EVENT_WINDOW_MOVED = 6
FSDX_EVENT_DPI_CHANGED = 7
FSDX_EVENT_CLOSE = 8
FSDX_EVENT_KEY_PRESS = 9
FSDX_EVENT_KEY_RELEASE = 10
FSDX_EVENT_REPAINT = 11
FSDX_EVENT_DEVICE_ERROR = 12
FSDX_EVENT_DEVICE_RECOVERED = 13
FSDX_EVENT_TEXT_INPUT = 16
FSDX_EVENT_IME_COMPOSITION = 17
FSDX_EVENT_IME_END = 18


class _WindowDesc(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class _WindowState(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("dpi", ctypes.c_uint32),
        ("native_handle", ctypes.c_uint64),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("screen_x", ctypes.c_int32),
        ("screen_y", ctypes.c_int32),
        ("screen_width", ctypes.c_uint32),
        ("screen_height", ctypes.c_uint32),
    ]


class _NativeEvent(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("window", ctypes.c_uint64),
        ("timestamp_ms", ctypes.c_uint64),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("screen_x", ctypes.c_int32),
        ("screen_y", ctypes.c_int32),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
        ("dpi", ctypes.c_uint32),
        ("key", ctypes.c_uint32),
        ("button", ctypes.c_uint32),
        ("buttons", ctypes.c_uint32),
        ("modifiers", ctypes.c_uint32),
        ("repeat_count", ctypes.c_uint32),
        ("codepoint", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


@dataclass(frozen=True, slots=True)
class DxHostEvent:
    """Stable Python snapshot of one event returned by the native queue."""

    type: int
    timestamp_ms: int
    local_pos: Point
    screen_pos: Point
    size: tuple[int, int]
    dpi: int
    key: int
    button: int
    buttons: int
    modifiers: int
    repeat_count: int
    codepoint: int
    flags: int
    pointer_id: int = 0


def _configure_window_api(library) -> None:
    library.fsdx_create_window.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(_WindowDesc),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.fsdx_create_window.restype = ctypes.c_int
    library.fsdx_destroy_window.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    library.fsdx_destroy_window.restype = ctypes.c_int
    library.fsdx_get_window_state.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(_WindowState),
    ]
    library.fsdx_get_window_state.restype = ctypes.c_int
    library.fsdx_show_window.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32]
    library.fsdx_show_window.restype = ctypes.c_int
    library.fsdx_set_window_geometry.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    library.fsdx_set_window_geometry.restype = ctypes.c_int
    library.fsdx_set_window_clickthrough.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint32,
    ]
    library.fsdx_set_window_clickthrough.restype = ctypes.c_int
    library.fsdx_set_window_capture.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32]
    library.fsdx_set_window_capture.restype = ctypes.c_int
    library.fsdx_activate_window.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    library.fsdx_activate_window.restype = ctypes.c_int
    library.fsdx_set_window_ime_position.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_int32,
        ctypes.c_int32,
    ]
    library.fsdx_set_window_ime_position.restype = ctypes.c_int
    library.fsdx_stack_window.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int64]
    library.fsdx_stack_window.restype = ctypes.c_int
    library.fsdx_request_window_repaint.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    library.fsdx_request_window_repaint.restype = ctypes.c_int
    library.fsdx_submit_window_frame.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(_DrawCommand),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
    ]
    library.fsdx_submit_window_frame.restype = ctypes.c_int
    library.fsdx_poll_events.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(_NativeEvent),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.fsdx_poll_events.restype = ctypes.c_int


_VK_TO_KEY = {
    0x08: Key.BACKSPACE,
    0x09: Key.TAB,
    0x0D: Key.RETURN,
    0x1B: Key.ESCAPE,
    0x21: Key.PAGE_UP,
    0x22: Key.PAGE_DOWN,
    0x23: Key.END,
    0x24: Key.HOME,
    0x25: Key.LEFT,
    0x26: Key.UP,
    0x27: Key.RIGHT,
    0x28: Key.DOWN,
    0x2D: Key.INSERT,
    0x2E: Key.DELETE,
}
for _value in range(0x30, 0x3A):
    _VK_TO_KEY[_value] = Key(_value)
for _value in range(0x41, 0x5B):
    _VK_TO_KEY[_value] = Key(_value)
for _offset in range(24):
    _VK_TO_KEY[0x70 + _offset] = Key(int(Key.F1) + _offset)
_VK_TO_KEY[0x20] = Key.SPACE


class DxWindowHost(DxOffscreenTarget):
    """One HWND and composition swap chain backed by a DX runtime.

    The host is intentionally diagnostic and is not registered as a complete
    desktop backend. Call :meth:`poll_events` from the owning thread.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        x: int = 0,
        y: int = 0,
        callbacks: object | None = None,
        warp: bool = False,
        topmost: bool = True,
        tool_window: bool = True,
        no_activate: bool = False,
        clickthrough: bool = False,
        library=None,
    ) -> None:
        super().__init__(width, height, warp=warp, library=library)
        _configure_window_api(self._library)
        self._callbacks = callbacks
        self._window = ctypes.c_uint64()
        self._window_closed = False
        self._repaint_viewport: Rect | None = None
        self.last_device_error: int | None = None
        self.last_device_recovery_generation: int | None = None
        self._ime_codepoints: list[str] = []
        flags = 0
        if topmost:
            flags |= FSDX_WINDOW_FLAG_TOPMOST
        if tool_window:
            flags |= FSDX_WINDOW_FLAG_TOOL
        if no_activate:
            flags |= FSDX_WINDOW_FLAG_NO_ACTIVATE
        if clickthrough:
            flags |= FSDX_WINDOW_FLAG_CLICKTHROUGH
        desc = _WindowDesc(
            FSDX_ABI_VERSION,
            ctypes.sizeof(_WindowDesc),
            int(x),
            int(y),
            self.width,
            self.height,
            flags,
            0,
        )
        try:
            self._call(
                self._library.fsdx_create_window,
                self._runtime,
                ctypes.byref(desc),
                ctypes.byref(self._window),
            )
        except Exception:
            super().cleanup()
            raise

    @property
    def identity(self) -> int:
        return int(self._window.value)

    def _state(self) -> _WindowState:
        if self._window_closed:
            raise DxBridgeError("DX window has been closed")
        state = _WindowState()
        self._call(
            self._library.fsdx_get_window_state,
            self._runtime,
            self._window,
            ctypes.byref(state),
        )
        if state.abi_version != FSDX_ABI_VERSION or state.struct_size != ctypes.sizeof(_WindowState):
            raise DxBridgeError("DX window state ABI mismatch")
        return state

    @property
    def native_handle(self) -> int | None:
        if self._window_closed:
            return None
        handle = int(self._state().native_handle)
        return handle or None

    def is_alive(self) -> bool:
        return not self._window_closed and not self._closed

    def is_visible(self) -> bool:
        return self.is_alive() and bool(self._state().flags & FSDX_WINDOW_STATE_VISIBLE)

    def show(self) -> None:
        if self.is_alive():
            self._call(self._library.fsdx_show_window, self._runtime, self._window, 1)

    def hide(self) -> None:
        if self.is_alive():
            self._call(self._library.fsdx_show_window, self._runtime, self._window, 0)

    def get_geometry(self) -> Rect:
        if not self.is_alive():
            return Rect()
        state = self._state()
        return Rect(state.x, state.y, state.width, state.height)

    def set_geometry(self, geometry: Rect) -> None:
        if not isinstance(geometry, Rect):
            raise TypeError("geometry must be a Rect")
        if not self.is_alive():
            return
        self._call_render_with_recovery(
            self._library.fsdx_set_window_geometry,
            self._runtime,
            self._window,
            int(round(geometry.x)),
            int(round(geometry.y)),
            max(1, int(round(geometry.width))),
            max(1, int(round(geometry.height))),
        )
        self.width = max(1, int(round(geometry.width)))
        self.height = max(1, int(round(geometry.height)))

    def get_dpi(self) -> int:
        return int(self._state().dpi) if self.is_alive() else 96

    def get_screen_geometry(self) -> Rect | None:
        if not self.is_alive():
            return None
        state = self._state()
        if state.screen_width == 0 or state.screen_height == 0:
            return None
        return Rect(state.screen_x, state.screen_y, state.screen_width, state.screen_height)

    def set_clickthrough(self, enabled: bool) -> None:
        if self.is_alive():
            self._call(
                self._library.fsdx_set_window_clickthrough,
                self._runtime,
                self._window,
                int(bool(enabled)),
            )

    def is_clickthrough_enabled(self) -> bool:
        return self.is_alive() and bool(self._state().flags & FSDX_WINDOW_STATE_CLICKTHROUGH)

    def is_active(self) -> bool:
        return self.is_alive() and bool(self._state().flags & FSDX_WINDOW_STATE_ACTIVE)

    def activate(self) -> None:
        if self.is_alive():
            self._call(self._library.fsdx_activate_window, self._runtime, self._window)

    def set_ime_position(self, x: int, y: int) -> None:
        """Place the native IME composition/candidate window in client pixels."""
        if self.is_alive():
            self._call(
                self._library.fsdx_set_window_ime_position,
                self._runtime,
                self._window,
                int(x),
                int(y),
            )

    def capture_mouse(self) -> None:
        if self.is_alive():
            self._call(self._library.fsdx_set_window_capture, self._runtime, self._window, 1)

    def release_mouse(self) -> None:
        if self.is_alive():
            self._call(self._library.fsdx_set_window_capture, self._runtime, self._window, 0)

    def has_mouse_capture(self) -> bool:
        return self.is_alive() and bool(self._state().flags & FSDX_WINDOW_STATE_CAPTURED)

    def raise_window(self) -> None:
        self.stack_window(None)

    def stack_window(self, insert_after: int | None) -> int | None:
        if not self.is_alive():
            return None
        try:
            self._call(
                self._library.fsdx_stack_window,
                self._runtime,
                self._window,
                -1 if insert_after is None else int(insert_after),
            )
        except DxBridgeError:
            return None
        return self.native_handle

    def request_repaint(self, viewport: Rect | None = None) -> None:
        if viewport is not None and not isinstance(viewport, Rect):
            raise TypeError("viewport must be a Rect or None")
        if self.is_alive():
            self._repaint_viewport = viewport
            self._call(
                self._library.fsdx_request_window_repaint,
                self._runtime,
                self._window,
            )

    def render_batch(self, batch: DrawBatch, viewport: Rect | None = None) -> None:
        if not isinstance(batch, DrawBatch):
            raise TypeError("DX window host requires a DrawBatch")
        commands = list(batch.commands)
        self._release_stale_resources(batch)
        native_commands = (_DrawCommand * len(commands))()
        payload = bytearray()
        for index, command in enumerate(commands):
            native_commands[index] = self._native_command(command, viewport, payload)
        native_payload = (
            (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
            if payload
            else None
        )
        self._call_render_with_recovery(
            self._library.fsdx_submit_window_frame,
            self._runtime,
            self._window,
            native_commands if commands else None,
            len(commands),
            native_payload,
            len(payload),
        )

    @staticmethod
    def _snapshot_event(event: _NativeEvent) -> DxHostEvent:
        if event.abi_version != FSDX_ABI_VERSION or event.struct_size != ctypes.sizeof(_NativeEvent):
            raise DxBridgeError("DX host event ABI mismatch")
        return DxHostEvent(
            type=int(event.type),
            timestamp_ms=int(event.timestamp_ms),
            local_pos=Point(event.x, event.y),
            screen_pos=Point(event.screen_x, event.screen_y),
            size=(int(event.width), int(event.height)),
            dpi=int(event.dpi),
            key=int(event.key),
            button=int(event.button),
            buttons=int(event.buttons),
            modifiers=int(event.modifiers),
            repeat_count=int(event.repeat_count),
            codepoint=int(event.codepoint),
            flags=int(event.flags),
            pointer_id=int(event.reserved),
        )

    def poll_events(self, capacity: int = 64, *, dispatch: bool = True) -> tuple[DxHostEvent, ...]:
        if not self.is_alive():
            return ()
        capacity = max(1, min(1024, int(capacity)))
        native_events = (_NativeEvent * capacity)()
        written = ctypes.c_uint32()
        pending = ctypes.c_uint32()
        self._call(
            self._library.fsdx_poll_events,
            self._runtime,
            native_events,
            capacity,
            ctypes.byref(written),
            ctypes.byref(pending),
        )
        events = tuple(self._snapshot_event(native_events[index]) for index in range(written.value))
        if dispatch:
            for event in events:
                self._dispatch_event(event)
                if not self.is_alive():
                    break
        return events

    @staticmethod
    def _mouse_button(value: int) -> MouseButton:
        try:
            return MouseButton(value)
        except ValueError:
            return MouseButton.NONE

    def _dispatch_event(self, event: DxHostEvent) -> None:
        if event.type == FSDX_EVENT_DPI_CHANGED and event.size[0] > 0 and event.size[1] > 0:
            self.width = event.size[0]
            self.height = event.size[1]
        if event.type == FSDX_EVENT_DEVICE_ERROR:
            self.last_device_error = event.key
        elif event.type == FSDX_EVENT_DEVICE_RECOVERED:
            self.last_device_recovery_generation = event.key
        callbacks = self._callbacks
        if callbacks is None:
            return
        if event.type == FSDX_EVENT_POINTER_ENTER:
            callbacks.handle_pointer_enter()
        elif event.type == FSDX_EVENT_POINTER_LEAVE:
            callbacks.handle_pointer_leave()
        elif event.type in (FSDX_EVENT_POINTER_PRESS, FSDX_EVENT_POINTER_MOVE):
            mouse = MouseInput(
                button=self._mouse_button(event.button),
                buttons=MouseButtons(event.buttons),
                global_pos=event.screen_pos,
                pos=event.local_pos,
                pet=callbacks,
            )
            if event.type == FSDX_EVENT_POINTER_PRESS:
                callbacks.handle_pointer_press(mouse)
            else:
                callbacks.handle_pointer_move(mouse)
        elif event.type == FSDX_EVENT_POINTER_RELEASE:
            callbacks.handle_pointer_release(self._mouse_button(event.button))
        elif event.type in (FSDX_EVENT_WINDOW_MOVED, FSDX_EVENT_DPI_CHANGED):
            callbacks.handle_window_moved(event.local_pos)
        elif event.type in (FSDX_EVENT_KEY_PRESS, FSDX_EVENT_KEY_RELEASE):
            key = _VK_TO_KEY.get(event.key, event.key)
            keyboard = KeyboardInput(
                key=key,
                text="",
                modifiers=KeyModifier(event.modifiers),
                is_auto_repeat=bool(event.flags & FSDX_EVENT_FLAG_AUTO_REPEAT),
                pet=callbacks,
            )
            if event.type == FSDX_EVENT_KEY_PRESS:
                callbacks.handle_key_press(keyboard)
            else:
                callbacks.handle_key_release(keyboard)
        elif event.type == FSDX_EVENT_TEXT_INPUT:
            if event.codepoint:
                try:
                    text = chr(event.codepoint)
                except ValueError:
                    text = ""
                callback = getattr(callbacks, "handle_text_input", None)
                if callable(callback) and text:
                    callback(text * max(1, event.repeat_count))
        elif event.type == FSDX_EVENT_IME_COMPOSITION:
            if event.flags & FSDX_EVENT_FLAG_TEXT_FIRST:
                self._ime_codepoints.clear()
            if event.codepoint:
                try:
                    self._ime_codepoints.append(chr(event.codepoint))
                except ValueError:
                    pass
            if event.flags & FSDX_EVENT_FLAG_TEXT_LAST:
                callback = getattr(callbacks, "handle_ime_composition", None)
                if callable(callback):
                    callback("".join(self._ime_codepoints))
        elif event.type == FSDX_EVENT_IME_END:
            self._ime_codepoints.clear()
            callback = getattr(callbacks, "handle_ime_end", None)
            if callable(callback):
                callback()
        elif event.type == FSDX_EVENT_REPAINT:
            draw_source = callbacks.prepare_render()
            if draw_source is None:
                return
            batch = draw_source if isinstance(draw_source, DrawBatch) else draw_source.build_batch()
            viewport = self._repaint_viewport
            self._repaint_viewport = None
            self.render_batch(batch, viewport)
        elif event.type == FSDX_EVENT_CLOSE:
            callbacks.handle_host_close()

    def cleanup(self) -> None:
        self._ime_codepoints.clear()
        if not self._window_closed:
            self._call(self._library.fsdx_destroy_window, self._runtime, self._window)
            self._window_closed = True
        super().cleanup()

    close = cleanup

    def shutdown_host(self) -> None:
        callbacks = self._callbacks
        if callbacks is not None:
            callbacks.handle_host_close()
        self.cleanup()


def create_dx_layer_window_host(window: object) -> DxWindowHost:
    """Resolve a native DX host for ``LayerManager`` composition."""
    if isinstance(window, DxWindowHost):
        return window
    host = getattr(window, "window_host", None)
    if isinstance(host, DxWindowHost):
        return host
    raise TypeError("DX layer window factory requires a DxWindowHost")


__all__ = ["DxHostEvent", "DxWindowHost", "create_dx_layer_window_host"]
