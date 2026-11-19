"""Damage rules for the duel (pure Python, one damage event per contact)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .constants import (
    MIN_IMPACT_SPEED,
    SEPARATION_MARGIN,
)
from .model import Dango, RoundResult, StatsSnapshot
from .physics import Ball, Contact
from .roster import DangoEntry, find_entry


@dataclass
class CombatTracker:
    """Latch a hit when two dango touch and only re-arm after separating."""

    touching: bool = False
    left_hits: int = 0
    right_hits: int = 0
    elapsed: float = 0.0
    finished: bool = False
    double_ko: bool = False
    last_hit_touches: int = 0

    def reset(self) -> None:
        self.touching = False
        self.left_hits = 0
        self.right_hits = 0
        self.elapsed = 0.0
        self.finished = False
        self.double_ko = False
        self.last_hit_touches = 0

    def resolve(self, left: Dango, right: Dango, contact: Contact, *, rng=None) -> bool:
        """Count one touch when a contact starts.

        Returns ``True`` when this call was a new touch. The engine already
        reports only fresh impacts, so this is the single place where a hit
        turns into HP loss.
        """
        if self.finished or contact is None:
            return False
        # The engine latches one hit per contact and calls ``release`` only
        # after the pair has clearly separated.
        if left.hp <= 0 or right.hp <= 0:
            return False
        speed = max(0.0, float(contact.relative_speed))
        if speed < MIN_IMPACT_SPEED:
            return False
        # Both attacks land in the same contact. Set both HP totals before
        # checking the result so asymmetric attacks can still double KO.
        attack_rng = rng if rng is not None else random
        incoming_right = _roll_contact_damage(left, attack_rng)
        incoming_left = _roll_contact_damage(right, attack_rng)
        left.hp, right.hp = max(0, left.hp - incoming_left), max(0, right.hp - incoming_right)
        self._check_finished(left, right)
        left.hits_taken += 1
        right.hits_dealt += 1
        right.hits_taken += 1
        left.hits_dealt += 1
        for receiver in (left, right):
            receiver.resolve_attack()
            receiver.resolve_hurt()
        self.left_hits = left.hits_dealt
        self.right_hits = right.hits_dealt
        self.last_hit_touches = self.left_hits + self.right_hits
        return True

    def apply_damage(self, left: Dango, right: Dango, *, damage_per_side: int = 0) -> None:
        """Deal equal HP damage to both fighters and check the finish condition."""
        left.hp = max(0, left.hp)
        right.hp = max(0, right.hp)
        if damage_per_side > 0:
            left.hp = max(0, left.hp - int(damage_per_side))
            right.hp = max(0, right.hp - int(damage_per_side))
        self._check_finished(left, right)
    def damage_one(self, target: Dango, amount: int) -> int:
        """Single-target skill damage; returns HP actually removed."""
        if self.finished or target.hp <= 0:
            return 0
        before = target.hp
        target.hp = max(0, target.hp - max(0, int(amount)))
        if target.hp < before:
            target.resolve_hurt()
        if target.hp <= 0:
            self.finished = True
            self.double_ko = False
        return before - target.hp

    def release(self) -> None:
        """Re-arm damage once the dango are clearly apart."""
        self.touching = False

    def _check_finished(self, left: Dango, right: Dango) -> None:
        left_dead = left.hp <= 0
        right_dead = right.hp <= 0
        if not left_dead and not right_dead:
            return
        self.finished = True
        self.double_ko = left_dead and right_dead

    def build_result(self, left: Dango, right: Dango, *, streak: int = 0, best_streak: int = 0) -> RoundResult:
        if self.double_ko:
            winner = ""
            loser = ""
        elif left.hp <= 0:
            winner, loser = right.side, left.side
        else:
            winner, loser = left.side, right.side
        snapshot = StatsSnapshot(
            left_hits=left.hits_dealt,
            right_hits=right.hits_dealt,
            left_hp=max(0, left.hp),
            right_hp=max(0, right.hp),
            duration_seconds=self.elapsed,
            winner=winner,
            loser=loser,
            double_ko=self.double_ko,
        )
        result = RoundResult(
            winner=winner,
            double_ko=self.double_ko,
            stats=snapshot,
            streak=streak,
            best_streak=best_streak,
        )
        return result


SEPARATION_MARGIN = SEPARATION_MARGIN


def separate(left: Ball, right: Ball, margin: float = SEPARATION_MARGIN) -> bool:
    """True when the two balls are far enough apart to allow a new hit."""
    dx = right.x - left.x
    dy = right.y - left.y
    return (dx * dx + dy * dy) ** 0.5 > left.radius + right.radius + margin


def reset_dango(side: str, character_id: str, name: str) -> Dango:
    entry = find_entry(character_id)
    if entry is None:
        return Dango(side=side, character_id=character_id, name=name)
    return Dango(
        side=side,
        character_id=entry.character_id,
        name=name,
        max_hp=entry.max_hp,
        meters=entry.meters,
        contact_damage=entry.contact_damage,
        contact_damage_range=entry.contact_damage_range,
    )


def _roll_contact_damage(dango: Dango, rng) -> int:
    """Roll the whole ordinary attack before either side can die this contact."""
    if dango.contact_damage_range is None:
        damage = max(0, int(dango.contact_damage))
    else:
        low, high = sorted(int(value) for value in dango.contact_damage_range)
        damage = max(0, int(rng.randint(low, high)))
    entry = entry_for(dango)
    frost = entry.frost_slash if entry is not None else None
    if frost is not None and dango.frost_energy > 0:
        spent = min(dango.frost_energy, max(0, frost.energy_per_contact))
        dango.frost_energy -= spent
        damage += spent * frost.contact_bonus
    return damage


def entry_for(dango: Dango) -> DangoEntry | None:
    return find_entry(dango.character_id)

