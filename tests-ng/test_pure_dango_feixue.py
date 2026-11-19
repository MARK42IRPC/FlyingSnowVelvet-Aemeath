"""Frost slash, simultaneous energy attacks and movement-safe slow effects."""

from __future__ import annotations

import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import combat, events as ev, roster, skills
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import Dango, MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.physics import Ball, Contact, reset_ball_to, step
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats

STEP = 1 / 60
CONTACT = Contact("left", "right", 1.0, 0.0, .01, .5, .5, 1.0)


class FixedRange:
    def __init__(self, amount):
        self.amount = amount

    def randint(self, low, high):
        if not low <= self.amount <= high:
            raise AssertionError("invalid test damage")
        return self.amount


class FrostFixture(unittest.TestCase):
    def make_match(self, character="feixue", *, seed=73, stationary=True):
        temporary = tempfile.TemporaryDirectory(prefix="dango-frost-")
        self.addCleanup(temporary.cleanup)
        emitted = []
        match = DangoMatch(DangoStats(Path(temporary.name) / "state.json"), rng=random.Random(seed),
                           bus=ev.ExtensionBus(lambda name, payload: emitted.append((name, payload))))
        match.fighter_ids = (character, "benchmark")
        match.choose_dango("left")
        match.choose_bet_amount(1)
        self.assertTrue(match.start_match())
        match.countdown = 0.0
        if stationary:
            match.balls = (Ball("left", .2, .2, 0, 0), Ball("right", .8, .8, 0, 0))
        return match, emitted

    @staticmethod
    def tick(match, frames):
        for _ in range(frames):
            match.update(STEP)

    @staticmethod
    def payloads(emitted, name):
        return [payload for event, payload in emitted if event == name]


