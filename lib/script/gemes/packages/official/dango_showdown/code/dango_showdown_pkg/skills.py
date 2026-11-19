from __future__ import annotations

import math
import random
import inspect
from typing import Callable

from . import events as ev
from . import geometry
from .model import Dango, PendingSunSpirit
from .roster import ChanceSummon, ChargeSpec, DangoEntry, find_entry

# ``defer_visual`` asks the sink to let the presenter time the number.
DamageSink = Callable[..., int]
DirectionSink = Callable[[Dango, Dango, float], None]
SlowSink = Callable[[Dango, float, float], None]
# The GIF plays at 2x: the mech landing at 1.82 s of the 2 s loop becomes
# 0.91 s on screen and the whole clip finishes inside one second.
STARBURST_IMPACT_MS = 910
STARBURST_TOTAL_MS = 1000

def frost_energy_changed(owner: Dango, bus: ev.ExtensionBus, *, source: str) -> None:
    spec = _entry(owner).frost_slash
    if spec is None:
        return
    bus.skill(ev.CHARGE_CHANGED, ev.ChargeChangedPayload(
        side=owner.side, meter_id="frost_energy", label="霜渐",
        value=owner.frost_energy, slots=spec.energy_slots, source=source,
    ))


def advance_frost_slash(
    owner: Dango, target: Dango, seconds: float, bus: ev.ExtensionBus,
    damage: DamageSink, slow: SlowSink,
) -> None:
    """Cast on the fixed simulation clock; each miss stores one frost charge."""
    spec = _entry(owner).frost_slash
    if spec is None or spec.interval <= 0.0 or seconds <= 0.0 or not owner.alive or not target.alive:
        return
    owner.frost_elapsed += seconds
    while owner.frost_elapsed + 1e-9 >= spec.interval and owner.alive and target.alive:
        owner.frost_elapsed = max(0.0, owner.frost_elapsed - spec.interval)
        bind_scene(damage, owner, target)
        point = (float(owner.x), float(owner.y))
        hit = math.hypot(target.x - owner.x, target.y - owner.y) <= spec.radius + target.radius + 1e-9
        removed = 0
        if hit:
            _, removed = snapshot_scene(damage, owner, target, spec.damage, skill="frost_slash")
            if target.alive:
                slow(target, spec.slow_seconds, spec.slow_multiplier)
        else:
            before = owner.frost_energy
            owner.frost_energy = min(spec.energy_slots, before + 1)
            if owner.frost_energy != before:
                frost_energy_changed(owner, bus, source="frost_slash")
        bus.skill(ev.FROST_SLASH, ev.FrostSlashPayload(
            side=owner.side, target=target.side, point=point, radius=spec.radius,
            hit=hit, damage=removed, slow_seconds=spec.slow_seconds,
            slow_multiplier=spec.slow_multiplier, energy=owner.frost_energy, cooldown=spec.interval,
            hp_left=_side_hp(owner, target, "left"), hp_right=_side_hp(owner, target, "right"),
        ))


def charge_from_contact(
    owner: Dango,
    other: Dango,
    amount: int,
    bus: ev.ExtensionBus,
    damage: DamageSink,
    deflect: DirectionSink,
) -> None:
    """Feed every meter configured to react to a ball contact."""
    if amount <= 0:
        return
    for spec in _entry(owner).meters:
        if spec.on == "contact":
            feed(
                owner,
                other,
                spec,
                spec.gain * max(1, int(amount)),
                bus,
                source="contact",
                damage=damage,
                deflect=deflect,
            )
    if _entry(owner).sun_spirit is not None:
        rng = getattr(getattr(damage, "__self__", None), "rng", random)
        for _ in range(int(amount)):
            gain_sun_energy(owner, other, bus, rng, damage)


def gain_sun_energy(
    owner: Dango, target: Dango, bus: ev.ExtensionBus, rng, damage: DamageSink,
    *, source: str = "contact",
) -> None:
    """Add a random colour, or turn full-slot overflow into cooldown recovery."""
    spec = _entry(owner).sun_spirit
    if spec is None or not owner.alive or not target.alive:
        return
    if len(owner.sun_energy) < spec.slots:
        owner.sun_energy.append("yellow" if rng.random() < 0.5 else "cyan")
        _sun_charge_changed(owner, bus, source=source)
    else:
        # Recover both existing timers before checking either summon. A
        # cooldown opened below must keep its full five seconds this time.
        for color in ("yellow", "cyan"):
            remaining = owner.sun_cooldowns.get(color, 0.0)
            if remaining > 0.0:
                owner.sun_cooldowns[color] = max(0.0, remaining - spec.overflow_cooldown_reduction)
    _summon_ready_sun_spirits(owner, target, bus, damage)


