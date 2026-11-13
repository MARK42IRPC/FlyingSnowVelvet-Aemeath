import unittest

from lib.core.event.center import Event, EventCenter, EventType
from lib.core.input.types import Key, KeyModifier
from lib.script.microphone_stt.push_to_talk import (
    HotkeyBinding,
    MicrophonePushToTalkHotkey,
    parse_hotkey_binding,
)
from tests.timing_fakes import FakePump


class PushToTalkKeyTests(unittest.TestCase):
    def test_parser_uses_core_keys_for_letters_specials_and_function_keys(self):
        letter = parse_hotkey_binding("Ctrl+Shift+V")
        special = parse_hotkey_binding("Alt+PageDown")
        function = parse_hotkey_binding("F24")

        self.assertEqual(letter, HotkeyBinding(
            key=Key.V,
            modifiers=KeyModifier.CONTROL | KeyModifier.SHIFT,
            display="CTRL+SHIFT+V",
        ))
        self.assertEqual(special.key, Key.PAGE_DOWN)
        self.assertEqual(special.modifiers, KeyModifier.ALT)
        self.assertEqual(function.key, Key.F24)
        self.assertEqual(function.modifiers, KeyModifier.NONE)

    def test_binding_matches_legacy_numeric_values_for_compatibility(self):
        binding = parse_hotkey_binding("Ctrl+Left")

        self.assertTrue(binding.matches(int(Key.LEFT), int(KeyModifier.CONTROL)))
        self.assertFalse(binding.matches(int(Key.RIGHT), int(KeyModifier.CONTROL)))

    def test_hotkey_events_start_and_stop_manual_microphone_session(self):
        event_center = EventCenter(
            pump_factory=lambda callback: FakePump(callback),
        )
        hotkey = MicrophonePushToTalkHotkey.__new__(MicrophonePushToTalkHotkey)
        hotkey._logger = type("Logger", (), {"debug": lambda *args: None})()
        hotkey._event_center = event_center
        hotkey._binding = parse_hotkey_binding("Ctrl+V")
        hotkey._subscriptions_active = False
        hotkey._session_active = False
        hotkey._subscribe()
        self.addCleanup(hotkey.cleanup)
        events = []
        event_center.subscribe(EventType.MIC_STT_START, events.append)
        event_center.subscribe(EventType.MIC_STT_STOP, events.append)

        payload = {
            "key": Key.V,
            "modifiers": KeyModifier.CONTROL,
            "is_auto_repeat": False,
        }
        event_center.publish(Event(EventType.KEY_PRESS, payload))
        event_center.publish(Event(EventType.KEY_RELEASE, payload))

        self.assertEqual(
            [event.type for event in events],
            [EventType.MIC_STT_START, EventType.MIC_STT_STOP],
        )
        self.assertFalse(hotkey._session_active)


if __name__ == "__main__":
    unittest.main()
