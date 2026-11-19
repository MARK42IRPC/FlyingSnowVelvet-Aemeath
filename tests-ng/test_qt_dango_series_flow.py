"""Drive complete series through the real widget's Qt frame timer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest

from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import widget as widget_module
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"


class _FrameClock:
    """Advance simulated time per timer callback without wall-clock sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.calls = 0

    def perf_counter(self):
        self.now += 0.1
        self.calls += 1
        return self.now


class DangoSeriesFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config

        init_font_config()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dango-series-flow-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = _FrameClock()
        clock_patch = patch.object(widget_module, "time", SimpleNamespace(perf_counter=self.clock.perf_counter))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8")))
        self.widget = widget_module.DangoShowdownWidget(GameContext(manifest, PACKAGE, root / "data", root / "cache"))
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget.shutdown)
        self.widget._event_center = Mock()
        self.widget._sfx = Mock()
        self.widget.resize(810, 540)
        self.widget.show()
        # A zero timer still enters the production timeout -> _on_tick path;
        # each event-loop turn advances 100 ms of game time for quick replay.
        self.widget._timer.setInterval(0)
        self.match = self.widget._match

    def frames(self, count):
        before = self.clock.calls
        for _ in range(count):
            self.app.processEvents()
        self.assertGreater(self.clock.calls, before)

    def until(self, predicate, *, max_frames=200):
        for _ in range(max_frames):
            if predicate():
                return
            self.app.processEvents()
        self.fail(f"timer did not reach expected series state: {self.match.phase}, round={self.match.round_index}")

    def start(self, format_id):
        self.assertTrue(self.match.choose_format(format_id))
        self.assertTrue(self.match.choose_dango("left"))
        self.assertTrue(self.match.choose_bet_amount(5))
        self.initial_chips = self.match.record["chips"]
        self.fighters = self.match.fighter_ids
        self.widget._start_match()
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.assertEqual(self.match.round_index, 1)

    def finish(self, winner):
        self.until(lambda: self.match.countdown <= 0.0)
        owner = self.match.dango_by_side(winner)
        target = self.match.right if winner == "left" else self.match.left
        # Use the real lethal-damage and frame-settlement path, avoiding a
        # long random battle while preserving actual score/stat accounting.
        self.match._skill_damage(owner, target, target.hp)
        self.until(lambda: self.match.phase is MatchPhase.RESULT)
        self.assertEqual(self.match.result.winner, winner)

    def assert_continued_fight(self, round_number):
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.assertEqual(self.match.round_index, round_number)
        self.assertEqual(self.match.fighter_ids, self.fighters)
        self.assertEqual((self.match.picked_side, self.match.bet.side, self.match.bet.amount), ("left", "left", 5))
        self.assertTrue(all(dango.hp == dango.max_hp for dango in self.match.dango))
        self.assertGreater(self.match.countdown, 0.0)
        self.assertTrue(self.widget._timer.isActive())

    def run_series(self, format_id, winners):
        self.start(format_id)
        expected_chips = self.initial_chips
        for index, winner in enumerate(winners, start=1):
            self.assert_continued_fight(index)
            self.finish(winner)
            expected_chips += 5 if winner == "left" else -5
            self.assertEqual(self.match.record["total_rounds"], index)
            self.assertEqual(self.match.record["chips"], expected_chips)
            if index < len(winners):
                observed_phases = []
                for _ in range(100):
                    self.app.processEvents()
                    observed_phases.append(self.match.phase)
                    if self.match.phase is MatchPhase.FIGHT:
                        break
                self.assertNotIn(MatchPhase.SELECT, observed_phases)
                self.assert_continued_fight(index + 1)
                self.assertEqual(self.match.record["total_rounds"], index)
                self.assertEqual(self.match.record["chips"], expected_chips)

        self.assertTrue(self.match.series_over)
        self.assertEqual(self.match.series.played, len(winners))
        settled_record = self.match.record.copy()
        self.frames(120)
        self.assertEqual(self.match.phase, MatchPhase.RESULT)
        self.assertEqual(self.match.record, settled_record)
        topics = [call.args[0].type for call in self.widget._event_center.publish.call_args_list]
        self.assertEqual(topics.count(ev.MATCH_START), len(winners))
        self.assertEqual(topics.count(ev.BET_SETTLED), len(winners))
        self.assertEqual(topics.count(ev.MATCH_END), len(winners))
        QTest.keyClick(self.widget, Qt.Key_R)
        self.assertEqual(self.match.phase, MatchPhase.SELECT)
        self.assertEqual(self.match.series.played, 0)
        self.assertFalse(self.match.bet.ready)
        self.assertEqual(self.match.record, settled_record)

    def test_bo3_timer_runs_all_three_rounds_then_stops(self):
        self.run_series("bo3", ("left", "right", "left"))

    def test_bo5_timer_runs_all_five_rounds_then_stops(self):
        self.run_series("bo5", ("left", "right", "left", "right", "left"))

    def test_keyboard_and_click_continue_same_fighters_and_bet(self):
        self.start("bo5")
        self.finish("left")
        self.until(lambda: self.match.result_reveal == 0.0)
        first_record = self.match.record.copy()
        QTest.keyClick(self.widget, Qt.Key_R)
        self.assert_continued_fight(2)
        self.assertEqual(self.match.record, first_record)

        self.finish("right")
        self.until(lambda: self.match.result_reveal == 0.0)
        second_record = self.match.record.copy()
        self.widget.grab()
        button = next(hit for hit in self.widget._hit_map.buttons if hit.action == "next")
        x, y = self.widget._transform().to_canvas(button.rect.x + button.rect.width / 2, button.rect.y + button.rect.height / 2)
        QTest.mouseClick(self.widget, Qt.LeftButton, pos=QPoint(round(x), round(y)))
        self.assert_continued_fight(3)
        self.assertEqual(self.match.record, second_record)

    def test_hidden_widget_waits_and_resumes_same_settlement(self):
        self.start("bo3")
        self.finish("left")
        self.until(lambda: self.match.result_reveal == 0.0)
        self.frames(8)
        remaining = self.widget._auto_advance
        record = self.match.record.copy()
        self.widget.hide()
        self.assertFalse(self.widget._timer.isActive())
        self.clock.now += 3600.0
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual(self.match.phase, MatchPhase.RESULT)
        self.assertEqual(self.widget._auto_advance, remaining)
        self.assertEqual(self.match.record, record)
        self.widget.show()
        self.assertTrue(self.widget._timer.isActive())
        self.frames(1)
        self.assertEqual(self.match.phase, MatchPhase.RESULT)
        self.assertGreater(self.widget._auto_advance, remaining - 0.2)
        self.until(lambda: self.match.phase is MatchPhase.FIGHT)
        self.assert_continued_fight(2)
        self.assertEqual(self.match.record, record)


if __name__ == "__main__":
    unittest.main()
