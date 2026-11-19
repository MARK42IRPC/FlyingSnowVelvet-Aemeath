"""Shared-queue BGM ownership, asynchronous races and progress regression."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from lib.core.event.center import Event, EventCenter, EventType
from lib.script.cloudmusic._mixin_events import _EventsMixin
from lib.script.cloudmusic._mixin_playback import _PlaybackMixin
from lib.script.gemes.MAIN.game_bgm import GameBgmController
from lib.script.music.types import MusicTrack
from lib.script.music.service import MusicService

ARTIST = "鸣潮先约电台"
READY = MusicTrack("netease", "netease:101", "团子准备中", ARTIST)
FIGHT = MusicTrack("netease", 102, "团子出击", ARTIST)


class _Service:
    def __init__(self):
        self.songs = [(9, "用户的歌")]
        self.index = 0
        self.playing = True
        self.paused = False
        self.position = 37
        self.pending = []
        self.pending_guards = []
        self.clears = []
        self.initialized_on = []
        self.searches = []
        self.results = {READY.title: [READY], FIGHT.title: [FIGHT]}

    def initialize(self):
        self.initialized_on.append(threading.get_ident())

    def search(self, keyword, *, mode, limit):
        self.searches.append((keyword, mode, limit))
        return self.results.get(keyword, [])

    def queue_snapshot(self):
        return list(self.songs)

    def current_index(self):
        return self.index

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def can_takeover_for_bgm(self):
        return not self.playing or self.paused

    def enqueue_track(self, track, *, silent, autoplay, request_is_current=None):
        assert silent and not autoplay
        self.pending.append((int(str(track.track_id).removeprefix("netease:")), track.title))
        self.pending_guards.append(request_is_current)
        return True

    def consume_enqueue_events(self):
        self.songs.extend(song for song, guard in zip(self.pending, self.pending_guards) if guard is None or guard())
        self.pending.clear()
        self.pending_guards.clear()

    def clear_queue(self):
        self.clears.append(threading.get_ident())
        self.songs.clear()
        self.index = -1
        self.playing, self.paused = False, False


class GameBgmTests(unittest.TestCase):
    def setUp(self):
        self.service = _Service()
        self.jobs = []
        self.events = []
        self.controller = GameBgmController(music_service=lambda: self.service, submit=self.submit, publish=self.publish)
        self.controller.activate()

    def submit(self, fn, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.jobs.append((future, fn, args))
        return future

    def finish(self, index=-1):
        future, fn, args = self.jobs[index]
        future.set_result(fn(*args))

    def publish(self, event):
        self.events.append(event)
        if event.type == EventType.MUSIC_PLAY_QUEUE_INDEX:
            song = self.service.songs.pop(event.data["index"])
            self.service.songs.insert(0, song)
            self.service.index = 0
            self.service.position = 0
            self.service.playing, self.service.paused = True, False
        elif event.type == EventType.MUSIC_PLAY_PAUSE:
            self.service.paused = not event.data["playing"]
            self.service.playing = event.data["playing"]

    def request_ready(self):
        self.controller.request(READY.title, ARTIST, force=True, exclusive=True)
        self.finish()
        self.controller.drain()
        self.service.consume_enqueue_events()
        self.controller.drain()

    def test_nonexclusive_legacy_track_appends_before_selecting_real_index(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.finish()
        self.controller.drain()
        self.assertEqual(self.service.songs, [(9, "用户的歌")])
        self.assertEqual(self.events, [])
        for _ in range(3):
            self.controller.drain()
        self.assertEqual(len(self.service.pending), 1)
        self.service.consume_enqueue_events()
        # A user move while enqueue was pending must not leave a guessed index.
        self.service.songs.insert(0, (10, "用户新加的歌"))
        self.controller.drain()
        self.assertEqual(self.events[-1].type, EventType.MUSIC_PLAY_QUEUE_INDEX)
        self.assertEqual(self.events[-1].data["index"], 2)
        self.assertEqual({song[0] for song in self.service.songs}, {9, 10, 101})

    def test_same_phase_never_searches_enqueues_or_restarts_again(self):
        self.request_ready()
        self.service.position = 42
        for _ in range(5):
            self.controller.request(READY.title, ARTIST, force=True, exclusive=True)
            self.controller.drain()
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.service.position, 42)
        self.assertEqual(len(self.service.songs), 1)
        self.assertEqual(len(self.service.clears), 1)

    def test_switching_back_uses_cached_track_and_replaces_the_exclusive_queue(self):
        self.request_ready()
        self.controller.request(FIGHT.title, ARTIST, force=True, exclusive=True)
        self.finish()
        self.controller.drain()
        self.service.consume_enqueue_events()
        self.controller.drain()
        self.controller.request(READY.title, ARTIST, force=True, exclusive=True)
        self.controller.drain()
        self.service.consume_enqueue_events()
        self.controller.drain()
        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(self.service.songs[0][0], 101)
        self.assertEqual(len(self.service.songs), 1)
        self.assertEqual(len(self.service.clears), 3)

    def test_already_current_paused_track_resumes_shared_progress(self):
        self.service.songs.append((101, READY.title))
        self.service.index = 1
        self.service.paused, self.service.playing = True, False
        self.controller.request(READY.title, ARTIST, force=True)
        self.finish()
        self.controller.drain()
        self.assertEqual(self.events[-1].type, EventType.MUSIC_PLAY_PAUSE)
        self.assertTrue(self.events[-1].data["playing"])
        self.assertTrue(self.events[-1].data["request_is_current"]())
        self.assertEqual(self.service.position, 37)
        self.assertEqual(self.service.pending, [])

    def test_already_current_playing_track_keeps_progress(self):
        self.service.songs[0] = (101, READY.title)
        self.controller.request(READY.title, ARTIST, force=True)
        self.finish()
        self.controller.drain()
        self.assertEqual(self.events, [])
        self.assertEqual(self.service.position, 37)

    def test_older_search_cannot_cache_enqueue_or_play_after_phase_switch(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.controller.request(FIGHT.title, ARTIST, force=True)
        self.finish(0)
        self.controller.drain()
        self.assertEqual(self.controller._cache, {})
        self.assertEqual(self.service.pending, [])
        self.finish(1)
        self.controller.drain()
        self.assertEqual(self.service.pending[0][0], 102)
        self.assertEqual(self.service.searches, [(READY.title, "song", 20), (FIGHT.title, "song", 20)])

    def test_close_invalidates_search_and_reopen_cannot_mix_cached_songs(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.controller.close()
        self.finish(0)
        self.controller.activate()
        self.controller.request(FIGHT.title, ARTIST, force=True)
        self.controller.drain()
        self.assertEqual(self.controller._cache, {})
        self.assertEqual(self.service.pending, [])
        self.finish(1)
        self.controller.drain()
        self.assertEqual(self.service.pending[0][0], 102)

    def test_close_pauses_owned_song_but_keeps_queue_and_progress(self):
        self.request_ready()
        self.service.position = 47
        self.controller.close()
        self.assertFalse(self.events[-1].data["playing"])
        self.assertTrue(self.events[-1].data["request_is_current"]())
        self.assertEqual(self.service.position, 47)
        self.assertEqual(len(self.service.songs), 1)
        event_count = len(self.events)
        self.controller.close()
        self.assertEqual(len(self.events), event_count)

    def test_close_does_not_pause_a_user_selected_different_song(self):
        self.request_ready()
        self.service.songs.append((9, "用户后来手动播放的歌曲"))
        self.service.index = 1
        event_count = len(self.events)
        self.controller.close()
        self.assertEqual(len(self.events), event_count)
        self.assertTrue(self.service.playing)

    def test_manifest_static_bgm_does_not_interrupt_user_music(self):
        self.controller.request(READY.title, ARTIST)
        self.finish()
        self.controller.drain()
        self.assertEqual(self.events, [])
        self.assertEqual(self.service.pending, [])

    def test_search_requires_exact_title_and_artist(self):
        self.service.results[READY.title] = [
            MusicTrack("netease", 103, READY.title + "伴奏", ARTIST),
            MusicTrack("netease", 104, READY.title, "另一位作者"),
        ]
        self.controller.request(READY.title, ARTIST, force=True)
        self.finish()
        self.controller.drain()
        self.assertEqual(self.events, [])
        self.assertEqual(self.service.pending, [])

    def test_only_search_runs_in_background_and_hidden_host_does_not_commit(self):
        main_thread = threading.get_ident()
        self.controller.request(READY.title, ARTIST, force=True)
        worker = threading.Thread(target=self.finish)
        worker.start()
        worker.join()
        self.assertEqual(self.service.initialized_on, [main_thread])
        self.controller.drain(can_play=False)
        self.assertEqual(self.controller._cache, {})
        self.assertEqual(self.service.pending, [])
        self.controller.drain()
        self.assertEqual(self.service.pending[0][0], 101)

    def test_direct_song_ids_skip_search_and_use_shared_queue(self):
        for title, track_ref in (("团子准备中……", "netease:3442263555"), ("团子出击！", "netease:3442262625")):
            self.controller.request(title, ARTIST, track_ref=track_ref, force=True, exclusive=True)
            self.controller.drain()
            self.service.consume_enqueue_events()
            self.controller.drain()
            self.assertEqual(self.service.songs[0][0], int(track_ref.split(":")[1]))
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.service.searches, [])
        self.assertEqual(len(self.service.songs), 1)
        self.service.position = 42
        self.controller.request("团子出击！", ARTIST, track_ref="netease:3442262625", force=True, exclusive=True)
        self.controller.drain()
        self.assertEqual(self.service.position, 42)
        self.assertEqual(len(self.service.songs), 1)
        self.assertEqual(len(self.service.clears), 2)

    def test_direct_song_id_reuses_existing_paused_recording_without_resetting_progress(self):
        self.service.songs.append((3442263555, "另一个显示标题"))
        self.service.index = 1
        self.service.paused, self.service.playing = True, False
        self.controller.request(READY.title, ARTIST, track_ref="netease:3442263555", force=True)
        self.controller.drain()
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.service.pending, [])
        self.assertEqual(self.events[-1].type, EventType.MUSIC_PLAY_PAUSE)
        self.assertTrue(self.events[-1].data["playing"])
        self.assertEqual(self.service.position, 37)

    def test_same_title_with_different_direct_id_changes_the_target(self):
        self.controller.request(READY.title, ARTIST, track_ref="netease:3442263555", force=True)
        self.controller.request(READY.title, ARTIST, track_ref="netease:3442262625", force=True)
        self.controller.drain()
        self.assertEqual(self.service.pending, [(3442262625, READY.title)])
        self.assertEqual(self.jobs, [])


class _QueueBackend(_EventsMixin):
    """Use production queue/pause event handlers without a media device."""

    def __init__(self):
        self._queue = [(9, "用户的歌"), (101, READY.title)]
        self._current_index = 0
        self._state_lock = threading.RLock()
        self._is_playing = True
        self._is_paused = False
        self.starts = []
        self.pauses = 0

    def _stop_internal(self):
        self._is_playing = False

    def _play_current(self):
        self.starts.append(self._queue[self._current_index][0])
        self._is_playing, self._is_paused = True, False

    def _resume(self):
        self.starts.append(self._queue[self._current_index][0])
        self._is_playing, self._is_paused = True, False

    def pause(self):
        self.pauses += 1
        self._is_playing, self._is_paused = False, True

    def _show_info(self, _text):
        pass


class GameBgmEventQueueTests(unittest.TestCase):
    def setUp(self):
        self.center = EventCenter()
        self.backend = _QueueBackend()
        self.service = _Service()
        self.service.queue_snapshot = lambda: list(self.backend._queue)
        self.service.current_index = lambda: self.backend._current_index
        self.service.is_playing = lambda: self.backend._is_playing
        self.service.is_paused = lambda: self.backend._is_paused
        self.center.subscribe(EventType.MUSIC_PLAY_QUEUE_INDEX, self.backend._on_play_queue_index)
        self.center.subscribe(EventType.MUSIC_PLAY_PAUSE, self.backend._on_play_pause)
        self.controller = GameBgmController(music_service=lambda: self.service, submit=self.submit, publish=self.center.publish)
        self.controller.activate()

    def submit(self, fn, *args):
        result = Future()
        result.set_result(fn(*args))
        return result

    def test_close_queued_before_playback_command_prevents_late_start(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.center.subscribe(EventType.TICK, lambda _: self.center.publish(Event(EventType.INPUT_HASH, {"text": "关闭"})))
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.center.subscribe(EventType.INPUT_HASH, lambda _: self.controller.close())
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend.starts, [])
        self.assertEqual(self.backend._queue[self.backend._current_index][0], 9)
        self.assertEqual(self.backend.pauses, 0)

    def test_direct_id_request_is_guarded_when_close_precedes_queued_playback(self):
        self.backend._queue.append((3442263555, "团子准备中……"))
        self.controller.request(READY.title, ARTIST, track_ref="netease:3442263555", force=True)
        self.center.subscribe(EventType.TICK, lambda _: self.center.publish(Event(EventType.INPUT_HASH, {})))
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.center.subscribe(EventType.INPUT_HASH, lambda _: self.controller.close())
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.service.searches, [])
        self.assertEqual(self.backend.starts, [])
        self.assertEqual(self.backend._queue[self.backend._current_index][0], 9)

    def test_close_queued_before_resume_does_not_restart_paused_song(self):
        self.backend._current_index = 1
        self.backend._is_paused, self.backend._is_playing = True, False
        self.controller.request(READY.title, ARTIST, force=True)
        self.center.subscribe(EventType.TICK, lambda _: self.center.publish(Event(EventType.INPUT_HASH, {})))
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.center.subscribe(EventType.INPUT_HASH, lambda _: self.controller.close())
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend.starts, [])
        self.assertTrue(self.backend._is_paused)

    def test_user_changes_song_before_close_pause_is_consumed(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.controller.drain()
        self.assertEqual(self.backend.starts, [101])
        self.center.subscribe(EventType.TICK, lambda _: self.center.publish(Event(EventType.INPUT_HASH, {})))
        self.center.subscribe(EventType.TICK, lambda _: self.controller.close())
        self.center.subscribe(EventType.INPUT_HASH, lambda _: setattr(self.backend, "_current_index", 1))
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend._queue[self.backend._current_index][0], 9)
        self.assertEqual(self.backend.pauses, 0)
        self.assertTrue(self.backend._is_playing)

    def test_reopen_invalidates_an_already_queued_close_pause(self):
        self.controller.request(READY.title, ARTIST, force=True)
        self.controller.drain()
        self.center.subscribe(EventType.TICK, lambda _: self.controller.close())
        self.center.subscribe(EventType.TICK, lambda _: self.controller.activate())
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend.pauses, 0)

    def test_user_queue_reorder_before_game_selection_never_plays_wrong_song(self):
        self.backend._queue.append((77, "用户另一首歌"))
        self.controller.request(READY.title, ARTIST, force=True)

        def user_selection(_event):
            self.center.publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {"index": 2}))

        self.center.subscribe(EventType.TICK, user_selection)
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend.starts, [77])
        self.assertTrue(self.controller._pending_play)
        self.center.unsubscribe(EventType.TICK, user_selection)
        self.center.publish(Event(EventType.TICK, {}))
        self.assertEqual(self.backend.starts, [77, 101])
        self.assertNotIn(9, self.backend.starts)

    def test_missing_guard_keeps_legacy_behavior_but_invalid_guards_are_rejected(self):
        for guard in (False, "not callable", lambda: False):
            self.center.publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {"index": 1, "request_is_current": guard}))
        def broken():
            raise RuntimeError("stale host")
        self.center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": False, "request_is_current": broken}))
        self.assertEqual(self.backend.starts, [])
        self.assertEqual(self.backend.pauses, 0)
        self.center.publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {"index": 1}))
        self.assertEqual(self.backend.starts, [101])


class ExclusiveGameBgmQueueTests(unittest.TestCase):
    """Real EventCenter + service enqueue + backend consumption and looping."""

    def setUp(self):
        self.center = EventCenter()
        self.backend = _QueueBackend()
        self.backend._ec = self.center
        self.backend._play_gen = 1
        self.backend._play_mode = "list_loop"
        self.service = _Service()
        self.service._get_backend_manager = lambda: self.backend
        self.service.format_track_display = lambda track, include_provider: track.title
        self.service.queue_snapshot = lambda: list(self.backend._queue)
        self.service.current_index = lambda: self.backend._current_index
        self.service.is_playing = lambda: self.backend._is_playing
        self.service.is_paused = lambda: self.backend._is_paused

        def clear():
            self.service.clears.append(threading.get_ident())
            self.backend._stop_internal()
            self.backend._queue.clear()
            self.backend._current_index = -1

        self.backend.clear_queue = clear
        self.service.clear_queue = lambda: MusicService.clear_queue(self.service)

        def enqueue(track, **kwargs):
            with patch("lib.script.music.service.get_event_center", return_value=self.center):
                return MusicService.enqueue_track(self.service, track, **kwargs)

        self.service.enqueue_track = enqueue
        self.center.subscribe(EventType.MUSIC_ENQUEUE, self.backend._on_enqueue)
        self.center.subscribe(EventType.MUSIC_PLAY_QUEUE_INDEX, self.backend._on_play_queue_index)
        self.center.subscribe(EventType.MUSIC_PLAY_PAUSE, self.backend._on_play_pause)
        self.controller = GameBgmController(music_service=lambda: self.service, submit=self.submit, publish=self.center.publish)
        self.controller.activate()

    @staticmethod
    def submit(*_args):
        raise AssertionError("Direct BGM references must not search")

    def ready(self):
        self.controller.request("团子准备中……", ARTIST, track_ref="netease:3442263555", force=True, exclusive=True)

    def fight(self):
        self.controller.request("团子出击！", ARTIST, track_ref="netease:3442262625", force=True, exclusive=True)

    def tick(self):
        self.center.publish(Event(EventType.TICK, {}))

    def test_each_target_replaces_queue_and_finished_battle_loops_only_itself(self):
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.ready()
        self.tick()
        self.tick()
        self.assertEqual([item[0] for item in self.backend._queue], [3442263555])
        self.fight()
        self.tick()
        self.tick()
        self.assertEqual([item[0] for item in self.backend._queue], [3442262625])
        before = len(self.backend.starts)
        _PlaybackMixin._on_player_finished(self.backend, self.backend._play_gen)
        self.assertEqual(self.backend.starts[before:], [3442262625])
        self.assertEqual([item[0] for item in self.backend._queue], [3442262625])
        self.assertTrue(all(thread_id == threading.get_ident() for thread_id in self.service.clears))
        # A series continuation reports the same target, preserving its playhead.
        self.fight()
        self.tick()
        self.assertEqual(len(self.service.clears), 2)
        self.assertEqual(len(self.backend.starts), before + 1)

    def test_fast_phase_change_rejects_old_enqueue_that_runs_after_new_clear(self):
        self.ready()
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())

        def switch(_event):
            self.fight()
            self.controller.drain()

        self.center.subscribe(EventType.TICK, switch)
        self.tick()
        self.assertEqual([item[0] for item in self.backend._queue], [3442262625])
        self.assertEqual(self.backend.starts, [])
        self.tick()
        self.assertEqual(self.backend.starts, [3442262625])
        self.assertEqual(len(self.backend._queue), 1)
        self.assertEqual(len(self.service.clears), 2)

    def test_close_ahead_of_queued_enqueue_cannot_repopulate_or_restart(self):
        self.ready()
        self.center.subscribe(EventType.TICK, lambda _: self.center.publish(Event(EventType.INPUT_HASH, {"text": "关闭"})))
        self.center.subscribe(EventType.TICK, lambda _: self.controller.drain())
        self.center.subscribe(EventType.INPUT_HASH, lambda _: self.controller.close())
        self.tick()
        self.assertEqual(self.backend._queue, [])
        self.assertEqual(self.backend.starts, [])
        self.tick()
        self.assertEqual(self.backend._queue, [])

    def test_enqueue_guard_is_optional_and_invalid_guard_never_changes_queue(self):
        original = list(self.backend._queue)
        self.center.publish(Event(EventType.MUSIC_ENQUEUE, {"song_id": 44, "silent": True, "autoplay": False, "request_is_current": False}))
        self.assertEqual(self.backend._queue, original)
        self.center.publish(Event(EventType.MUSIC_ENQUEUE, {"song_id": 44, "silent": True, "autoplay": False}))
        self.assertEqual(self.backend._queue, original + [(44, "")])
