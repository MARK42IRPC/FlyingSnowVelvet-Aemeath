"""Package-local event names, payloads and extension hooks (pure Python)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

PREFIX = "dango_showdown"
MATCH_START = f"{PREFIX}.match_start"
HIT = f"{PREFIX}.hit"
KO = f"{PREFIX}.ko"
BET_SETTLED = f"{PREFIX}.bet_settled"
MATCH_END = f"{PREFIX}.match_end"
CHARGE_CHANGED = f"{PREFIX}.charge_changed"
LASER_FIRE = f"{PREFIX}.laser_fire"
STARBURST_FIRE = f"{PREFIX}.starburst_fire"
VOID_HAND = f"{PREFIX}.void_hand"
SUN_SPIRIT_SUMMON = f"{PREFIX}.sun_spirit_summon"
SUN_SPIRIT_BURST = f"{PREFIX}.sun_spirit_burst"
FROST_SLASH = f"{PREFIX}.frost_slash"
SKILL_DAMAGE = f"{PREFIX}.skill_damage"

COMMAND = f"{PREFIX}.command"

VOICE_CUES = ("match_start", "hit", "ko", "win", "lose", "streak")
EXTERNAL_COMMANDS = ("start_match", "pick_side", "pick_amount", "next_round", "close")

CONTACT_POINT = "contact_point"
SKILL_CHARGE = "skill_charge"
LASER_BURST = "laser_burst"
FULLSCREEN_BURST = "fullscreen_burst"
VOID_HAND_RIFT = "void_hand_rift"
KO_FLASH = "ko_flash"
STREAK_BURST = "streak_burst"


@dataclass(frozen=True, slots=True)
class MatchStartPayload:
    round: int
    left_id: str
    right_id: str
    bet_side: str
    bet_amount: int
    left_hp: int = 0
    right_hp: int = 0


@dataclass(frozen=True, slots=True)
class HitPayload:
    side: str
    speed: float
    hp_left: int
    hp_right: int
    point: tuple[float, float]
    normal: tuple[float, float]
    damage_left: int = 1
    damage_right: int = 1


@dataclass(frozen=True, slots=True)
class KoPayload:
    winner: str
    loser: str
    hp_left: int
    hp_right: int
    hits: int
    draw: bool = False


@dataclass(frozen=True, slots=True)
class BetSettledPayload:
    bet_side: str
    winner: str
    hit: bool
    streak: int
    best_streak: int
    draw: bool = False


@dataclass(frozen=True, slots=True)
class MatchEndPayload:
    winner: str
    duration_ms: int
    stats: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChargeChangedPayload:
    side: str
    meter_id: str
    label: str
    value: int
    slots: int
    source: str
    colors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SunSpiritSummonPayload:
    side: str
    target: str
    color: str
    point: tuple[float, float]
    radius: float
    delay: float
    second_delay: float
    second_radius: float
    summon_id: str


@dataclass(frozen=True, slots=True)
class SunSpiritBurstPayload:
    side: str
    target: str
    color: str
    point: tuple[float, float]
    radius: float
    stage: int
    damage: int
    hit: bool
    summon_id: str
    hp_left: int
    hp_right: int


@dataclass(frozen=True, slots=True)
class FrostSlashPayload:
    side: str
    target: str
    point: tuple[float, float]
    radius: float
    hit: bool
    damage: int
    slow_seconds: float
    slow_multiplier: float
    energy: int
    cooldown: float
    hp_left: int
    hp_right: int


@dataclass(frozen=True, slots=True)
class LaserFirePayload:
    side: str
    target: str
    damage: int
    hp_left: int
    hp_right: int
    start: tuple[float, float]
    end: tuple[float, float]
    angle: float = 0.0
    point: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class StarburstFirePayload:
    side: str
    target: str
    damage: int
    hp_left: int
    hp_right: int
    impact_ms: int = 0
    total_ms: int = 0
    point: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class VoidHandPayload:
    side: str
    target: str
    source: str
    damage: int
    hp_left: int
    hp_right: int
    point: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SkillDamagePayload:
    side: str
    target: str
    skill: str
    damage: int
    hp_left: int
    hp_right: int
    direction: tuple[float, float]
    defer_visual: bool = False
    point: tuple[float, float] | None = None



class SkillHook(Protocol):
    def on_match_start(self, match: object) -> None: ...

    def on_hit(self, match: object, event: HitPayload) -> None: ...

    def on_low_hp(self, match: object, side: str) -> None: ...

    def on_match_end(self, match: object, result: object) -> None: ...


class NullSkillHook:
    def on_match_start(self, match: object) -> None:
        return None

    def on_hit(self, match: object, event: HitPayload) -> None:
        return None

    def on_low_hp(self, match: object, side: str) -> None:
        return None

    def on_match_end(self, match: object, result: object) -> None:
        return None


class ExtensionBus:
    """Collects skill hooks and forwards pure-Python payloads to observers.

    The publisher callback is injected by the widget so this module never
    needs the global event center; tests can collect payloads instead.
    """

    def __init__(self, publish: Callable[[str, dict], None] | None = None) -> None:
        self._publish = publish or (lambda name, payload: None)
        self._hooks: list = []
        self._low_hp_sent: dict[str, bool] = {}

    def add_hook(self, hook: object) -> None:
        if hook is not None and hook not in self._hooks:
            self._hooks.append(hook)

    def reset(self) -> None:
        self._low_hp_sent = {"left": False, "right": False}

    def emit(self, name: str, payload: object) -> None:
        data = asdict(payload) if hasattr(payload, "__dataclass_fields__") else dict(payload or {})
        data["name"] = name
        try:
            self._publish(name, data)
        except Exception:
            pass

    def match_start(self, payload: MatchStartPayload, match: object) -> None:
        self.reset()
        self.emit(MATCH_START, payload)
        for hook in self._hooks:
            _safe(hook, "on_match_start", match)

    def hit(self, payload: HitPayload, match: object, hp_ratio_left: float, hp_ratio_right: float) -> None:
        self.emit(HIT, payload)
        for hook in self._hooks:
            _safe(hook, "on_hit", match, payload)
        self.check_low_hp(match, hp_ratio_left, hp_ratio_right)

    def check_low_hp(self, match: object, hp_ratio_left: float, hp_ratio_right: float) -> None:
        """Notify each side once per round after any source of HP damage."""
        for side, ratio in (("left", hp_ratio_left), ("right", hp_ratio_right)):
            if ratio > 0.25 or self._low_hp_sent.get(side):
                continue
            self._low_hp_sent[side] = True
            for hook in self._hooks:
                _safe(hook, "on_low_hp", match, side)

    def ko(self, payload: KoPayload) -> None:
        self.emit(KO, payload)

    def bet_settled(self, payload: BetSettledPayload) -> None:
        self.emit(BET_SETTLED, payload)

    def match_end(self, payload: MatchEndPayload, match: object, result: object) -> None:
        self.emit(MATCH_END, payload)
        for hook in self._hooks:
            _safe(hook, "on_match_end", match, result)

    def skill(self, name: str, payload: object) -> None:
        self.emit(name, payload)


def _safe(hook: object, attr: str, *args: object) -> None:
    callback = getattr(hook, attr, None)
    if not callable(callback):
        return
    try:
        callback(*args)
    except Exception:
        # A broken extension must never take the match down.
        pass

