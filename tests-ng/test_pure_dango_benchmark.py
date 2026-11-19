"""Hidden benchmark identity and simultaneous data-driven contact attacks."""

from __future__ import annotations

import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import combat, events, roster, skills
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch, _spawn
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import Dango, MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.physics import Ball, Contact, step
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats


CONTACT = Contact("left", "right", 1.0, 0.0, 0.01, 0.5, 0.5, 1.0)


class RangeRng:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def randint(self, low, high):
        self.calls.append((low, high))
        value = next(self.values)
        if not low <= value <= high:
            raise AssertionError("test RNG result is outside the requested inclusive range")
        return value


class DangoContactDamageTests(unittest.TestCase):
    def test_new_fields_default_to_legacy_fixed_one_damage_without_changing_positionals(self):
        entry = roster.DangoEntry("legacy", "旧角色", "旧标签", (1, 2, 3), "", 123, (), (), (), None)
        fighter = Dango("left", "legacy", "旧角色", 35, 123, ())
        self.assertEqual((entry.max_hp, entry.contact_damage, entry.contact_damage_range), (123, 1, None))
        self.assertEqual((fighter.hp, fighter.max_hp, fighter.contact_damage, fighter.contact_damage_range),
                         (35, 123, 1, None))

    def test_ordinary_contact_still_deals_one_each_and_consumes_no_rng(self):
        left = Dango("left", "custom_left", "甲")
        right = Dango("right", "custom_right", "乙")
        rng = Mock()
        rng.randint.side_effect = AssertionError("fixed attacks must not draw random values")
        tracker = combat.CombatTracker()
        self.assertTrue(tracker.resolve(left, right, CONTACT, rng=rng))
        self.assertEqual((left.hp, right.hp), (99, 99))
        self.assertEqual((left.hits_dealt, left.hits_taken, right.hits_dealt, right.hits_taken), (1, 1, 1, 1))
        self.assertFalse(tracker.finished)
        rng.randint.assert_not_called()

    def test_fixed_three_and_one_attacks_work_in_both_side_orders(self):
        for strong_side in ("left", "right"):
            with self.subTest(strong_side=strong_side):
                left = Dango("left", "custom_left", "甲", contact_damage=3 if strong_side == "left" else 1)
                right = Dango("right", "custom_right", "乙", contact_damage=3 if strong_side == "right" else 1)
                tracker = combat.CombatTracker()
                self.assertTrue(tracker.resolve(left, right, CONTACT, rng=RangeRng([])))
                self.assertEqual((left.hp, right.hp), (99, 97) if strong_side == "left" else (97, 99))

    def test_ranged_contact_rolls_every_attack_and_accepts_both_inclusive_bounds(self):
        left = combat.reset_dango("left", "benchmark", "基准团子")
        right = Dango("right", "custom", "普通团子")
        tracker = combat.CombatTracker()
        rng = RangeRng([1, 5, 2, 4, 3])
        expected_hp = 100
        for rolled in (1, 5, 2, 4, 3):
            self.assertTrue(tracker.resolve(left, right, CONTACT, rng=rng))
            expected_hp -= rolled
            self.assertEqual(right.hp, expected_hp)
        self.assertEqual(left.hp, 495)
        self.assertEqual(rng.calls, [(1, 5)] * 5)

    def test_both_attacks_are_rolled_before_simultaneous_double_ko(self):
        left = Dango("left", "custom_left", "甲", hp=5, contact_damage_range=(1, 5))
        right = Dango("right", "custom_right", "乙", hp=1, contact_damage_range=(1, 5))
        tracker = combat.CombatTracker()
        rng = RangeRng([1, 5])
        self.assertTrue(tracker.resolve(left, right, CONTACT, rng=rng))
        self.assertEqual(rng.calls, [(1, 5), (1, 5)])
        self.assertEqual((left.hp, right.hp), (0, 0))
        self.assertTrue(tracker.finished)
        self.assertTrue(tracker.double_ko)
        result = tracker.build_result(left, right)
        self.assertTrue(result.double_ko)
        self.assertEqual(result.winner, "")

    def test_one_sided_ko_still_applies_the_losing_fighters_attack(self):
        for strong_side in ("left", "right"):
            with self.subTest(strong_side=strong_side):
                left = Dango("left", "custom_left", "甲", hp=5 if strong_side == "left" else 2,
                             contact_damage=3 if strong_side == "left" else 1)
                right = Dango("right", "custom_right", "乙", hp=5 if strong_side == "right" else 2,
                              contact_damage=3 if strong_side == "right" else 1)
                tracker = combat.CombatTracker()
                tracker.resolve(left, right, CONTACT, rng=RangeRng([]))
                self.assertEqual((left.hp, right.hp), (4, 0) if strong_side == "left" else (0, 4))
                self.assertTrue(tracker.finished)
                self.assertFalse(tracker.double_ko)
                self.assertEqual(tracker.build_result(left, right).winner, strong_side)

    def test_apply_damage_keeps_its_old_equal_damage_parameter_semantics(self):
        left = Dango("left", "custom_left", "甲", contact_damage=3, contact_damage_range=(1, 5))
        right = Dango("right", "custom_right", "乙")
        tracker = combat.CombatTracker()
        tracker.apply_damage(left, right, damage_per_side=7)
        self.assertEqual((left.hp, right.hp), (93, 93))
        tracker.apply_damage(left, right)
        self.assertEqual((left.hp, right.hp), (93, 93))


