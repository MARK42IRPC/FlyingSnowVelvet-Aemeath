"""Dango Showdown pure logic: physics, HP combat, betting and streaks."""

from __future__ import annotations
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from lib.core.render.visuals.types import Color
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import constants as C
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.animator import deform, mirrored
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.betting import StreakState
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.combat import CombatTracker, reset_dango, separate
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import Dango, MatchPhase, RoundResult
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import geometry
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import skills
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.physics import (
    Ball,
    Contact,
    create_balls,
    step,
)
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import roster as R
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import (
    ACTION_NEXT,
    CharacterView,
    ScreenSnapshot,
    paint_snapshot,
)
from lib.core.render.visuals.commands import SpriteCommand
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import FrameSource as HandFrame
from lib.core.render.visuals.image_loader import load_image_resource
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.roster import ROSTER
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats


def _contact(speed: float = 0.6, left_vx: float = 0.4, right_vx: float = -0.2) -> Contact:
    return Contact("left", "right", 1.0, 0.0, 0.01, 0.5, 0.5, speed, left_vx, 0.0, right_vx, 0.0)


def _balls(vx_left: float = 0.1, vx_right: float = -0.1) -> tuple[Ball, Ball]:
    # Touching pair: centres are exactly two radii apart.
    radius = C.BASE_RADIUS
    return (
        Ball("left", 0.5 - radius, 0.5, vx_left, 0.0),
        Ball("right", 0.5 + radius, 0.5, vx_right, 0.0),
    )


def _dango_pair() -> tuple[Dango, Dango]:
    return Dango("left", "amis", "爱弥斯"), Dango("right", "daniya", "达妮娅")


class DangoModelTests(unittest.TestCase):
    def test_initial_hp_and_ratio(self):
        dango = Dango(side="left", character_id="amis", name="爱弥斯")
        self.assertEqual(dango.hp, C.MAX_HP)
        self.assertEqual(dango.hp, C.MAX_HP)
        self.assertEqual(dango.hp_ratio, 1.0)

    def test_hurt_pose_stays_visible_for_300ms(self):
        self.assertAlmostEqual(C.HURT_SECONDS, 0.3)
        self.assertAlmostEqual(C.HURT_FLASH_SECONDS, 0.3)
        dango = Dango("left", "amis", "爱弥斯")
        dango.resolve_hurt()
        dango.advance_state(0.2)
        self.assertEqual(dango.state, C.HURT)
        self.assertGreater(dango.hurt_flash_seconds, 0.0)
        dango.advance_state(0.11)
        self.assertEqual(dango.state, C.IDLE)
        self.assertEqual(dango.hurt_flash_seconds, 0.0)

    def test_hurt_pose_flattens_the_body(self):
        dango = Dango("left", "amis", "爱弥斯")
        dango.resolve_hurt()
        dango.advance_state(C.HURT_SECONDS * 0.9)
        sx, sy = deform(C.HURT, dango.state_seconds, dango.stretch_phase, C.HURT_SECONDS)
        self.assertLess(sx * sy, 1.0)
        self.assertGreater(sx, sy)

    def test_attack_pose_holds_half_a_second_or_until_hurt(self):
        self.assertAlmostEqual(C.ATTACK_SECONDS, 0.5)
        dango = Dango("left", "amis", "爱弥斯")
        dango.resolve_attack()
        dango.advance_state(0.4)
        self.assertEqual(dango.state, C.ATTACK)
        dango.resolve_hurt()
        self.assertEqual(dango.state, C.HURT)

    def test_state_machine_returns_to_idle(self):
        dango = Dango(side="left", character_id="amis", name="爱弥斯")
        dango.resolve_attack()
        self.assertEqual(dango.state, C.ATTACK)
        dango.advance_state(C.ATTACK_SECONDS + 0.01)
        self.assertEqual(dango.state, C.IDLE)

    def test_hurt_wins_over_attack_until_flash_ends(self):
        dango = Dango(side="left", character_id="amis", name="爱弥斯")
        dango.resolve_hurt()
        self.assertEqual(dango.state, C.HURT)
        dango.resolve_attack()
        self.assertEqual(dango.state, C.HURT)
        dango.advance_state(C.HURT_SECONDS + 0.01)
        self.assertEqual(dango.state, C.IDLE)

    def test_round_result_bet_outcomes(self):
        win = RoundResult(winner="left", bet_side="left", bet_amount=5)
        self.assertEqual(win.apply_bet(bet_side="left", bet_amount=5, streak=2, best_streak=2), (3, 3))
        self.assertTrue(win.bet_won)
        lose = RoundResult(winner="right", bet_side="left", bet_amount=5)
        self.assertEqual(lose.apply_bet(bet_side="left", bet_amount=5, streak=4, best_streak=4), (0, 4))
        self.assertFalse(lose.bet_won)
        draw = RoundResult(winner="", bet_side="left", bet_amount=5, double_ko=True)
        self.assertEqual(draw.apply_bet(bet_side="left", bet_amount=5, streak=3, best_streak=5), (3, 5))
        self.assertTrue(draw.draw)
        unbet = RoundResult(winner="left")
        self.assertEqual(unbet.apply_bet(bet_side="", bet_amount=0, streak=3, best_streak=3), (3, 3))


class DangoStreakTests(unittest.TestCase):
    def test_settle_wins_losses_and_draws(self):
        state = StreakState()
        self.assertTrue(state.settle(bet_side="left", winner="left", double_ko=False, amount=1))
        self.assertTrue(state.settle(bet_side="right", winner="right", double_ko=False, amount=5))
        self.assertEqual(state.current, 2)
        self.assertEqual(state.best, 2)
        self.assertFalse(state.settle(bet_side="left", winner="right", double_ko=False, amount=10))
        self.assertEqual(state.current, 0)
        self.assertEqual(state.best, 2)
        self.assertFalse(state.settle(bet_side="left", winner="", double_ko=True, amount=5))
        self.assertEqual(state.current, 0)
        self.assertEqual(state.draws, 1)
        record = state.as_record()
        self.assertEqual(record["total_rounds"], 4)
        self.assertEqual(record["total_bet_wins"], 2)
        self.assertEqual(record["total_bet_losses"], 1)
        self.assertEqual(record["total_draws"], 1)

    def test_chips_track_wins_and_losses_and_may_go_negative(self):
        state = StreakState()
        self.assertEqual(state.chips, C.BET_AMOUNTS[0])
        self.assertTrue(state.settle(bet_side="left", winner="left", double_ko=False, amount=5))
        self.assertEqual(state.chips, C.BET_AMOUNTS[0] + 5)
        self.assertFalse(state.settle(bet_side="left", winner="right", double_ko=False, amount=10))
        self.assertEqual(state.chips, C.BET_AMOUNTS[0] - 5)
        self.assertFalse(state.settle(bet_side="left", winner="right", double_ko=False, amount=10))
        self.assertEqual(state.chips, C.BET_AMOUNTS[0] - 15)
        self.assertLess(state.chips, 0)
        self.assertFalse(state.settle(bet_side="left", winner="", double_ko=True, amount=10))
        self.assertEqual(state.chips, C.BET_AMOUNTS[0] - 15)
        record = state.as_record()
        self.assertEqual(record["chips"], C.BET_AMOUNTS[0] - 15)
        restored = StreakState.from_record(record)
        self.assertEqual(restored.chips, record["chips"])
        self.assertEqual(StreakState.from_record({"total_rounds": 3}).chips, C.BET_AMOUNTS[0])

    def test_invalid_bet_does_not_touch_streak(self):
        state = StreakState(current=2, best=3)
        self.assertFalse(state.settle(bet_side="", winner="left", double_ko=False, amount=0))
        self.assertFalse(state.settle(bet_side="left", winner="left", double_ko=False, amount=7))
        self.assertEqual(state.current, 2)
        self.assertEqual(state.best, 3)


class DangoPhysicsTests(unittest.TestCase):
    def test_create_balls_are_inside_arena(self):
        left, right = create_balls(random.Random(3))
        for ball in (left, right):
            self.assertGreaterEqual(ball.x - ball.radius, -1e-9)
            self.assertLessEqual(ball.x + ball.radius, 1.0 + 1e-9)
            self.assertGreater(ball.speed, 0.0)

    def test_ball_hits_keep_speed_and_wall_turns_preserve_it(self):
        left = Ball("left", 0.48, 0.5, 0.3, 0.1)
        right = Ball("right", 0.64, 0.5, -0.3, 0.1)
        result = step((left, right), C.FIXED_STEP_SECONDS)
        self.assertIsNotNone(result.contact)
        self.assertAlmostEqual(result.balls[0].speed, left.speed, places=9)
        self.assertAlmostEqual(result.balls[1].speed, right.speed, places=9)
        ball, other = Ball("left", 0.09, 0.3, -0.5, -0.2), Ball("right", 0.9, 0.8, 0.0, 0.0)
        speed = ball.speed
        for _ in range(8):
            moved = step((ball, other), C.FIXED_STEP_SECONDS)
            ball, other = moved.balls
            if moved.wall_hits:
                break
        self.assertAlmostEqual(ball.speed, speed, places=9)

    def test_wall_bounce_turns_velocity_without_changing_speed(self):
        directions = set()
        speeds = []
        for y in (0.2, 0.4, 0.6):
            ball = Ball("left", 0.09, y, -0.5, -0.2)
            other = Ball("right", 0.9, 0.8, 0.0, 0.0)
            start_speed = ball.speed
            for _ in range(8):
                result = step((ball, other), C.FIXED_STEP_SECONDS)
                wall_hits = [hit for hit in result.wall_hits if hit.side == "left"]
                if wall_hits:
                    ball = result.balls[0]
                    break
            self.assertGreater(ball.vx, 0.0)
            directions.add(round(ball.vy, 9))
            speeds.append(ball.speed)
            self.assertAlmostEqual(ball.speed, start_speed, places=9)
            self.assertEqual(ball.hp if hasattr(ball, "hp") else start_speed, start_speed)
        self.assertGreater(len(directions), 1)

    def test_wall_keeps_ball_inside(self):
        ball = Ball("left", 0.09, 0.3, -0.5, 0.0)
        other = Ball("right", 0.9, 0.8, 0.0, 0.0)
        bounced = False
        for _ in range(10):
            result = step((ball, other), C.FIXED_STEP_SECONDS)
            ball, other = result.balls
            self.assertGreaterEqual(ball.x, ball.radius - 1e-9)
            bounced = bounced or bool(result.wall_hits)
        self.assertTrue(bounced)
        self.assertGreater(ball.vx, 0.0)

    def test_head_on_collision_reverses_both(self):
        result = step(_balls(0.3, -0.3), C.FIXED_STEP_SECONDS)
        self.assertIsNotNone(result.contact)
        self.assertLess(result.balls[0].vx, 0.0)
        self.assertGreater(result.balls[1].vx, 0.0)

    def test_separation_margin(self):
        self.assertFalse(separate(*_balls()))
        apart = (Ball("left", 0.2, 0.5, 0.1, 0.0), Ball("right", 0.8, 0.5, -0.1, 0.0))
        self.assertTrue(separate(*apart))

    def test_free_flight_reverses_when_hitting_walls(self):
        start = (Ball("left", 0.25, 0.5, 0.3, 0.2), Ball("right", 0.75, 0.5, -0.3, 0.2))
        balls = start
        seen_left_flip = seen_top_flip = False
        previous = balls
        for _ in range(400):
            balls = step(balls, C.FIXED_STEP_SECONDS).balls
            for before, after in zip(previous, balls):
                if before.vx < 0.0 < after.vx:
                    seen_left_flip = True
                if before.vy < 0.0 < after.vy:
                    seen_top_flip = True
            previous = balls
        self.assertTrue(seen_left_flip)
        self.assertTrue(seen_top_flip)
        for ball in balls:
            self.assertGreaterEqual(ball.x - ball.radius, -1e-9)
            self.assertLessEqual(ball.x + ball.radius, 1.0 + 1e-9)

    def test_step_has_no_hidden_steering(self):
        left = Ball("left", 0.15, 0.2, 0.26, 0.31)
        right = Ball("right", 0.85, 0.8, -0.22, -0.27)
        straight = step((left, right), C.FIXED_STEP_SECONDS)
        self.assertIsNone(straight.contact)
        self.assertAlmostEqual(straight.balls[0].vx, left.vx, places=9)
        self.assertAlmostEqual(straight.balls[0].vy, left.vy, places=9)
        self.assertAlmostEqual(straight.balls[1].vx, right.vx, places=9)
        self.assertAlmostEqual(straight.balls[1].vy, right.vy, places=9)

    def test_large_dt_is_clamped(self):
        start = create_balls(random.Random(11))
        huge = step(start, 10.0).balls
        for ball in huge:
            self.assertGreaterEqual(ball.x - ball.radius, -1e-9)
            self.assertLessEqual(ball.x + ball.radius, 1.0 + 1e-9)
            self.assertGreaterEqual(ball.y - ball.radius, -1e-9)
            self.assertLessEqual(ball.y + ball.radius, 1.0 + 1e-9)


