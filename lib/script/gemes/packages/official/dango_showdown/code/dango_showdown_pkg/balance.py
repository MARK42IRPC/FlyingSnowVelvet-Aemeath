"""Headless calibration against the hidden baseline, using the real match engine."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from fractions import Fraction
import math
import random
from typing import Callable

from . import events as ev
from .constants import FIXED_STEP_SECONDS
from .engine import DangoMatch
from .model import MatchPhase
from .roster import BENCHMARK_ENTRY, ROSTER, find_entry
from .stats import DangoStats

_PROBE_HP = 10_000  # At most 500 basic hits, each causing at most five HP.


def create_benchmark_match(
    stats: DangoStats, *, character_id: str, seed: int, side: str = "left",
) -> DangoMatch:
    """Explicit laboratory entry; the normal chooser cannot select the baseline."""
    if character_id not in {entry.character_id for entry in ROSTER}:
        raise ValueError("benchmark participant must be a public roster character")
    if side not in ("left", "right"):
        raise ValueError("side must be left or right")
    match = DangoMatch(stats, rng=random.Random(seed))
    match.prepare_round()
    if not match.choose_format("bo1"):
        raise RuntimeError("could not prepare benchmark format")
    match.fighter_ids = ((character_id, BENCHMARK_ENTRY.character_id) if side == "left"
                         else (BENCHMARK_ENTRY.character_id, character_id))
    if not (match.choose_dango(side) and match.choose_bet_amount(1) and match.start_match()):
        raise RuntimeError("could not start benchmark match")
    return match


def run_benchmark_series(
    stats: DangoStats, *, character_id: str, count: int, start_seed: int = 0,
    hp: int | None = None, thresholds: bool = False, max_sim_seconds: float = 10_000,
    progress: Callable[[int], None] | None = None,
) -> list[dict]:
    """Run production fights, alternating sides and preserving normal countdown.

    Threshold probes keep only the participant alive. HP changes no trajectory,
    skill rule or RNG before death in the current roster. The received-damage
    prefix therefore determines each candidate HP's win/draw/loss exactly. A
    real-HP replay must still validate this equivalence before publishing a fit.
    """
    if count <= 0 or count % 2:
        raise ValueError("count must be positive and even, for balanced sides")
    if hp is not None and hp <= 0:
        raise ValueError("hp must be positive")
    if thresholds and hp is not None:
        raise ValueError("threshold probes do not take a candidate HP")
    if max_sim_seconds <= 0:
        raise ValueError("simulation guard must be positive")
    initial_rounds = stats.snapshot()["total_rounds"]
    rows = []
    for index in range(count):
        seed = start_seed + index
        side = "left" if index % 2 == 0 else "right"
        other_side = "right" if side == "left" else "left"
        match = create_benchmark_match(stats, character_id=character_id, seed=seed, side=side)
        fighter = match.dango_by_side(side)
        initial_hp = _PROBE_HP if thresholds else (hp if hp is not None else fighter.max_hp)
        fighter.max_hp = initial_hp
        fighter.hp = initial_hp
        received = 0
        previous_received = 0
        body_finish = False
        contacts = 0

        def observe(name: str, payload: dict) -> None:
            nonlocal received, previous_received, body_finish, contacts
            if name == ev.HIT:
                contacts += 1
                previous_received = received
                received = initial_hp - int(payload["hp_" + side])
                body_finish = int(payload["hp_" + other_side]) == 0

        # No desktop/UI publisher and no user stats; observe only the real rules.
        match.bus = ev.ExtensionBus(publish=observe)
        while match.phase is MatchPhase.FIGHT:
            match.update(5 * FIXED_STEP_SECONDS)
            if match.tracker.elapsed >= max_sim_seconds and match.phase is MatchPhase.FIGHT:
                raise RuntimeError(f"Unfinished benchmark: {character_id}, seed={seed}, side={side}")
        if stats.snapshot()["total_rounds"] != initial_rounds + index + 1:
            raise RuntimeError("benchmark settled more than once")
        if not all(ball.radius - 1e-8 <= ball.x <= 1 - ball.radius + 1e-8
                   and ball.radius - 1e-8 <= ball.y <= 1 - ball.radius + 1e-8 for ball in match.balls):
            raise RuntimeError("benchmark ended outside the arena")
        outcome = ("draw" if match.result.draw else
                   ("win" if match.result.winner == side else "loss"))
        benchmark = match.dango_by_side(other_side)
        if thresholds and (outcome != "win" or benchmark.hp != 0):
            raise RuntimeError("probe HP was insufficient")
        rows.append({"seed": seed, "side": side, "outcome": outcome,
                     "duration_seconds": match.tracker.elapsed,
                     "participant_hp": fighter.hp, "benchmark_hp": benchmark.hp,
                     "received_damage": received, "previous_received_damage": previous_received,
                     "body_finish": body_finish, "contacts": contacts,
                     "starting_hp": initial_hp})
        if progress is not None:
            progress(index + 1)
    return rows


def outcome_at_hp(row: dict, hp: int) -> str:
    """Predict a real-HP outcome from an alive-until-baseline-death probe."""
    if hp <= 0:
        raise ValueError("hp must be positive")
    if hp > row["received_damage"]:
        return "win"
    if row["body_finish"] and hp > row["previous_received_damage"]:
        return "draw"
    return "loss"


def choose_hp(rows: list[dict], *, step: int = 1) -> int:
    """Choose the smallest allowed HP nearest to a 50% calibration score."""
    if not rows:
        raise ValueError("at least one threshold observation is required")
    if not isinstance(step, int) or step <= 0:
        raise ValueError("HP step must be a positive integer")
    received = sorted(row["received_damage"] for row in rows)
    body_received = sorted(row["received_damage"] for row in rows if row["body_finish"])
    body_previous = sorted(row["previous_received_damage"] for row in rows if row["body_finish"])

    def distance(hp: int) -> tuple[float, int]:
        wins = bisect_left(received, hp)
        draws = bisect_left(body_previous, hp) - bisect_left(body_received, hp)
        return abs((wins + draws / 2) / len(rows) - 0.5), hp

    return min(range(step, received[-1] + step + 1, step), key=distance)


def summarize(rows: list[dict], *, candidate_hp: int | None = None) -> dict:
    if not rows:
        raise ValueError("at least one match is required")
    outcomes = [outcome_at_hp(row, candidate_hp) if candidate_hp is not None else row["outcome"]
                for row in rows]
    counts = Counter(outcomes)
    scores = [{"win": 1.0, "draw": 0.5, "loss": 0.0}[outcome] for outcome in outcomes]
    n = len(rows)
    mean = sum(scores) / n
    variance = sum((score - mean) ** 2 for score in scores) / (n - 1) if n > 1 else 0.0
    half = 1.959963984540054 * math.sqrt(variance / n)
    return {"count": n, **{key: counts[key] for key in ("win", "loss", "draw")},
            "win_rate": counts["win"] / n, "draw_rate": counts["draw"] / n,
            "balance_score": mean, "score_95_normal": [max(0.0, mean-half), min(1.0, mean+half)],
            "mean_duration_seconds": sum(row["duration_seconds"] for row in rows) / n,
            "max_duration_seconds": max(row["duration_seconds"] for row in rows),
            "by_side": {side: dict(Counter(outcome for row, outcome in zip(rows, outcomes)
                                          if row["side"] == side)) for side in ("left", "right")}}


def _damage_cdf(hits: int, hp: int) -> Fraction:
    """Exact P(sum of hits independent uniform integer 1..5 rolls < hp)."""
    counts = [1]
    for _ in range(hits):
        next_counts = [0] * (len(counts) + 5)
        for value, count in enumerate(counts):
            if count:
                for damage in range(1, 6):
                    next_counts[value + damage] += count
        counts = next_counts
    return Fraction(sum(counts[:max(0, hp)]), 5 ** hits)


def theoretical_score(character_id: str, hp: int) -> dict:
    """Exact contact-count result for the two non-stochastic damage schedules.

    This is an analytical distribution, not a second physics implementation.
    Production-engine replays verify the terminal contact and event ordering.
    """
    if hp <= 0:
        raise ValueError("hp must be positive")
    entry = find_entry(character_id)
    if entry is None or entry.contact_damage <= 0 or entry.contact_damage_range is not None:
        raise ValueError("theory requires fixed positive participant contact damage")
    if BENCHMARK_ENTRY.max_hp != 500 or BENCHMARK_ENTRY.contact_damage_range != (1, 5):
        raise ValueError("theory requires the 500 HP, uniform 1..5 baseline")
    if character_id == "feixue" and not (entry.meters or entry.collision_rules or entry.sun_spirit
                                         or entry.reactions or getattr(entry, "frost_slash", None)):
        contacts = math.ceil(BENCHMARK_ENTRY.max_hp / entry.contact_damage)
        body_finish = True
    elif character_id == "amis":
        laser, burst = entry.meter("starlight"), entry.meter("starburst")
        if (laser is None or burst is None or laser.gain != 1 or burst.gain != 1
                or laser.slots <= 0 or burst.slots <= 0 or entry.collision_rules or entry.sun_spirit
                or entry.reactions or getattr(entry, "frost_slash", None)
                or laser.on != "contact" or burst.on != "laser"):
            raise ValueError("unsupported charge schedule")

        def total(n: int) -> int:
            return (n * entry.contact_damage + (n // laser.slots) * laser.damage
                    + (n // (laser.slots * burst.slots)) * burst.damage)

        contacts = 1
        while total(contacts) < BENCHMARK_ENTRY.max_hp:
            contacts += 1
        body_finish = total(contacts - 1) + entry.contact_damage >= BENCHMARK_ENTRY.max_hp
    else:
        raise ValueError("this character requires production-engine Monte Carlo sampling")
    win = _damage_cdf(contacts, hp)
    draw = _damage_cdf(contacts - 1, hp) - win if body_finish else Fraction(0)
    score = win + draw / 2
    return {"character_id": character_id, "hp": hp, "contacts_to_defeat_baseline": contacts,
            "body_finish": body_finish, "win_rate": float(win), "draw_rate": float(draw),
            "loss_rate": float(1-win-draw), "balance_score": float(score),
            "method": "Exact integer convolution of independent uniform 1..5 contact damage",
            "assumption": "Natural completion of the contact sequence, verified separately using DangoMatch"}
