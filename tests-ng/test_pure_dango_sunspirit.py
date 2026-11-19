"""Sun-spirit resource, delayed area damage and simulation-clock contracts."""

from __future__ import annotations

import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import skills
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import Dango, MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.physics import StepResult, WallHit
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.roster import SunSpiritSpec
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats

STEP = 1 / 60


class SequenceRng:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    def random(self):
        self.calls += 1
        return next(self.values)


class SunSpiritRulesTests(unittest.TestCase):
    def setUp(self):
        self.emitted = []
        self.bus = ev.ExtensionBus(lambda name, payload: self.emitted.append((name, payload)))
        self.owner = Dango("left", "xigelika", "西格莉卡", x=0.2, y=0.2)
        self.target = Dango("right", "feixue", "绯雪", x=0.5, y=0.5)
        self.damage_calls = []
        self.spec = SunSpiritSpec()

    def damage(self, owner, target, amount, **options):
        removed = min(target.hp, amount)
        target.hp -= removed
        self.damage_calls.append((amount, options))
        return removed

    def deflect(self, *args):
        pass

    def tick(self, count=1):
        for _ in range(count):
            skills.advance_sun_spirits(self.owner, self.target, STEP, self.bus, self.damage, self.deflect)

    def events(self, name):
        return [payload for event, payload in self.emitted if event == name]

    def summon(self, color):
        rng = SequenceRng([0.0 if color == "yellow" else 0.99] * 2)
        for _ in range(2):
            skills.gain_sun_energy(self.owner, self.target, self.bus, rng, self.damage)

    def test_balance_configuration_uses_scaled_radii_and_damage_with_existing_trigger_timing(self):
        self.assertEqual((self.spec.radius, self.spec.second_radius), (0.162, 0.27))
        self.assertEqual((self.spec.damage, self.spec.second_damage), (28, 14))
        self.assertEqual((self.spec.slots, self.spec.cost), (4, 2))
        self.assertEqual((self.spec.cooldown, self.spec.delay, self.spec.second_delay, self.spec.wall_chance),
                         (5.0, 1.0, 0.45, 0.5))
        self.assertEqual(self.spec.overflow_cooldown_reduction, 1.0)

    def test_random_colours_share_four_slots_and_preserve_full_energy_during_cooldown(self):
        self.owner.sun_cooldowns = {"yellow": 5.0, "cyan": 5.0}
        rng = SequenceRng([0.0, 0.5, 0.49, 0.99])
        for _ in range(4):
            skills.gain_sun_energy(self.owner, self.target, self.bus, rng, self.damage)
        self.assertEqual(self.owner.sun_energy, ["yellow", "cyan", "yellow", "cyan"])
        self.assertEqual(self.owner.charge("sun_energy"), 4)
        self.assertEqual(rng.calls, 4)
        self.assertFalse(self.events(ev.SUN_SPIRIT_SUMMON))
        self.assertEqual(self.events(ev.CHARGE_CHANGED)[-1]["colors"], tuple(self.owner.sun_energy))
        self.tick(299)
        self.assertEqual(len(self.owner.sun_energy), 4)
        self.assertFalse(self.events(ev.SUN_SPIRIT_SUMMON))
        self.tick()
        self.assertEqual(self.owner.sun_energy, [])
        self.assertEqual({p["color"] for p in self.events(ev.SUN_SPIRIT_SUMMON)}, {"yellow", "cyan"})
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 5.0, "cyan": 5.0})

    def test_wall_roll_uses_fifty_percent_threshold_then_a_separate_equal_colour_roll(self):
        self.assertEqual(self.spec.wall_chance, 0.5)
        for value in (0.5, 0.99):
            rng = SequenceRng([value])
            skills.charge_from_wall(self.owner, self.target, self.bus, rng, self.damage)
            self.assertEqual(rng.calls, 1)
        self.assertEqual(self.owner.sun_energy, [])
        self.assertFalse(self.events(ev.CHARGE_CHANGED))
        for chance, colour in ((0.499, 0.0), (0.0, 0.5)):
            rng = SequenceRng([chance, colour])
            skills.charge_from_wall(self.owner, self.target, self.bus, rng, self.damage)
            self.assertEqual(rng.calls, 2)
        self.assertEqual(self.owner.sun_energy, ["yellow", "cyan"])
        self.assertEqual([p["source"] for p in self.events(ev.CHARGE_CHANGED)], ["wall", "wall"])
        self.assertEqual((self.owner.hp, self.target.hp), (100, 100))
        self.assertFalse(self.damage_calls)

    def test_successful_wall_roll_keeps_full_energy_reduces_cooldowns_and_does_not_affect_other_fighters(self):
        self.owner.sun_cooldowns = {"yellow": 5.0, "cyan": 5.0}
        self.owner.sun_energy = ["yellow", "cyan", "yellow", "cyan"]
        rng = SequenceRng([0.0])
        skills.charge_from_wall(self.owner, self.target, self.bus, rng, self.damage)
        self.assertEqual(rng.calls, 1)
        self.assertEqual(self.owner.sun_energy, ["yellow", "cyan", "yellow", "cyan"])
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 4.0, "cyan": 4.0})
        self.assertFalse(self.emitted)
        for character_id in ("amis", "daniya", "feixue"):
            owner = Dango("left", character_id, character_id)
            rng = SequenceRng([])
            skills.charge_from_wall(owner, self.target, self.bus, rng, self.damage)
            self.assertEqual(owner.sun_energy, [])
            self.assertEqual(rng.calls, 0)

    def test_full_attack_reduces_both_cooldowns_once_without_rolling_or_replacing_energy(self):
        self.owner.sun_energy = ["yellow", "cyan", "yellow", "cyan"]
        self.owner.sun_cooldowns = {"yellow": 5.0, "cyan": 3.0}
        rng = SequenceRng([])
        skills.gain_sun_energy(self.owner, self.target, self.bus, rng, self.damage)
        self.assertEqual(rng.calls, 0)
        self.assertEqual(self.owner.sun_energy, ["yellow", "cyan", "yellow", "cyan"])
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 4.0, "cyan": 2.0})
        self.assertFalse(self.emitted)
        skills.charge_from_contact(self.owner, self.target, 1, self.bus, self.damage, self.deflect)
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 3.0, "cyan": 1.0})
        self.assertEqual(len(self.owner.sun_energy), 4)

    def test_full_overflow_reduces_a_single_active_cooldown_and_leaves_zero_alone(self):
        self.owner.sun_energy = ["yellow", "yellow", "yellow", "cyan"]
        self.owner.sun_cooldowns = {"yellow": 3.0, "cyan": 0.0}
        skills.gain_sun_energy(self.owner, self.target, self.bus, SequenceRng([]), self.damage)
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 2.0, "cyan": 0.0})
        self.assertEqual(len(self.owner.sun_energy), 4)
        self.assertFalse(self.events(ev.SUN_SPIRIT_SUMMON))

    def test_overflow_floors_short_cooldown_and_immediately_casts_without_reducing_new_cooldown(self):
        for remaining in (0.25, 1.0):
            with self.subTest(remaining=remaining):
                self.emitted.clear()
                self.owner.reset_hp()
                self.owner.sun_energy = ["yellow", "yellow", "yellow", "cyan"]
                self.owner.sun_cooldowns = {"yellow": remaining, "cyan": 4.0}
                skills.gain_sun_energy(self.owner, self.target, self.bus, SequenceRng([]), self.damage)
                self.assertEqual(self.owner.sun_energy, ["yellow", "cyan"])
                self.assertEqual(self.owner.sun_cooldowns, {"yellow": 5.0, "cyan": 3.0})
                self.assertEqual([p["color"] for p in self.events(ev.SUN_SPIRIT_SUMMON)], ["yellow"])
                self.assertEqual([p["source"] for p in self.events(ev.CHARGE_CHANGED)], ["sun_spirit"])

    def test_overflow_can_ready_both_colours_and_each_new_five_second_cooldown_stays_intact(self):
        self.owner.sun_energy = ["yellow", "cyan", "yellow", "cyan"]
        self.owner.sun_cooldowns = {"yellow": 1.0, "cyan": 0.1}
        skills.gain_sun_energy(self.owner, self.target, self.bus, SequenceRng([]), self.damage)
        self.assertEqual(self.owner.sun_energy, [])
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 5.0, "cyan": 5.0})
        self.assertEqual([p["color"] for p in self.events(ev.SUN_SPIRIT_SUMMON)], ["yellow", "cyan"])

    def test_failed_wall_roll_does_not_reduce_full_slot_cooldowns(self):
        self.owner.sun_energy = ["yellow", "cyan", "yellow", "cyan"]
        self.owner.sun_cooldowns = {"yellow": 3.0, "cyan": 5.0}
        rng = SequenceRng([0.5])
        skills.charge_from_wall(self.owner, self.target, self.bus, rng, self.damage)
        self.assertEqual(rng.calls, 1)
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 3.0, "cyan": 5.0})
        self.assertEqual(len(self.owner.sun_energy), 4)
        self.assertFalse(self.emitted)

    def test_unfilled_inventory_adds_colour_without_reducing_cooldowns(self):
        self.owner.sun_energy = ["yellow"]
        self.owner.sun_cooldowns = {"yellow": 5.0, "cyan": 3.0}
        rng = SequenceRng([0.9])
        skills.gain_sun_energy(self.owner, self.target, self.bus, rng, self.damage)
        self.assertEqual(rng.calls, 1)
        self.assertEqual(self.owner.sun_energy, ["yellow", "cyan"])
        self.assertEqual(self.owner.sun_cooldowns, {"yellow": 5.0, "cyan": 3.0})
        self.assertEqual(self.events(ev.CHARGE_CHANGED)[-1]["source"], "contact")

    def test_colour_cooldowns_are_independent_and_stored_pair_casts_without_new_attack(self):
        self.owner.sun_energy = ["yellow", "cyan", "yellow", "cyan"]
        self.owner.sun_cooldowns["yellow"] = 3.0
        self.tick()
        self.assertEqual(self.owner.sun_energy, ["yellow", "yellow"])
        self.assertEqual([p["color"] for p in self.events(ev.SUN_SPIRIT_SUMMON)], ["cyan"])
        self.assertEqual(self.owner.sun_cooldowns["cyan"], 5.0)
        # Walk away from the first summon so this test measures cooldowns only.
        self.target.x, self.target.y = 0.95, 0.95
        self.tick(178)
        self.assertEqual(self.owner.sun_energy, ["yellow", "yellow"])
        self.tick()
        self.assertEqual(self.owner.sun_energy, [])
        self.assertEqual([p["color"] for p in self.events(ev.SUN_SPIRIT_SUMMON)], ["cyan", "yellow"])
        self.assertEqual(self.owner.sun_cooldowns["yellow"], 5.0)
        self.assertAlmostEqual(self.owner.sun_cooldowns["cyan"], 5.0 - 179 * STEP)

    def test_yellow_has_real_one_second_delay_fixed_anchor_and_twenty_eight_damage_once(self):
        self.summon("yellow")
        self.assertEqual(self.target.hp, 100)
        self.assertEqual(self.owner.sun_cooldowns["yellow"], 5.0)
        self.assertEqual(self.owner.sun_cooldowns["cyan"], 0.0)
        self.target.x += self.spec.radius + self.target.radius - 0.0001
        self.tick(59)
        self.assertEqual(self.target.hp, 100)
        self.assertFalse(self.events(ev.SUN_SPIRIT_BURST))
        self.tick()
        self.assertEqual(self.target.hp, 72)
        burst = self.events(ev.SUN_SPIRIT_BURST)[0]
        self.assertEqual((burst["point"], burst["stage"], burst["damage"], burst["hit"]), ((0.5, 0.5), 1, 28, True))
        self.assertEqual(self.damage_calls[0][1]["skill"], "sun_spirit_yellow")
        self.tick(120)
        self.assertEqual(self.target.hp, 72)
        self.assertEqual(len(self.events(ev.SUN_SPIRIT_BURST)), 1)

    def test_cyan_second_explosion_is_later_larger_and_deals_fourteen_damage(self):
        self.target.x = 0.4
        self.summon("cyan")
        self.tick(60)
        self.assertEqual(self.target.hp, 72)
        # Outside the first blast, but inside the second blast's circle.
        self.target.x += (self.spec.radius + self.spec.second_radius) / 2 + self.target.radius
        self.tick(26)
        self.assertEqual(self.target.hp, 72)
        self.tick()
        self.assertEqual(self.target.hp, 58)
        bursts = self.events(ev.SUN_SPIRIT_BURST)
        self.assertEqual([p["damage"] for p in bursts], [28, 14])
        self.assertEqual([p["stage"] for p in bursts], [1, 2])
        self.assertEqual([p["radius"] for p in bursts], [0.162, 0.27])
        self.assertEqual(bursts[0]["summon_id"], bursts[1]["summon_id"])
        self.assertEqual(bursts[0]["point"], bursts[1]["point"])
        self.assertFalse(self.owner.sun_spirits)

    def test_opponent_can_evade_both_explosions_without_cancelling_the_visual_events(self):
        self.summon("cyan")
        self.target.x, self.target.y = 0.05, 0.05
        self.tick(87)
        self.assertEqual(self.target.hp, 100)
        self.assertFalse(self.damage_calls)
        bursts = self.events(ev.SUN_SPIRIT_BURST)
        self.assertEqual(len(bursts), 2)
        self.assertTrue(all(not p["hit"] and p["damage"] == 0 for p in bursts))
        self.assertTrue(all(p["point"] == (0.5, 0.5) for p in bursts))

    def test_other_fighters_do_not_gain_sun_energy_and_reset_clears_every_skill_state(self):
        self.owner.character_id = "amis"
        skills.gain_sun_energy(self.owner, self.target, self.bus, SequenceRng([]), self.damage)
        self.assertEqual(self.owner.sun_energy, [])
        self.owner.character_id = "xigelika"
        self.summon("cyan")
        self.owner.sun_energy.append("yellow")
        self.owner.reset_hp()
        self.assertEqual(self.owner.sun_energy, [])
        self.assertFalse(any(self.owner.sun_cooldowns.values()))
        self.assertEqual(self.owner.sun_spirits, [])
        self.assertEqual(self.owner.sun_summon_count, 0)