class DangoSettlementTests(unittest.TestCase):
    """押中 / 押错 / 平局 三条结算路径都要有正式行为。"""

    def test_bet_outcomes_drive_streak_differently(self):
        state = StreakState()
        self.assertTrue(state.settle(bet_side="left", winner="left", double_ko=False, amount=5))
        self.assertEqual(state.current, 1)
        self.assertTrue(state.settle(bet_side="left", winner="left", double_ko=False, amount=5))
        self.assertEqual((state.current, state.best), (2, 2))
        # 平局：连押保持不变，只累计平局次数。
        self.assertFalse(state.settle(bet_side="left", winner="", double_ko=True, amount=5))
        self.assertEqual(state.current, 2)
        self.assertEqual(state.draws, 1)
        self.assertEqual((state.wins, state.losses), (2, 0))
        # 押错：清零，但最高连押保留。
        self.assertFalse(state.settle(bet_side="right", winner="left", double_ko=False, amount=1))
        self.assertEqual(state.current, 0)
        self.assertEqual(state.best, 2)
        self.assertEqual((state.wins, state.losses, state.draws, state.rounds), (2, 1, 1, 4))

    def test_round_result_apply_bet_matches_streak_state(self):
        draw = RoundResult(winner="", double_ko=True)
        self.assertEqual(draw.apply_bet(bet_side="left", bet_amount=5, streak=3, best_streak=3), (3, 3))
        self.assertTrue(draw.draw)
        self.assertFalse(draw.bet_won)
        miss = RoundResult(winner="right")
        self.assertEqual(miss.apply_bet(bet_side="left", bet_amount=10, streak=4, best_streak=4), (0, 4))
        hit = RoundResult(winner="right")
        self.assertEqual(hit.apply_bet(bet_side="right", bet_amount=10, streak=4, best_streak=4), (5, 5))
        self.assertTrue(hit.bet_won)

    def test_bet_settled_payload_covers_all_three_outcomes(self):
        events: list[tuple[str, dict]] = []
        bus = ev.ExtensionBus(publish=lambda name, payload: events.append((name, payload)))
        bus.bet_settled(ev.BetSettledPayload(bet_side="left", winner="left", hit=True, streak=2, best_streak=2))
        bus.bet_settled(ev.BetSettledPayload(bet_side="left", winner="right", hit=False, streak=0, best_streak=2))
        bus.bet_settled(ev.BetSettledPayload(bet_side="right", winner="", hit=False, streak=2, best_streak=2, draw=True))
        self.assertEqual([name for name, _ in events], [ev.BET_SETTLED] * 3)
        self.assertEqual([payload["hit"] for _, payload in events], [True, False, False])
        self.assertEqual([payload["draw"] for _, payload in events], [False, False, True])
        self.assertEqual(events[2][1]["streak"], 2)


class DangoCombatTests(unittest.TestCase):
    def test_every_ball_hit_deals_equal_hp_damage(self):
        tracker = CombatTracker()
        left = Dango("left", "amis", "爱弥斯")
        right = Dango("right", "daniya", "达妮娅")
        start = left.hp
        self.assertTrue(tracker.resolve(left, right, _contact(left_vx=0.9, right_vx=-0.1)))
        self.assertEqual(left.hp, right.hp)
        self.assertEqual(left.hp, start - 1)
        self.assertEqual(right.hp, start - 1)
        self.assertEqual(left.hits_taken, 1)
        before = left.hp
        self.assertFalse(tracker.resolve(left, right, _contact(speed=0.0)))
        self.assertEqual(left.hp, before)
        tracker.release()
        self.assertTrue(tracker.resolve(left, right, _contact()))
        self.assertEqual(left.hits_taken, 2)

    def test_attacks_and_hurts_are_both_reported(self):
        tracker = CombatTracker()
        left = Dango("left", "amis", "爱弥斯")
        right = Dango("right", "daniya", "达妮娅")
        tracker.resolve(left, right, _contact())
        self.assertIn(left.state, (C.ATTACK, C.HURT))
        self.assertIn(right.state, (C.ATTACK, C.HURT))

    def test_slow_impact_is_ignored(self):
        tracker = CombatTracker()
        left = Dango("left", "amis", "爱弥斯")
        right = Dango("right", "daniya", "达妮娅")
        self.assertFalse(tracker.resolve(left, right, _contact(speed=0.001)))
        self.assertEqual(left.hits_taken, 0)

    def test_double_ko_is_a_draw(self):
        tracker = CombatTracker()
        left = Dango("left", "amis", "爱弥斯", hp=0)
        right = Dango("right", "daniya", "达妮娅", hp=0)
        tracker.apply_damage(left, right)
        self.assertTrue(tracker.finished)
        self.assertTrue(tracker.double_ko)
        result = tracker.build_result(left, right)
        self.assertEqual(result.winner, "")
        self.assertTrue(result.double_ko)

    def test_single_side_out_of_hp_wins_for_the_other(self):
        tracker = CombatTracker()
        left = Dango("left", "amis", "爱弥斯", hp=12)
        right = Dango("right", "daniya", "达妮娅", hp=0)
        tracker.apply_damage(left, right)
        self.assertTrue(tracker.finished)
        self.assertFalse(tracker.double_ko)
        self.assertEqual(tracker.build_result(left, right).winner, "left")


    def test_randomize_resets_stale_pick_and_bet(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(8))
            match.prepare_round()
            match.choose_dango("right")
            match.choose_bet_amount(10)
            match.randomize()
            self.assertTrue(match.can_start)
            self.assertIn(match.picked_side, ("left", "right"))
            self.assertEqual(match.bet.side, match.picked_side)
            self.assertIn(match.bet.amount, C.BET_AMOUNTS)


