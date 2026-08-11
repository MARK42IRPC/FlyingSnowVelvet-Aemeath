"""Win32 notification-area host backed by the DX native event queue."""
from __future__ import annotations

import ctypes
from collections.abc import Callable
from pathlib import Path

from lib.core.logger import get_logger
from lib.core.tray_host import (
    TrayCommand,
    TrayCommandCallback,
    TrayMenuState,
)

from .loop import DxLoopContext, DxScheduledCall
from .offscreen import FSDX_ABI_VERSION, DxBridgeError, DxOffscreenTarget
from .window_host import _NativeEvent


FSDX_TRAY_STATE_VISIBLE = 0x00000001
FSDX_EVENT_FLAG_CHECKED = 0x00000100
FSDX_TRAY_MENU_STATE_GAME_MODE = 0x00000001
FSDX_TRAY_MENU_STATE_CLICKTHROUGH = 0x00000002
FSDX_TRAY_MENU_STATE_AUTOSTART = 0x00000004
FSDX_TRAY_COMMAND_ANNOUNCEMENT = int(TrayCommand.ANNOUNCEMENT)
FSDX_TRAY_COMMAND_QUIT = int(TrayCommand.QUIT)
FSDX_TRAY_COMMAND_OPEN_SETTINGS = int(TrayCommand.OPEN_SETTINGS)
FSDX_EVENT_TRAY_COMMAND = 19

_CHECKABLE_COMMANDS = {
    TrayCommand.TOGGLE_GAME_MODE,
    TrayCommand.TOGGLE_CLICKTHROUGH,
    TrayCommand.TOGGLE_AUTOSTART,
}

_logger = get_logger(__name__)


class _TrayState(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("native_handle", ctypes.c_uint64),
    ]


def _configure_tray_api(library) -> None:
    library.fsdx_create_tray.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.fsdx_create_tray.restype = ctypes.c_int
    library.fsdx_destroy_tray.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    library.fsdx_destroy_tray.restype = ctypes.c_int
    library.fsdx_get_tray_state.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(_TrayState),
    ]
    library.fsdx_get_tray_state.restype = ctypes.c_int
    library.fsdx_show_tray.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32]
    library.fsdx_show_tray.restype = ctypes.c_int
    library.fsdx_set_tray_menu_state.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint32,
    ]
    library.fsdx_set_tray_menu_state.restype = ctypes.c_int
    library.fsdx_poll_events.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(_NativeEvent),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.fsdx_poll_events.restype = ctypes.c_int


def _default_icon_path() -> Path:
    return Path(__file__).resolve().parents[3] / "resc" / "icon.ico"


def _byte_buffer(value: bytes):
    return (ctypes.c_uint8 * len(value)).from_buffer_copy(value) if value else None


