"""Exercise an expanded fighter pool through real scaled widget clicks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest

from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import roster as R
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget import DangoShowdownWidget

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"


class DangoExpandedRosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config

        init_font_config()

    def setUp(self):
        future = tuple(R.DangoEntry(f"future_{index:03d}", f"未来团子{index}", max_hp=140 + index)
                       for index in range(95))
        self.roster = (*R.ROSTER, *future)
        roster_patch = patch.object(R, "ROSTER", self.roster)
        roster_patch.start()
        self.addCleanup(roster_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="dango-roster-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8")))
        self.widget = DangoShowdownWidget(GameContext(manifest, PACKAGE, root / "data", root / "cache"))
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget.shutdown)
        self.widget._event_center = Mock()
        self.widget._sfx = Mock()
        self.widget.resize(810, 540)
        self.widget.show()
        self.widget._timer.stop()
        self.match = self.widget._match

    def buttons(self):
        self.assertFalse(self.widget.grab().isNull())
        return self.widget._hit_map.buttons

    def click(self, button):
        x, y = self.widget._transform().to_canvas(button.rect.x + button.rect.width / 2,
                                                button.rect.y + button.rect.height / 2)
        point = QPoint(round(x), round(y))
        self.assertTrue(self.widget.rect().contains(point), (button.value, point))
        QTest.mouseClick(self.widget, Qt.LeftButton, pos=point)

    def click_value(self, value):
        button = next((hit for hit in self.buttons() if hit.value == value), None)
        self.assertIsNotNone(button, value)
        self.click(button)

    def page(self):
        return self.widget._snapshot().roster_page

    def go_to_last_page(self):
        seen = set()
        for _ in range(len(self.roster)):
            current = self.page()
            buttons = self.buttons()
            seen.update(hit.value.split(":", 2)[2] for hit in buttons if hit.value.startswith("pick:"))
            forward = [hit for hit in buttons if hit.value.startswith("roster_page:")
                       and int(hit.value.split(":", 1)[1]) > current]
            if not forward:
                return seen
            self.click(min(forward, key=lambda hit: int(hit.value.split(":", 1)[1])))
            self.assertGreater(self.page(), current)
        self.fail("roster pager did not reach its last page")

    def select_from_last_page(self):
        self.go_to_last_page()
        last_page = self.page()
        self.assertGreater(last_page, 0)
        self.click_value("left")
        self.click_value("pick:left:" + self.roster[-1].character_id)
        self.click_value("right")
        self.click_value("pick:right:" + self.roster[-2].character_id)
        self.assertEqual(self.page(), last_page)
        return self.roster[-1].character_id, self.roster[-2].character_id

    def test_every_fighter_and_last_page_buttons_are_reachable_at_minimum_size(self):
        original = (self.match.fighter_ids, self.match.record.copy())
        seen = self.go_to_last_page()
        self.assertEqual(seen, {entry.character_id for entry in self.roster})
        self.assertGreater(self.page(), 0)
        self.assertEqual((self.match.fighter_ids, self.match.record), original)
        self.assertTrue(any(hit.value.endswith(":" + self.roster[-1].character_id)
                            for hit in self.buttons() if hit.value.startswith("pick:")))
        previous = [hit for hit in self.buttons() if hit.value.startswith("roster_page:")
                    and int(hit.value.split(":", 1)[1]) < self.page()]
        self.assertTrue(previous)
        old_page = self.page()
        self.click(max(previous, key=lambda hit: int(hit.value.split(":", 1)[1])))
        self.assertLess(self.page(), old_page)

    def test_current_bet_side_controls_selection_without_losing_page_or_fighters(self):
        selected = self.select_from_last_page()
        self.assertEqual(self.match.fighter_ids, selected)
        self.assertEqual((self.match.left.character_id, self.match.right.character_id), selected)
        last_page = self.page()
        self.click_value("amount:5")
        for side in ("left", "right", "left"):
            self.click_value(side)
            snapshot = self.widget._snapshot()
            self.assertEqual(snapshot.roster_page, last_page)
            self.assertEqual((snapshot.left_id, snapshot.right_id), selected)
            self.assertEqual((self.match.bet.side, self.match.bet.amount), (side, 5))
            self.assertEqual(self.match.fighter_ids, selected)

    def test_keyboard_pagination_clamps_and_never_changes_the_bet(self):
        self.click_value("left")
        self.click_value("amount:10")
        original = (self.match.fighter_ids, self.match.bet.side, self.match.bet.amount, self.match.record.copy())
        QTest.keyClick(self.widget, Qt.Key_PageUp)
        self.assertEqual(self.page(), 0)
        for _ in range(10):
            QTest.keyClick(self.widget, Qt.Key_PageDown)
        last_page = self.page()
        self.assertGreater(last_page, 0)
        QTest.keyClick(self.widget, Qt.Key_PageDown)
        self.assertEqual(self.page(), last_page)
        self.assertTrue(any(hit.value.endswith(":" + self.roster[-1].character_id)
                            for hit in self.buttons() if hit.value.startswith("pick:")))
        for _ in range(10):
            QTest.keyClick(self.widget, Qt.Key_PageUp)
        self.assertEqual(self.page(), 0)
        self.assertEqual((self.match.fighter_ids, self.match.bet.side, self.match.bet.amount, self.match.record), original)

    def test_clicking_opponents_occupied_tile_does_not_switch_the_bet(self):
        selected = self.select_from_last_page()
        self.click_value("left")
        self.click_value("amount:5")
        last_page = self.page()
        self.click_value("pick:left:" + selected[1])
        self.assertEqual(self.page(), last_page)
        self.assertEqual(self.match.fighter_ids, selected)
        self.assertEqual((self.match.picked_side, self.match.bet.side, self.match.bet.amount), ("left", "left", 5))

    def test_expanded_roster_selection_survives_a_series_round(self):
        selected = self.select_from_last_page()
        self.click_value("left")
        self.click_value("amount:5")
        QTest.keyClick(self.widget, Qt.Key_F2)
        QTest.keyClick(self.widget, Qt.Key_Return)
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.assertEqual(self.match.fighter_ids, selected)
        QTest.keyClick(self.widget, Qt.Key_PageDown)
        self.match.countdown = 0.0
        self.match._skill_damage(self.match.left, self.match.right, self.match.right.hp)
        self.match.update(1 / 60)
        self.assertEqual(self.match.phase, MatchPhase.RESULT)
        self.assertEqual(self.match.record["total_rounds"], 1)
        record = self.match.record.copy()
        self.match.result_reveal = 0.0
        QTest.keyClick(self.widget, Qt.Key_R)
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.assertEqual(self.match.round_index, 2)
        self.assertEqual(self.match.fighter_ids, selected)
        self.assertEqual((self.match.bet.side, self.match.bet.amount), ("left", 5))
        self.assertEqual(self.match.record, record)
        self.assertTrue(all(dango.hp == dango.max_hp for dango in self.match.dango))


if __name__ == "__main__":
    unittest.main()