class DangoRosterTests(unittest.TestCase):
    def test_grid_rows_and_selection_are_roster_driven(self):
        self.assertEqual(R.selection_rows(), max(1, (len(ROSTER) + R.SELECT_COLUMNS - 1) // R.SELECT_COLUMNS))
        for entry in ROSTER:
            self.assertIs(R.find_entry(entry.character_id), entry)
        self.assertIsNone(R.find_entry("missing"))

    def test_both_sides_can_never_share_one_fighter(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(2))
            match.prepare_round()
            left_id, right_id = match.fighter_ids
            self.assertFalse(match.choose_dango("left", right_id))
            self.assertEqual(match.fighter_ids, (left_id, right_id))


class DangoAnimatorTests(unittest.TestCase):
    def test_idle_is_slow_and_small(self):
        scale_x, scale_y = deform(C.IDLE, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(scale_x * scale_y, 1.0, places=6)
        self.assertLess(abs(scale_x - 1.0), C.IDLE_STRETCH_AMPLITUDE + 1e-9)
        attack = deform(C.ATTACK, 0.02, 0.0, C.ATTACK_SECONDS)
        self.assertGreater(abs(attack[0] - 1.0), 0.0)

    def test_mirrored_has_unit_determinant(self):
        sx, sy = 1.2, 1.0 / 1.2
        m11, m12, m21, m22 = mirrored(sx, sy, 0.6, 0.8)
        self.assertAlmostEqual(abs(m11 * m22 - m12 * m21), 1.0, places=6)


class DangoStatsTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            stats = DangoStats(path)
            stats.settle(bet_side="left", winner="left", double_ko=False, amount=5)
            stats.settle(bet_side="left", winner="right", double_ko=False, amount=1)
            reopened = DangoStats(path)
            record = reopened.snapshot()
            self.assertEqual(record["best_streak"], 1)
            self.assertEqual(record["current_streak"], 0)
            self.assertEqual(record["total_bet_wins"], 1)
            self.assertEqual(record["total_bet_losses"], 1)

    def test_corrupt_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(DangoStats(path).snapshot()["current_streak"], 0)
            path.write_text(json.dumps({"version": 99, "current_streak": 8}), encoding="utf-8")
            self.assertEqual(DangoStats(path).snapshot()["current_streak"], 0)


class DangoMatchTests(unittest.TestCase):
    @staticmethod
    def _use_short_round_hp(match):
        # These cases cover settlement and lifecycle, independently of balance HP.
        for dango in match.dango:
            dango.max_hp = dango.hp = 20

    def test_cannot_start_without_pick_and_bet(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(1))
            match.prepare_round()
            self.assertFalse(match.can_start)
            self.assertFalse(match.start_match())
            match.choose_dango("left")
            self.assertFalse(match.can_start)
            match.choose_bet_amount(5)
            self.assertTrue(match.can_start)

    def test_full_round_reaches_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(0))
            match.prepare_round()
            match.choose_dango("left")
            match.choose_bet_amount(5)
            self.assertTrue(match.start_match())
            self._use_short_round_hp(match)
            frames = 0
            while match.phase is MatchPhase.FIGHT and frames < 60 * 400:
                match.update(1 / 60)
                frames += 1
            self.assertIs(match.phase, MatchPhase.RESULT)
            self.assertTrue(match.left.hp <= 0 or match.right.hp <= 0)
            record = match.record
            self.assertEqual(record["total_rounds"], 1)
            self.assertGreater(match.result.stats.total_hits, 0)
            if match.result.draw:
                self.assertEqual(record["current_streak"], 0)
                self.assertIn(record["total_draws"], (0, 1))

    def test_rounds_settle_win_loss_and_draw_from_the_seeded_matches(self):
        outcomes = set()
        with tempfile.TemporaryDirectory() as tmp:
            stats = DangoStats(Path(tmp) / "state.json")
            for seed in range(12):
                match = DangoMatch(stats, rng=random.Random(seed))
                match.prepare_round()
                match.choose_dango("left")
                match.choose_bet_amount(1)
                match.start_match()
                self._use_short_round_hp(match)
                for _ in range(60 * 400):
                    match.update(1 / 60)
                    if match.phase is MatchPhase.RESULT:
                        break
                self.assertIs(match.phase, MatchPhase.RESULT)
                result = match.result
                if result.draw:
                    outcomes.add("draw")
                    self.assertFalse(result.bet_won)
                else:
                    self.assertEqual(result.bet_won, result.winner == "left")
                    outcomes.add("win" if result.bet_won else "lose")
        snapshot = stats.snapshot()
        self.assertEqual(snapshot["total_rounds"], 12)
        self.assertGreaterEqual(len(outcomes), 2)
        self.assertEqual(snapshot["total_rounds"], snapshot["total_bet_wins"] + snapshot["total_bet_losses"] + snapshot["total_draws"])

    def test_round_end_freezes_the_tableau_and_waits_three_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(0))
            match.prepare_round()
            match.choose_dango("left")
            match.choose_bet_amount(5)
            match.start_match()
            self._use_short_round_hp(match)
            for _ in range(60 * 400):
                match.update(1 / 60)
                if match.phase is MatchPhase.RESULT:
                    break
            self.assertIs(match.phase, MatchPhase.RESULT)
            self.assertAlmostEqual(match.result_reveal, C.RESULT_REVEAL_SECONDS)
            balls = tuple((ball.x, ball.y) for ball in match.balls)
            states = tuple(dango.state_seconds for dango in match.dango)
            for _ in range(60 * 2):
                match.update(1 / 60)
            self.assertEqual(tuple((ball.x, ball.y) for ball in match.balls), balls)
            self.assertEqual(tuple(dango.state_seconds for dango in match.dango), states)
            self.assertGreater(match.result_reveal, 0.0)
            for _ in range(60 * 2):
                match.update(1 / 60)
            self.assertEqual(match.result_reveal, 0.0)
            loser = match.dango_by_side(match.result.stats.loser)
            self.assertTrue(loser.knocked_out)
            self.assertEqual(loser.state, "hurt")
            self.assertGreater(loser.hurt_flatten, 0.0)

    def test_prepare_selection_keeps_record_and_resets_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), rng=random.Random(4))
            match.prepare_round()
            match.choose_dango("right")
            match.choose_bet_amount(1)
            match.start_match()
            self._use_short_round_hp(match)
            for _ in range(60 * 400):
                match.update(1 / 60)
                if match.phase is MatchPhase.RESULT:
                    break
            self.assertIs(match.phase, MatchPhase.RESULT)
            before = match.record
            match.prepare_round()
            self.assertEqual(before, match.record)
            self.assertEqual(match.left.hp, match.left.max_hp)
            self.assertEqual(match.right.hits_taken, 0)
            self.assertEqual(match.bet.side, "")
            self.assertFalse(match.can_start)


class DangoAssetLoaderTests(unittest.TestCase):
    ASSETS = Path(__file__).resolve().parent.parent / "lib" / "script" / "gemes" / "packages" / "official" / "dango_showdown" / "assets"

    def test_inline_loader_decodes_every_roster_state(self):
        loader = DangoAssetLoader(self.ASSETS, inline=True)
        self.assertTrue(loader.ready)
        self.assertEqual(loader.error, "")
        payload = loader.assets()
        states = payload["states"]
        for entry in ROSTER:
            for state in ("idle", "attack", "hurt"):
                self.assertIn((entry.character_id, state), states)
                self.assertEqual(states[(entry.character_id, state)]["resource"].frames[0].width, C.SPRITE_TARGET_PX)
            self.assertIsNotNone(states[(entry.character_id, "hurt")]["hurt_tint"])
        self.assertEqual(len(payload["starburst"].frames), 40)
        beam = payload["laser"].frames[0]
        self.assertEqual((beam.width, beam.height), (1024, 96))

    def test_background_loader_is_pickup_ready_without_blocking(self):
        loader = DangoAssetLoader(self.ASSETS)
        self.assertIsNone(loader.assets())
        self.assertTrue(loader.wait(10.0))
        self.assertTrue(loader.ready)
        self.assertIsNotNone(loader.assets()["starburst"])

    def test_missing_assets_report_instead_of_raising(self):
        loader = DangoAssetLoader(self.ASSETS.parent / "does_not_exist", inline=True)
        self.assertTrue(loader.ready)
        self.assertEqual(loader.assets()["states"], {})