class DxTrayHost:
    """Own one Shell_NotifyIcon instance and dispatch its native commands."""

    RETRY_INTERVAL_MS = 1500
    MAX_RETRY_COUNT = 40

    def __init__(
        self,
        context: DxLoopContext,
        *,
        icon_path: str | Path | None = None,
        tooltip: str = "\u98de\u884c\u96ea\u7ed2",
        warp: bool = False,
        library=None,
    ) -> None:
        self._context = context
        self._icon_path = Path(icon_path) if icon_path is not None else _default_icon_path()
        self._tooltip = str(tooltip)
        self._warp = bool(warp)
        self._provided_library = library
        self._target: DxOffscreenTarget | None = None
        self._tray = ctypes.c_uint64()
        self._quit_callbacks: list[Callable[[], None]] = []
        self._announcement_callbacks: list[Callable[[], None]] = []
        self._command_callbacks: list[TrayCommandCallback] = []
        self._menu_state = TrayMenuState()
        self._retry_call: DxScheduledCall | None = None
        self._retry_count = 0
        self._cleanup_done = False
        self._shutdown_started = False
        self._registered = False
        self.last_error: str | None = None

    def connect_quit_requested(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("tray callback must be callable")
        if callback not in self._quit_callbacks:
            self._quit_callbacks.append(callback)

    def disconnect_quit_requested(self, callback: Callable[[], None]) -> None:
        self._quit_callbacks = [item for item in self._quit_callbacks if item != callback]

    def connect_announcement_requested(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("tray callback must be callable")
        if callback not in self._announcement_callbacks:
            self._announcement_callbacks.append(callback)

    def disconnect_announcement_requested(self, callback: Callable[[], None]) -> None:
        self._announcement_callbacks = [
            item for item in self._announcement_callbacks if item != callback
        ]

    def connect_command_requested(self, callback: TrayCommandCallback) -> None:
        if not callable(callback):
            raise TypeError("tray callback must be callable")
        if callback not in self._command_callbacks:
            self._command_callbacks.append(callback)

    def disconnect_command_requested(self, callback: TrayCommandCallback) -> None:
        self._command_callbacks = [item for item in self._command_callbacks if item != callback]

    @staticmethod
    def _encode_menu_state(state: TrayMenuState) -> int:
        flags = 0
        if state.game_mode_enabled:
            flags |= FSDX_TRAY_MENU_STATE_GAME_MODE
        if state.clickthrough_enabled:
            flags |= FSDX_TRAY_MENU_STATE_CLICKTHROUGH
        if state.autostart_enabled:
            flags |= FSDX_TRAY_MENU_STATE_AUTOSTART
        return flags

    def set_menu_state(self, state: TrayMenuState) -> None:
        if not isinstance(state, TrayMenuState):
            raise TypeError("tray menu state must be TrayMenuState")
        self._menu_state = state
        target = self._target
        if target is not None:
            target._call(
                target._library.fsdx_set_tray_menu_state,
                target._runtime,
                self._tray,
                self._encode_menu_state(state),
            )

    def _try_initialize(self) -> bool:
        if self._cleanup_done or self._shutdown_started:
            return False
        if self._target is not None:
            self.show()
            return True

        target: DxOffscreenTarget | None = None
        try:
            target = DxOffscreenTarget(
                1,
                1,
                warp=self._warp,
                library=self._provided_library,
            )
            _configure_tray_api(target._library)
            tooltip = self._tooltip.encode("utf-8")
            icon_path = str(self._icon_path.resolve()).encode("utf-8")
            tooltip_buffer = _byte_buffer(tooltip)
            icon_path_buffer = _byte_buffer(icon_path)
            tray = ctypes.c_uint64()
            target._call(
                target._library.fsdx_create_tray,
                target._runtime,
                tooltip_buffer,
                len(tooltip),
                icon_path_buffer,
                len(icon_path),
                ctypes.byref(tray),
            )
            target._call(
                target._library.fsdx_set_tray_menu_state,
                target._runtime,
                tray,
                self._encode_menu_state(self._menu_state),
            )
        except Exception as exc:
            self.last_error = str(exc)
            if target is not None:
                target.cleanup()
            return False

        self._target = target
        self._tray = tray
        self.last_error = None
        if not self._registered:
            self._context.register_poller(self)
            self._registered = True
        return True

    def initialize(self) -> bool:
        self._context.assert_owner_thread()
        if self._try_initialize():
            self._cancel_retry()
            return True
        self._schedule_retry()
        return False

    def _schedule_retry(self) -> None:
        if (
            self._cleanup_done
            or self._shutdown_started
            or self._retry_count >= self.MAX_RETRY_COUNT
            or (self._retry_call is not None and self._retry_call.pending)
        ):
            return
        self._retry_call = self._context.call_later(
            self.RETRY_INTERVAL_MS,
            self._on_retry,
        )

    def _on_retry(self) -> None:
        self._retry_call = None
        if self._cleanup_done or self._shutdown_started:
            return
        self._retry_count += 1
        if self._try_initialize():
            _logger.info("DX tray initialized after %d retries", self._retry_count)
            self._retry_count = 0
            return
        if self._retry_count >= self.MAX_RETRY_COUNT:
            _logger.error(
                "DX tray initialization stopped after %d retries: %s",
                self.MAX_RETRY_COUNT,
                self.last_error or "unknown error",
            )
            return
        self._schedule_retry()

    def _cancel_retry(self) -> None:
        retry = self._retry_call
        self._retry_call = None
        self._retry_count = 0
        if retry is not None:
            retry.cancel()

    def _state(self) -> _TrayState:
        target = self._target
        if target is None:
            raise DxBridgeError("DX tray has not been initialized")
        state = _TrayState()
        target._call(
            target._library.fsdx_get_tray_state,
            target._runtime,
            self._tray,
            ctypes.byref(state),
        )
        if state.abi_version != FSDX_ABI_VERSION or state.struct_size != ctypes.sizeof(_TrayState):
            raise DxBridgeError("DX tray state ABI mismatch")
        return state

    @property
    def native_handle(self) -> int | None:
        if self._target is None:
            return None
        value = int(self._state().native_handle)
        return value or None

    def is_alive(self) -> bool:
        return not self._cleanup_done and self._target is not None

    def is_visible(self) -> bool:
        return self.is_alive() and bool(self._state().flags & FSDX_TRAY_STATE_VISIBLE)

    def show(self) -> None:
        target = self._target
        if target is not None and not self._shutdown_started:
            target._call(target._library.fsdx_show_tray, target._runtime, self._tray, 1)

    def hide(self) -> None:
        target = self._target
        if target is not None:
            target._call(target._library.fsdx_show_tray, target._runtime, self._tray, 0)

    def poll_events(self, capacity: int = 64) -> tuple[int, ...]:
        target = self._target
        if target is None or self._cleanup_done:
            return ()
        capacity = max(1, min(1024, int(capacity)))
        native_events = (_NativeEvent * capacity)()
        written = ctypes.c_uint32()
        pending = ctypes.c_uint32()
        target._call(
            target._library.fsdx_poll_events,
            target._runtime,
            native_events,
            capacity,
            ctypes.byref(written),
            ctypes.byref(pending),
        )
        event_types: list[int] = []
        for index in range(written.value):
            event = native_events[index]
            if event.abi_version != FSDX_ABI_VERSION or event.struct_size != ctypes.sizeof(_NativeEvent):
                raise DxBridgeError("DX tray event ABI mismatch")
            if int(event.window) != int(self._tray.value):
                continue
            event_type = int(event.type)
            event_types.append(event_type)
            if event_type != FSDX_EVENT_TRAY_COMMAND:
                continue
            try:
                command = TrayCommand(int(event.key))
            except ValueError:
                _logger.warning("DX tray returned unknown command id: %s", int(event.key))
                continue
            if command == TrayCommand.ANNOUNCEMENT:
                for callback in tuple(self._announcement_callbacks):
                    callback()
            elif command == TrayCommand.QUIT:
                for callback in tuple(self._quit_callbacks):
                    callback()
            else:
                checked = bool(event.flags & FSDX_EVENT_FLAG_CHECKED) if command in _CHECKABLE_COMMANDS else None
                for callback in tuple(self._command_callbacks):
                    callback(command, checked)
        return tuple(event_types)

    def begin_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._cancel_retry()
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._cancel_retry()
        if self._registered:
            self._context.unregister_poller(self)
            self._registered = False
        target, self._target = self._target, None
        if target is not None:
            try:
                target._call(
                    target._library.fsdx_destroy_tray,
                    target._runtime,
                    self._tray,
                )
            finally:
                target.cleanup()
        self._tray = ctypes.c_uint64()
        self._quit_callbacks.clear()
        self._announcement_callbacks.clear()
        self._command_callbacks.clear()

    close = cleanup


def create_tray_host_factory(
    context: DxLoopContext,
    *,
    icon_path: str | Path | None = None,
    warp: bool = False,
    library=None,
) -> Callable[[], DxTrayHost]:
    """Bind a shared owner-thread context to the desktop tray factory."""

    def create() -> DxTrayHost:
        return DxTrayHost(
            context,
            icon_path=icon_path,
            warp=warp,
            library=library,
        )

    return create


__all__ = [
    "DxTrayHost",
    "create_tray_host_factory",
    "FSDX_EVENT_TRAY_COMMAND",
]
