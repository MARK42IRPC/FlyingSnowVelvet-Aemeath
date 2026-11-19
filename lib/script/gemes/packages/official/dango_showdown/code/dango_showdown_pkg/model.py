"""Match state, betting and streak bookkeeping (no GUI toolkit)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .constants import (
    BASE_RADIUS,
    BET_AMOUNTS,
    BET_MULTIPLIER,
    BET_SIDES,
    HURT_FLASH_SECONDS,
    IDLE,
    MAX_HP,
)
from .roster import ChargeSpec, find_entry


SIDE_LEFT = "left"
SIDE_RIGHT = "right"


class MatchPhase(Enum):
    SELECT = "select"
    FIGHT = "fight"
    RESULT = "result"


@dataclass
class PendingSunSpirit:
    """A fixed arena point; its clock advances only in simulation steps."""

    summon_id: str
    color: str
    point: tuple[float, float]
    elapsed: float = 0.0
    stage: int = 0


@dataclass
class Dango:
    side: str
    character_id: str
    name: str
    hp: int | None = None
    max_hp: int = MAX_HP
    meters: tuple = ()
    radius: float = BASE_RADIUS
    hits_taken: int = 0
    hits_dealt: int = 0
    state: str = IDLE
    state_seconds: float = 0.0
    stretch_phase: float = 0.0
    hurt_flash_seconds: float = 0.0
    # 0..1: how long the hurt pose keeps the body flattened after the flash.
    hurt_flatten: float = 0.0
    # Set when the round ends: this dango keeps its hurt picture and stays put.
    knocked_out: bool = False
    charges: dict[str, int] = field(default_factory=dict)
    x: float = 0.5
    y: float = 0.5
    contact_x: float = 0.5
    contact_y: float = 0.5
    # Engine hook that pulls the live ball centre onto this object; a fresh
    # test instance has no engine, so it is a no-op until bound.
    _sync_scene: object = None
    sun_energy: list[str] = field(default_factory=list)
    sun_cooldowns: dict[str, float] = field(default_factory=lambda: {"yellow": 0.0, "cyan": 0.0})
    sun_spirits: list[PendingSunSpirit] = field(default_factory=list)
    sun_summon_count: int = 0
    contact_damage: int = 1
    contact_damage_range: tuple[int, int] | None = None
    frost_elapsed: float = 0.0
    frost_energy: int = 0
    slow_seconds: float = 0.0
    slow_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_hp <= 0:
            entry = find_entry(self.character_id)
            self.max_hp = entry.max_hp if entry is not None else MAX_HP
        if self.hp is None:
            self.hp = self.max_hp
        for spec in self.meters:
            self.charges.setdefault(spec.meter_id, 0)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def hp_ratio(self) -> float:
        return max(0.0, min(1.0, self.hp / float(max(1, self.max_hp))))

    def meter(self, meter_id: str) -> ChargeSpec | None:
        for spec in self.meters:
            if spec.meter_id == meter_id:
                return spec
        return None

    def charge(self, meter_id: str) -> int:
        if meter_id == "sun_energy":
            return len(self.sun_energy)
        if meter_id == "frost_energy":
            return self.frost_energy
        return int(self.charges.get(meter_id, 0))

    def reset_hp(self) -> None:
        self.hp = self.max_hp
        self.hits_taken = 0
        self.hits_dealt = 0
        self.state = IDLE
        self.state_seconds = 0.0
        self.hurt_flash_seconds = 0.0
        self.hurt_flatten = 0.0
        self.knocked_out = False
        self.charges = {spec.meter_id: 0 for spec in self.meters}
        self.sun_energy.clear()
        self.sun_cooldowns = {"yellow": 0.0, "cyan": 0.0}
        self.sun_spirits.clear()
        self.sun_summon_count = 0
        self.frost_elapsed = 0.0
        self.frost_energy = 0
        self.slow_seconds = 0.0
        self.slow_multiplier = 1.0

    def resolve_attack(self) -> None:
        # Every touch makes the attacking dango lunge; a hurt dango is already
        # deforming, so its hurt pose wins until the flash ends.
        if self.state == "hurt":
            return
        self.state = "attack"
        self.state_seconds = 0.0

    def resolve_hurt(self) -> None:
        self.state = "hurt"
        self.state_seconds = 0.0
        self.hurt_flash_seconds = HURT_FLASH_SECONDS
        self.hurt_flatten = 1.0

    def sync_to(self, x: float, y: float) -> None:
        """Snap the rules position to the live physics body."""
        self.x = float(x)
        self.y = float(y)

    def advance_state(self, seconds: float) -> None:
        if self.knocked_out:
            # The bout is over: no breathing, no return to idle.
            return
        self.state_seconds += max(0.0, float(seconds))
        self.stretch_phase += max(0.0, float(seconds))
        elapsed = max(0.0, float(seconds))
        self.hurt_flash_seconds = max(0.0, self.hurt_flash_seconds - elapsed)
        total = HURT_FLASH_SECONDS * (0.66 if self.hurt_flatten > 0.0 else 1.0)
        if self.hurt_flatten > 0.0:
            self.hurt_flatten = max(0.0, self.hurt_flatten - elapsed / max(1e-3, total))
        if self.state == IDLE:
            return
        from .constants import ATTACK_SECONDS, HURT_SECONDS

        limit = ATTACK_SECONDS if self.state == "attack" else HURT_SECONDS
        if self.state_seconds >= limit:
            self.state = IDLE
            self.state_seconds = 0.0


@dataclass
class StatsSnapshot:
    left_hits: int = 0
    right_hits: int = 0
    left_hp: int = MAX_HP
    right_hp: int = MAX_HP
    duration_seconds: float = 0.0
    winner: str = ""
    loser: str = ""
    double_ko: bool = False

    @property
    def total_hits(self) -> int:
        return self.left_hits + self.right_hits


@dataclass
class RoundResult:
    phase: MatchPhase = MatchPhase.SELECT
    winner: str = ""
    bet_side: str = ""
    bet_amount: int = 0
    bet_won: bool = False
    streak: int = 0
    best_streak: int = 0
    double_ko: bool = False
    draw: bool = False
    chips: int = 0
    # Series context for the settlement card and 3 s auto-advance.
    series_left: int = 0
    series_right: int = 0
    series_draws: int = 0
    series_round: int = 0
    series_label: str = ""
    series_over: bool = False
    series_winner: str = ""
    stats: StatsSnapshot = field(default_factory=StatsSnapshot)

    def apply_bet(
        self,
        *,
        bet_side: str,
        bet_amount: int,
        streak: int,
        best_streak: int,
    ) -> tuple[int, int]:
        """Apply the bet outcome and return ``(streak, best_streak)``."""
        self.bet_side = bet_side if bet_side in BET_SIDES else ""
        self.bet_amount = int(bet_amount) if int(bet_amount) in BET_AMOUNTS else 0
        streak = max(0, int(streak))
        best_streak = max(streak, int(best_streak))
        if not self.bet_side or self.bet_amount <= 0:
            self.bet_won = False
            return streak, best_streak
        if self.double_ko:
            # 押注规则：押中 / 押错 / 平局。平局既不算赢也不算输，连押保持不变。
            self.bet_won = False
            self.draw = True
            return streak, best_streak
        self.bet_won = self.bet_side == self.winner
        if self.bet_won:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
        return streak, best_streak

    @property
    def payout(self) -> int:
        if self.draw or self.double_ko:
            return 0
        return int(self.bet_amount) * BET_MULTIPLIER if self.bet_won else -int(self.bet_amount)