def charge_from_wall(owner: Dango, target: Dango, bus: ev.ExtensionBus, rng, damage: DamageSink) -> None:
    """Roll once per real wall contact, then independently choose its colour."""
    spec = _entry(owner).sun_spirit
    if spec is None or not owner.alive or not target.alive:
        return
    if rng.random() < spec.wall_chance:
        gain_sun_energy(owner, target, bus, rng, damage, source="wall")


def _sun_charge_changed(owner: Dango, bus: ev.ExtensionBus, *, source: str) -> None:
    spec = _entry(owner).sun_spirit
    bus.skill(ev.CHARGE_CHANGED, ev.ChargeChangedPayload(
        side=owner.side, meter_id="sun_energy", label="日能", value=len(owner.sun_energy),
        slots=spec.slots, source=source, colors=tuple(owner.sun_energy),
    ))


def _summon_ready_sun_spirits(owner: Dango, target: Dango, bus: ev.ExtensionBus, damage: DamageSink) -> None:
    spec = _entry(owner).sun_spirit
    if spec is None or not owner.alive or not target.alive:
        return
    for color in ("yellow", "cyan"):
        if owner.sun_cooldowns.get(color, 0.0) > 1e-9 or owner.sun_energy.count(color) < spec.cost:
            continue
        # Keep the order of unused colours: the UI displays this shared queue.
        for _ in range(spec.cost):
            owner.sun_energy.remove(color)
        owner.sun_cooldowns[color] = spec.cooldown
        owner.sun_summon_count += 1
        bind_scene(damage, owner, target)
        spirit = PendingSunSpirit(
            summon_id=f"{owner.side}:{owner.sun_summon_count}", color=color,
            point=(float(target.x), float(target.y)),
        )
        owner.sun_spirits.append(spirit)
        _sun_charge_changed(owner, bus, source="sun_spirit")
        bus.skill(ev.SUN_SPIRIT_SUMMON, ev.SunSpiritSummonPayload(
            side=owner.side, target=target.side, color=color, point=spirit.point,
            radius=spec.radius, delay=spec.delay, second_delay=spec.second_delay,
            second_radius=spec.second_radius, summon_id=spirit.summon_id,
        ))


def advance_sun_spirits(
    owner: Dango, target: Dango, seconds: float, bus: ev.ExtensionBus,
    damage: DamageSink, deflect: DirectionSink,
) -> None:
    """Advance cooldowns and fixed-point explosions on the simulation clock."""
    spec = _entry(owner).sun_spirit
    if spec is None or not owner.alive or not target.alive or seconds <= 0.0:
        return
    for color in ("yellow", "cyan"):
        remaining = max(0.0, owner.sun_cooldowns.get(color, 0.0) - seconds)
        owner.sun_cooldowns[color] = 0.0 if remaining < 1e-9 else remaining
    pending = []
    for spirit in owner.sun_spirits:
        if not owner.alive or not target.alive:
            break
        spirit.elapsed += seconds
        if spirit.stage == 0 and spirit.elapsed + 1e-9 >= spec.delay:
            _burst_sun_spirit(owner, target, spirit, 1, bus, damage, deflect)
            spirit.stage = 1
        if (spirit.color == "cyan" and spirit.stage == 1
                and spirit.elapsed + 1e-9 >= spec.delay + spec.second_delay
                and owner.alive and target.alive):
            _burst_sun_spirit(owner, target, spirit, 2, bus, damage, deflect)
            spirit.stage = 2
        if spirit.stage == 0 or (spirit.color == "cyan" and spirit.stage == 1):
            pending.append(spirit)
    owner.sun_spirits = pending if owner.alive and target.alive else []
    # Stored pairs wait through cooldown, then cast immediately without a new hit.
    # New summons start at zero elapsed and never receive this step twice.
    _summon_ready_sun_spirits(owner, target, bus, damage)


def _burst_sun_spirit(
    owner: Dango, target: Dango, spirit: PendingSunSpirit, stage: int,
    bus: ev.ExtensionBus, damage: DamageSink, deflect: DirectionSink,
) -> None:
    spec = _entry(owner).sun_spirit
    radius = spec.radius if stage == 1 else spec.second_radius
    amount = spec.damage if stage == 1 else spec.second_damage
    bind_scene(damage, owner, target)
    hit = math.hypot(target.x - spirit.point[0], target.y - spirit.point[1]) <= radius + target.radius
    removed = 0
    if hit:
        _, removed = snapshot_scene(damage, owner, target, amount, skill=f"sun_spirit_{spirit.color}")
        if removed > 0:
            deflect(target, owner, _away_from(owner, target))
    # Misses still explode visually at the original summon point.
    bus.skill(ev.SUN_SPIRIT_BURST, ev.SunSpiritBurstPayload(
        side=owner.side, target=target.side, color=spirit.color, point=spirit.point,
        radius=radius, stage=stage, damage=removed, hit=hit, summon_id=spirit.summon_id,
        hp_left=_side_hp(owner, target, "left"), hp_right=_side_hp(owner, target, "right"),
    ))


