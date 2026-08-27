"""Single owner for companion/office input routing."""

from __future__ import annotations

import threading

from lib.core.event.center import Event, EventCenter, EventType, get_event_center

from .contracts import InteractionMode, normalize_mode


class InteractionModeService:
    """Route ordinary text while exposing a generation token for stale work."""

    def __init__(self, event_center: EventCenter | None = None) -> None:
        self._event_center = event_center or get_event_center()
        self._lock = threading.RLock()
        self._mode = InteractionMode.COMPANION
        self._generation = 0
        self._cleaned = False
        self._event_center.subscribe(EventType.INPUT_TEXT, self._on_input_text)
        self._event_center.subscribe(EventType.INTERACTION_MODE_SET, self._on_mode_set)

    @property
    def mode(self) -> InteractionMode:
        with self._lock:
            return self._mode

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def is_companion(self) -> bool:
        return self.mode is InteractionMode.COMPANION

    @property
    def is_office(self) -> bool:
        return self.mode is InteractionMode.OFFICE

    def snapshot(self) -> tuple[InteractionMode, int]:
        with self._lock:
            return self._mode, self._generation

    def accepts_companion_generation(self, generation: int) -> bool:
        with self._lock:
            return (
                not self._cleaned
                and self._mode is InteractionMode.COMPANION
                and self._generation == int(generation)
            )

    def set_mode(self, mode: InteractionMode | str, *, source: str = "") -> bool:
        resolved = normalize_mode(mode)
        with self._lock:
            if self._cleaned or resolved is self._mode:
                return False
            self._mode = resolved
            self._generation += 1
            generation = self._generation

        if resolved is InteractionMode.OFFICE:
            self._event_center.publish(Event(EventType.MIC_STT_STOP, {
                "source": "interaction_mode",
                "auto_only": True,
            }))
        self._event_center.publish(Event(EventType.INTERACTION_MODE_CHANGED, {
            "mode": resolved.value,
            "generation": generation,
            "source": str(source or ""),
        }))
        return True

    def _on_mode_set(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        requested = data.get("mode")
        if requested is None and bool(data.get("toggle", False)):
            requested = (
                InteractionMode.OFFICE.value
                if self.is_companion
                else InteractionMode.COMPANION.value
            )
        if requested is None:
            return
        self.set_mode(requested, source=str(data.get("source", "")))
        event.mark_handled()

    def _on_input_text(self, event: Event) -> None:
        data = dict(event.data) if isinstance(event.data, dict) else {}
        text = str(data.get("text", "")).strip()
        if not text:
            return
        mode, generation = self.snapshot()
        data["text"] = text
        data["interaction_mode"] = mode.value
        data["mode_generation"] = generation
        target = EventType.INPUT_CHAT if mode is InteractionMode.COMPANION else EventType.OFFICE_INPUT
        self._event_center.publish(Event(target, data))
        event.mark_handled()

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            self._generation += 1
        self._event_center.unsubscribe(EventType.INPUT_TEXT, self._on_input_text)
        self._event_center.unsubscribe(EventType.INTERACTION_MODE_SET, self._on_mode_set)


_instance: InteractionModeService | None = None


def get_interaction_mode_service() -> InteractionModeService:
    global _instance
    if _instance is None:
        _instance = InteractionModeService()
    return _instance


def cleanup_interaction_mode_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