class DangoSkillTests(unittest.TestCase):
    def _match(self, seed=1):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        events = []
        bus = ev.ExtensionBus(publish=lambda name, payload: events.append((name, payload)))
        match = DangoMatch(DangoStats(Path(temp.name) / "state.json"), rng=random.Random(seed), bus=bus)
        match.prepare_round()
        # Fixed sides: skills are tested against Amis on the left. Keep the
        # match's own (scene-synced) dango objects: replacing them would
        # detach the live ball positions the skills anchor on.
        match.fighter_ids = ("amis", "daniya")
        match.left = match._bind(match.left.__class__("left", "amis", "爱弥斯", max_hp=100, meters=R.find_entry("amis").meters))
        match.right = match._bind(match.right.__class__("right", "daniya", "达妮娅", max_hp=120))
        return match, events

    def _make_owner(self, entry_id, left, other):
        entry = R.find_entry(entry_id)
        owner = left if left.character_id == entry_id else other
        owner.meters = entry.meters
        owner.charges = {spec.meter_id: 0 for spec in entry.meters}
        return owner

    def test_roster_stats_are_data_driven(self):
        amis, daniya = R.find_entry("amis"), R.find_entry("daniya")
        self.assertEqual(amis.max_hp, 180)
        self.assertEqual(daniya.max_hp, 260)
        self.assertEqual([m.slots for m in amis.meters], [3, 4])
        left = reset_dango("left", "amis", "爱弥斯")
        right = reset_dango("right", "daniya", "达妮娅")
        self.assertEqual((left.hp, right.hp), (180, 260))
        self.assertEqual(C.DAMAGE_PER_IMPACT, 1)

    def test_three_contacts_fire_eight_damage_laser_and_bank_one_pink(self):
        match, events = self._match(2)
        match.rng = random.Random(0)
        amis, daniya = self._make_owner("amis", match.left, match.right), match.right
        contact = _contact()
        for _ in range(3):
            self.assertTrue(match.tracker.resolve(match.left, match.right, contact))
            skills.charge_from_contact(amis, daniya, 1, match.bus, match._skill_damage, match._deflect)
            match.tracker.release()
        self.assertEqual(amis.charge("starlight"), 0)
        self.assertEqual(amis.charge("starburst"), 1)
        self.assertEqual(daniya.hp, 120 - 3 - 8)
        names = [name for name, _ in events]
        self.assertIn(ev.LASER_FIRE, names)
        self.assertEqual([p["damage"] for n, p in events if n == ev.LASER_FIRE], [8])
        laser = next(p for n, p in events if n == ev.LASER_FIRE)
        self.assertEqual(laser["target"], "right")

    def test_three_laser_charges_bank_one_pink(self):
        match, events = self._match(3)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        for _ in range(3):
            skills.charge_from_contact(amis, daniya, 1, match.bus, match._skill_damage, match._deflect)
        self.assertEqual(amis.charge("starlight"), 0)
        self.assertEqual(amis.charge("starburst"), 1)
        self.assertEqual([name for name, _ in events].count(ev.LASER_FIRE), 1)

    def test_four_pink_units_fire_sixty_damage_starburst_and_clear_pink(self):
        match, events = self._match(3)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges = {"starlight": 0, "starburst": 3}
        skills.feed(
            amis,
            daniya,
            amis.meter("starburst"),
            1,
            match.bus,
            source="laser",
            damage=match._skill_damage,
            deflect=match._deflect,
        )
        self.assertEqual(amis.charge("starburst"), 0)
        self.assertEqual(daniya.hp, 120 - 60)
        self.assertEqual([name for name, _ in events].count(ev.STARBURST_FIRE), 1)
        starburst = next(p for n, p in events if n == ev.STARBURST_FIRE)
        self.assertEqual((starburst["damage"], starburst["target"]), (60, "right"))
        damage_events = [p for n, p in events if n == ev.SKILL_DAMAGE]
        self.assertTrue(damage_events and all(p["defer_visual"] for p in damage_events))

    def test_starburst_parks_the_enemy_at_the_bottom_centre(self):
        match, _ = self._match(5)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges = {"starlight": 0, "starburst": 3}
        skills.feed(amis, daniya, amis.meter("starburst"), 1, match.bus, source="laser", damage=match._skill_damage, deflect=match._deflect)
        ball = match.balls[1]
        match.update(0.2)
        self.assertAlmostEqual(match.balls[1].x, 0.5, places=6)
        ball = match.balls[1]
        self.assertGreater(ball.y, 0.9)
        self.assertLessEqual(ball.y + ball.radius, 1.0 + 1e-9)

    def test_laser_and_void_hand_never_reposition_the_victim(self):
        match, events = self._match(7)
        match.rng = random.Random(1)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges["starlight"] = 2
        skills.charge_from_contact(amis, daniya, 1, match.bus, match._skill_damage, match._deflect)
        laser = next(p for n, p in events if n == ev.LASER_FIRE)
        # The beam visibly passes through the victim's live body centre.
        self.assertAlmostEqual(laser["end"][0], match.balls[1].x, places=9)
        self.assertAlmostEqual(laser["end"][1], match.balls[1].y, places=9)
        self.assertNotAlmostEqual(match.balls[1].x, 0.5, places=3)
        before = (match.balls[1].x, match.balls[1].y)
        daniya.hp = 120
        # Configured chance per roll: these seeds include at least one hand
        # without depending on the exact first random draw.
        for seed in range(12):
            skills.roll_collisions(daniya, amis, "any", match.bus, random.Random(seed), match._skill_damage, match._deflect)
        self.assertTrue([n for n, _ in events if n == ev.VOID_HAND])
        self.assertAlmostEqual(match.balls[1].x, before[0], places=9)
        self.assertAlmostEqual(match.balls[1].y, before[1], places=9)

    def test_void_hand_payload_points_at_the_struck_dangos_centre(self):
        match, events = self._match(8)
        # Move the victim away from its spawn point first: a stale (0.5,
        # 0.5) centre would then be indistinguishable from the arena centre.
        left, right = match.balls
        match.balls = (
            left.moved(0.0, 0.0, 0.41, 0.22),
            right.moved(0.0, 0.0, -0.27, 0.63),
        )
        daniya = match.right
        amis = match.left
        amis.hp = 200
        daniya.hp = 200
        for seed in range(40):
            daniya.hp = 200
            skills.roll_collisions(daniya, amis, "body", match.bus, random.Random(seed), match._skill_damage, match._deflect)
        hands = [p for n, p in events if n == ev.VOID_HAND]
        self.assertTrue(hands)
        for payload in hands:
            self.assertEqual(payload["target"], "left")
            # The decal centre must be the struck dango body, not the
            # window centre and not the attacker.
            self.assertAlmostEqual(payload["point"][0], match.balls[0].x, places=9)
            self.assertAlmostEqual(payload["point"][1], match.balls[0].y, places=9)
            self.assertTrue(1 <= payload["damage"] <= 4)

    def test_starburst_parks_the_victim_even_when_it_is_deflected(self):
        match, events = self._match(5)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges = {"starlight": 0, "starburst": 3}
        skills.feed(amis, daniya, amis.meter("starburst"), 1, match.bus, source="laser", damage=match._skill_damage, deflect=match._deflect)
        match.update(0.5)
        self.assertEqual((match.balls[1].vx, match.balls[1].vy), (0.0, 0.0))
        self.assertAlmostEqual(match.balls[1].x, 0.5, places=6)
        self.assertGreater(match.balls[1].y, 0.9)

        match, events = self._match(7)
        match.rng = random.Random(1)
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges["starlight"] = 2
        skills.charge_from_contact(amis, daniya, 1, match.bus, match._skill_damage, match._deflect)
        laser = next(p for n, p in events if n == ev.LASER_FIRE)
        # The beam visibly passes through the victim's live body centre.
        self.assertAlmostEqual(laser["end"][0], match.balls[1].x, places=9)
        self.assertAlmostEqual(laser["end"][1], match.balls[1].y, places=9)
        self.assertNotAlmostEqual(match.balls[1].x, 0.5, places=3)
        before = (match.balls[1].x, match.balls[1].y)
        daniya.hp = 120
        # Configured chance per roll: these seeds include at least one hand
        # without depending on the exact first random draw.
        for seed in range(12):
            skills.roll_collisions(daniya, amis, "any", match.bus, random.Random(seed), match._skill_damage, match._deflect)
        self.assertTrue([n for n, _ in events if n == ev.VOID_HAND])
        self.assertAlmostEqual(match.balls[1].x, before[0], places=9)
        self.assertAlmostEqual(match.balls[1].y, before[1], places=9)

    def test_void_hand_rolls_configured_probability_on_body_and_wall(self):
        match, events = self._match(4)
        daniya = reset_dango("right", "daniya", "达妮娅")
        amis = reset_dango("left", "amis", "爱弥斯")
        fired = 0
        rng = random.Random(11)
        for trigger in ("body", "wall"):
            for _ in range(100):
                daniya.hp, amis.hp = 200, 200
                fired += skills.roll_collisions(daniya, amis, trigger, match.bus, rng, match._skill_damage, match._deflect)
        self.assertGreaterEqual(fired, 70)
        self.assertLessEqual(fired, 130)
        payloads = [p for n, p in events if n == ev.VOID_HAND]
        self.assertEqual(set(p["source"] for p in payloads), {"body", "wall"})
        self.assertTrue(payloads)
        # Void hand rolls 1~4 per hit; every value is reachable.
        damages = [p["damage"] for p in payloads]
        self.assertTrue(all(1 <= d <= 4 for d in damages))
        self.assertEqual(set(damages), {1, 2, 3, 4})

    def test_laser_direction_is_random_but_passes_through_the_target(self):
        match, events = self._match(9)
        match.rng = random.Random(3)
        # One hit per loop: the laser must be visible every time, not just
        # the first time the meter fills.
        match.start_match()
        match.countdown = 0.0
        amis = self._make_owner("amis", match.left, match.right)
        daniya = match.right
        amis.charges["starlight"] = 2
        angles = set()
        for _ in range(20):
            # Reset HP and the starburst meter: a full starburst would park
            # the enemy, which is the starburst's own test.
            amis.charges = {"starlight": 2, "starburst": 0}
            daniya.hp = 120
            amis.hp = 200
            before = len([n for n, _ in events if n == ev.LASER_FIRE])
            skills.charge_from_contact(amis, daniya, 1, match.bus, match._skill_damage, match._deflect)
            lasers = [p for n, p in events if n == ev.LASER_FIRE]
            if len(lasers) <= before:
                continue
            payload = lasers[-1]
            angles.add(round(float(payload["angle"]), 6))
            tx, ty = match.right.x, match.right.y
            sx, sy = payload["start"]
            ex, ey = payload["end"]
            self.assertAlmostEqual(ex, tx, places=9)
            self.assertAlmostEqual(ey, ty, places=9)
            # The start lies on the same line as the random direction.
            cross = (sx - tx) * math.sin(float(payload["angle"])) - (sy - ty) * math.cos(float(payload["angle"]))
            self.assertAlmostEqual(cross, 0.0, places=9)
        self.assertGreater(len(angles), 5)

    def test_beam_through_matches_target_line(self):
        owner, target = _dango_pair()
        owner.x, owner.y = 0.25, 0.3
        target.x, target.y = 0.7, 0.62
        for angle in (0.0, 0.7, 1.9, 3.3, 5.9):
            start, end = geometry.beam_through(owner, target, angle)
            self.assertAlmostEqual(end[0], 0.7, places=12)
            self.assertAlmostEqual(end[1], 0.62, places=12)
            # Distance from target centre to the infinite line is zero.
            cross = (start[0] - end[0]) * math.sin(angle) - (start[1] - end[1]) * math.cos(angle)
            self.assertAlmostEqual(cross, 0.0, places=9)

    def test_starburst_payload_aligns_impact_with_landing_frame(self):
        match, events = self._match(10)
        amis = self._make_owner("amis", match.left, match.right)
        amis.charges = {"starlight": 0, "starburst": 3}
        skills.feed(amis, match.right, amis.meter("starburst"), 1, match.bus, source="laser", damage=match._skill_damage, deflect=match._deflect)
        payload = next(p for n, p in events if n == ev.STARBURST_FIRE)
        self.assertEqual((payload["impact_ms"], payload["total_ms"]), (910, 1000))

    def test_skill_damage_changes_direction_but_preserves_speed(self):
        match, _ = self._match(5)
        match.prepare_round()
        match.choose_dango("left")
        match.choose_bet_amount(1)
        match.start_match()
        match.countdown = 0.0
        match.balls = (Ball("left", 0.3, 0.5, 0.5, 0.0), Ball("right", 0.7, 0.5, -0.5, 0.0))
        before = {ball.side: (ball.vx, ball.vy, ball.speed) for ball in match.balls}
        match._deflect(match.right, match.left, 1.0)
        after = {ball.side: (ball.vx, ball.vy, ball.speed) for ball in match.balls}
        self.assertEqual(before["left"], after["left"])
        self.assertAlmostEqual(before["right"][2], after["right"][2], places=9)
        self.assertNotAlmostEqual(before["right"][1], after["right"][1], places=6)

    def test_damage_number_size_grows_with_damage(self):
        sizes = [C.damage_number_size(value) for value in (0, 1, 2, 5, 8, 30, 60)]
        self.assertEqual(sizes[:2], [C.DAMAGE_NUMBER_MIN_SIZE, C.DAMAGE_NUMBER_MIN_SIZE])
        self.assertEqual(sizes, sorted(sizes))
        self.assertGreater(C.damage_number_size(8), C.damage_number_size(1))
        self.assertGreater(C.damage_number_size(60), C.damage_number_size(8))
        self.assertLessEqual(C.damage_number_size(9999), C.DAMAGE_NUMBER_MAX_SIZE)
        # One bounce (1) and a laser (8) must be clearly distinguishable.
        self.assertGreaterEqual(C.damage_number_size(8) - C.damage_number_size(1), 15)

    def test_hp_meter_slots_follow_roster(self):
        snapshot = ScreenSnapshot(
            phase=MatchPhase.FIGHT,
            characters=(
                CharacterView("left", "amis", "爱弥斯", hp=82, max_hp=100, meters=R.find_entry("amis").meters, charges={"starlight": 2, "starburst": 1}),
                CharacterView("right", "daniya", "达妮娅", hp=95, max_hp=120, meters=R.find_entry("daniya").meters),
            ),
        )
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        blue = [c for c in batch.commands if getattr(getattr(c, "fill", None), "green", None) == C.C_CHARGE_BLUE.green]
        pink = [c for c in batch.commands if getattr(getattr(c, "fill", None), "green", None) == C.C_CHARGE_PINK.green]
        self.assertEqual(len(blue), 2)
        self.assertEqual(len(pink), 1)
        texts = {getattr(c, "text", "") for c in batch.commands}
        self.assertIn("星能", texts)
        self.assertIn("2/3", texts)
        self.assertIn("星爆", texts)
        self.assertIn("1/4", texts)