class SunSpiritEngineTests(unittest.TestCase):
    def make_match(self, path, *, seed=73, emitted=None):
        emitted = emitted if emitted is not None else []
        match = DangoMatch(DangoStats(path), rng=random.Random(seed),
                           bus=ev.ExtensionBus(lambda name, payload: emitted.append((name, payload))))
        match.fighter_ids = ("xigelika", "feixue")
        match.choose_dango("left")
        match.choose_bet_amount(1)
        self.assertTrue(match.start_match())
        return match

    def test_engine_clocks_freeze_outside_live_fight_and_round_reset_discards_pending_blasts(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitted = []
            match = self.make_match(Path(tmp) / "state.json", emitted=emitted)
            owner, target = match.dango
            skills.gain_sun_energy(owner, target, match.bus, SequenceRng([0]), match._skill_damage)
            skills.gain_sun_energy(owner, target, match.bus, SequenceRng([0]), match._skill_damage)
            match.update(0.1)  # Opening countdown never advances ability clocks.
            self.assertEqual(owner.sun_cooldowns["yellow"], 5.0)
            self.assertEqual(owner.sun_spirits[0].elapsed, 0.0)
            match.phase = MatchPhase.RESULT
            match.update(0.1)
            self.assertEqual(owner.sun_cooldowns["yellow"], 5.0)
            self.assertEqual(owner.sun_spirits[0].elapsed, 0.0)
            match.result_reveal = 0.0
            self.assertTrue(match.advance_round())
            self.assertEqual(match.left.sun_energy, [])
            self.assertFalse(any(match.left.sun_cooldowns.values()))
            self.assertEqual(match.left.sun_spirits, [])
            self.assertFalse(any(name == ev.SUN_SPIRIT_BURST for name, _ in emitted))

    def test_engine_uses_live_target_body_and_damage_does_not_generate_more_energy(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitted = []
            match = self.make_match(Path(tmp) / "state.json", emitted=emitted)
            match.countdown = 0.0
            match.balls = (replace(match.balls[0], x=.2, y=.2, vx=0, vy=0),
                           replace(match.balls[1], x=.7, y=.7, vx=0, vy=0))
            match.left.sun_energy = ["cyan", "cyan"]
            match.update(STEP)
            summon = next(p for name, p in emitted if name == ev.SUN_SPIRIT_SUMMON)
            self.assertEqual(summon["point"], (.7, .7))
            for _ in range(87):
                match.update(STEP)
            self.assertEqual(match.right.hp, match.right.max_hp - 28 - 14)
            self.assertEqual(match.left.sun_energy, [])
            numbers = [p for name, p in emitted if name == ev.SKILL_DAMAGE]
            self.assertEqual([p["damage"] for p in numbers], [28, 14])
            self.assertTrue(all(p["point"] == (.7, .7) for p in numbers))

    def test_each_real_wall_hit_rolls_once_without_contact_charge_or_base_damage(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitted = []
            match = self.make_match(Path(tmp) / "state.json", emitted=emitted)
            match.countdown = 0.0
            match.rng = SequenceRng([0.49, 0.1, 0.5])
            wall = WallHit("left", "top", 1.0, .2, 0.0)
            with_wall = StepResult(match.balls, wall_hits=(wall,))
            no_wall = StepResult(match.balls)
            with patch("lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine.step",
                       side_effect=(with_wall, with_wall, no_wall)):
                for _ in range(3):
                    match.update(STEP)
            self.assertEqual(match.left.sun_energy, ["yellow"])
            self.assertEqual(match.rng.calls, 3)
            self.assertEqual((match.left.hp, match.right.hp), (match.left.max_hp, match.right.max_hp))
            charges = [p for name, p in emitted if name == ev.CHARGE_CHANGED]
            self.assertEqual(len(charges), 1)
            self.assertEqual(charges[0]["source"], "wall")
            self.assertFalse(any(name == ev.HIT for name, _ in emitted))

    def test_seeded_skill_events_and_outcome_are_identical_at_30_60_and_120_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            for fps in (30, 60, 120):
                emitted = []
                match = self.make_match(Path(tmp) / f"{fps}.json", emitted=emitted)
                match.countdown = 0.0
                for _ in range(1000 * fps):
                    match.update(1 / fps)
                    if match.phase is MatchPhase.RESULT:
                        break
                self.assertEqual(match.phase, MatchPhase.RESULT)
                skills_seen = [(n, p) for n, p in emitted if n in (ev.CHARGE_CHANGED, ev.SUN_SPIRIT_SUMMON, ev.SUN_SPIRIT_BURST)]
                self.assertTrue(any(n == ev.SUN_SPIRIT_SUMMON for n, _ in skills_seen))
                self.assertTrue(any(n == ev.CHARGE_CHANGED and p["source"] == "wall" for n, p in skills_seen))
                results.append((match.result.winner, match.result.stats.duration_seconds,
                                match.result.stats.left_hp, match.result.stats.right_hp, skills_seen))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
