"""Dango roster and selection layout (pure Python).

Adding a dango is a data change: append a ``DangoEntry`` here and drop
``idle.png`` / ``attack.png`` / ``hurt.png`` under ``assets/dango/<id>/``.
Physics, the match engine and the select screen all read this pool, so no
fight-side code may hard-code a pair of fighters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SELECT_COLUMNS = 6
SELECT_TILE_SIZE = 68.0


class TriggerRule(Protocol):
    """A data-only rule; the opposing reaction resolves it, not the roster."""

    trigger: str


@dataclass(frozen=True, slots=True)
class ChargeSpec:
    """One filled meter: trigger, slot count, skill damage and overflow meter."""

    meter_id: str
    label: str
    slots: int
    on: str = "contact"
    gain: int = 1
    damage: int = 0
    fill_meter: str = ""


@dataclass(frozen=True, slots=True)
class ChanceSummon:
    """Independent chance per collision to cast a skill on the opposing dango."""

    trigger: str
    chance: float
    skill: str
    damage: int = 0
    # Inclusive damage spread; ``damage`` is the fixed value when unset.
    damage_range: tuple[int, int] | None = None

    def roll_damage(self, rng) -> int:
        if self.damage_range is None:
            return int(self.damage)
        low, high = sorted((int(self.damage_range[0]), int(self.damage_range[1])))
        return int(rng.randint(low, high))


@dataclass(frozen=True, slots=True)
class Metamorphosis:
    """After the owner loses any HP, use reaction_id on the next collision."""

    trigger: str = "damaged"
    reaction_id: str = ""


@dataclass(frozen=True, slots=True)
class SunSpiritSpec:
    """Shared random-colour energy, independently cooling delayed summons."""

    slots: int = 4
    cost: int = 2
    cooldown: float = 5.0
    delay: float = 1.0
    second_delay: float = 0.45
    radius: float = 0.162
    second_radius: float = 0.27
    damage: int = 28
    second_damage: int = 14
    wall_chance: float = 0.5
    overflow_cooldown_reduction: float = 1.0


@dataclass(frozen=True, slots=True)
class FrostSlashSpec:
    """Periodic circular slash and energy-backed ordinary contact damage."""

    interval: float = 3.0
    radius: float = 0.286
    damage: int = 30
    slow_seconds: float = 3.0
    slow_multiplier: float = 0.5
    energy_slots: int = 4
    contact_bonus: int = 2
    energy_per_contact: int = 1


@dataclass(frozen=True, slots=True)
class DangoEntry:
    """One selectable fighter: identity, stats and data-driven skill rules."""

    character_id: str
    name: str
    tag: str = ""
    accent: tuple[int, int, int] = (173, 140, 255)
    skill: str = ""
    max_hp: int = 100
    meters: tuple[ChargeSpec, ...] = ()
    reactions: tuple[TriggerRule, ...] = ()
    collision_rules: tuple[TriggerRule, ...] = ()
    sun_spirit: SunSpiritSpec | None = None
    contact_damage: int = 1
    contact_damage_range: tuple[int, int] | None = None
    frost_slash: FrostSlashSpec | None = None

    def meter(self, meter_id: str) -> ChargeSpec | None:
        for meter in self.meters:
            if meter.meter_id == meter_id:
                return meter
        return None


# Simulation-only opponent: discoverable by explicit identity, never offered
# by the public roster, random selection or asset loader.
BENCHMARK_ENTRY = DangoEntry(
    "benchmark", "基准团子", max_hp=500, contact_damage=3, contact_damage_range=(1, 5),
)


ROSTER: tuple[DangoEntry, ...] = (
    DangoEntry(
        "amis",
        "爱弥斯",
        "星辉团子",
        (255, 150, 196),
        "碰撞积攒星能，满 3 格发射激光；激光叠加星爆，满 4 格造成全屏伤害。",
        max_hp=180,
        meters=(
            ChargeSpec("starlight", "星能", 3, on="contact", gain=1, damage=8, fill_meter="starburst"),
            ChargeSpec("starburst", "星爆", 4, on="laser", gain=1, damage=60),
        ),
    ),
    DangoEntry(
        "daniya",
        "达妮娅",
        "虚质团子",
        (126, 200, 255),
        "每次碰撞有 50.5% 概率唤出虚空之手，隔空造成 1~4 点伤害。",
        max_hp=260,
        collision_rules=(ChanceSummon("any", 0.505, "void_hand", 3, damage_range=(1, 4)),),
    ),
    DangoEntry(
        "xigelika",
        "西格莉卡",
        "日灵团子",
        (255, 184, 112),
        skill="攻击积攒黄/青日能，撞墙有 50% 概率积攒；满槽再获能量时，双色冷却各减 1 秒。同色两枚召唤日灵，1 秒后爆炸 28；青灵再扩圈爆炸 14。双色各冷却 5 秒。",
        max_hp=185,
        sun_spirit=SunSpiritSpec(),
    ),
    DangoEntry(
        "feixue",
        "绯雪",
        "霜渐团子",
        (188, 216, 237),
        skill="每 3 秒环身斩，范围命中造成 30 伤害并减速 50% 持续 3 秒；落空积攒霜渐，最多 4 格，每次碰撞消耗 1 格附加 2 点伤害。",
        max_hp=165,
        frost_slash=FrostSlashSpec(),
    ),
)


def find_entry(character_id: str) -> DangoEntry | None:
    for entry in ROSTER:
        if entry.character_id == character_id:
            return entry
    if character_id == BENCHMARK_ENTRY.character_id:
        return BENCHMARK_ENTRY
    return None


def entry_at(index: int) -> DangoEntry:
    return ROSTER[int(index) % len(ROSTER)]


def selection_rows(count: int | None = None) -> int:
    """Number of square-tile rows needed for the roster (at least one)."""
    available = len(ROSTER) if count is None else max(0, min(len(ROSTER), int(count)))
    return max(1, (available + SELECT_COLUMNS - 1) // SELECT_COLUMNS)


__all__ = [
    "ChanceSummon",
    "ChargeSpec",
    "DangoEntry",
    "BENCHMARK_ENTRY",
    "Metamorphosis",
    "SunSpiritSpec",
    "FrostSlashSpec",
    "TriggerRule",
    "ROSTER",
    "SELECT_COLUMNS",
    "SELECT_TILE_SIZE",
    "entry_at",
    "find_entry",
    "selection_rows",
]
