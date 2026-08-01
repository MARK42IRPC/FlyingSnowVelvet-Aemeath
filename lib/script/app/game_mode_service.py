"""Runtime game mode coordinator."""

from __future__ import annotations

from typing import Any

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.physics import get_physics_world

_logger = get_logger(__name__)
_AUTO_COMPANION_INTERVAL_OVERRIDE_MS: tuple[int, int] | None = None


def get_game_mode_auto_companion_interval_override() -> tuple[int, int] | None:
    """Return auto companion interval override applied by game mode."""
    return _AUTO_COMPANION_INTERVAL_OVERRIDE_MS


def _set_game_mode_auto_companion_interval_override(value: tuple[int, int] | None) -> None:
    global _AUTO_COMPANION_INTERVAL_OVERRIDE_MS
    _AUTO_COMPANION_INTERVAL_OVERRIDE_MS = value


class GameModeService:
    """Coordinate low-intrusion runtime switches for game mode."""

    GAME_FRAME_FPS = 30
    GAME_AUTO_COMPANION_INTERVAL_MS = (300000, 300000)

    def __init__(self) -> None:
        self._event_center = get_event_center()
        self._enabled = False
        self._pet = None
        self._particles = None
        self._effects = None
        self._restore_frame_fps: int | None = None
        self._restore_gif_fps: int | None = None
        self._event_center.subscribe(EventType.GAME_MODE_SET, self._on_game_mode_set)
        self._event_center.subscribe(EventType.GAME_MODE_EXIT, self._on_game_mode_exit)

    def configure_runtime(self, pet=None, particles=None, effects=None) -> None:
        self._pet = pet
        self._particles = particles
        self._effects = effects
        if self._enabled:
            self._apply_runtime_state(enable=True)

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool, *, source: str = "manual", notify: bool = True) -> bool:
        target = bool(enabled)
        if target == self._enabled:
            return False
        if target:
            self._enter(source=source, notify=notify)
        else:
            self._exit(source=source, notify=notify)
        return True

    def _on_game_mode_set(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self.set_enabled(True, source=str(data.get("source", "manual") or "manual"))
        event.mark_handled()

    def _on_game_mode_exit(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self.set_enabled(False, source=str(data.get("source", "manual") or "manual"))
        event.mark_handled()

    def _enter(self, *, source: str, notify: bool) -> None:
        self._capture_restore_fps()
        _set_game_mode_auto_companion_interval_override(self.GAME_AUTO_COMPANION_INTERVAL_MS)
        self._enabled = True
        self._apply_runtime_state(enable=True)
        self._publish_status_change(source=source)
        if notify:
            self._publish_info(self._build_enter_message(source))
        _logger.info("[GameMode] enabled by source=%s", source)

    def _exit(self, *, source: str, notify: bool) -> None:
        _set_game_mode_auto_companion_interval_override(None)
        self._enabled = False
        self._apply_runtime_state(enable=False)
        self._publish_status_change(source=source)
        if notify:
            self._publish_info("已退出游戏模式，恢复普通运行频率。")
        _logger.info("[GameMode] disabled by source=%s", source)

    def _capture_restore_fps(self) -> None:
        timing = getattr(self._pet, "_timing_manager", None)
        if timing is None:
            return
        try:
            configured_getter = getattr(
                timing,
                "get_configured_frame_fps",
                timing.get_frame_fps,
            )
            self._restore_frame_fps = int(configured_getter())
        except Exception:
            self._restore_frame_fps = None
        try:
            self._restore_gif_fps = int(timing.get_gif_fps())
        except Exception:
            self._restore_gif_fps = None

    def _apply_runtime_state(self, *, enable: bool) -> None:
        if enable:
            self._set_visual_paused(True)
            self._set_physics_paused(True)
            self._set_layer_refresh_paused(True)
            self._set_timing_fps(self.GAME_FRAME_FPS, None)
            return
        self._set_timing_fps(
            self._restore_frame_fps or self.GAME_FRAME_FPS,
            self._restore_gif_fps,
        )
        self._set_layer_refresh_paused(False)
        self._set_physics_paused(False)
        self._set_visual_paused(False)

    def _set_visual_paused(self, paused: bool) -> None:
        for overlay in (self._particles, self._effects):
            if overlay is None:
                continue
            setter = getattr(overlay, "set_paused", None)
            if callable(setter):
                setter(paused)
                continue
            if paused:
                flusher = getattr(overlay, "flush_immediately", None)
                if callable(flusher):
                    flusher()

    def _set_physics_paused(self, paused: bool) -> None:
        try:
            world = get_physics_world()
        except Exception:
            return
        method = getattr(world, "pause" if paused else "resume", None)
        if callable(method):
            method()

    def _set_layer_refresh_paused(self, paused: bool) -> None:
        manager = get_layer_manager()
        method = getattr(manager, "pause" if paused else "resume", None)
        if callable(method):
            method()

    def _set_timing_fps(self, frame_fps: int | None, gif_fps: int | None) -> None:
        timing = getattr(self._pet, "_timing_manager", None)
        if timing is None:
            return
        if frame_fps is not None:
            try:
                timing.set_frame_fps(int(frame_fps))
            except Exception:
                pass
        if gif_fps is not None:
            try:
                timing.set_gif_fps(int(gif_fps))
            except Exception:
                pass

    def _publish_status_change(self, *, source: str) -> None:
        self._event_center.publish(Event(EventType.GAME_MODE_STATUS_CHANGE, {
            "enabled": self._enabled,
            "source": str(source or "manual"),
        }))

    def _publish_info(self, text: str) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 0,
            "max": 100,
        }))

    def _build_enter_message(self, source: str) -> str:
        prefix = "已自动进入" if str(source or "").strip() == "auto" else "已进入"
        return f"{prefix}游戏模式：粒子、特效、物理与层级刷新已暂停，主帧率已降至 30fps。"

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.GAME_MODE_SET, self._on_game_mode_set)
        self._event_center.unsubscribe(EventType.GAME_MODE_EXIT, self._on_game_mode_exit)
        _set_game_mode_auto_companion_interval_override(None)
        self._pet = None
        self._particles = None
        self._effects = None
        self._enabled = False


_instance: GameModeService | None = None


def get_game_mode_service() -> GameModeService:
    global _instance
    if _instance is None:
        _instance = GameModeService()
    return _instance


def cleanup_game_mode_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