class FrostSlashRulesTests(FrostFixture):
    def test_frost_configuration_is_data_driven_and_does_not_attach_to_other_fighters(self):
        spec = roster.FrostSlashSpec()
        self.assertEqual((spec.interval, spec.radius, spec.damage), (3.0, .286, 30))
        self.assertEqual((spec.slow_seconds, spec.slow_multiplier), (3.0, .5))
        self.assertEqual((spec.energy_slots, spec.contact_bonus, spec.energy_per_contact), (4, 2, 1))
        self.assertEqual(roster.find_entry("feixue").frost_slash, spec)
        self.assertTrue(roster.find_entry("feixue").skill)
        for identity in ("amis", "daniya", "xigelika", "benchmark"):
            self.assertIsNone(roster.find_entry(identity).frost_slash)
        fighter = Dango("left", "custom", "旧角色")
        self.assertEqual((fighter.frost_elapsed, fighter.frost_energy, fighter.slow_seconds, fighter.slow_multiplier),
                         (0.0, 0, 0.0, 1.0))

    def test_first_cast_waits_three_seconds_and_every_miss_stores_one_up_to_four(self):
        match, emitted = self.make_match()
        self.tick(match, 179)
        self.assertEqual(match.left.frost_energy, 0)
        self.assertFalse(self.payloads(emitted, ev.FROST_SLASH))
        self.tick(match, 1)
        self.assertEqual(match.left.frost_energy, 1)
        self.tick(match, 4 * 180)
        slashes = self.payloads(emitted, ev.FROST_SLASH)
        self.assertEqual(len(slashes), 5)
        self.assertTrue(all(not p["hit"] and p["damage"] == 0 for p in slashes))
        self.assertEqual(match.left.frost_energy, 4)
        self.assertEqual(match.left.charge("frost_energy"), 4)
        charges = [p for p in self.payloads(emitted, ev.CHARGE_CHANGED) if p["meter_id"] == "frost_energy"]
        self.assertEqual([p["value"] for p in charges], [1, 2, 3, 4])
        self.assertTrue(all(p["source"] == "frost_slash" and p["label"] == "霜渐" and p["slots"] == 4 for p in charges))
        self.assertEqual(match.right.hp, 500)
        self.assertEqual(match.right.slow_seconds, 0.0)

    def test_real_circle_edge_hits_for_thirty_but_just_outside_misses(self):
        for outside in (False, True):
            with self.subTest(outside=outside):
                match, emitted = self.make_match()
                radius = roster.FrostSlashSpec().radius
                target_x = .3 + radius + match.balls[1].radius + (1e-5 if outside else 0)
                match.balls = (Ball("left", .3, .5, 0, 0), Ball("right", target_x, .5, 0, 0))
                match.left.frost_energy = 2
                match.left.frost_elapsed = 3.0 - STEP
                match.update(STEP)
                slash = self.payloads(emitted, ev.FROST_SLASH)[0]
                self.assertEqual(slash["point"], (.3, .5))
                self.assertEqual(slash["radius"], .286)
                self.assertEqual((slash["cooldown"], slash["slow_seconds"], slash["slow_multiplier"]), (3.0, 3.0, .5))
                self.assertEqual(slash["hit"], not outside)
                self.assertEqual(match.right.hp, 500 if outside else 470)
                self.assertEqual(match.left.frost_energy, 3 if outside else 2)
                self.assertEqual(match.right.slow_seconds, 0.0 if outside else 3.0)
                self.assertEqual(match.balls[1].motion_multiplier, 1.0 if outside else .5)
                numbers = self.payloads(emitted, ev.SKILL_DAMAGE)
                self.assertEqual(len(numbers), 0 if outside else 1)
                if numbers:
                    self.assertEqual((numbers[0]["skill"], numbers[0]["damage"]), ("frost_slash", 30))

    def test_lethal_slash_reports_actual_damage_once_without_gaining_energy_or_slowing_a_corpse(self):
        match, emitted = self.make_match()
        match.balls = (Ball("left", .3, .5, 0, 0), Ball("right", .55, .5, 0, 0))
        match.right.hp = 17
        match.left.frost_energy = 2
        match.left.frost_elapsed = 3.0 - STEP
        match.update(STEP)
        self.assertEqual(match.phase, MatchPhase.RESULT)
        self.assertEqual(match.left.frost_energy, 2)
        self.assertEqual(match.right.slow_seconds, 0)
        self.assertEqual(self.payloads(emitted, ev.FROST_SLASH)[0]["damage"], 17)
        self.assertEqual([p["damage"] for p in self.payloads(emitted, ev.SKILL_DAMAGE)], [17])

    def test_valid_contact_spends_one_charge_and_its_bonus_is_in_the_actual_hit_payload(self):
        match, emitted = self.make_match()
        match.rng = FixedRange(1)
        match.left.frost_energy = 4
        left_before = match.left.hp
        match._apply_hit(CONTACT)
        self.assertEqual((match.left.hp, match.right.hp), (left_before - 1, 497))
        self.assertEqual(match.left.frost_energy, 3)
        hit = self.payloads(emitted, ev.HIT)[0]
        self.assertEqual((hit["damage_left"], hit["damage_right"]), (1, 3))
        charges = self.payloads(emitted, ev.CHARGE_CHANGED)
        self.assertEqual([(p["meter_id"], p["value"], p["source"]) for p in charges], [("frost_energy", 3, "contact")])
        self.assertFalse(self.payloads(emitted, ev.SKILL_DAMAGE))
        match._apply_hit(replace(CONTACT, relative_speed=.01))
        self.assertEqual(match.left.frost_energy, 3)
        self.assertEqual(len(self.payloads(emitted, ev.HIT)), 1)

    def test_energy_attack_and_opposing_attack_both_land_before_double_ko(self):
        match, emitted = self.make_match()
        match.rng = FixedRange(3)
        match.left.hp, match.right.hp = 3, 3
        match.left.frost_energy = 1
        match._apply_hit(CONTACT)
        self.assertEqual((match.left.hp, match.right.hp, match.left.frost_energy), (0, 0, 0))
        self.assertTrue(match.result.double_ko)
        hit = self.payloads(emitted, ev.HIT)[0]
        self.assertEqual((hit["damage_left"], hit["damage_right"]), (3, 3))

    def test_contact_damage_payload_is_truncated_to_actual_remaining_hp(self):
        match, emitted = self.make_match()
        match.rng = FixedRange(1)
        match.right.hp = 2
        match.left.frost_energy = 1
        match._apply_hit(CONTACT)
        self.assertEqual(self.payloads(emitted, ev.HIT)[0]["damage_right"], 2)

    def test_walls_and_other_skill_damage_do_not_spend_frost_energy(self):
        match, _ = self.make_match()
        match.left.frost_energy = 2
        skills.roll_collisions(match.left, match.right, "wall", match.bus, match.rng, match._skill_damage, match._deflect)
        match._skill_damage(match.left, match.right, 30, skill="frost_slash")
        self.assertEqual(match.left.frost_energy, 2)

    def test_countdown_result_and_new_round_preserve_or_clear_ability_clocks_as_required(self):
        match, emitted = self.make_match()
        match.left.frost_elapsed = 2.5
        match.left.frost_energy = 3
        match._apply_slow(match.right, 2.0, .5)
        match.countdown = 1.0
        match.update(.1)
        self.assertEqual((match.left.frost_elapsed, match.right.slow_seconds), (2.5, 2.0))
        match.phase = MatchPhase.RESULT
        match.update(.1)
        self.assertEqual((match.left.frost_elapsed, match.right.slow_seconds), (2.5, 2.0))
        self.assertFalse(self.payloads(emitted, ev.FROST_SLASH))
        match.result_reveal = 0
        self.assertTrue(match.advance_round())
        self.assertEqual((match.left.frost_elapsed, match.left.frost_energy), (0.0, 0))
        self.assertEqual((match.right.slow_seconds, match.right.slow_multiplier), (0.0, 1.0))
        self.assertTrue(all(ball.motion_multiplier == 1 for ball in match.balls))
        match.left.frost_elapsed, match.left.frost_energy = 2.0, 4
        match.left.slow_seconds, match.left.slow_multiplier = 1.0, .5
        match.left.reset_hp()
        self.assertEqual((match.left.frost_elapsed, match.left.frost_energy, match.left.slow_seconds, match.left.slow_multiplier),
                         (0.0, 0, 0.0, 1.0))

    def test_real_match_replays_frost_events_and_outcome_at_30_60_and_120_fps(self):
        results = []
        for fps in (30, 60, 120):
            match, emitted = self.make_match(seed=38, stationary=False)
            for _ in range(1000 * fps):
                match.update(1 / fps)
                if match.phase is MatchPhase.RESULT:
                    break
            self.assertEqual(match.phase, MatchPhase.RESULT)
            slashes = self.payloads(emitted, ev.FROST_SLASH)
            self.assertTrue(slashes)
            selected = [(name, payload) for name, payload in emitted
                        if name in (ev.FROST_SLASH, ev.CHARGE_CHANGED, ev.HIT, ev.SKILL_DAMAGE)]
            results.append((match.result.stats, selected, tuple(ball.signature for ball in match.balls)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


class SlowPhysicsTests(FrostFixture):
    def test_slow_refresh_does_not_multiply_velocity_and_last_slow_step_keeps_half_displacement(self):
        match, _ = self.make_match("amis")
        match.balls = (Ball("left", .1, .1, 0, 0), Ball("right", .4, .4, .1, 0))
        match._apply_slow(match.right, 3.0, .5)
        self.assertAlmostEqual(match.balls[1].speed, .1)
        self.assertAlmostEqual(match.balls[1].movement_speed, .05)
        self.tick(match, 60)
        match._apply_slow(match.right, 3.0, .5)
        self.assertEqual(match.right.slow_seconds, 3.0)
        self.assertAlmostEqual(match.balls[1].speed, .1)
        self.tick(match, 179)
        self.assertEqual(match.balls[1].motion_multiplier, .5)
        self.tick(match, 1)
        self.assertEqual((match.right.slow_seconds, match.balls[1].motion_multiplier), (0, 1.0))
        self.assertAlmostEqual(match.balls[1].x, .4 + .05 * 4)
        self.assertAlmostEqual(match.balls[1].speed, .1)

    def test_normal_body_catches_slowed_body_then_effective_motion_separates_without_creating_energy(self):
        left = Ball("left", .4, .5, .8, 0)
        right = Ball("right", .54, .5, 1.0, 0, motion_multiplier=.5)
        before = left.speed ** 2 + right.speed ** 2
        result = step((left, right), STEP)
        self.assertIsNotNone(result.contact)
        self.assertAlmostEqual(result.contact.relative_speed, .3)
        first, second = result.balls
        self.assertAlmostEqual(first.vx * first.motion_multiplier - second.vx * second.motion_multiplier, -.3)
        self.assertAlmostEqual(first.speed ** 2 + second.speed ** 2, before)
        self.assertIsNone(step(result.balls, STEP).contact)

    def test_equal_multipliers_keep_the_existing_raw_normal_exchange(self):
        first = Ball("left", .4, .5, .8, .1, motion_multiplier=.5)
        second = Ball("right", .54, .5, -.4, .1, motion_multiplier=.5)
        result = step((first, second), STEP)
        self.assertIsNotNone(result.contact)
        self.assertAlmostEqual(result.balls[0].vx, -.4)
        self.assertAlmostEqual(result.balls[1].vx, .8)
        self.assertAlmostEqual(result.contact.relative_speed, .6)

    def test_effective_contact_speed_controls_valid_attack_gate(self):
        first = Ball("left", .4, .5, .08, 0, motion_multiplier=.5)
        second = Ball("right", .54, .5, 0, 0)
        result = step((first, second), STEP)
        self.assertAlmostEqual(result.contact.relative_speed, .04)
        owner = Dango("left", "feixue", "绯雪", frost_energy=1)
        target = Dango("right", "custom", "团子")
        self.assertFalse(combat.CombatTracker().resolve(owner, target, result.contact))
        self.assertEqual((owner.hp, target.hp, owner.frost_energy), (100, 100, 1))

    def test_walls_deflection_and_teleports_preserve_slow_multiplier_and_raw_energy(self):
        ball = Ball("right", .92, .4, .8, .2, motion_multiplier=.5)
        result = step((ball,), STEP)
        self.assertTrue(result.wall_hits)
        bounced = result.balls[0]
        self.assertEqual(bounced.motion_multiplier, .5)
        self.assertAlmostEqual(bounced.speed, ball.speed)
        self.assertEqual(reset_ball_to(bounced, .5, .5).motion_multiplier, .5)
        match, _ = self.make_match("amis")
        match.balls = (match.balls[0], ball)
        match._apply_slow(match.right, 3.0, .5)
        match._deflect(match.right, match.left, 1)
        self.assertEqual(match.balls[1].motion_multiplier, .5)
        self.assertAlmostEqual(match.balls[1].speed, ball.speed)

    def test_slow_and_starburst_orderings_share_raw_energy_without_bypassing_the_multiplier(self):
        for slow_first in (False, True):
            with self.subTest(slow_first=slow_first):
                match, _ = self.make_match("amis")
                match.balls = (match.balls[0], Ball("right", .7, .4, .3, -.4))
                if slow_first:
                    match._apply_slow(match.right, 3.0, .5)
                match._reset_to_bottom_center(match.right)
                if not slow_first:
                    match._apply_slow(match.right, 3.0, .5)
                self.assertEqual(match.balls[1].movement_speed, 0)
                match._advance_starburst_motion(1.0)
                self.assertAlmostEqual(match.balls[1].speed, .5)
                self.assertAlmostEqual(match.balls[1].movement_speed, .25)
                self.assertEqual(match.balls[1].motion_multiplier, .5)
                match._advance_slow(3.0)
                self.assertAlmostEqual(match.balls[1].movement_speed, .5)

    def test_slow_can_expire_while_starburst_motion_is_still_stored(self):
        match, _ = self.make_match("amis")
        match.balls = (match.balls[0], Ball("right", .7, .4, .3, -.4))
        match._apply_slow(match.right, .25, .5)
        match._reset_to_bottom_center(match.right)
        match._advance_slow(.25)
        self.assertEqual(match.balls[1].motion_multiplier, 1.0)
        self.assertEqual(match.balls[1].speed, 0)
        match._advance_starburst_motion(1.0)
        self.assertAlmostEqual(match.balls[1].movement_speed, .5)
        self.assertEqual(match._starburst_motion, {})

    def test_collision_and_slow_expiry_keep_total_raw_energy_and_starburst_borrowed_energy(self):
        match, _ = self.make_match("amis")
        match.balls = (Ball("left", .4, .5, .8, 0), Ball("right", .54, .5, 1, 0))
        match._apply_slow(match.right, 3.0, .5)
        total = sum(ball.speed ** 2 for ball in match.balls)
        match.balls = step(match.balls, STEP).balls
        self.assertAlmostEqual(sum(ball.speed ** 2 for ball in match.balls), total)
        match._reset_to_bottom_center(match.right)
        match._apply_slow(match.right, 3.0, .5)
        match._advance_starburst_motion(1.0)
        self.assertAlmostEqual(sum(ball.speed ** 2 for ball in match.balls), total)
        match._advance_slow(3.0)
        self.assertAlmostEqual(sum(ball.speed ** 2 for ball in match.balls), total)


if __name__ == "__main__":
    unittest.main()
