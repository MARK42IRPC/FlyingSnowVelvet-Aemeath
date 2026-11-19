"""Bet selection and streak bookkeeping (pure Python)."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import BET_AMOUNTS, BET_MULTIPLIER

#: A brand-new save starts with the smallest chip in hand.
STARTING_CHIPS = BET_AMOUNTS[0]


@dataclass
class BetSlip:
    side: str = ""
    amount: int = 0

    @property
    def ready(self) -> bool:
        return self.side in ("left", "right") and self.amount in BET_AMOUNTS

    def choose_side(self, side: str) -> bool:
        if side not in ("left", "right"):
            return False
        self.side = side
        return True

    def choose_amount(self, amount: int) -> bool:
        try:
            value = int(amount)
        except (TypeError, ValueError):
            return False
        if value not in BET_AMOUNTS:
            return False
        self.amount = value
        return True

    def clear(self) -> None:
        self.side = ""
        self.amount = 0


@dataclass
class StreakState:
    current: int = 0
    best: int = 0
    rounds: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    chips: int = STARTING_CHIPS

    def settle(self, *, bet_side: str, winner: str, double_ko: bool, amount: int) -> bool:
        """Apply one settlement; returns True when the bet won."""
        self.rounds += 1
        if not bet_side or int(amount) not in BET_AMOUNTS:
            return False
        if double_ko or not winner:
            self.draws += 1
            return False
        won = bet_side == winner
        self.chips += int(amount) * BET_MULTIPLIER if won else -int(amount)
        if won:
            self.current += 1
            self.best = max(self.best, self.current)
            self.wins += 1
        else:
            self.current = 0
            self.losses += 1
        return won

    def as_record(self) -> dict[str, int]:
        return {
            "current_streak": int(self.current),
            "best_streak": int(self.best),
            "total_rounds": int(self.rounds),
            "total_bet_wins": int(self.wins),
            "total_bet_losses": int(self.losses),
            "total_draws": int(self.draws),
            "chips": int(self.chips),
        }

    @classmethod
    def from_record(cls, record: dict | None) -> "StreakState":
        if not isinstance(record, dict):
            return cls()
        def _int(key: str) -> int:
            try:
                return max(0, int(record.get(key, 0)))
            except (TypeError, ValueError, OverflowError):
                return 0

        def _chips(value: object) -> int:
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return STARTING_CHIPS
        current = _int("current_streak")
        return cls(
            current=current,
            best=max(current, _int("best_streak")),
            rounds=_int("total_rounds"),
            wins=_int("total_bet_wins"),
            losses=_int("total_bet_losses"),
            draws=_int("total_draws"),
            chips=_chips(record.get("chips")),
        )


def payout(amount: int, won: bool) -> int:
    amount = int(amount)
    return amount * BET_MULTIPLIER if won else -amount

