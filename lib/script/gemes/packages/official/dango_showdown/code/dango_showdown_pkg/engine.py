"""Match controller: owns the phase machine and drives pure simulation."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import Callable

from . import combat as combat_rules
from . import events as ev
from . import roster as roster_rules
from . import skills
from .betting import BetSlip
from .combat import CombatTracker
from .constants import (
    BET_AMOUNTS,
    COUNTDOWN_SECONDS,
    FIXED_STEP_SECONDS,
    RESULT_REVEAL_SECONDS,
    MAX_STEPS_PER_FRAME,
    RESULT_MIN_SECONDS,
    SKILL_TURN_DEG,
)
from .model import Dango, MatchPhase, RoundResult, SIDE_LEFT, SIDE_RIGHT
from .physics import Ball, create_balls, reset_ball_to, step
from .roster import ROSTER, find_entry
from .series import BY_ID, SeriesState, get_format
from .stats import DangoStats


class DangoMatch:
    """One round at a time; streak record survives rounds via ``DangoStats``."""

    def __init__(
        self,
        stats: DangoStats,
        *,
        rng: random.Random | None = None,
        bus: ev.ExtensionBus | None = None,
        on_finish: Callable[[RoundResult], None] | None = None,
    ) -> None:
        self.stats = stats
        self.rng = rng or random.Random()
        self.bus = bus or ev.ExtensionBus()
        self.on_finish = on_finish
        self.phase = MatchPhase.SELECT
        self.bet = BetSlip()
        self.picked_side = ""
        self.countdown = 0.0
        self.result_cooldown = 0.0
        # Presentation clock for the settlement card; None means "already up".
        self.result_reveal: float | None = None
        self.balls: tuple[Ball, Ball] = create_balls(self.rng)
        self.fighter_ids: tuple[str, str] = _default_pair()
        self.left = self._bind(_spawn(SIDE_LEFT, self.fighter_ids[0], self._ball(SIDE_LEFT)))
        self.right = self._bind(_spawn(SIDE_RIGHT, self.fighter_ids[1], self._ball(SIDE_RIGHT)))
        self.tracker = CombatTracker()
        self.result = RoundResult()
        self._accumulator = 0.0
        # side -> (remaining seconds, stored speed squared, fallback heading).
        # A nonlethal starburst borrows motion for its one-second landing beat.
        self._starburst_motion: dict[str, tuple[float, float, float]] = {}
        self.series = SeriesState()
    # ── derived state ─────────────────────────────────────────────
    @property
    def dango(self) -> tuple[Dango, Dango]:
        # Positions follow the live balls (the widget used to be the only
        # place syncing them, so pure rules/tests saw stale 0.5/0.5 centres).
        for dango, ball in zip((self.left, self.right), self.balls):
            dango.sync_to(ball.x, ball.y)
        return self.left, self.right

    def dango_by_side(self, side: str) -> Dango:
        # Reading the pair first pushes live ball positions into the dango
        # (the rules layer must never see the stale 0.5/0.5 spawn point).
        self.dango
        return self.left if side == SIDE_LEFT else self.right

    def _ball(self, side: str) -> Ball:
        return self.balls[0] if side == SIDE_LEFT else self.balls[1]

    def _ball_for(self, dango: Dango) -> Ball:
        # Identity first: tests and roster swaps can hand us a dango whose
        # side label no longer matches the ball list order.
        if dango is self.left:
            return self.balls[0]
        if dango is self.right:
            return self.balls[1]
        return self._ball(dango.side)

    def _sync_scene(self, owner: Dango, target: Dango) -> None:
        """Copy the live ball centres onto the pair a skill is about to hit."""
        for dango in (owner, target):
            ball = self._ball_for(dango)
            dango.sync_to(ball.x, ball.y)

    def _bind(self, dango: Dango) -> Dango:
        # The caller (a skill) must see the live physics centre even when a
        # test replaced ``match.left`` / ``match.right`` with a fresh object.
        # The module-level ``dango`` property cannot reach those, so bind the
        # sync on the object itself.
        ball = self._ball(dango.side)
        dango.sync_to(ball.x, ball.y)
        dango._sync_scene = lambda: dango.sync_to(self._ball_for(dango).x, self._ball_for(dango).y)
        return dango

    @property
    def match_format(self):
        return get_format(self.series.format.format_id)

    @property
    def series_over(self) -> bool:
        return bool(self.series.over)

    @property
    def round_index(self) -> int:
        """Current one-based series round, including its settlement card."""
        if self.phase is MatchPhase.RESULT:
            return self.series.played
        return self.series.round_number

    def choose_format(self, format_id: str) -> bool:
        """Pick the series length; only possible between series."""
        if format_id not in BY_ID or self.phase is not MatchPhase.SELECT:
            return False
        if self.series.played > 0 and not self.series.over:
            return False
        # Between series (or before the first round) the format is free
        # to change; a finished series restarts with the new one.
        self.series.over = False
        self.series.reset(BY_ID[format_id])
        return True

    @property
    def record(self) -> dict[str, int]:
        return self.stats.snapshot()

    def prepare_round(self) -> None:
        self.phase = MatchPhase.SELECT
        self._starburst_motion.clear()
        self.picked_side = ""
        self.bet.clear()
        self.result = RoundResult()
        if self.series.over:
            # A finished series starts a fresh one with the same format; the
            # chooser stays open on the select screen.
            self.series.reset()
        self.balls = create_balls(self.rng)
        self._roll_fighters()
        self.tracker.reset()
        self.countdown = 0.0
        self.result_reveal = None
        self._accumulator = 0.0

    def _roll_fighters(self) -> None:
        pool = tuple(entry.character_id for entry in ROSTER)
        if not pool:
            raise RuntimeError("dango roster is empty")
        if len(pool) == 1:
            ids = (pool[0], pool[0])
        else:
            first, second = self.rng.sample(pool, 2)
            ids = (first, second)
        if self.rng.random() < 0.5:
            ids = (ids[1], ids[0])
        self.fighter_ids = ids
        self.left = self._bind(_spawn(SIDE_LEFT, ids[0], self._ball(SIDE_LEFT)))
        self.right = self._bind(_spawn(SIDE_RIGHT, ids[1], self._ball(SIDE_RIGHT)))
        self.picked_side = ""
        self.bet.clear()

    def choose_dango(self, side: str, character_id: str | None = None) -> bool:
        """Pick a side to back; a roster id also swaps that side's fighter."""
        if self.phase is not MatchPhase.SELECT:
            return False
        if character_id is None:
            if side not in (SIDE_LEFT, SIDE_RIGHT):
                return False
        else:
            entry = find_entry(character_id)
            if entry is None or not any(candidate.character_id == character_id for candidate in roster_rules.ROSTER):
                return False
            if side not in (SIDE_LEFT, SIDE_RIGHT):
                side = SIDE_LEFT if entry.character_id == self.left.character_id else SIDE_RIGHT
            other = self.right if side == SIDE_LEFT else self.left
            if entry.character_id == other.character_id:
                return False
            if side == SIDE_LEFT:
                self.fighter_ids = (entry.character_id, self.fighter_ids[1])
                self.left = self._bind(_spawn(SIDE_LEFT, entry.character_id, self._ball(SIDE_LEFT)))
            else:
                self.fighter_ids = (self.fighter_ids[0], entry.character_id)
                self.right = self._bind(_spawn(SIDE_RIGHT, entry.character_id, self._ball(SIDE_RIGHT)))
        self.picked_side = side
        self.bet.choose_side(side)
        self.balls = create_balls(self.rng)
        return True

    def choose_bet_amount(self, amount: int) -> bool:
        if self.phase is not MatchPhase.SELECT:
            return False
        if self.bet.choose_amount(amount):
            return True
        return False

    def randomize(self) -> None:
        if self.phase is not MatchPhase.SELECT:
            return
        self._roll_fighters()
        self.balls = create_balls(self.rng)
        self.choose_dango(self.rng.choice((SIDE_LEFT, SIDE_RIGHT)))
        self.bet.choose_amount(self.rng.choice(BET_AMOUNTS))

    @property
    def can_start(self) -> bool:
        return self.phase is MatchPhase.SELECT and bool(self.picked_side) and self.bet.ready

    def advance_round(self) -> bool:
        """Continue this series, or return to selection after its final card.

        A series keeps its fighters and bet across rounds; only the physical
        bodies and per-round combat state are recreated by ``start_match``.
        The result reveal remains mandatory for automatic and manual paths.
        """
        if self.phase is not MatchPhase.RESULT or self.result_reveal not in (None, 0.0):
            return False
        if self.series_over:
            self.prepare_round()
            return True
        if not self.bet.ready or self.picked_side not in (SIDE_LEFT, SIDE_RIGHT):
            return False
        self.balls = create_balls(self.rng)
        self.phase = MatchPhase.SELECT
        return self.start_match()

    def start_match(self) -> bool:
        if not self.can_start:
            return False
        self.phase = MatchPhase.FIGHT
        self._starburst_motion.clear()
        self.result = RoundResult()
        self.tracker.reset()
        if self.series.over:
            # A finished series starts afresh with the same format; the loser
            # of the previous series still gets to fight.
            self.series.reset()
        self.left = self._bind(_spawn(SIDE_LEFT, self.fighter_ids[0], self.balls[0]))
        self.right = self._bind(_spawn(SIDE_RIGHT, self.fighter_ids[1], self.balls[1]))
        self._sync_motion_multipliers()
        self.countdown = COUNTDOWN_SECONDS
        self.result_cooldown = 0.0
        self.result_reveal = None
        self._accumulator = 0.0
        self._armed = True
        self.bus.match_start(
            ev.MatchStartPayload(
                round=self.round_index,
                left_id=self.left.character_id,
                right_id=self.right.character_id,
                bet_side=self.bet.side,
                bet_amount=self.bet.amount,
                left_hp=self.left.hp,
                right_hp=self.right.hp,
            ),
            self,
        )
        return True

    def update(self, seconds: float) -> None:
        seconds = max(0.0, min(0.1, float(seconds)))
        for dango in self.dango:
            dango.advance_state(seconds)
        if self.phase is MatchPhase.FIGHT:
            if self.countdown > 0.0:
                waiting = min(self.countdown, seconds)
                self.countdown = max(0.0, self.countdown - waiting)
                seconds -= waiting
                if self.countdown <= 1e-9:
                    self.countdown = 0.0
                if self.countdown > 0.0 or seconds <= 1e-9:
                    return
            self._step_physics(seconds)
        elif self.phase is MatchPhase.RESULT:
            # The round is over: enemy stays frozen in its hurt pose while the
            # settlement card waits out its reveal delay.
            if self.result_reveal is not None:
                self.result_reveal = max(0.0, self.result_reveal - seconds)
                if self.result_reveal <= 1e-9:
                    self.result_reveal = 0.0

    def _step_physics(self, seconds: float) -> None:
        self._accumulator += seconds
        last_contact = None
        steps = 0
        while self._accumulator >= FIXED_STEP_SECONDS and steps < MAX_STEPS_PER_FRAME:
            self._accumulator -= FIXED_STEP_SECONDS
            self._advance_starburst_motion(FIXED_STEP_SECONDS)
            self.tracker.elapsed += FIXED_STEP_SECONDS
            steps += 1
            outcome = step(self.balls, FIXED_STEP_SECONDS)
            self.balls = outcome.balls
            self._advance_slow(FIXED_STEP_SECONDS)
            for owner, other in ((self.left, self.right), (self.right, self.left)):
                skills.advance_sun_spirits(
                    owner, other, FIXED_STEP_SECONDS, self.bus, self._skill_damage, self._deflect,
                )
                skills.advance_frost_slash(
                    owner, other, FIXED_STEP_SECONDS, self.bus, self._skill_damage, self._apply_slow,
                )
            if self.tracker.finished:
                break
            for wall in outcome.wall_hits:
                owner = self.dango_by_side(wall.side)
                other = self.right if owner is self.left else self.left
                owner.contact_x, owner.contact_y = wall.point_x, wall.point_y
                skills.charge_from_wall(owner, other, self.bus, self.rng, self._skill_damage)
                skills.roll_collisions(owner, other, "wall", self.bus, self.rng, self._skill_damage, self._deflect)
            if outcome.contact is not None:
                last_contact = outcome.contact
            gap = self._gap()
            if gap > combat_rules.SEPARATION_MARGIN:
                self._armed = True
            elif last_contact is not None and self._armed:
                # One real impact per contact: a fresh overlap reports a
                # contact while the previous separation still arms the latch.
                self._apply_hit(last_contact)
                self._armed = False
                last_contact = None
            if self.tracker.finished:
                break
        if self.tracker.finished:
            self._finish_round()

    def _gap(self) -> float:
        left, right = self.balls
        return math.hypot(right.x - left.x, right.y - left.y) - (left.radius + right.radius)

    def _apply_hit(self, contact) -> None:
        if contact is None:
            return
        hp_before = (self.left.hp, self.right.hp)
        frost_before = (self.left.frost_energy, self.right.frost_energy)
        dealt = self.tracker.resolve(self.left, self.right, contact, rng=self.rng)
        if not dealt:
            return
        for owner, before in zip((self.left, self.right), frost_before):
            if owner.frost_energy != before:
                skills.frost_energy_changed(owner, self.bus, source="contact")
        payload = ev.HitPayload(
            side=contact.side_a,
            speed=contact.relative_speed,
            hp_left=self.left.hp,
            hp_right=self.right.hp,
            point=(contact.point_x, contact.point_y),
            normal=(contact.normal_x, contact.normal_y),
            damage_left=hp_before[0] - self.left.hp,
            damage_right=hp_before[1] - self.right.hp,
        )
        self.bus.hit(payload, self, self.left.hp_ratio, self.right.hp_ratio)
        for dango in self.dango:
            dango.contact_x, dango.contact_y = contact.point_x, contact.point_y
        for owner, other in ((self.left, self.right), (self.right, self.left)):
            skills.charge_from_contact(owner, other, 1, self.bus, self._skill_damage, self._deflect)
        for owner, other in ((self.left, self.right), (self.right, self.left)):
            skills.roll_collisions(owner, other, "body", self.bus, self.rng, self._skill_damage, self._deflect)
        if self.tracker.finished:
            self._finish_round()

    def _skill_damage(
        self,
        owner: Dango,
        target: Dango,
        amount: int,
        defer_visual: bool = False,
        reposition: bool = False,
        *,
        skill: str = "",
    ) -> int | tuple[int, tuple[float, float], tuple[float, float]]:
        """Apply skill damage; ``defer_visual`` lets the presenter time the number.

        ``SKILL_DAMAGE`` drives the floating number and the small shake. Timing
        it to a cinematic frame (the starburst landing) is presentation, not a
        rule, so the rule object is emitted untouched and the UI skips it.

        ``reposition`` is the starburst's knock-down: only Amis's ultimate
        parks the victim at the bottom centre (void hand leaves it in place;
        its ``_deflect`` already turns the flight direction).
        """
        removed = self.tracker.damage_one(target, amount)
        if removed <= 0:
            return 0
        target.hits_taken += 1
        owner.hits_dealt += 1
        payload = ev.SkillDamagePayload(
            side=owner.side,
            target=target.side,
            skill=skill,
            damage=removed,
            hp_left=self.left.hp,
            hp_right=self.right.hp,
            direction=(-1.0 if owner.side == "left" else 1.0, 0.0),
            defer_visual=bool(defer_visual),
            point=(float(target.x), float(target.y)),
        )
        if reposition:
            self._reset_to_bottom_center(target)
            payload = replace(payload, point=(float(target.x), float(target.y)))
        self.bus.skill(ev.SKILL_DAMAGE, payload)
        self.bus.check_low_hp(self, self.left.hp_ratio, self.right.hp_ratio)
        if reposition:
            # The starburst is the one skill that moves the victim; report the
            # landing spot so the impact FX and the card agree with the rules.
            return removed, (float(owner.x), float(owner.y)), (float(target.x), float(target.y))
        return removed

    def _reset_to_bottom_center(self, target: Dango) -> None:
        """Starburst landing: drop the enemy to the bottom centre of the arena.

        Living targets lend their current kinetic energy for the one-second
        landing animation. The fixed simulation clock returns it afterwards.
        """
        balls = list(self.balls)
        for index, ball in enumerate(balls):
            if ball.side != target.side:
                continue
            if target.alive:
                _, stored, heading = self._starburst_motion.get(ball.side, (0.0, 0.0, 0.0))
                if ball.speed > 0.0:
                    heading = math.atan2(ball.vy, ball.vx)
                self._starburst_motion[ball.side] = (
                    skills.STARBURST_TOTAL_MS / 1000.0,
                    stored + ball.speed * ball.speed,
                    heading,
                )
            else:
                self._starburst_motion.pop(ball.side, None)
            # Compact AABB check against the other ball: if the knock-back
            # spot is occupied, stack the dango slightly higher.
            other = balls[1 - index]
            y = 1.0 - ball.radius - 0.004
            if abs(other.x - 0.5) < other.radius + ball.radius and abs(other.y - y) < other.radius + ball.radius:
                y = max(ball.radius, other.y - other.radius - ball.radius - 0.01)
            # Keep the immediate landing pose; recovery adds the exact stored
            # energy to whatever later collisions transfer onto this body.
            balls[index] = reset_ball_to(
                ball,
                0.5,
                y,
                vx=0.0,
                vy=0.0,
            )
        self.balls = tuple(balls)
        target.x, target.y = self.balls[0 if target.side == SIDE_LEFT else 1].x, self.balls[0 if target.side == SIDE_LEFT else 1].y

    def _sync_motion_multipliers(self) -> None:
        """Apply status to movement without repeatedly scaling raw momentum."""
        targets = {target.side: target for target in (self.left, self.right)}
        self.balls = tuple(
            replace(ball, motion_multiplier=targets[ball.side].slow_multiplier)
            if ball.motion_multiplier != targets[ball.side].slow_multiplier else ball
            for ball in self.balls
        )

    def _apply_slow(self, target: Dango, seconds: float, multiplier: float) -> None:
        if not target.alive:
            return
        target.slow_seconds = max(0.0, float(seconds))
        target.slow_multiplier = max(0.0, min(1.0, float(multiplier))) if target.slow_seconds > 0.0 else 1.0
        self._sync_motion_multipliers()

    def _advance_slow(self, seconds: float) -> None:
        # Consume the interval after movement: the last slowed fixed step
        # keeps its full multiplier, then expiry restores the next step.
        changed = False
        for target in (self.left, self.right):
            if target.slow_seconds <= 0.0:
                continue
            target.slow_seconds = max(0.0, target.slow_seconds - seconds)
            if target.slow_seconds <= 1e-9:
                target.slow_seconds = 0.0
                target.slow_multiplier = 1.0
                changed = True
        if changed:
            self._sync_motion_multipliers()

    def _advance_starburst_motion(self, seconds: float) -> None:
        """Return borrowed kinetic energy, without replacing collision energy."""
        if self.phase is not MatchPhase.FIGHT or self.tracker.finished:
            return
        for side, (remaining, stored, heading) in tuple(self._starburst_motion.items()):
            target = self.dango_by_side(side)
            if not target.alive:
                self._starburst_motion.pop(side, None)
                continue
            remaining -= seconds
            if remaining > 1e-9:
                self._starburst_motion[side] = (remaining, stored, heading)
                continue
            balls = list(self.balls)
            for index, ball in enumerate(balls):
                if ball.side != side:
                    continue
                if ball.speed > 0.0:
                    heading = math.atan2(ball.vy, ball.vx)
                speed = math.sqrt(ball.speed * ball.speed + stored)
                balls[index] = ball.moved(0.0, 0.0, speed * math.cos(heading), speed * math.sin(heading))
            self.balls = tuple(balls)
            self._starburst_motion.pop(side, None)

    def _deflect(self, target: Dango, owner: Dango, sign: float) -> None:
        balls = list(self.balls)
        for index, ball in enumerate(balls):
            if ball.side != target.side:
                continue
            if ball.speed <= 1e-6:
                # Deflection alone cannot release the motion temporarily
                # stored by a starburst's landing animation.
                continue
            speed = ball.speed
            angle = math.atan2(ball.vy, ball.vx)
            lateral = owner.x - ball.x
            turn = math.radians(SKILL_TURN_DEG) * sign * (1.0 if lateral >= 0.0 else -1.0)
            balls[index] = ball.moved(0.0, 0.0, speed * math.cos(angle + turn), speed * math.sin(angle + turn))
        self.balls = tuple(balls)

    def _finish_round(self) -> None:
        if self.phase is not MatchPhase.FIGHT:
            return
        self.phase = MatchPhase.RESULT
        self._starburst_motion.clear()
        self.result_cooldown = RESULT_MIN_SECONDS
        self.result_reveal = max(RESULT_MIN_SECONDS, RESULT_REVEAL_SECONDS)
        series_winner = self.series.record_round("" if self.tracker.double_ko else self.tracker.build_result(self.left, self.right).winner)
        record = self.stats.snapshot()
        result = self.tracker.build_result(self.left, self.right, streak=record["current_streak"], best_streak=record["best_streak"])
        won = self.stats.settle(
            bet_side=self.bet.side,
            winner=result.winner,
            double_ko=result.double_ko,
            amount=self.bet.amount,
        )
        record = self.stats.snapshot()
        result.bet_side = self.bet.side
        result.bet_amount = self.bet.amount
        result.bet_won = bool(won)
        result.draw = bool(result.double_ko)
        result.streak = record["current_streak"]
        result.best_streak = record["best_streak"]
        result.chips = record["chips"]
        result.series_left, result.series_right = self.series.score
        result.series_draws = self.series.draws
        result.series_round = self.series.played
        result.series_label = self.series.format.label
        result.series_over = self.series.over
        result.series_winner = series_winner
        self.result = result
        # Freeze the tableau: the loser stays in its hurt pose and the winner
        # stops breathing, so the 3 s reveal is a still frame, not a fight.
        for dango in self.dango:
            dango.knocked_out = True
            if dango.hp <= 0:
                # A defeated dango keeps the hurt picture (not its last attack
                # frame) for the whole settlement beat.
                dango.resolve_hurt()
        # Freeze the tableau where it fell; the losing dango's resting spot
        # is clamped into the chamber by the presenter.
        self.balls = tuple(
            ball.moved(0.0, 0.0, 0.0, 0.0)
            for ball in self.balls
        )
        for dango in self.dango:
            if dango.hp <= 0:
                dango.resolve_hurt()
        if not result.draw:
            winner = self.dango_by_side(result.winner)
            winner.state = "idle"
            winner.state_seconds = 0.0
            winner.hurt_flash_seconds = 0.0
        self.bus.ko(
            ev.KoPayload(
                winner=result.winner,
                loser=result.stats.loser,
                hp_left=result.stats.left_hp,
                hp_right=result.stats.right_hp,
                hits=result.stats.total_hits,
                draw=result.draw,
            )
        )
        self.bus.bet_settled(
            ev.BetSettledPayload(
                bet_side=result.bet_side,
                winner=result.winner,
                hit=result.bet_won,
                streak=result.streak,
                best_streak=result.best_streak,
                draw=result.draw,
            )
        )
        self.bus.match_end(
            ev.MatchEndPayload(
                winner=result.winner,
                duration_ms=int(result.stats.duration_seconds * 1000),
                stats={"left_hits": result.stats.left_hits, "right_hits": result.stats.right_hits},
            ),
            self,
            result,
        )
        if self.on_finish is not None:
            try:
                self.on_finish(result)
            except Exception:
                pass


def _default_pair() -> tuple[str, str]:
    if not ROSTER:
        raise RuntimeError("dango roster is empty")
    first = ROSTER[0]
    second = ROSTER[1] if len(ROSTER) > 1 else ROSTER[0]
    return first.character_id, second.character_id


def _spawn(side: str, character_id: str, ball: Ball | None = None) -> Dango:
    entry = find_entry(character_id) or ROSTER[0]
    dango = combat_rules.reset_dango(side, entry.character_id, entry.name)
    if ball is not None:
        dango.x, dango.y = ball.x, ball.y
    return dango


__all__ = ["DangoMatch"]
