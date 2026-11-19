"""Shared sound catalog, injected storage and legacy cue compatibility."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from lib.core.event.center import EventType
from lib.script.gemes.MAIN import game_sfx


class GameSfxTests(unittest.TestCase):
    def test_injected_catalog_caches_real_pcm_and_keeps_all_legacy_helper_gains(self):
        expected = {
            "move": 0.28, "fall": 0.18, "rotate": 0.34, "lock": 0.36,
            "drop_start": 0.34, "drop_impact": 0.48, "clear": 0.46,
            "skill_press": 0.22, "skill_cast": 0.30, "skill_release": 0.54,
            "partner_burst": 0.40, "game_over": 0.46,
        }
        emitted = []
        with tempfile.TemporaryDirectory(prefix="shared-game-sfx-") as tmp:
            cache = Path(tmp)
            with patch.object(game_sfx, "get_user_cache_dir", side_effect=AssertionError("injected cache must be isolated")), \
                    patch.object(game_sfx, "get_event_center", side_effect=AssertionError("injected sink owns publication")):
                sound = game_sfx.GameSfx(cache_dir=cache, emit=lambda *args: emitted.append(args))
                for key, gain in expected.items():
                    with self.subTest(cue=key):
                        getattr(sound, f"play_{key}")()
                        name, source, volume = emitted[-1]
                        self.assertEqual((name, volume), (key, gain))
                        self.assertEqual(Path(source), cache / f"lahai_{key}.wav")
                        with wave.open(source, "rb") as wav:
                            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
                            self.assertTrue(any(wav.readframes(wav.getnframes())))
            self.assertEqual(len(emitted), len(expected))
            self.assertEqual(len(list(cache.glob("*.wav"))), len(expected))

    def test_default_sink_publishes_sound_request_and_volume_override_is_per_call(self):
        events = Mock()
        with tempfile.TemporaryDirectory(prefix="shared-game-sfx-") as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            with patch.object(game_sfx, "get_user_cache_dir", return_value=cache), \
                    patch.object(game_sfx, "get_event_center", return_value=events):
                sound = game_sfx.GameSfx()
            sound.play("move", volume_gain=0.24)
            request = events.publish.call_args.args[0]
            self.assertEqual(request.type, EventType.SOUND_REQUEST)
            self.assertEqual(request.data, {
                "audio_type": "game_sfx", "source": str(cache / "lahai_move.wav"),
                "volume_gain": 0.24, "interruptible": True,
            })
            sound.play_move()
            self.assertEqual(events.publish.call_args.args[0].data["volume_gain"], 0.28)
            sound.play("move", volume_gain=-1)
            self.assertEqual(events.publish.call_args.args[0].data["volume_gain"], 0.0)
            sound.play("move", volume_gain=2)
            self.assertEqual(events.publish.call_args.args[0].data["volume_gain"], 1.0)
            count = events.publish.call_count
            sound.play("not-a-cue")
            self.assertEqual(events.publish.call_count, count)

    def test_existing_shared_files_are_reused_without_rebuilding_waveforms(self):
        with tempfile.TemporaryDirectory(prefix="shared-game-sfx-") as tmp:
            cache = Path(tmp)
            first = game_sfx.GameSfx(cache_dir=cache, emit=lambda *args: None)
            path = Path(first._files["move"])
            original = path.read_bytes()
            emitted = []
            with patch.object(game_sfx.GameSfx, "_build_move", side_effect=AssertionError("cached cue rebuilt")):
                second = game_sfx.GameSfx(cache_dir=cache, emit=lambda *args: emitted.append(args))
                second.play("move")
            self.assertEqual(emitted, [("move", str(path), 0.28)])
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
