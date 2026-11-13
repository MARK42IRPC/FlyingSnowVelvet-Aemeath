"""召唤音响时的「音响时」语音：两条入口都要触发，且每次召唤只播一次。"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType

speaker_module = importlib.import_module("lib.script.obj-音响.manager")
SpeakerManager = speaker_module.SpeakerManager


class _EventSink:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class _Speaker:
    def __init__(self):
        self.gravity_enabled = True

    def set_gravity_enabled(self, enabled):
        self.gravity_enabled = enabled


def _manager():
    """只装配 `_spawn_speakers` 需要的状态，绕开 __init__ 里的图片与音乐服务。"""
    manager = SpeakerManager.__new__(SpeakerManager)
    manager._speakers = []
    manager._event_center = _EventSink()
    manager._resource = object()
    manager._actual_size = (120, 120)
    manager._cfg = {}
    manager._entity = None
    manager._gravity_enabled = True
    manager._speaker_create_sound = Mock()
    return manager


class SpeakerSpawnVoiceTests(unittest.TestCase):
    def _spawn(self, manager, count: int) -> None:
        with patch.object(
            speaker_module, "create_world_object", side_effect=lambda *a, **k: _Speaker(),
        ), patch.object(speaker_module, "get_screen_rect_for_point"):
            manager._spawn_speakers(count)

    def test_hash_command_plays_the_create_voice_once(self):
        manager = _manager()

        self._spawn(manager, 3)
        manager._speaker_create_sound.play.reset_mock()

        with patch.object(speaker_module, "create_world_object", side_effect=lambda *a, **k: _Speaker()), patch.object(
            speaker_module, "get_screen_rect_for_point",
        ):
            manager._on_hash_command(Event(EventType.INPUT_HASH, {"text": "音响 3"}))

        self.assertEqual(len(manager._speakers), 6)
        manager._speaker_create_sound.play.assert_called_once_with()

    def test_spawning_plays_the_create_voice_once(self):
        manager = _manager()

        self._spawn(manager, 3)

        self.assertEqual(len(manager._speakers), 3)
        manager._speaker_create_sound.play.assert_called_once_with()

    def test_spawn_request_plays_the_create_voice_once(self):
        manager = _manager()

        with patch.object(speaker_module, "create_world_object", side_effect=lambda *a, **k: _Speaker()), patch.object(
            speaker_module, "get_screen_rect_for_point",
        ):
            manager._on_spawn_request(Event(
                EventType.MANAGER_SPAWN_REQUEST,
                {"manager_id": "speaker", "count": 2},
            ))

        self.assertEqual(len(manager._speakers), 2)
        manager._speaker_create_sound.play.assert_called_once_with()

    def test_spawn_request_for_another_manager_is_ignored(self):
        manager = _manager()

        with patch.object(manager, "_spawn_speakers") as spawn:
            manager._on_spawn_request(Event(
                EventType.MANAGER_SPAWN_REQUEST,
                {"manager_id": "sofa", "count": 1},
            ))

        spawn.assert_not_called()
        manager._speaker_create_sound.play.assert_not_called()

    def test_missing_image_skips_spawning_and_the_voice(self):
        manager = _manager()
        manager._resource = None

        with patch.object(speaker_module, "create_world_object") as create:
            manager._spawn_speakers(1)

        create.assert_not_called()
        manager._speaker_create_sound.play.assert_not_called()

    def test_voice_class_is_wired_into_the_manager(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "lib" / "script" / "obj-音响" / "manager.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from lib.script.voice.ams_speaker_create import AmsSpeakerCreateSound", source)
        self.assertIn("AmsSpeakerCreateSound()", source)


if __name__ == "__main__":
    unittest.main()
