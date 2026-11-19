"""New fighter data/assets and natural series acceptance."""

from __future__ import annotations

import itertools
import random
import tempfile
import unittest
from pathlib import Path

from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import roster as R
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"
NEW_FIGHTERS = (("xigelika", "西格莉卡", "日灵团子"), ("feixue", "绯雪", "霜渐团子"))


def _pick_pair(match, left_id, right_id):
    # Move through a free temporary slot so even a reversed initial pair can
    # be selected through the public chooser without duplicate identities.
    temporary = next(entry.character_id for entry in R.ROSTER
                     if entry.character_id not in {match.right.character_id, right_id})
    if not match.choose_dango("left", temporary):
        raise AssertionError("temporary fighter selection failed")
    if not match.choose_dango("right", right_id):
        raise AssertionError("right fighter selection failed")
    if not match.choose_dango("left", left_id):
        raise AssertionError("left fighter selection failed")


class DangoNewRosterTests(unittest.TestCase):
    def test_new_fighters_have_requested_identity_health_and_separate_skill_rules(self):
        for character_id, name, tag in NEW_FIGHTERS:
            with self.subTest(character=character_id):
                entry = R.find_entry(character_id)
                self.assertIsNotNone(entry)
                expected_hp = {"xigelika": 185, "feixue": 165}[character_id]
                self.assertEqual((entry.name, entry.tag, entry.max_hp), (name, tag, expected_hp))
                if character_id == "xigelika":
                    self.assertTrue(entry.skill)
                    self.assertEqual(entry.sun_spirit, R.SunSpiritSpec())
                    self.assertIsNone(entry.frost_slash)
                else:
                    self.assertTrue(entry.skill)
                    self.assertIsNone(entry.sun_spirit)
                    self.assertEqual(entry.frost_slash, R.FrostSlashSpec())
                self.assertEqual(entry.meters, ())
                self.assertEqual(entry.reactions, ())
                self.assertEqual(entry.collision_rules, ())

    def test_every_fighter_decodes_all_three_states_and_hurt_tint(self):
        loader = DangoAssetLoader(PACKAGE / "assets", inline=True)
        self.assertTrue(loader.ready, loader.error)
        payload = loader.assets()
        expected = {(entry.character_id, state) for entry in R.ROSTER for state in ("idle", "attack", "hurt")}
        self.assertEqual(set(payload["states"]), expected)
        self.assertEqual(len(payload["states"]), len(R.ROSTER) * 3)
        for identity, state in expected:
            with self.subTest(character=identity, state=state):
                resource = payload["states"][(identity, state)]["resource"]
                frame = resource.frames[0]
                self.assertGreater(frame.width, 0)
                self.assertGreater(frame.height, 0)
                self.assertTrue(any(frame.pixels[3::4]))
                if state == "hurt":
                    self.assertIsNotNone(payload["states"][(identity, state)]["hurt_tint"])

    def test_all_distinct_old_and_new_fighter_pairs_can_be_selected_and_started(self):
        with tempfile.TemporaryDirectory(prefix="dango-new-pairs-") as tmp:
            for index, (left, right) in enumerate(itertools.permutations(R.ROSTER, 2)):
                with self.subTest(left=left.character_id, right=right.character_id):
                    match = DangoMatch(DangoStats(Path(tmp) / f"pair-{index}.json"), rng=random.Random(index))
                    match.prepare_round()
                    _pick_pair(match, left.character_id, right.character_id)
                    self.assertTrue(match.choose_bet_amount(5))
                    self.assertTrue(match.start_match())
                    self.assertEqual(match.fighter_ids, (left.character_id, right.character_id))
                    self.assertEqual((match.left.name, match.right.name), (left.name, right.name))
                    self.assertEqual((match.left.hp, match.right.hp), (left.max_hp, right.max_hp))
                    self.assertEqual((match.bet.side, match.bet.amount), ("left", 5))

    def test_new_fighters_naturally_complete_three_rounds_with_sun_spirits_only(self):
        emitted = []
        bus = ev.ExtensionBus(publish=lambda name, payload: emitted.append((name, payload)))
        forbidden = {ev.LASER_FIRE, ev.STARBURST_FIRE, ev.VOID_HAND}
        with tempfile.TemporaryDirectory(prefix="dango-new-series-") as tmp:
            stats = DangoStats(Path(tmp) / "state.json")
            # Existing stats persist while each new round settles once.
            for _ in range(2):
                stats.settle(bet_side="left", winner="left", double_ko=False, amount=5)
            original = stats.snapshot()
            match = DangoMatch(stats, rng=random.Random(19), bus=bus)
            match.prepare_round()
            self.assertTrue(match.choose_format("ft3"))
            _pick_pair(match, "xigelika", "feixue")
            self.assertTrue(match.choose_bet_amount(10))
            self.assertTrue(match.start_match())

            for round_number in range(1, 4):
                self.assertEqual(match.phase, MatchPhase.FIGHT)
                self.assertEqual(match.round_index, round_number)
                self.assertEqual(match.fighter_ids, ("xigelika", "feixue"))
                self.assertEqual((match.bet.side, match.bet.amount), ("left", 10))
                self.assertEqual((match.left.hp, match.right.hp),
                                 (R.find_entry("xigelika").max_hp, R.find_entry("feixue").max_hp))
                self.assertEqual(match.left.sun_energy, [])
                self.assertFalse(any(match.left.sun_cooldowns.values()))
                self.assertEqual(match.left.sun_spirits, [])
                before = len(emitted)
                for _ in range(20000):
                    match.update(0.1)
                    if match.phase is MatchPhase.RESULT:
                        break
                self.assertEqual(match.phase, MatchPhase.RESULT, (match.left.hp, match.right.hp))
                hits = [payload for name, payload in emitted[before:] if name == ev.HIT]
                self.assertGreater(len(hits), 1)
                summons = [payload for name, payload in emitted[before:] if name == ev.SUN_SPIRIT_SUMMON]
                self.assertTrue(summons)
                self.assertTrue(all(payload["side"] == "left" for payload in summons))
                self.assertFalse(forbidden & {name for name, _ in emitted})
                record = stats.snapshot()
                self.assertEqual(record["total_rounds"], original["total_rounds"] + round_number)
                total = record["total_draws"] + record["total_bet_wins"] + record["total_bet_losses"]
                self.assertEqual(total, record["total_rounds"])
                for _ in range(32):
                    match.update(0.1)
                self.assertEqual(match.result_reveal, 0.0)
                self.assertTrue(match.advance_round())

            self.assertEqual(match.phase, MatchPhase.SELECT)
            self.assertFalse(match.can_start)
            self.assertFalse(match.bet.ready)
            reloaded = DangoStats(Path(tmp) / "state.json").snapshot()
            self.assertEqual(reloaded, stats.snapshot())


if __name__ == "__main__":
    unittest.main()