def feed(
    owner: Dango,
    other: Dango,
    spec: ChargeSpec,
    amount: int,
    bus: ev.ExtensionBus,
    *,
    source: str,
    damage: DamageSink,
    deflect: DirectionSink,
) -> None:
    """Add charge; when the meter fills, cast and clear it."""
    if owner.hp <= 0 or other.hp <= 0 or spec.slots <= 0:
        return
    before = min(spec.slots, owner.charge(spec.meter_id))
    after = min(spec.slots, before + max(0, int(amount)))
    owner.charges[spec.meter_id] = after
    if after != before:
        bus.skill(ev.CHARGE_CHANGED, ev.ChargeChangedPayload(
            side=owner.side,
            meter_id=spec.meter_id,
            label=spec.label,
            value=after,
            slots=spec.slots,
            source=source,
        ))
    if after < spec.slots:
        return
    owner.charges[spec.meter_id] = 0
    bind_scene(damage, owner, other)
    cast(owner, other, spec, bus, damage=damage, deflect=deflect)


def cast(
    owner: Dango,
    target: Dango,
    spec: ChargeSpec,
    bus: ev.ExtensionBus,
    *,
    damage: DamageSink,
    deflect: DirectionSink,
) -> None:
    if spec.meter_id == "starlight":
        # Capture the target BEFORE damage: the engine syncs the dango to
        # the live balls, and the beam/impact must use that same body centre.
        paint_point = (float(target.x), float(target.y))
        scene, removed = snapshot_scene(damage, owner, target, spec.damage, skill=spec.meter_id)
        if removed <= 0:
            return
        paint_point = scene.get("target", paint_point)
        match = getattr(damage, "__self__", None)
        rng = getattr(match, "rng", random)
        angle = rng.random() * math.tau
        start, end = geometry.beam_through_points(*scene.get("owner", (owner.x, owner.y)), *scene.get("target", paint_point), angle)
        deflect(target, owner, -1.0 if owner.side == "left" else 1.0)
        bus.skill(ev.LASER_FIRE, ev.LaserFirePayload(
            side=owner.side,
            target=target.side,
            damage=removed,
            hp_left=_side_hp(owner, target, "left"),
            hp_right=_side_hp(owner, target, "right"),
            start=start,
            end=end,
            angle=angle,
            point=paint_point,
        ))
        for follow in _entry(owner).meters:
            if follow.on == "laser":
                if owner.hp <= 0 or target.hp <= 0:
                    return
                before = min(follow.slots, owner.charge(follow.meter_id))
                after = min(follow.slots, before + follow.gain)
                owner.charges[follow.meter_id] = after
                if after != before:
                    bus.skill(ev.CHARGE_CHANGED, ev.ChargeChangedPayload(
                        side=owner.side,
                        meter_id=follow.meter_id,
                        label=follow.label,
                        value=after,
                        slots=follow.slots,
                        source="laser",
                    ))
                if after >= follow.slots:
                    owner.charges[follow.meter_id] = 0
                    cast(owner, target, follow, bus, damage=damage, deflect=deflect)
    elif spec.meter_id == "starburst":
        if owner.hp <= 0 or target.hp <= 0:
            return
        deflect(target, owner, -1.0 if owner.side == "left" else 1.0)
        # Damage settles immediately so the match can end here; the widget
        # shows the number and the impact FX together on the landing frame.
        # Only the starburst knocks the victim to the arena floor. ``reposition``
        # is opt-in so the single damage sink keeps its old three-arg shape.
        paint_point = (float(target.x), float(target.y))
        scene, removed = snapshot_scene(
            damage, owner, target, spec.damage, defer_visual=True, reposition=True, skill=spec.meter_id
        )
        if removed <= 0:
            return
        paint_point = scene.get("target", paint_point)
        bus.skill(ev.STARBURST_FIRE, ev.StarburstFirePayload(
            side=owner.side,
            target=target.side,
            damage=removed,
            hp_left=_side_hp(owner, target, "left"),
            hp_right=_side_hp(owner, target, "right"),
            impact_ms=STARBURST_IMPACT_MS,
            total_ms=STARBURST_TOTAL_MS,
            point=paint_point,
        ))

def bind_scene(damage: DamageSink, owner: Dango, target: Dango) -> None:
    """Pull live physics centres before a skill builds its payload.

    The sink belongs to the match; use its live balls. Tests replace
    ``match.left`` / ``match.right``, so the match-installed callback is not
    always there. Callers pass the current pair, which is authoritative.
    """
    scene_sync = getattr(damage, "__self__", None)
    method_sync = getattr(scene_sync, "_sync_scene", None)
    if callable(method_sync):
        method_sync(owner, target)
        return
    for dango in (owner, target):
        sync = getattr(dango, "_sync_scene", None)
        if callable(sync):
            sync()