class DangoSharedImageEffectTests(unittest.TestCase):
    def _effect(self, **options):
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffectScript
        values = dict(resource_path="test.png", intro_start_pos=(200,30), display_pos=(200,30),
                      outro_end_pos=(200,30), intro_duration=0, display_duration=1,
                      outro_duration=.12, scale=.5)
        values.update(options)
        return SmoothImageShowEffectScript().create_effects("point", (200,30), values, {})[0]

    def test_shared_image_accepts_overlay_tick_bookkeeping(self):
        from lib.core.services.feedback_scene import prepare_effects, interpolate_effects
        effect = self._effect()
        prepare_effects([effect])
        effect.update()
        interpolate_effects([effect], 1)
        self.assertGreater(effect._render_opacity, 0)
        self.assertAlmostEqual(effect._render_age, .05)

    def test_shared_image_holds_one_second_then_fades(self):
        effect = self._effect()
        for _ in range(20):
            effect.update()
        self.assertGreaterEqual(effect.opacity, .999)
        effect.update()
        self.assertGreater(effect.opacity, 0)
        self.assertLess(effect.opacity, 1)
        for _ in range(5):
            effect.update()
        self.assertFalse(effect.alive)

    def test_shared_frame_sampling_handles_variable_duration_and_loop_modes(self):
        from lib.core.render.visuals.resources import ImageResource, RasterFrame
        from lib.core.render.visuals.visuals import sample_effect_frame
        resource=ImageResource('timed',tuple(RasterFrame(1,1,bytes((i,0,0,255)),ms)
                                            for i,ms in enumerate((30,70,150))))
        effect=self._effect(animate=True,playback_rate=2,animation_loop=False)
        for age,index in ((0,0),(.015,1),(.05,2),(.2,2)):
            effect.animation_seconds=age
            self.assertEqual(sample_effect_frame(resource,effect)[0],index)
        effect.animation_loop=True
        effect.animation_seconds=.14
        self.assertEqual(sample_effect_frame(resource,effect)[0],1)
        effect.animate=False
        self.assertEqual(sample_effect_frame(resource,effect)[0],0)

    def test_shared_image_rejects_missing_positions_and_resource(self):
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffectScript
        script=SmoothImageShowEffectScript()
        self.assertEqual(script.create_effects('point',(0,0),{},{}),[])
        self.assertEqual(script.create_effects('point',(0,0),{'resource_path':'x.png'},{}),[])


