"""Series (match format) bookkeeping for Dango Showdown (pure Python).

A *series* is the best-of format chosen on the select screen. Each round is
played as before; the series tracks how many rounds each dango has won and
decides when the match is over. "死战三局" plays every scheduled round and is
won by the side with more round wins.
"""

from __future__ import annotations

from dataclasses import dataclass

SERIES_IDS = ("bo5", "bo3", "bo1")


@dataclass(frozen=True, slots=True)
class MatchFormat:
    format_id: str
    label: str
    short: str
    max_rounds: int
    wins_needed: int
    dead_rubber: bool = False

    @property
    def rule_text(self) -> str:
        if self.dead_rubber:
            return f"{self.max_rounds} 轮后总胜场多者获胜"
        if self.max_rounds == 1:
            return "一轮定胜负"
        return f"先赢 {self.wins_needed} 轮"


FORMATS: tuple[MatchFormat, ...] = (
    MatchFormat("bo5", "五轮三胜", "BO5", 5, 3),
    MatchFormat("bo3", "三局两胜", "BO3", 3, 2),
    MatchFormat("bo1", "一决死战", "BO1", 1, 1),
    MatchFormat("ft3", "死战三局", "FT3", 3, 3, True),
)
BY_ID = {item.format_id: item for item in FORMATS}
DEFAULT_FORMAT = "bo5"
# Shown by the select console before the first round picks a format.
DEFAULT_FORMAT_LABEL = BY_ID[DEFAULT_FORMAT].label
# Pseudo-format for a finished series: no further round is allowed.
FINISHED_FORMAT = MatchFormat("done", "系列赛结束", "END", 0, 0)


def get_format(format_id: str | None) -> MatchFormat:
    return BY_ID.get(str(format_id or ""), BY_ID[DEFAULT_FORMAT])


def other_side(side: str) -> str:
    return "right" if side == "left" else "left"


def winner_of(left_wins: int, right_wins: int) -> str:
    if int(left_wins) > int(right_wins):
        return "left"
    if int(right_wins) > int(left_wins):
        return "right"
    return ""


@dataclass
class SeriesState:
    """Round wins for the current series (never persisted)."""

    format: MatchFormat = BY_ID[DEFAULT_FORMAT]
    left: int = 0
    right: int = 0
    draws: int = 0
    played: int = 0
    over: bool = False

    def reset(self, fmt: MatchFormat | None = None) -> None:
        if fmt is not None:
            self.format = fmt
        self.left = self.right = self.draws = self.played = 0
        self.over = False

    def record_round(self, winner: str) -> str:
        """Add one round result; returns the series winner, or "" if undecided.

        A double KO adds a draw and does not advance either side's score.
        """
        self.played += 1
        if winner == "left":
            self.left += 1
        elif winner == "right":
            self.right += 1
        else:
            self.draws += 1
        fmt = self.format
        if fmt.max_rounds <= 0:
            return ""
        if not fmt.dead_rubber:
            if self.left >= fmt.wins_needed:
                self.over = True
                return "left"
            if self.right >= fmt.wins_needed:
                self.over = True
                return "right"
        if self.played >= fmt.max_rounds:
            self.over = True
            return winner_of(self.left, self.right)
        return ""

    @property
    def score(self) -> tuple[int, int]:
        return self.left, self.right

    @property
    def round_number(self) -> int:
        """One-based number of the round about to be played."""
        return min(self.format.max_rounds, self.played + 1)


__all__ = [
    "BY_ID",
    "DEFAULT_FORMAT",
    "FINISHED_FORMAT",
    "FORMATS",
    "MatchFormat",
    "SERIES_IDS",
    "SeriesState",
    "get_format",
    "other_side",
    "winner_of",
]
