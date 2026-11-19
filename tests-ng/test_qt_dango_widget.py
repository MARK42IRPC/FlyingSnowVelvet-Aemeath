"""Real widget input, frame timing and presentation lifecycle regression tests."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import QPointF, Qt, QEvent
from PyQt5.QtGui import QKeyEvent, QMouseEvent

from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import constants as C
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import CharacterView
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget import DangoShowdownWidget

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"


class DangoWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config
        init_font_config()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dango-widget-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8")))
        self.widget = DangoShowdownWidget(GameContext(manifest, PACKAGE, root / "data", root / "cache"))
        # Let the real decode worker finish before this test's next widget is
        # created; otherwise short input tests pile up CPU-bound daemon jobs.
        self.addCleanup(self.widget._asset_loader.wait, 10.0)
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.shutdown)
        self.widget._timer.stop()
        self.widget.resize(1080, 720)
        self.widget._event_center = Mock()
        self.widget._sfx = Mock()

    def key(self, key, modifiers=Qt.NoModifier):
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))

    def render(self):
        return self.widget.grab()

    def test_bet_shortcuts_and_keypad_use_qt_number_keys(self):
        for modifier in (Qt.NoModifier, Qt.KeypadModifier):
            for key, amount in ((Qt.Key_3, 1), (Qt.Key_4, 5), (Qt.Key_5, 10)):
                with self.subTest(key=key, modifier=modifier):
                    self.key(key, modifier)
                    self.assertEqual(self.widget._match.bet.amount, amount)

    def test_randomize_and_unbound_keys_do_not_raise(self):
        self.key(Qt.Key_R)
        self.assertTrue(self.widget._match.can_start)
        self.key(Qt.Key_Z)

    def test_mouse_close_remains_available_during_fight_at_minimum_size(self):
        self.widget.resize(810, 540)
        self.widget._match.randomize()
        self.widget._match.start_match()
        self.render()
        close = next(hit for hit in self.widget._hit_map.buttons if hit.action == "close")
        x, y = self.widget._transform().to_canvas(close.rect.x + close.rect.width / 2, close.rect.y + close.rect.height / 2)
        closed = Mock()
        self.widget.set_close_callback(closed)
        self.widget.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        closed.assert_called_once_with()

    def test_hurt_sprite_persists_between_red_flash_frames(self):
        self.assertTrue(self.widget._apply_assets(blocking=True))
        view = CharacterView("left", "amis", "爱弥斯", state="hurt", flash=0.0)
        self.assertIs(self.widget._sprite_for(view), self.widget._state_sprites[("amis", "hurt")])

    def test_paint_does_not_wait_for_background_assets(self):
        loader = Mock(ready=False)
        loader.assets.return_value = None
        self.widget._asset_loader = loader
        self.widget._assets_applied = False
        self.widget._match.phase = MatchPhase.FIGHT
        self.render()
        loader.wait.assert_not_called()

    def test_void_hand_rotation_does_not_consume_game_randomness(self):
        before = self.widget._match.rng.getstate()
        self.widget._handle_skill_event(ev.VOID_HAND, {"side": "right", "target": "left", "point": (0.5, 0.5), "damage": 2})
        self.assertEqual(before, self.widget._match.rng.getstate())

    def test_named_events_reach_their_documented_topic(self):
        payload = {"name": ev.MATCH_START, "round": 1}
        self.widget._publish_event(ev.MATCH_START, payload)
        event = self.widget._event_center.publish.call_args.args[0]
        self.assertEqual(event.type, ev.MATCH_START)
        self.assertEqual(event.data, payload)

    def test_laser_uses_clipped_presenter_without_unclipped_desktop_decal(self):
        from lib.core.event.center import EventType

        self.widget._apply_assets(blocking=True)
        for angle in (0.0, 0.1, 1.5707963267948966):
            self.widget._event_center.reset_mock()
            self.widget._handle_skill_event(ev.LASER_FIRE, {"side": "left", "target": "right", "damage": 8, "start": (0, .1), "end": (1, .2), "angle": angle})
            self.widget._apply_assets(blocking=True)
            self.assertTrue(self.widget._feedback.effects)
            self.assertTrue(all(type(effect).__name__=='SmoothImageShowEffect' for effect in self.widget._feedback.effects))
            import math
            effect = self.widget._feedback.effects[-1]
            target = self.widget._hit_canvas({"target": "right"})
            theta = math.radians(effect.rotation)
            self.assertAlmostEqual((target[0]-effect.x)*math.sin(theta)-(target[1]-effect.y)*math.cos(theta),0)
            self.assertFalse(any(call.args[0].type == EventType.EFFECT_REQUEST
                                 for call in self.widget._event_center.publish.call_args_list))

    def test_starburst_lands_at_910ms_and_fades_over_last_180ms(self):
        self.assertTrue(self.widget._apply_assets(blocking=True))
        self.widget._handle_skill_event(ev.STARBURST_FIRE, {"side": "left", "target": "right", "damage": 60, "impact_ms": 910})
        with patch.object(self.widget, "_starburst_impact") as impact:
            self.widget._advance_visuals(0.90)
            impact.assert_not_called()
            self.assertIsNotNone(self.widget._starburst)
            self.assertGreater(self.widget._starburst.opacity, 0)
            self.assertLess(self.widget._starburst.opacity, 1)
            self.widget._advance_visuals(0.02)
            impact.assert_called_once()
            self.widget._advance_visuals(0.09)
            self.assertIsNone(self.widget._starburst)

    def test_round_reset_clears_previous_fight_effects(self):
        self.widget._spawn_particle("star_streak", "point", (500,350))
        self.widget._spawn_particle("burst_line", "point", (500,350))
        self.widget._card_flash = {"side": "left", "until": 10.0}
        self.widget._flash = 1.0
        self.widget._hit_stop = 0.07
        self.widget._series_winner = "left"
        self.widget._deferred_numbers = [{"damage": 60}]
        self.widget.reset_game(start_running=False)
        self.assertFalse(self.widget._timer.isActive())
        self.assertEqual(self.widget._feedback.particles, ())
        self.assertEqual(self.widget._feedback.effects, ())
        self.assertEqual(self.widget._deferred_numbers, [])
        self.assertEqual(self.widget._card_flash, {})
        self.assertEqual(self.widget._series_winner, "")
        self.assertEqual((self.widget._flash, self.widget._hit_stop), (0.0, 0.0))

    def test_pause_freezes_fight_and_visual_clocks(self):
        self.widget._match.randomize()
        self.widget._match.start_match()
        self.key(Qt.Key_P)
        before = (self.widget._match.countdown, self.widget._clock)
        self.widget._on_tick()
        self.assertEqual(before, (self.widget._match.countdown, self.widget._clock))
        self.key(Qt.Key_P)
        self.assertFalse(self.widget._paused)

    def test_host_activation_keeps_visible_widget_ticking(self):
        from lib.script.ui.game_runtime_panel import GameRuntimePanel

        panel = GameRuntimePanel()
        self.addCleanup(panel.deleteLater)
        panel.configure_game(self.widget._context.manifest, self.widget, Mock())
        panel.show()
        self.app.processEvents()
        self.widget.deactivate()
        panel.activate()
        self.assertTrue(self.widget._timer.isActive())
        self.assertEqual(self.widget._match.phase, MatchPhase.SELECT)
        for phase in MatchPhase:
            self.widget._match.phase = phase
            with self.subTest(phase=phase):
                self.key(Qt.Key_F11)
                self.assertTrue(panel._fullscreen_active)
                self.key(Qt.Key_F11)
                self.assertFalse(panel._fullscreen_active)

    def test_starburst_damage_number_appears_once_on_landing(self):
        self.widget._apply_assets(blocking=True)
        payload = {"side": "left", "target": "right", "damage": 7, "defer_visual": True, "point": (0.5, 0.9)}
        self.widget._handle_skill_event(ev.SKILL_DAMAGE, payload)
        self.widget._handle_skill_event(ev.STARBURST_FIRE, {**payload, "impact_ms": 910})
        with patch.object(self.widget, "_show_damage_number") as number:
            self.widget._advance_visuals(0.90)
            number.assert_not_called()
            self.widget._advance_visuals(0.02)
            number.assert_called_once()
            self.assertEqual(number.call_args.args[0]["damage"], 7)
            self.widget._advance_visuals(0.10)
            self.widget._snapshot()
            number.assert_called_once()

    def test_auto_advance_continues_the_same_fighters_and_bet_until_series_end(self):
        match = self.widget._match
        match.choose_format("bo3")
        match.randomize()
        match.start_match()
        match.tracker.damage_one(match.right, match.right.hp)
        match._finish_round()
        match.result_reveal = 0.0
        record = match.record.copy()
        fighters = match.fighter_ids
        bet = (match.picked_side, match.bet.side, match.bet.amount)
        self.widget._spawn_particle("star_streak", "point", (500,350))
        self.widget._spawn_particle("burst_line", "point", (500,350))
        self.widget._auto_advance_tick(1.0)
        self.assertEqual(match.phase, MatchPhase.RESULT)
        self.widget._auto_advance_tick(2.0)
        self.assertEqual(match.phase, MatchPhase.FIGHT)
        self.assertFalse(match.can_start)
        self.assertEqual(match.fighter_ids, fighters)
        self.assertEqual((match.picked_side, match.bet.side, match.bet.amount), bet)
        self.assertEqual(match.countdown, C.COUNTDOWN_SECONDS)
        self.assertEqual(match.record, record)
        self.assertTrue(all(d.hp == d.max_hp for d in match.dango))
        self.assertEqual(self.widget._feedback.particles, ())
        self.assertEqual(self.widget._feedback.effects, ())
        # One more win clinches bo3; the card remains until the player returns.
        match.tracker.damage_one(match.right, match.right.hp)
        match._finish_round()
        match.result_reveal = 0.0
        self.assertTrue(match.series_over)
        self.widget._auto_advance_tick(10.0)
        self.assertEqual(match.phase, MatchPhase.RESULT)
        self.key(Qt.Key_R)
        self.assertEqual(match.phase, MatchPhase.SELECT)
        self.assertEqual(match.series.played, 0)
        self.assertEqual(match.bet.side, "")
        self.assertEqual(match.bet.amount, 0)

    def test_select_snapshot_and_cards_follow_the_actual_randomized_fighters(self):
        match = self.widget._match
        match.rng = random.Random(7)
        seen = set()
        for _ in range(12):
            match.randomize()
            snapshot = self.widget._snapshot()
            expected = (match.left.character_id, match.right.character_id)
            seen.add(expected)
            self.assertEqual((snapshot.left_id, snapshot.right_id), expected)
            self.assertEqual(tuple(d.character_id for d in snapshot.characters), expected)
            self.render()
            self.widget._start_match()
            self.assertEqual((match.left.character_id, match.right.character_id), expected)
            match.prepare_round()
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.roster import ROSTER
        available = {entry.character_id for entry in ROSTER}
        self.assertGreater(len(seen), 1)
        self.assertEqual({identity for pair in seen for identity in pair}, available)
        self.assertTrue(all(left in available and right in available and left != right for left, right in seen))

    def test_round_card_timer_starts_only_after_reveal_at_variable_frame_rates(self):
        for deltas in ((1 / 30,), (1 / 60,), (1 / 120,), (0.013, 0.027, 0.045, 0.011, 0.033)):
            with self.subTest(deltas=deltas):
                self.widget.reset_game(start_running=False)
                match = self.widget._match
                match.series.reset()
                match.choose_format("bo3")
                match.randomize()
                self.widget._start_match()
                match.tracker.damage_one(match.right, match.right.hp)
                match._finish_round()
                # Clear cinematic hit-stop: this test measures reveal+card.
                self.widget._hit_stop = 0.0
                started = self.widget._last_tick = 100.0
                now = started
                elapsed = 0.0
                frame = 0
                target = C.RESULT_REVEAL_SECONDS + C.AUTO_ADVANCE_SECONDS
                while elapsed < target - 1e-9:
                    delta = min(deltas[frame % len(deltas)], target - elapsed)
                    elapsed += delta
                    now += delta
                    frame += 1
                    with patch("lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget.time.perf_counter", return_value=now):
                        self.widget._on_tick()
                    if elapsed < C.RESULT_REVEAL_SECONDS - 1e-9:
                        self.assertIsNone(self.widget._snapshot().auto_advance_seconds)
                    if elapsed < target - 1e-9:
                        self.assertIs(match.phase, MatchPhase.RESULT)
                self.assertIs(match.phase, MatchPhase.FIGHT)
                self.assertEqual(match.round_index, 2)
                self.assertEqual(match.countdown, C.COUNTDOWN_SECONDS)
                self.assertIsNone(self.widget._snapshot().auto_advance_seconds)

    def test_card_snapshot_reports_actual_remaining_seconds_and_manual_next_uses_same_path(self):
        match = self.widget._match
        match.choose_format("bo3")
        match.randomize()
        self.widget._start_match()
        match.tracker.damage_one(match.right, match.right.hp)
        match._finish_round()
        self.assertIsNone(self.widget._snapshot().auto_advance_seconds)
        self.widget._next_round()
        self.assertIs(match.phase, MatchPhase.RESULT)
        match.result_reveal = 0.0
        self.assertEqual(self.widget._snapshot().auto_advance_seconds, C.AUTO_ADVANCE_SECONDS)
        self.widget._auto_advance_tick(1.2)
        self.assertAlmostEqual(self.widget._snapshot().auto_advance_seconds, 1.8)
        before = match.record
        self.key(Qt.Key_R)
        self.assertIs(match.phase, MatchPhase.FIGHT)
        self.assertEqual(match.record, before)
        self.assertEqual(match.countdown, C.COUNTDOWN_SECONDS)