class DangoBenchmarkTests(unittest.TestCase):
    def test_benchmark_is_explicitly_resolvable_but_absent_from_public_roster_and_skills(self):
        entry = roster.BENCHMARK_ENTRY
        self.assertIs(roster.find_entry("benchmark"), entry)
        self.assertEqual((entry.max_hp, entry.contact_damage, entry.contact_damage_range), (500, 3, (1, 5)))
        self.assertEqual((entry.meters, entry.reactions, entry.collision_rules), ((), (), ()))
        self.assertIsNone(entry.sun_spirit)
        self.assertEqual(entry.skill, "")
        self.assertNotIn(entry.character_id, {item.character_id for item in roster.ROSTER})
        self.assertIsNone(roster.find_entry("not-a-fighter"))
        owner = combat.reset_dango("left", entry.character_id, entry.name)
        target = Dango("right", "feixue", "绯雪")
        publish = Mock()
        bus = events.ExtensionBus(publish)
        rng = Mock()
        skills.charge_from_contact(owner, target, 1, bus, Mock(), Mock())
        skills.charge_from_wall(owner, target, bus, rng, Mock())
        skills.roll_collisions(owner, target, "body", bus, rng, Mock(), Mock())
        publish.assert_not_called()
        rng.assert_not_called()
        self.assertEqual(rng.method_calls, [])

    def test_reset_and_spawn_inherit_benchmark_stats_and_body_position(self):
        for side in ("left", "right"):
            with self.subTest(side=side):
                ball = Ball(side, .2, .7, .5, .6)
                fighter = _spawn(side, "benchmark", ball)
                self.assertEqual((fighter.character_id, fighter.hp, fighter.max_hp), ("benchmark", 500, 500))
                self.assertEqual((fighter.contact_damage, fighter.contact_damage_range), (3, (1, 5)))
                self.assertEqual((fighter.x, fighter.y), (.2, .7))
                fighter.hp = 1
                fighter.reset_hp()
                self.assertEqual((fighter.hp, fighter.contact_damage, fighter.contact_damage_range), (500, 3, (1, 5)))

    def test_public_chooser_and_randomization_never_offer_hidden_benchmark(self):
        with tempfile.TemporaryDirectory(prefix="dango-hidden-benchmark-") as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(7))
            identities = match.fighter_ids
            for side in ("left", "right", ""):
                self.assertFalse(match.choose_dango(side, "benchmark"))
                self.assertEqual(match.fighter_ids, identities)
            public_ids = {entry.character_id for entry in roster.ROSTER}
            for _ in range(30):
                match.randomize()
                self.assertTrue(set(match.fighter_ids) <= public_ids)
                self.assertNotIn("benchmark", match.fighter_ids)

    def test_runtime_public_roster_extension_can_be_selected_while_benchmark_stays_hidden(self):
        with tempfile.TemporaryDirectory(prefix="dango-live-roster-") as tmp:
            # Construct the match before extending the authoritative roster:
            # a static import snapshot must not reject the new public identity.
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(7))
            future = roster.DangoEntry("runtime_public", "扩展团子", max_hp=217, contact_damage=2)
            with patch.object(roster, "ROSTER", (*roster.ROSTER, future)):
                self.assertIs(roster.find_entry(future.character_id), future)
                self.assertTrue(match.choose_dango("left", future.character_id))
                self.assertEqual((match.left.character_id, match.left.hp, match.left.contact_damage),
                                 (future.character_id, 217, 2))
                self.assertFalse(match.choose_dango("right", "benchmark"))
                self.assertTrue(match.choose_bet_amount(1))
                self.assertTrue(match.start_match())
                self.assertEqual((match.left.character_id, match.left.hp, match.left.contact_damage),
                                 (future.character_id, 217, 2))

    def test_explicit_simulation_spawn_uses_the_match_rng_for_benchmark_damage(self):
        with tempfile.TemporaryDirectory(prefix="dango-benchmark-rng-") as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(9))
            match.fighter_ids = ("feixue", "benchmark")
            match.choose_dango("left")
            match.choose_bet_amount(1)
            self.assertTrue(match.start_match())
            self.assertEqual(match.phase, MatchPhase.FIGHT)
            self.assertEqual((match.right.hp, match.right.contact_damage_range), (500, (1, 5)))
            match.rng = RangeRng([5])
            with patch.object(combat.random, "randint", side_effect=AssertionError("global RNG used")):
                match._apply_hit(CONTACT)
            self.assertEqual((match.left.hp, match.right.hp),
                             (roster.find_entry("feixue").max_hp - 5, 499))
            self.assertEqual(match.rng.calls, [(1, 5)])