class DangoSeriesTests(unittest.TestCase):
    def _match(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return DangoMatch(DangoStats(Path(temp.name) / "state.json"), rng=random.Random(4))

    def test_formats_cover_the_three_select_options(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import series

        labels = {fmt.format_id: fmt.label for fmt in series.FORMATS}
        self.assertEqual(labels["bo5"], "五轮三胜")
        self.assertEqual(labels["bo3"], "三局两胜")
        self.assertEqual(labels["bo1"], "一决死战")

    def test_format_can_change_only_between_series(self):
        match = self._match()
        self.assertTrue(match.choose_format("bo3"))
        self.assertEqual(match.series.format.label, "三局两胜")
        match.series.played = 1
        self.assertFalse(match.choose_format("bo5"))
        self.assertEqual(match.series.format.label, "三局两胜")
        match.series.over = True
        self.assertTrue(match.choose_format("bo1"))
        self.assertEqual(match.series.format.label, "一决死战")
        self.assertEqual((match.series.left, match.series.right, match.series.played), (0, 0, 0))

    def test_best_of_three_ends_after_two_wins(self):
        match = self._match()
        match.choose_format("bo3")
        self.assertEqual(match.series.record_round("left"), "")
        self.assertFalse(match.series_over)
        self.assertEqual(match.series.record_round("left"), "left")
        self.assertTrue(match.series_over)
        self.assertEqual(match.series.score, (2, 0))

    def test_dead_rubber_plays_every_round_then_uses_total_wins(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import series

        state = series.SeriesState(format=series.BY_ID["ft3"])
        for winner in ("left", "right", "left"):
            series_winner = state.record_round(winner)
        self.assertEqual(series_winner, "left")
        self.assertTrue(state.over)


class DangoPresenterTests(unittest.TestCase):
    def test_batches_cover_every_phase(self):
        for phase in (MatchPhase.SELECT, MatchPhase.FIGHT):
            snapshot = ScreenSnapshot(phase=phase, bet_side="left", bet_amount=5, can_start=True, countdown=1.0)
            batch, hit_map = paint_snapshot(snapshot, 1000.0, 800.0)
            self.assertTrue(batch.commands)
            self.assertTrue(hit_map.buttons)
        self.assertIsInstance(C.HURT_TINT, Color)

    def test_shared_laser_image_batch_is_nested_inside_the_arena_clip(self):
        from lib.core.render.visuals.visuals import build_effect_batch
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect
        from lib.core.render.visuals.resources import ImageResource, RasterFrame
        from lib.core.render.visuals.commands import ClipPush, ClipPop
        source=ImageResource('laser-test',(RasterFrame(10,2,bytes((255,255,255,255))*20),))
        effect=SmoothImageShowEffect('laser-test',(500,350),0,(500,350),1,(500,350),.12,40)
        effect._visual_resource=source
        batch,_=paint_snapshot(ScreenSnapshot(phase=MatchPhase.FIGHT),1080,720,feedback=build_effect_batch([effect]))
        index=next(i for i,c in enumerate(batch.commands) if isinstance(c,SpriteCommand) and c.resource_id=='laser-test')
        self.assertTrue(any(isinstance(c,ClipPush) for c in batch.commands[:index]))
        self.assertTrue(any(isinstance(c,ClipPop) for c in batch.commands[index+1:]))

    def test_starburst_gif_uses_existing_shared_image_effect_and_composer(self):
        from lib.core.render.visuals.visuals import build_effect_batch
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect
        gif = Path(__file__).resolve().parents[1] / 'lib/script/gemes/packages/official/dango_showdown/assets/effects/amis_starburst.gif'
        resource=load_image_resource(gif)
        self.assertEqual(len(resource.frames),40)
        effect=SmoothImageShowEffect(str(gif), (500,350),0,(500,350),.82,(500,350),.18,1,
                                     animate=True,playback_rate=2,animation_loop=False)
        effect._visual_resource=resource
        for age,index in ((0,0),(.5,20),(.91,36),(.999,39)):
            effect.animation_seconds=age
            source=build_effect_batch([effect]).commands[0]
            self.assertEqual(source.frame_index,index)
        snapshot=ScreenSnapshot(phase=MatchPhase.FIGHT)
        batch,_=paint_snapshot(snapshot,1080,720,feedback=build_effect_batch([effect]))
        self.assertTrue(any(isinstance(cmd,SpriteCommand) for cmd in batch.commands))

    def test_paint_tolerates_missing_optional_inputs(self):
        views = (
            CharacterView("left", "amis", "爱弥斯", x=0.2, y=0.4),
            CharacterView("right", "daniya", "达妮娅", x=0.78, y=0.62),
        )
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, characters=views)
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0, feedback=None, flash=None)
        self.assertTrue(batch.commands)

    def test_impact_flash_is_one_arena_bounded_camera_overlay(self):
        views = (
            CharacterView("left", "amis", "爱弥斯", x=0.2, y=0.4),
            CharacterView("right", "daniya", "达妮娅", x=0.78, y=0.62),
        )
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, characters=views)
        quiet, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        flash, _ = paint_snapshot(snapshot, 1080.0, 720.0, flash=1.0)
        self.assertGreater(len(flash.commands), len(quiet.commands))
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import Layout
        box = Layout(1080.0, 720.0).arena.rect
        overlay = [cmd for cmd in flash.commands if getattr(cmd, "z", 0) >= 14]
        self.assertEqual(len(overlay), 1)
        self.assertEqual(type(overlay[0]).__name__, "RectCommand")
        for cmd in overlay:
            rect = getattr(cmd, "rect", None)
            if rect is not None:
                self.assertGreaterEqual(rect.x, box.x - 0.01)
                self.assertLessEqual(rect.right, box.right + 0.01)

    def test_arena_is_square_at_minimum_size(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT)
        _, hit_map = paint_snapshot(snapshot, 600.0, 480.0)
        arena = hit_map.arena.rect
        self.assertAlmostEqual(arena.width, arena.height)
        self.assertGreater(arena.width, 0.0)

    def test_select_cards_are_guided_and_tiles_are_square(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, left_id="amis", right_id="daniya", bet_amount=5)
        batch, hit_map = paint_snapshot(snapshot, 1080.0, 720.0)
        cards = [b for b in hit_map.buttons if b.value in ("left", "right")]
        self.assertEqual(len(cards), 2)
        tiles = [b for b in hit_map.buttons if b.value.startswith("pick:")]
        self.assertTrue(tiles)
        for card in cards:
            self.assertGreater(card.rect.height, 100.0)
            self.assertLessEqual(card.rect.bottom, min(tile.rect.y for tile in tiles))
        for tile in tiles:
            self.assertAlmostEqual(tile.rect.width, tile.rect.height)
        texts = {getattr(c, "text", "") for c in batch.commands}
        self.assertIn("对 决", texts)
        self.assertIn("先选押注方", texts)
        self.assertIn("押注给", texts)
        # Skill copy is laid out as explicit lines inside the card, so no single
        # command may hold the full sentence and no line may leave the card.
        cards = [b for b in hit_map.buttons if b.value in ("left", "right")]
        card_rects = [b.rect for b in cards]
        def inside(text_rect, card):
            return text_rect.right > card.x and text_rect.x < card.right and text_rect.bottom > card.y and text_rect.y < card.bottom

        for command in batch.commands:
            text = getattr(command, "text", "")
            if not text or not any(inside(command.rect, card) for card in card_rects):
                continue
            if text.startswith(("碰撞", "星爆", "每次碰撞", "虚空之手", "。")):
                host = next(card for card in card_rects if inside(command.rect, card))
                self.assertLessEqual(command.rect.right, host.right - 4.0)
        joined = "".join(c.text.replace("\n", "") for c in batch.commands if getattr(c, "text", ""))
        for fragment in ("碰撞积攒星能", "满3格发射激光", "满4格造成全屏伤害", "概率唤出虚空之手", "隔空造成1~4点伤害"):
            self.assertIn(fragment, joined.replace(" ", ""))

    def test_select_controls_do_not_overlap_the_cards(self):
        for width, height in ((1080.0, 720.0), (810.0, 540.0)):
            snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, left_id="amis", right_id="daniya", bet_amount=5, can_start=True)
            _, hit_map = paint_snapshot(snapshot, width, height)
            cards = [b for b in hit_map.buttons if b.value in ("left", "right")]
            chips = [b for b in hit_map.buttons if b.value.startswith("amount:")]
            starts = [b for b in hit_map.buttons if not b.value and b.rect.width > 60.0 and b not in cards]
            self.assertEqual(len(cards), 2)
            self.assertEqual(len(chips), 3)
            self.assertEqual(len(starts), 1)
            for button in chips + starts:
                for card in cards:
                    overlap_x = min(button.rect.right, card.rect.right) - max(button.rect.x, card.rect.x)
                    overlap_y = min(button.rect.bottom, card.rect.bottom) - max(button.rect.y, card.rect.y)
                    self.assertFalse(overlap_x > 0.5 and overlap_y > 0.5, (width, button.value, card.value))
            for chip in chips:
                self.assertLess(chip.rect.bottom, cards[0].rect.bottom)

    def test_select_screen_fits_minimum_window(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, left_id="amis", right_id="daniya", bet_amount=5, can_start=True)
        batch, hit_map = paint_snapshot(snapshot, 810.0, 540.0)
        self.assertLessEqual(max(b.rect.bottom for b in hit_map.buttons), 540.0 - 20.0)
        self.assertLessEqual(max(b.rect.right for b in hit_map.buttons), 810.0 - 20.0)

    def test_side_card_has_no_stats_block(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya")
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        texts = {getattr(c, "text", "") for c in batch.commands}
        for removed in ("本局命中", "剩余生命", "状态"):
            self.assertNotIn(removed, texts)

    def test_hp_bar_is_a_flat_strip_without_extra_tick_lines(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya")
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        troughs = [c for c in batch.commands if getattr(getattr(c, "fill", None), "green", None) == C.C_HP_BACK.green]
        self.assertEqual(len(troughs), 2)
        for trough in troughs:
            self.assertAlmostEqual(trough.rect.height, 10.0)
        self.assertFalse(hasattr(C, "C_HP_EMPTY"))
        # HUD brackets and rulers must never land on an HP strip.
        bars = [c.rect for c in troughs]
        line_commands = [c for c in batch.commands if c.__class__.__name__ == "LineCommand"]
        for line in line_commands:
            for bar in bars:
                for point in (line.start, line.end):
                    self.assertFalse(bar.y - 1.0 <= point.y <= bar.bottom + 1.0 and bar.x - 1.0 <= point.x <= bar.right + 1.0)
        # The overlay paths never hit the HP strip either.
        for path in (c for c in batch.commands if c.__class__.__name__ == "PathCommand"):
            for segment in path.segments:
                for point in (segment.start, segment.end):
                    for bar in bars:
                        self.assertFalse(bar.y - 1.0 <= point.y <= bar.bottom + 1.0 and bar.x - 1.0 <= point.x <= bar.right + 1.0)

    def test_hp_bar_drains_in_one_uniform_bright_red(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya")
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        fills = [c for c in batch.commands if getattr(getattr(c, "fill", None), "green", None) == C.C_HP_FILL.green]
        self.assertTrue(fills)
        self.assertEqual(C.C_HP_FILL, C.C_DAMAGE)
        self.assertEqual(C.C_HP_FILL_LOW, C.C_HP_FILL)

    def test_select_grid_cells_stay_inside_the_panel(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, left_id="amis", right_id="daniya", countdown=0.0)
        _, hit_map = paint_snapshot(snapshot, 1080.0, 720.0)
        grid_cells = [button for button in hit_map.buttons if button.value.startswith("pick:")]
        self.assertEqual(len(grid_cells), len(ROSTER))
        for cell in grid_cells:
            self.assertGreaterEqual(cell.rect.x, 20.0)
            self.assertLessEqual(cell.rect.right, 1060.0 + 1e-6)
            self.assertGreaterEqual(cell.rect.y, 20.0)
            self.assertLessEqual(cell.rect.bottom, 720.0 - 20.0)

    def test_arena_watermark_and_shared_hand_effect_are_in_the_clipped_batch(self):
        from lib.core.render.visuals.visuals import build_effect_batch
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect
        from lib.core.render.visuals.commands import ClipPush, ClipPop
        assets=Path(__file__).resolve().parents[1]/'lib/script/gemes/packages/official/dango_showdown/assets'
        payload=DangoAssetLoader(assets,inline=True).assets()
        hand=payload['void_hand']
        mark=payload['watermark']
        self.assertIsNotNone(hand)
        marks=HandFrame(mark.resource_id,1,mark.frames[0])
        effect=SmoothImageShowEffect('hand',(300,100),0,(300,100),0,(300,100),2,.5)
        effect._visual_resource=hand
        snapshot=ScreenSnapshot(phase=MatchPhase.FIGHT)
        batch,_=paint_snapshot(snapshot,1080,720,watermark=marks,feedback=build_effect_batch([effect]))
        index=next(i for i,c in enumerate(batch.commands) if isinstance(c,SpriteCommand) and c.resource_id==hand.resource_id)
        self.assertTrue(any(isinstance(c,ClipPush) for c in batch.commands[:index]))
        self.assertTrue(any(isinstance(c,ClipPop) for c in batch.commands[index+1:]))
        self.assertTrue(any(isinstance(c,SpriteCommand) and c.resource_id==mark.resource_id for c in batch.commands))

    def test_chips_are_shown_and_negative_chips_turn_red(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya", record={"chips": -4, "current_streak": 0, "best_streak": 2})
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        texts = {getattr(c, "text", ""): c for c in batch.commands if getattr(c, "text", "")}
        self.assertIn("-4", texts)
        self.assertEqual(texts["-4"].color, C.C_RED)
        positive = ScreenSnapshot(phase=MatchPhase.SELECT, record={"chips": 7})
        batch, _ = paint_snapshot(positive, 1080.0, 720.0)
        seven = [c for c in batch.commands if getattr(c, "text", "") == "7"]
        self.assertTrue(seven and all(c.color == C.C_GREEN for c in seven))

    def test_result_panel_lists_chip_change(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import RoundResult, StatsSnapshot
        result = RoundResult(winner="left", bet_side="left", bet_amount=5, bet_won=True, streak=2, best_streak=2, chips=4,
                             stats=StatsSnapshot(left_hits=9, right_hits=9, left_hp=12, right_hp=0, duration_seconds=33.0, winner="left", loser="right"))
        snapshot = ScreenSnapshot(phase=MatchPhase.RESULT, result=result, characters=(
            CharacterView("left", "amis", "爱弥斯", hp=12),
            CharacterView("right", "daniya", "达妮娅", hp=0, state="hurt", stretch=(1.0, 0.0, 0.0, 0.74)),
        ))
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        texts = {getattr(c, "text", "") for c in batch.commands}
        self.assertIn("筹码变化", texts)
        self.assertIn("+5", texts)
        self.assertIn("-1 → 4", texts)
        for text in ("本局撞击", "剩余生命", "本轮用时", "连续押胜", "当前筹码"):
            self.assertTrue(any(text in value for value in texts), text)

    def test_select_console_offers_the_three_formats(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, left_id="amis", right_id="daniya", record={"chips": 0})
        for width, height in ((1080.0, 720.0), (810.0, 540.0)):
            batch, hit_map = paint_snapshot(snapshot, width, height)
            texts = {getattr(c, "text", "") for c in batch.commands}
            for label in ("五轮三胜", "三局两胜", "一决死战"):
                self.assertIn(label, texts)
            actions = {b.value for b in hit_map.buttons}
            self.assertIn("format:bo5", actions)
            self.assertIn("format:bo3", actions)
            self.assertIn("format:bo1", actions)
            for button in hit_map.buttons:
                self.assertGreaterEqual(button.rect.x, 0.0)
                self.assertLessEqual(button.rect.right, width + 0.01)
                self.assertLessEqual(button.rect.bottom, height + 0.01)
                self.assertGreater(button.rect.height, 0.0)

    def test_format_buttons_lock_while_the_series_runs(self):
        snapshot = ScreenSnapshot(
            phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya",
            series_label="五轮三胜", series_round=2,
        )
        _batch, hit_map = paint_snapshot(snapshot, 1080.0, 720.0)
        self.assertEqual([b for b in hit_map.buttons if str(b.value).startswith("format:")], [])

    def test_win_counts_are_painted_under_both_cards(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import Layout

        def views():
            return (
                CharacterView("left", "amis", "爱弥斯", hp=80, max_hp=100),
                CharacterView("right", "daniya", "达妮娅", hp=90, max_hp=120),
            )

        snapshot = ScreenSnapshot(
            phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya",
            characters=views(), series_label="五轮三胜", series_left=2, series_right=1,
        )
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        layout = Layout()
        for card, wins in ((layout.left_panel, 2), (layout.right_panel, 1)):
            tally = [c for c in batch.commands if "胜场" in getattr(c, "text", "")]
            self.assertTrue(tally)
            inside = [c for c in tally if c.rect.y >= card.y and c.rect.bottom <= card.bottom]
            self.assertTrue(inside, f"win row must sit inside the card for {wins}")
        filled = [
            c for c in batch.commands
            if getattr(getattr(c, "fill", None), "red", None) == C.C_SIDE_A.red
        ]
        self.assertGreaterEqual(len(filled), 3)

    def test_skill_cast_lights_the_caster_card(self):
        snapshot = ScreenSnapshot(
            phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya",
            characters=(
                CharacterView("left", "amis", "爱弥斯", hp=80, max_hp=100),
                CharacterView("right", "daniya", "达妮娅", hp=90, max_hp=120),
            ),
            skill_flash_side="left",
        )
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        glow = [
            c for c in batch.commands
            if getattr(getattr(c, "fill", None), "red", None) == C.C_CARD_FLASH.red
            and getattr(getattr(c, "fill", None), "green", None) == C.C_CARD_FLASH.green
        ]
        self.assertTrue(glow)

    def test_settlement_names_the_dango_and_hides_left_right(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import RoundResult

        snapshot = ScreenSnapshot(
            phase=MatchPhase.RESULT, left_id="amis", right_id="daniya", bet_side="right",
            characters=(
                CharacterView("left", "amis", "爱弥斯", hp=0, max_hp=100),
                CharacterView("right", "daniya", "达妮娅", hp=40, max_hp=120),
            ),
            result=RoundResult(winner="right", bet_side="right", bet_amount=5, bet_won=True, chips=9),
            series_label="三局两胜", series_left=0, series_right=2, series_round=2, series_over=True,
            series_winner="right",
        )
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0)
        texts = [getattr(c, "text", "") for c in batch.commands if getattr(c, "text", "")]
        joined = "".join(texts)
        self.assertIn("达妮娅 获胜", joined)
        self.assertIn("达妮娅 2 : 0 爱弥斯", joined)
        for banned in ("左边", "右边", "左侧", "右侧", "左方", "右方"):
            self.assertNotIn(banned, joined)
        self.assertIn("返回选人", joined)
        text_rects = [(c.text, c.rect) for c in batch.commands if getattr(c, "text", "")]
        for text, rect in text_rects:
            self.assertGreaterEqual(rect.x, -0.01, text)
            self.assertLessEqual(rect.right, 1080.0 + 0.01, text)

    def test_duel_card_text_rows_never_overlap(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import Layout

        characters = (
            CharacterView("left", "amis", "爱弥斯", hp=82, max_hp=100, meters=R.find_entry("amis").meters,
                          charges={"starlight": 2, "starburst": 3}),
            CharacterView("right", "daniya", "达妮娅", hp=95, max_hp=120, meters=R.find_entry("daniya").meters,
                          charges={"void_hand": 3}),
        )
        snapshot = ScreenSnapshot(
            phase=MatchPhase.FIGHT, left_id="amis", right_id="daniya", characters=characters,
            bet_side="left", series_label="五轮三胜", series_left=1, series_right=0,
        )
        for width, height in ((1080.0, 720.0), (810.0, 540.0)):
            batch, _ = paint_snapshot(snapshot, width, height)
            layout = Layout(width, height)
            for card in (layout.left_panel, layout.right_panel):
                own = [
                    (c.text, c.rect) for c in batch.commands
                    if getattr(c, "text", "") and c.rect.x >= card.x - 0.5 and c.rect.right <= card.right + 0.5
                    and c.rect.y >= card.y - 0.5 and c.rect.bottom <= card.bottom + 0.5
                ]
                self.assertTrue(own)
                for idx, (text_a, rect_a) in enumerate(own):
                    for text_b, rect_b in own[idx + 1:]:
                        overlap_x = min(rect_a.right, rect_b.right) - max(rect_a.x, rect_b.x)
                        overlap_y = min(rect_a.bottom, rect_b.bottom) - max(rect_a.y, rect_b.y)
                        self.assertFalse(
                            overlap_x > 1.0 and overlap_y > 1.0,
                            f"{width}x{height}: {text_a!r} overlaps {text_b!r}",
                        )

    def test_settlement_rows_stay_above_the_action(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import RoundResult

        characters = (
            CharacterView("left", "amis", "爱弥斯", hp=0, max_hp=100),
            CharacterView("right", "daniya", "达妮娅", hp=23, max_hp=120),
        )
        result = RoundResult(winner="right", bet_side="right", bet_amount=5, bet_won=True, streak=3, best_streak=5)
        result.chips = -3
        snapshot = ScreenSnapshot(
            phase=MatchPhase.RESULT, left_id="amis", right_id="daniya", characters=characters,
            result=result, bet_side="right", series_label="三局两胜", series_left=0, series_right=2,
            series_round=2, series_over=True,
        )
        for width, height in ((1080.0, 720.0), (810.0, 540.0)):
            batch, hit_map = paint_snapshot(snapshot, width, height)
            buttons = [b for b in hit_map.buttons if b.action == ACTION_NEXT]
            self.assertEqual(len(buttons), 1)
            button = buttons[0].rect
            # Inside the settlement card, nothing may take room from the
            # action: the card's own text all sits above the button.
            plate_path = [
                c for c in batch.commands if c.__class__.__name__ == "PathCommand"
                and getattr(getattr(c, "fill", None), "stops", None)
                and getattr(c.fill.stops[0].color, "red", None) == C.C_PANEL_LIFTED.red
            ]
            self.assertTrue(plate_path)
            points = [seg.start for seg in plate_path[-1].segments]
            left = min(p.x for p in points)
            top = min(p.y for p in points)
            right = max(p.x for p in points)
            bottom = max(p.y for p in points)
            inside = [
                c for c in batch.commands
                if getattr(c, "text", "") and c.rect.x >= left - 0.5 and c.rect.right <= right + 0.5
                and c.rect.y >= top - 0.5 and c.rect.bottom <= bottom + 0.5
            ]
            self.assertTrue(inside)
            # Only the button caption sits below the action; the card's hint is
            # a separate line inside the card's lower margin.
            for command in inside:
                if command.text in ("返回选人", "系列赛结束 · 点击返回选人界面"):
                    continue
                self.assertLessEqual(command.rect.bottom, button.y + 0.01, command.text)
            self.assertLessEqual(button.bottom, min(bottom, height))
            hint = [c for c in inside if c.text == "系列赛结束 · 点击返回选人界面"]
            self.assertEqual(len(hint), 1)
            self.assertLessEqual(hint[0].rect.bottom, bottom)
            self.assertGreaterEqual(button.x, left)
            self.assertLessEqual(button.right, right)

    def test_reveal_delay_shows_a_hold_banner_instead_of_the_card(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.RESULT, result=None, draw=True)
        batch, hit_map = paint_snapshot(snapshot, 1080.0, 720.0)
        texts = {getattr(c, "text", "") for c in batch.commands}
        self.assertIn("对战结束", texts)
        self.assertNotIn("再来一轮  R", texts)
        self.assertEqual([b for b in hit_map.buttons if b.action == "next"], [])

    def test_loser_is_painted_flat_and_stays_in_the_chamber(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import RoundResult, StatsSnapshot
        assets = Path(__file__).resolve().parent.parent / "lib" / "script" / "gemes" / "packages" / "official" / "dango_showdown" / "assets"
        payload = DangoAssetLoader(assets, inline=True).assets()
        sprites = {
            cid: HandFrame(e["resource"].resource_id, 1, e["resource"].frames[0])
            for (cid, state), e in payload["states"].items() if state == "idle"
        }
        result = RoundResult(winner="left", stats=StatsSnapshot(winner="left", loser="right"))
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.presenter import Layout
        box = Layout(1080.0, 720.0).arena.rect
        snapshot = ScreenSnapshot(phase=MatchPhase.RESULT, result=result, characters=(
            CharacterView("left", "amis", "爱弥斯", hp=20, x=0.5, y=0.5),
            # The frozen hurt pose is already flattened by the animator.
            CharacterView("right", "daniya", "达妮娅", hp=0, state="hurt", x=0.5, y=0.97, stretch=(1.26, 0.0, 0.0, 0.72)),
        ))
        batch, _ = paint_snapshot(snapshot, 1080.0, 720.0, sprites=sprites)
        fighter = [c for c in batch.commands if isinstance(c, SpriteCommand) and c.resource_id == sprites["daniya"].resource_id and c.z == 2]
        self.assertTrue(fighter)
        # Alive dango first, then the loser (drawn last, on top).
        sprite = fighter[-1]
        self.assertGreater(sprite.target_size.width / sprite.target_size.height, 1.25)
        self.assertLessEqual(sprite.position.y + sprite.target_size.height, box.bottom + 1.0)

    def test_hit_map_inside_and_outside(self):
        snapshot = ScreenSnapshot(phase=MatchPhase.SELECT, bet_side="left", bet_amount=5)
        _, hit_map = paint_snapshot(snapshot, 1000.0, 800.0)
        button = hit_map.buttons[0]
        self.assertIsNotNone(hit_map.hit_test(button.rect.center.x, button.rect.center.y))
        self.assertIsNone(hit_map.hit_test(-50.0, -50.0))


class DangoAcceptanceRegressionTests(unittest.TestCase):
    def _match(self, seed=3, fighters=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        events = []
        match = DangoMatch(
            DangoStats(Path(temp.name) / "state.json"), rng=random.Random(seed),
            bus=ev.ExtensionBus(publish=lambda name, payload: events.append((name, payload))),
        )
        match.prepare_round()
        if fighters is not None:
            match.fighter_ids = fighters
        match.choose_dango("left")
        match.choose_bet_amount(5)
        match.start_match()
        match.countdown = 0.0
        return match, events

    def test_grazing_contact_is_elastic_without_extra_acceleration(self):
        balls = _balls(0.05, -0.02)
        result = step(balls, C.FIXED_STEP_SECONDS)
        self.assertIsNotNone(result.contact)
        self.assertAlmostEqual(result.balls[0].vx, -0.02)
        self.assertAlmostEqual(result.balls[1].vx, 0.05)
        self.assertAlmostEqual(sum(b.speed ** 2 for b in balls), sum(b.speed ** 2 for b in result.balls))

    def test_wall_flight_replays_without_global_match_state(self):
        def replay():
            balls = create_balls(random.Random(37))
            for _ in range(240):
                balls = step(balls, C.FIXED_STEP_SECONDS).balls
            return balls
        self.assertEqual(replay(), replay())

    def test_grazing_wall_reflections_always_point_inside(self):
        radius = C.BASE_RADIUS
        for y in (0.2, 0.4, 0.6, 0.8):
            ball = Ball("left", radius, y, -0.001, 0.9)
            result = step((ball,), C.FIXED_STEP_SECONDS)
            self.assertGreaterEqual(result.balls[0].vx, 0.0)
            self.assertAlmostEqual(result.balls[0].speed, ball.speed)

    def test_round_numbers_advance_and_restart_for_a_new_series(self):
        match, events = self._match()
        match.tracker.apply_damage(match.left, match.right, damage_per_side=200)
        match._finish_round()
        self.assertEqual(match.round_index, 1)
        self.assertEqual(next(p["round"] for n, p in events if n == ev.MATCH_START), 1)
        match.prepare_round()
        self.assertEqual(match.round_index, 2)
        match.choose_dango("left")
        match.choose_bet_amount(1)
        match.start_match()
        self.assertEqual([p["round"] for n, p in events if n == ev.MATCH_START], [1, 2])
        match.tracker.apply_damage(match.left, match.right, damage_per_side=200)
        match._finish_round()
        match.series.over = True
        match.prepare_round()
        self.assertEqual(match.round_index, 1)

    def test_single_target_skill_triggers_hurt_and_finishing_hit_is_counted(self):
        match, _ = self._match(fighters=("amis", "daniya"))
        match.right.hp = 4
        self.assertEqual(match._skill_damage(match.left, match.right, 8), 4)
        self.assertEqual(match.right.state, C.HURT)
        self.assertEqual(match.right.hurt_flash_seconds, C.HURT_FLASH_SECONDS)
        match._finish_round()
        self.assertEqual(match.result.stats.left_hits, match.left.hits_dealt)
        self.assertEqual(match.result.stats.right_hits, match.right.hits_dealt)
        self.assertEqual(match.result.stats.total_hits, 1)

    def test_right_caster_skill_payload_preserves_left_right_hp(self):
        for skill in ("starlight", "starburst", "void_hand"):
            with self.subTest(skill=skill):
                fighters = ("daniya", "amis") if skill != "void_hand" else ("amis", "daniya")
                match, events = self._match(fighters=fighters)
                if skill == "void_hand":
                    for seed in range(12):
                        if skills.roll_collisions(match.right, match.left, "body", match.bus, random.Random(seed), match._skill_damage, match._deflect):
                            break
                    name = ev.VOID_HAND
                else:
                    spec = match.right.meter(skill)
                    skills.feed(match.right, match.left, spec, spec.slots, match.bus, source="test", damage=match._skill_damage, deflect=match._deflect)
                    name = ev.LASER_FIRE if skill == "starlight" else ev.STARBURST_FIRE
                payload = next(p for n, p in events if n == name)
                self.assertEqual((payload["hp_left"], payload["hp_right"]), (match.left.hp, match.right.hp))

    def test_draw_payout_is_zero(self):
        result = RoundResult(bet_amount=10, double_ko=True, draw=True)
        self.assertEqual(result.payout, 0)

    def test_nonfinite_save_scalars_fall_back_and_best_cannot_trail_current(self):
        restored = StreakState.from_record({"current_streak": 4, "best_streak": 1, "chips": float("inf"), "total_rounds": float("inf")})
        self.assertEqual((restored.current, restored.best, restored.rounds, restored.chips), (4, 4, 0, C.BET_AMOUNTS[0]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"version":1e999,"current_streak":99}', encoding="utf-8")
            self.assertEqual(DangoStats(path).snapshot()["current_streak"], 0)

    def test_all_series_formats_settle_once_including_draws(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import series

        for format_id, winners in (("bo5", ("left", "", "right", "left", "left")), ("bo3", ("left", "", "right")), ("bo1", ("",)), ("ft3", ("left", "left", "right"))):
            with self.subTest(format=format_id):
                match, _ = self._match()
                match.series.reset(series.BY_ID[format_id])
                fighters = match.fighter_ids
                bet = (match.bet.side, match.bet.amount)
                for round_number, winner in enumerate(winners, 1):
                    if round_number > 1:
                        match.result_reveal = 0.0
                        before = match.record
                        self.assertTrue(match.advance_round())
                        self.assertEqual(match.record, before)
                    self.assertIs(match.phase, MatchPhase.FIGHT)
                    self.assertEqual(match.fighter_ids, fighters)
                    self.assertEqual((match.bet.side, match.bet.amount), bet)
                    self.assertEqual(match.round_index, round_number)
                    # Force the requested series outcome with equal fixture HP.
                    for dango in match.dango:
                        dango.max_hp = dango.hp = 20
                    if winner:
                        loser = match.right if winner == "left" else match.left
                        match.tracker.damage_one(loser, loser.hp)
                    else:
                        match.tracker.apply_damage(match.left, match.right, damage_per_side=20)
                    match._finish_round()
                    match._finish_round()
                    self.assertEqual(match.record["total_rounds"], round_number)
                    self.assertEqual(match.result.series_round, round_number)
                self.assertTrue(match.series_over)
                self.assertEqual(match.result.series_winner, "left" if format_id in ("bo5", "ft3") else "")

    def test_continuation_requires_a_revealed_result_and_starts_fresh_combat(self):
        match, events = self._match(fighters=("amis", "daniya"))
        self.assertFalse(match.advance_round())
        match.left.charges["starlight"] = 2
        match.left.resolve_hurt()
        match.tracker.damage_one(match.right, match.right.hp)
        match._finish_round()
        record = match.record.copy()
        fighters = match.fighter_ids
        bet = (match.picked_side, match.bet.side, match.bet.amount)
        self.assertFalse(match.advance_round())
        self.assertIs(match.phase, MatchPhase.RESULT)
        match.result_reveal = 0.0
        self.assertTrue(match.advance_round())
        self.assertFalse(match.advance_round())
        self.assertIs(match.phase, MatchPhase.FIGHT)
        self.assertEqual(match.record, record)
        self.assertEqual(match.fighter_ids, fighters)
        self.assertEqual((match.picked_side, match.bet.side, match.bet.amount), bet)
        self.assertEqual(match.countdown, C.COUNTDOWN_SECONDS)
        self.assertEqual(match.tracker.elapsed, 0.0)
        self.assertEqual(match.tracker.left_hits + match.tracker.right_hits, 0)
        self.assertTrue(all(d.hp == d.max_hp and not d.knocked_out and d.state == C.IDLE for d in match.dango))
        self.assertTrue(all(value == 0 for d in match.dango for value in d.charges.values()))
        self.assertTrue(all(b.speed > 0.0 and b.y == 0.5 for b in match.balls))
        self.assertEqual([p["round"] for n, p in events if n == ev.MATCH_START], [1, 2])

    def test_series_end_returns_to_unbet_selection_without_starting_another_series(self):
        match, events = self._match()
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import series

        match.series.reset(series.BY_ID["bo1"])
        match.tracker.damage_one(match.right, match.right.hp)
        match._finish_round()
        record = match.record.copy()
        match.result_reveal = 0.0
        self.assertTrue(match.advance_round())
        self.assertIs(match.phase, MatchPhase.SELECT)
        self.assertFalse(match.advance_round())
        self.assertFalse(match.can_start)
        self.assertEqual(match.bet.side, "")
        self.assertEqual(match.bet.amount, 0)
        self.assertEqual(match.series.played, 0)
        self.assertEqual(match.record, record)
        self.assertEqual(len([n for n, p in events if n == ev.MATCH_START]), 1)

    def test_launch_countdown_uses_elapsed_time_with_fixed_and_variable_frame_rates(self):
        for deltas in ((1 / 30,), (1 / 60,), (1 / 120,), (0.013, 0.027, 0.045, 0.011, 0.033)):
            with self.subTest(deltas=deltas):
                match, _ = self._match()
                match.countdown = C.COUNTDOWN_SECONDS
                initial_balls = match.balls
                elapsed = 0.0
                frame = 0
                while elapsed < C.COUNTDOWN_SECONDS - 1e-9:
                    delta = min(deltas[frame % len(deltas)], C.COUNTDOWN_SECONDS - elapsed)
                    match.update(delta)
                    elapsed += delta
                    frame += 1
                    self.assertEqual(match.tracker.elapsed, 0.0)
                    self.assertEqual(match.balls, initial_balls)
                self.assertEqual(match.countdown, 0.0)
                match.update(0.06)
                self.assertAlmostEqual(match.tracker.elapsed, 3 * C.FIXED_STEP_SECONDS)
                self.assertNotEqual(match.balls, initial_balls)

    def test_seeded_round_replays_at_different_frame_rates(self):
        results = []
        for fps in (30, 60, 120):
            match, events = self._match(seed=12, fighters=("amis", "daniya"))
            match.countdown = C.COUNTDOWN_SECONDS
            for _ in range(fps * 400):
                match.update(1.0 / fps)
                if match.phase is MatchPhase.RESULT:
                    break
            self.assertIs(match.phase, MatchPhase.RESULT)
            results.append((match.result.stats, [(n, p) for n, p in events if n in (ev.LASER_FIRE, ev.VOID_HAND, ev.STARBURST_FIRE)]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_skill_damage_events_identify_each_skill_by_stable_id(self):
        for skill_id in ("starlight", "starburst", "void_hand"):
            with self.subTest(skill=skill_id):
                match, events = self._match(fighters=("amis", "daniya"))
                if skill_id == "void_hand":
                    for seed in range(12):
                        if skills.roll_collisions(match.right, match.left, "body", match.bus, random.Random(seed), match._skill_damage, match._deflect):
                            break
                else:
                    spec = match.left.meter(skill_id)
                    skills.feed(match.left, match.right, spec, spec.slots, match.bus, source="test", damage=match._skill_damage, deflect=match._deflect)
                payload = next(p for n, p in events if n == ev.SKILL_DAMAGE)
                self.assertEqual(payload["skill"], skill_id)

    def test_low_hp_hook_covers_skills_and_hits_once_per_side_per_round(self):
        match, _ = self._match(fighters=("amis", "daniya"))
        calls = []

        class Hook:
            def on_low_hp(self, current, side):
                calls.append((current, side))

        match.bus.add_hook(Hook())
        match.left.hp = match.left.max_hp // 4 + 1
        match.right.hp = match.right.max_hp // 4 + 1
        self.assertGreater(match.left.hp_ratio, 0.25)
        self.assertGreater(match.right.hp_ratio, 0.25)
        match._skill_damage(match.left, match.right, 1, skill="starlight")
        self.assertEqual(calls, [(match, "right")])
        match._skill_damage(match.left, match.right, 1, skill="starlight")
        match._apply_hit(_contact())
        match._skill_damage(match.right, match.left, 1, skill="void_hand")
        self.assertEqual(calls, [(match, "right"), (match, "left")])
        match.tracker.apply_damage(match.left, match.right, damage_per_side=200)
        match._finish_round()
        match.prepare_round()
        match.choose_dango("left")
        match.choose_bet_amount(1)
        match.start_match()
        match._skill_damage(match.left, match.right, match.right.hp - match.right.max_hp // 4, skill="starlight")
        self.assertEqual(calls, [(match, "right"), (match, "left"), (match, "right")])

    def test_skill_sink_keeps_three_arg_and_earlier_optional_flag_support(self):
        owner, target = _dango_pair()
        calls = []

        def legacy_sink(attacker, victim, amount):
            calls.append((attacker, victim, amount))
            victim.hp -= amount
            return amount

        _, removed = skills.snapshot_scene(legacy_sink, owner, target, 7, skill="starlight", defer_visual=True)
        self.assertEqual(removed, 7)
        self.assertEqual(target.hp, 93)
        self.assertEqual(len(calls), 1)

        def previous_sink(attacker, victim, amount, *, defer_visual=False, reposition=False):
            calls.append((defer_visual, reposition))
            return amount

        skills.snapshot_scene(previous_sink, owner, target, 60, skill="starburst", defer_visual=True, reposition=True)
        self.assertEqual(calls[-1], (True, True))

    def test_skill_sink_internal_type_error_never_retries_damage(self):
        owner, target = _dango_pair()

        def broken_sink(attacker, victim, amount, **kwargs):
            victim.hp -= amount
            raise TypeError("sink implementation failed")

        with self.assertRaisesRegex(TypeError, "sink implementation failed"):
            skills.snapshot_scene(broken_sink, owner, target, 7, skill="starlight")
        self.assertEqual(target.hp, 93)


if __name__ == "__main__":
    unittest.main()



