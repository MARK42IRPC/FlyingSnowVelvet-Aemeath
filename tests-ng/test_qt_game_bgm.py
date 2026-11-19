"""GameRuntime binds dynamic BGM after activation and owns its shutdown."""

from __future__ import annotations

import json
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtWidgets import QWidget

from lib.core.event.center import Event, EventType
from lib.script.gemes.MAIN.game_packages import GamePackageManifest
from lib.script.ui import game_runtime as runtime_module
from tests_ng.test_pure_game_bgm import ARTIST, FIGHT, READY, _Service

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"


class _DynamicWidget(QWidget):
    def __init__(self, parent=None, *, track_ref=""):
        super().__init__(parent)
        self.was_reset = False
        self.callback = None
        self.bindings = []
        self.track_ref = track_ref

    def reset_game(self, start_running=False):
        self.was_reset = True

    def set_bgm_callback(self, callback):
        self.bindings.append((self.was_reset, callback))
        self.callback = callback
        if callback is not None:
            if self.track_ref:
                callback(READY.title, ARTIST, track_ref=self.track_ref)
            else:
                callback(READY.title, ARTIST)


class GameRuntimeBgmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config
        init_font_config()

    def setUp(self):
        self.service = _Service()
        self.events = []
        self.jobs = []
        self.center = Mock()
        self.center.publish.side_effect = self.events.append
        self.packages = Mock()
        self.packages.list_installed_games.return_value = []
        self.manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8")))
        self.widgets = []

        def load(game_id):
            def create(parent):
                widget = _DynamicWidget(parent)
                self.widgets.append(widget)
                return widget
            return SimpleNamespace(game_id=game_id, manifest=self.manifest), None, SimpleNamespace(create_widget=create)

        self.packages.load_game_entry.side_effect = load
        patches = {
            "get_event_center": Mock(return_value=self.center),
            "get_game_package_service": Mock(return_value=self.packages),
            "get_music_service": Mock(return_value=self.service),
            "get_compute_hub": Mock(return_value=SimpleNamespace(submit_interactive_io=self.submit)),
            "get_hash_cmd_registry": Mock(return_value=Mock()),
            "GameManagerWindow": Mock(),
            "AmsOpenLahaiTetrisSound": Mock(),
            "configure_game_obstacle_provider": Mock(),
            "reset_game_obstacle_provider": Mock(),
            "cleanup_game_package_service": Mock(),
        }
        for name, replacement in patches.items():
            patcher = patch.object(runtime_module, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runtime = runtime_module.GameRuntime()
        self.addCleanup(self.runtime._panel.deleteLater)
        self.addCleanup(self.runtime.cleanup)

    def submit(self, fn, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.jobs.append((future, fn, args))
        return future

    def finish(self, index=-1):
        future, fn, args = self.jobs[index]
        future.set_result(fn(*args))

    def tick(self):
        self.runtime._on_bgm_tick(Event(EventType.TICK, {}))

    def test_dynamic_binding_happens_after_host_reset_and_ticks_commit_results(self):
        self.runtime.open_game("dango_showdown")
        widget = self.widgets[-1]
        self.assertTrue(widget.bindings[-1][0])
        self.assertEqual(len(self.jobs), 1)
        self.finish()
        self.assertEqual(self.runtime._bgm._cache, {})
        self.tick()
        self.assertEqual(self.service.pending[0][0], 101)
        self.service.consume_enqueue_events()
        self.tick()
        self.assertTrue(any(e.type == EventType.MUSIC_PLAY_QUEUE_INDEX for e in self.events))
        self.assertEqual([song[0] for song in self.service.songs], [101])
        self.assertEqual(len(self.service.clears), 1)

    def test_dynamic_widget_direct_reference_reaches_shared_queue_without_search(self):
        self.packages.load_game_entry.side_effect = lambda game_id: (
            SimpleNamespace(game_id=game_id, manifest=self.manifest), None,
            SimpleNamespace(create_widget=lambda parent: _DynamicWidget(parent, track_ref="netease:3442263555")),
        )
        self.runtime.open_game("dango_showdown")
        self.assertEqual(self.jobs, [])
        self.tick()
        self.assertEqual(self.service.pending[0][0], 3442263555)
        self.service.consume_enqueue_events()
        self.tick()
        self.assertTrue(any(e.type == EventType.MUSIC_PLAY_QUEUE_INDEX for e in self.events))
        self.assertEqual(self.service.searches, [])
        self.assertEqual([song[0] for song in self.service.songs], [3442263555])
        self.assertEqual(len(self.service.clears), 1)

    def test_closed_or_replaced_widgets_cannot_submit_or_complete_old_requests(self):
        self.runtime.open_game("dango_showdown")
        old_callback = self.widgets[-1].callback
        self.runtime.open_game("another_game")
        old_callback(FIGHT.title, ARTIST)
        self.assertEqual(len(self.jobs), 2)
        self.finish(0)
        self.tick()
        self.assertEqual(self.runtime._bgm._cache, {})
        self.assertEqual(self.service.pending, [])
        callback = self.widgets[-1].callback
        self.runtime.close_active_game()
        self.assertIsNone(self.widgets[-1].callback)
        callback(FIGHT.title, ARTIST)
        self.finish(1)
        self.tick()
        self.assertEqual(self.runtime._bgm._cache, {})
        self.assertEqual(self.service.pending, [])
        self.assertEqual(len(self.jobs), 2)

    def test_close_pauses_only_still_owned_shared_track_and_unsubscribes_tick(self):
        self.runtime.open_game("dango_showdown")
        self.finish()
        self.tick()
        self.service.consume_enqueue_events()
        self.tick()
        # The real event center's music listener would apply the queue command.
        self.service.index = next(i for i, song in enumerate(self.service.songs) if song[0] == 101)
        self.runtime.close_active_game()
        self.assertTrue(any(e.type == EventType.MUSIC_PLAY_PAUSE and e.data["playing"] is False for e in self.events))
        self.runtime.cleanup()
        self.center.unsubscribe.assert_any_call(EventType.TICK, self.runtime._on_bgm_tick)

    def test_static_manifest_without_dynamic_setter_keeps_existing_music(self):
        data = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
        self.manifest = GamePackageManifest.from_dict({**data, "bgm_keyword": READY.title, "bgm_artist": ARTIST})
        self.packages.load_game_entry.side_effect = lambda game_id: (
            SimpleNamespace(game_id=game_id, manifest=self.manifest), None,
            SimpleNamespace(create_widget=lambda parent: QWidget(parent)),
        )
        self.runtime.open_game("legacy_game")
        self.finish()
        self.tick()
        self.assertEqual(self.service.pending, [])
        self.assertFalse(any(e.type in (EventType.MUSIC_PLAY_QUEUE_INDEX, EventType.MUSIC_PLAY_PAUSE) for e in self.events))
        self.assertEqual(self.service.clears, [])