class StarburstMotionRecoveryTests(unittest.TestCase):
    def make_match(self):
        temporary = tempfile.TemporaryDirectory(prefix="dango-starburst-motion-")
        self.addCleanup(temporary.cleanup)
        match = DangoMatch(DangoStats(Path(temporary.name) / "state.json"), rng=random.Random(73))
        match.fighter_ids = ("feixue", "benchmark")
        match.choose_dango("left")
        match.choose_bet_amount(1)
        self.assertTrue(match.start_match())
        match.countdown = 0.0
        match.balls = (Ball("left", .1, .1, 0.0, 0.0), Ball("right", .7, .3, .3, -.4))
        return match

    def transfer_collision_energy(self, match):
        # A moving body strikes the temporarily parked victim. This is the
        # real equal-mass collision solver, not a fabricated post-collision speed.
        victim = match.balls[1]
        match.balls = (Ball("left", victim.x, victim.y - .17, 0.0, .6), victim)
        for _ in range(5):
            outcome = step(match.balls, 1 / 60)
            match.balls = outcome.balls
            if outcome.contact is not None:
                break
        self.assertIsNotNone(outcome.contact)
        self.assertGreater(match.balls[1].speed, 0.0)

    def test_survivor_stays_parked_before_one_second_then_recovers_original_speed_and_heading(self):
        match = self.make_match()
        match._reset_to_bottom_center(match.right)
        self.assertEqual(match.balls[1].speed, 0.0)
        for _ in range(59):
            match.update(1 / 60)
        self.assertEqual(match.balls[1].speed, 0.0)
        match.update(1 / 60)
        self.assertAlmostEqual(match.balls[1].vx, .3)
        self.assertAlmostEqual(match.balls[1].vy, -.4)
        self.assertAlmostEqual(match.balls[1].speed, .5)
        self.assertEqual(match._starburst_motion, {})

    def test_motion_transferred_by_a_collision_is_preserved_when_stored_energy_returns(self):
        match = self.make_match()
        match._reset_to_bottom_center(match.right)
        self.transfer_collision_energy(match)
        before = sum(ball.speed ** 2 for ball in match.balls)
        current = match.balls[1]
        heading = math.atan2(current.vy, current.vx)
        match._advance_starburst_motion(1.0)
        after = sum(ball.speed ** 2 for ball in match.balls)
        self.assertAlmostEqual(after - before, .3 ** 2 + .4 ** 2)
        self.assertAlmostEqual(math.atan2(match.balls[1].vy, match.balls[1].vx), heading)
        self.assertEqual(match._starburst_motion, {})

    def test_repeated_starburst_accumulates_borrowed_energy_and_restarts_the_one_second_hold(self):
        match = self.make_match()
        match._reset_to_bottom_center(match.right)
        match._advance_starburst_motion(.75)
        self.transfer_collision_energy(match)
        expected_energy = sum(ball.speed ** 2 for ball in match.balls) + .25
        match._reset_to_bottom_center(match.right)
        match._advance_starburst_motion(.5)
        self.assertEqual(match.balls[1].speed, 0.0)
        match._advance_starburst_motion(.5)
        self.assertAlmostEqual(sum(ball.speed ** 2 for ball in match.balls), expected_energy)
        self.assertEqual(match._starburst_motion, {})

    def test_ko_stays_down_and_never_recovers_motion_during_settlement(self):
        match = self.make_match()
        match._reset_to_bottom_center(match.right)
        match.right.hp = 1
        match._skill_damage(match.left, match.right, 1, reposition=True, skill="starburst")
        match.update(1 / 60)
        self.assertEqual(match.phase, MatchPhase.RESULT)
        for _ in range(20):
            match.update(.1)
        self.assertEqual(match.right.hp, 0)
        self.assertTrue(all(ball.speed == 0.0 for ball in match.balls))
        self.assertEqual(match._starburst_motion, {})

    def test_preparing_or_starting_a_new_round_discards_old_borrowed_energy(self):
        match = self.make_match()
        match._reset_to_bottom_center(match.right)
        self.assertTrue(match._starburst_motion)
        match.prepare_round()
        self.assertEqual(match._starburst_motion, {})
        match._starburst_motion["right"] = (.5, .25, 0.0)
        match.choose_dango("left")
        match.choose_bet_amount(1)
        self.assertTrue(match.start_match())
        self.assertEqual(match._starburst_motion, {})

    def test_low_speed_trap_recovers_and_replays_identically_at_30_60_and_120_fps(self):
        results = []
        for fps in (30, 60, 120):
            match = self.make_match()
            match._reset_to_bottom_center(match.right)
            self.assertTrue(all(ball.speed == 0.0 for ball in match.balls))
            for _ in range(2 * fps):
                match.update(1 / fps)
            self.assertGreater(match.balls[1].speed, .49)
            results.append((tuple(ball.signature for ball in match.balls), match.tracker.elapsed,
                            match._starburst_motion))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
