"""Shared music pause/resume across download and native startup boundaries."""

from __future__ import annotations

import threading
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.cloudmusic.manager import CloudMusicManager


class MusicPendingPauseTests(unittest.TestCase):
    def setUp(self):
        self.manager = CloudMusicManager.__new__(CloudMusicManager)
        manager = self.manager
        manager._state_lock = threading.Lock()
        manager._queue = [(101, "团子出击 - 鸣潮先约电台"), (202, "另一首歌")]
        manager._current_index = 0
        manager._is_playing = False
        manager._is_paused = False
        manager._play_gen = 4
        manager._playback_future = None
        manager._download_thread = None
        manager._player_start_pending = False
        manager._paused_file = None
        manager._music_player = Mock()
        manager._music_player.duration_ms.return_value = 180000
        manager._music_player.position_ms.return_value = 43210
        manager._volume = 0.5
        manager._current_duration_ms = 0
        manager._pending_play_display = ""
        manager._pending_play_path = ""
        manager._duration_cache = {}
        manager._schedule_duration_probe = Mock()
        manager._publish_brief_info = Mock()
        manager._publish_status = Mock()
        manager._show_info = Mock()
        manager._ec = Mock()
        manager._history_provider_for_song_id = Mock(return_value="netease")
        self.original_queue = manager.queue
        history = patch("lib.script.cloudmusic._mixin_playback.get_music_history", return_value=Mock())
        history.start()
        self.addCleanup(history.stop)

    def pause(self):
        self.manager._on_play_pause(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": False}))

    def resume(self):
        self.manager._on_play_pause(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": True}))

    def assert_queue_preserved(self):
        self.assertEqual(self.manager.queue, self.original_queue)
        self.assertEqual(self.manager.current_index, 0)

    def test_download_completing_after_pause_waits_for_explicit_resume(self):
        manager = self.manager
        manager._download_thread = Future()
        self.pause()
        self.assertTrue(manager.is_paused)
        manager._play_file(Path("cached-bgm.mp3"), "团子出击")
        manager._music_player.play.assert_not_called()
        self.assert_queue_preserved()
        self.resume()
        manager._music_player.play.assert_called_once()
        self.assertEqual(manager._music_player.play.call_args.args[0], "cached-bgm.mp3")
        self.assertFalse(manager.is_paused)
        self.assert_queue_preserved()

    def test_resume_while_download_is_running_does_not_submit_another_job(self):
        manager = self.manager
        manager._download_thread = Future()
        with patch.object(manager, "_play_current") as start:
            self.pause()
            self.resume()
            start.assert_not_called()
        manager._music_player.resume.assert_not_called()
        manager._play_file(Path("ready.mp3"), "团子出击")
        manager._music_player.play.assert_called_once()
        self.assert_queue_preserved()

    def test_initial_worker_finishing_after_pause_defers_cached_audio(self):
        manager = self.manager
        future = Future()
        hub = Mock()
        hub.submit_io.return_value = future
        with tempfile.TemporaryDirectory(prefix="music-pause-cache-") as tmp:
            cached = Path(tmp) / "cached.mp3"
            cached.touch()
            manager._find_cached_audio = Mock(return_value=cached)
            with patch("lib.script.cloudmusic._mixin_playback.get_compute_hub", return_value=hub):
                manager._play_current()
            self.assertIs(manager._playback_future, future)
            self.pause()
            worker, *args = hub.submit_io.call_args.args
            worker(*args)
            manager._music_player.play.assert_not_called()
            # Even before the initial Future reports done, its deferred file
            # is ready and resume must launch it rather than waiting forever.
            self.resume()
        manager._music_player.play.assert_called_once()
        self.assert_queue_preserved()

    def test_native_started_after_pause_stays_paused_and_resumes_without_restart(self):
        manager = self.manager
        manager._play_file(Path("starting.mp3"), "团子出击")
        self.assertTrue(manager._player_start_pending)
        self.pause()
        manager._music_player.pause.reset_mock()
        manager._on_player_started(manager._play_gen)
        self.assertTrue(manager.is_paused)
        manager._music_player.pause.assert_called_once_with()
        self.resume()
        manager._music_player.resume.assert_called_once_with()
        manager._music_player.play.assert_called_once()
        manager._music_player.stop.assert_not_called()
        manager._music_player.seek.assert_not_called()
        self.assertEqual(manager._music_player.position_ms(), 43210)
        self.assert_queue_preserved()

    def test_resume_before_native_started_does_not_restart_the_pending_play(self):
        manager = self.manager
        manager._play_file(Path("starting.mp3"), "团子出击")
        self.pause()
        self.resume()
        manager._on_player_started(manager._play_gen)
        self.assertFalse(manager.is_paused)
        manager._music_player.play.assert_called_once()
        manager._music_player.resume.assert_called_once()
        self.assert_queue_preserved()

    def test_normal_pause_preserves_position_and_late_finished_does_not_advance(self):
        manager = self.manager
        manager._is_playing = True
        self.pause()
        manager._on_player_finished(manager._play_gen)
        self.assertTrue(manager.is_paused)
        self.assert_queue_preserved()
        self.resume()
        manager._music_player.pause.assert_called_once()
        manager._music_player.resume.assert_called_once()
        manager._music_player.play.assert_not_called()
        manager._music_player.stop.assert_not_called()
        manager._music_player.seek.assert_not_called()
        self.assert_queue_preserved()

    def test_empty_player_pause_does_not_create_a_phantom_paused_track(self):
        manager = self.manager
        manager._queue = []
        manager._current_index = -1
        self.pause()
        self.assertFalse(manager.is_paused)
        manager._music_player.pause.assert_not_called()

    def test_old_cached_worker_cannot_play_after_a_phase_switch(self):
        manager = self.manager
        hub = Mock()
        hub.submit_io.return_value = Future()
        with tempfile.TemporaryDirectory(prefix="music-old-worker-") as tmp:
            old_file = Path(tmp) / "selection.mp3"
            old_file.touch()
            manager._find_cached_audio = Mock(return_value=old_file)
            with patch("lib.script.cloudmusic._mixin_playback.get_compute_hub", return_value=hub):
                manager._play_current()
            worker, *args = hub.submit_io.call_args.args
            manager._stop_internal()
            manager._queue = [(202, "团子出击 - 鸣潮先约电台")]
            manager._current_index = 0
            worker(*args)
            manager._music_player.play.assert_not_called()
            self.assertEqual(manager.queue, [(202, "团子出击 - 鸣潮先约电台")])
            self.assertEqual(manager.current_index, 0)

            # Also switch while the lookup itself is in flight. Both the
            # cached-play and uncached-download admission need a late check.
            for result in (old_file, None):
                with self.subTest(cache_hit=result is not None):
                    manager._queue = self.original_queue.copy()
                    manager._current_index = 0
                    with patch("lib.script.cloudmusic._mixin_playback.get_compute_hub", return_value=hub):
                        manager._play_current()
                    worker, *args = hub.submit_io.call_args.args
                    submissions = hub.submit_io.call_count

                    def lookup_then_switch(_song_id):
                        manager._stop_internal()
                        manager._queue = [(202, "团子出击 - 鸣潮先约电台")]
                        manager._current_index = 0
                        return result

                    manager._find_cached_audio.side_effect = lookup_then_switch
                    with patch("lib.script.cloudmusic._mixin_playback.get_compute_hub", return_value=hub):
                        worker(*args)
                    manager._music_player.play.assert_not_called()
                    self.assertEqual(hub.submit_io.call_count, submissions)
                    self.assertEqual(manager.queue, [(202, "团子出击 - 鸣潮先约电台")])


if __name__ == "__main__":
    unittest.main()
