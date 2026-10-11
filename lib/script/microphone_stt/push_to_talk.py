"""Push-to-talk hotkey integration for the microphone STT service.
"""

from __future__ import annotations

from typing import Optional

from config.config import VOICE
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.input.hotkey import HotkeyBinding, parse_hotkey_binding
from lib.core.input.types import KeyModifier
from lib.core.logger import get_logger

#: 热键文本解析（`HotkeyBinding` / `parse_hotkey_binding`）已下沉到
#: `lib/core/input/hotkey.py`（core 不得反向 import 本包）。这里按原名再导出，
#: 既有 `from lib.script.microphone_stt.push_to_talk import parse_hotkey_binding` 不变。
__all__ = [
    "HotkeyBinding",
    "MicrophonePushToTalkHotkey",
    "cleanup_microphone_push_to_talk_manager",
    "get_microphone_push_to_talk_manager",
    "parse_hotkey_binding",
]


class MicrophonePushToTalkHotkey:
    """Listen for the configured hotkey and control microphone STT."""

    def __init__(self) -> None:
        self._logger = get_logger(__name__)
        self._event_center = get_event_center()
        self._binding = self._load_binding()
        self._subscriptions_active = False
        self._session_active = False

        if self._binding is not None:
            self._subscribe()
        else:
            self._logger.info("[MicPushToTalk] Hotkey disabled or not configured.")

    def _load_binding(self) -> Optional[HotkeyBinding]:
        raw_value = str(VOICE.get("microphone_push_to_talk_key", "") or "").strip()
        if not raw_value:
            return None
        binding = parse_hotkey_binding(raw_value)
        if binding is None:
            self._logger.warning("[MicPushToTalk] Invalid hotkey: %r", raw_value)
        else:
            self._logger.info("[MicPushToTalk] Push-to-talk hotkey: %s", binding.display)
        return binding

    def _subscribe(self) -> None:
        if self._subscriptions_active:
            return
        self._event_center.subscribe(EventType.KEY_PRESS, self._on_key_press)
        self._event_center.subscribe(EventType.KEY_RELEASE, self._on_key_release)
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)
        self._subscriptions_active = True

    def cleanup(self) -> None:
        if not self._subscriptions_active:
            return
        self._event_center.unsubscribe(EventType.KEY_PRESS, self._on_key_press)
        self._event_center.unsubscribe(EventType.KEY_RELEASE, self._on_key_release)
        self._event_center.unsubscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)
        self._subscriptions_active = False
        self._session_active = False

    def _on_key_press(self, event: Event) -> None:
        if self._binding is None:
            return
        if event.data.get("is_auto_repeat"):
            return

        key_code = int(event.data.get("key", 0))
        modifiers = event.data.get("modifiers", KeyModifier.NONE)
        if not self._binding.matches(key_code, modifiers):
            return

        self._session_active = True
        self._logger.debug("[MicPushToTalk] Hotkey pressed, starting STT session.")
        self._event_center.publish(Event(EventType.MIC_STT_START, {
            "source": "microphone_push_to_talk",
            "auto_mode": False,
            "auto_submit": True,
            "emit_partial": True,
        }))

    def _on_key_release(self, event: Event) -> None:
        if not self._session_active or self._binding is None:
            return

        key_code = int(event.data.get("key", 0))
        modifiers = event.data.get("modifiers", KeyModifier.NONE)
        if not self._binding.matches(key_code, modifiers):
            return

        self._session_active = False
        self._logger.debug("[MicPushToTalk] Hotkey released, stopping STT session.")
        self._event_center.publish(Event(EventType.MIC_STT_STOP, {
            "source": "microphone_push_to_talk",
        }))

    def _on_stt_state_change(self, event: Event) -> None:
        if not self._session_active:
            return
        if event.data.get("is_listening"):
            return
        self._logger.debug("[MicPushToTalk] STT stopped externally, resetting session state.")
        self._session_active = False


_push_to_talk_instance: Optional[MicrophonePushToTalkHotkey] = None


def get_microphone_push_to_talk_manager() -> MicrophonePushToTalkHotkey:
    global _push_to_talk_instance
    if _push_to_talk_instance is None:
        _push_to_talk_instance = MicrophonePushToTalkHotkey()
    return _push_to_talk_instance


def cleanup_microphone_push_to_talk_manager() -> None:
    global _push_to_talk_instance
    if _push_to_talk_instance is not None:
        _push_to_talk_instance.cleanup()
        _push_to_talk_instance = None