def snapshot_scene(
    sink: DamageSink,
    owner: Dango,
    target: Dango,
    amount: int,
    *,
    defer_visual: bool = False,
    reposition: bool = False,
    skill: str = "",
) -> tuple[dict[str, tuple[float, float]], int]:
    """Apply damage and return the body centres captured around it.

    The sink may snap the rules objects to the live physics bodies. The
    renderer has to anchor on exactly those coordinates, so the sink reports
    them as a ``(removed, owner_point, target_point)`` triple for moving
    skills, or publishes them on the damage event. Test sinks that return a
    bare integer still get the values from the dango objects.
    """
    # Pull the live physics centres in before reading anything: callers can
    # hold a fresh dango object (tests, roster swaps) whose x/y are still the
    # spawn default. Without this the beam fired from 0.5/0.5, i.e. the window
    # centre, and the hand landed nowhere near the enemy.
    scene_sync = getattr(owner, "_sync_scene", None)
    if callable(scene_sync):
        scene_sync()
    scene_sync = getattr(target, "_sync_scene", None)
    if callable(scene_sync):
        scene_sync()
    owner_point = (float(owner.x), float(owner.y))
    target_point = (float(target.x), float(target.y))
    options = {"defer_visual": defer_visual, "reposition": reposition, "skill": skill}
    try:
        parameters = inspect.signature(sink).parameters
    except (TypeError, ValueError):
        options = {}
    else:
        # Legacy three-argument sinks (and sinks supporting only earlier
        # flags) remain valid. Inspect before calling: a TypeError inside a
        # sink must never cause a second application of the same damage.
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            options = {key: value for key, value in options.items()
                       if key in parameters and parameters[key].kind in
                       (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}
    result = sink(owner, target, amount, **options)
    scene = {"owner": owner_point, "target": target_point}
    if isinstance(result, tuple) and result and isinstance(result[0], int):
        removed = int(result[0])
        if len(result) >= 3:
            scene["owner"] = (float(result[1][0]), float(result[1][1]))
            scene["target"] = (float(result[2][0]), float(result[2][1]))
    else:
        removed = int(result or 0)
        if not reposition:
            scene["owner"] = (float(owner.x), float(owner.y))
            scene["target"] = (float(target.x), float(target.y))
    return scene, removed


def roll_collisions(
    owner: Dango,
    other: Dango,
    trigger: str,
    bus: ev.ExtensionBus,
    rng,
    damage: DamageSink,
    deflect: DirectionSink,
) -> int:
    """Roll each chance skill on one collision; returns the number that fired."""
    if owner.hp <= 0 or other.hp <= 0:
        return 0
    fired = 0
    for rule in _entry(owner).collision_rules:
        if not isinstance(rule, ChanceSummon):
            continue
        if rule.trigger not in ("any", trigger):
            continue
        if rng.random() >= rule.chance:
            continue
        bind_scene(damage, owner, other)
        amount = rule.roll_damage(rng) if hasattr(rule, "roll_damage") else int(rule.damage)
        scene, removed = snapshot_scene(damage, owner, other, amount, skill=rule.skill)
        if removed <= 0:
            continue
        fired += 1
        deflect(other, owner, _away_from(owner, other))
        bus.skill(ev.VOID_HAND, ev.VoidHandPayload(
            side=owner.side,
            target=other.side,
            source=trigger,
            damage=removed,
            hp_left=_side_hp(owner, other, "left"),
            hp_right=_side_hp(owner, other, "right"),
            point=scene.get("target", (float(other.x), float(other.y))),
        ))
    return fired


def _side_hp(owner: Dango, target: Dango, side: str) -> int:
    return owner.hp if owner.side == side else target.hp


def _away_from(owner: Dango, target: Dango) -> float:
    """Turn the hurt target away from the owner; preserves flight speed."""
    owner_x = getattr(owner, "x", None)
    target_x = getattr(target, "x", None)
    if isinstance(owner_x, (int, float)) and isinstance(target_x, (int, float)):
        delta = float(target_x) - float(owner_x)
        if abs(delta) > 1e-6:
            return 1.0 if delta > 0.0 else -1.0
    return -1.0 if owner.side == "left" else 1.0


def _entry(dango: Dango) -> DangoEntry:
    return find_entry(dango.character_id) or DangoEntry(dango.character_id, dango.name)


__all__ = [
    "DamageSink",
    "DirectionSink",
    "SlowSink",
    "advance_frost_slash",
    "frost_energy_changed",
    "cast",
    "charge_from_contact",
    "feed",
    "bind_scene",
    "gain_sun_energy",
    "charge_from_wall",
    "advance_sun_spirits",
    "roll_collisions",
    "snapshot_scene",
]
