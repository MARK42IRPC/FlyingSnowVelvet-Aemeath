"""Skill slots for Lahai Tetris."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PyQt5.QtGui import QPixmap

from .model import apply_board_gravity, lowest_fill_columns, rows_with_more_than_four_colors
from .constants import (
    AUTHORIZATION_COOLDOWN_SECS,
    AUTHORIZATION_SKILL_SLOT,
    BOARD_H,
    FILL_SKILL_COOLDOWN_SECS,
    FILL_SKILL_SLOT,
    GRAVITY_COOLDOWN_SECS,
    GRAVITY_SKILL_SLOT,
    PARTNER_SKILL_COOLDOWN_SECS,
    PARTNER_SKILL_SLOT,
    RED_BAR_KIND,
    RED_BAR_WEIGHT,
    SPLENDOR_SKILL_COOLDOWN_SECS,
    SPLENDOR_SKILL_SLOT,
    STARLIGHT_CLEAR_ROWS,
    STARLIGHT_COOLDOWN_SECS,
    STARLIGHT_SKILL_SLOT,
)

if TYPE_CHECKING:
    from .widget import LahaiTetrisWidget


_FAILED_RELEASE_PENALTY_SECS = 15.0


class LahaiSkillSlot(ABC):
    """Base class for Lahai Tetris skill slots."""

    def __init__(
        self,
        slot_index: int,
        *,
        name: str = "",
        avatar_filename: str | None = None,
        cooldown_secs: float = 0.0,
    ) -> None:
        self.slot_index = int(slot_index)
        self.name = str(name or "")
        self.avatar_filename = avatar_filename
        self.base_cooldown_secs = max(0.0, float(cooldown_secs))
        self.cooldown_secs = self.base_cooldown_secs
        self.cooldown_until = 0.0
        self._paused_remaining = 0.0
        self.avatar = QPixmap()

    def load_assets(self, owner: "LahaiTetrisWidget") -> None:
        if self.avatar_filename:
            self.avatar = owner._load_skill_avatar(self.avatar_filename)

    def reset(self) -> None:
        self.cooldown_secs = self.base_cooldown_secs
        self.cooldown_until = 0.0
        self._paused_remaining = 0.0

    def cooldown_remaining(self) -> float:
        if self._paused_remaining > 0.0:
            return self._paused_remaining
        return max(0.0, self.cooldown_until - time.monotonic())

    def pause_cooldown(self) -> None:
        if self._paused_remaining > 0.0:
            return
        self._paused_remaining = max(0.0, self.cooldown_until - time.monotonic())
        if self._paused_remaining > 0.0:
            self.cooldown_until = 0.0

    def resume_cooldown(self) -> None:
        if self._paused_remaining <= 0.0:
            return
        self.cooldown_until = time.monotonic() + self._paused_remaining
        self._paused_remaining = 0.0

    def increase_cooldown(self, seconds: float) -> bool:
        delta = max(0.0, float(seconds))
        if delta <= 0.0:
            return False
        if self._paused_remaining > 0.0:
            self._paused_remaining += delta
            return True
        now = time.monotonic()
        remaining = max(0.0, self.cooldown_until - now)
        self.cooldown_until = now + remaining + delta
        return True

    def reduce_cooldown(self, seconds: float) -> None:
        delta = max(0.0, float(seconds))
        if delta <= 0.0:
            return False
        if self._paused_remaining > 0.0:
            before = self._paused_remaining
            self._paused_remaining = max(0.0, self._paused_remaining - delta)
            return self._paused_remaining < before
        now = time.monotonic()
        remaining = max(0.0, self.cooldown_until - now)
        if remaining <= 0.0:
            return False
        self.cooldown_until = 0.0 if remaining <= delta else now + (remaining - delta)
        return True

    def trigger(self, owner: "LahaiTetrisWidget") -> bool:
        if not self.can_trigger(owner):
            return False
        if not self.can_release(owner):
            self.cooldown_until = time.monotonic() + _FAILED_RELEASE_PENALTY_SECS
            self._paused_remaining = 0.0
            owner._play_skill_trigger_failure_voice(self.slot_index)
            return False
        owner._queue_skill_trigger(self.slot_index)
        return True

    def can_trigger(self, owner: "LahaiTetrisWidget") -> bool:
        return (
            not owner._game_over
            and not owner._awaiting_start
            and not owner._paused
            and not getattr(owner, "_skill_pause_active", False)
            and self.cooldown_remaining() <= 0.0
        )

    def resolved_cooldown_secs(self) -> float:
        return self.cooldown_secs

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        return True

    def advance_cooldown_curve(self, applied_cooldown_secs: float) -> None:
        applied = max(0.0, float(applied_cooldown_secs))
        if applied <= 0.0:
            self.cooldown_secs = 0.0
            return
        self.cooldown_secs = float(math.ceil(applied * 1.1))

    @abstractmethod
    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        raise NotImplementedError


class StarlightSkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            STARLIGHT_SKILL_SLOT,
            name="星辉",
            avatar_filename="爱弥斯.png",
            cooldown_secs=STARLIGHT_COOLDOWN_SECS,
        )

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        candidate_rows = [idx for idx, row in enumerate(owner._board) if any(cell is not None for cell in row)]
        if not candidate_rows:
            return False
        clear_count = min(STARLIGHT_CLEAR_ROWS, len(candidate_rows))
        cleared_rows = sorted(owner._rng.sample(candidate_rows, clear_count))
        if not owner._clear_board_rows(cleared_rows):
            return False
        owner._start_board_shake(force=8.0)
        return True

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        return any(any(cell is not None for cell in row) for row in owner._board)


class GravitySkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            GRAVITY_SKILL_SLOT,
            name="重力",
            avatar_filename="达妮娅.png",
            cooldown_secs=GRAVITY_COOLDOWN_SECS,
        )

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        if not any(any(cell is not None for cell in row) for row in owner._board):
            return False
        changed = owner._apply_board_gravity()
        cleared_rows = [idx for idx, row in enumerate(owner._board) if all(cell is not None for cell in row)]
        cleared = False
        if cleared_rows:
            if changed and owner._settled_fall_anim:
                owner._queue_clear_board_rows_after_settled_animation(cleared_rows)
                cleared = True
            else:
                cleared = owner._clear_board_rows(cleared_rows)
        if not changed and not cleared:
            return False
        owner._start_board_shake(force=7.0)
        return True

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        board = owner._board
        gravity_board = apply_board_gravity(board)
        if gravity_board != board:
            return True
        return any(all(cell is not None for cell in row) for row in board)


class AuthorizationSkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            AUTHORIZATION_SKILL_SLOT,
            name="授权",
            avatar_filename="莫宁.png",
            cooldown_secs=AUTHORIZATION_COOLDOWN_SECS,
        )

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        owner._piece_randomizer.reset_with_weights({
            RED_BAR_KIND: RED_BAR_WEIGHT,
        }, default_weight=0.5)
        owner._replace_next_piece(RED_BAR_KIND)
        return True


class FillSkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            FILL_SKILL_SLOT,
            name="剪定",
            avatar_filename="千咲.png",
            cooldown_secs=FILL_SKILL_COOLDOWN_SECS,
        )
        self._last_effective_columns = 0

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        self._last_effective_columns = owner._apply_fill_skill()
        return self._last_effective_columns > 0

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        board = owner._board
        candidate_columns = lowest_fill_columns(board, count=3)
        for col in candidate_columns:
            occupied_rows = [row for row in range(BOARD_H) if board[row][col] is not None]
            if not occupied_rows:
                continue
            target_top = min(occupied_rows)
            if any(board[row][col] is None for row in range(target_top, BOARD_H)):
                return True
        return False

    def resolved_cooldown_secs(self) -> float:
        if self._last_effective_columns <= 0:
            return 0.0
        if self._last_effective_columns == 1:
            return max(0.0, self.cooldown_secs - 20.0)
        if self._last_effective_columns == 2:
            return max(0.0, self.cooldown_secs - 10.0)
        return self.cooldown_secs


class SplendorSkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            SPLENDOR_SKILL_SLOT,
            name="绚烂",
            avatar_filename="琳奈.png",
            cooldown_secs=SPLENDOR_SKILL_COOLDOWN_SECS,
        )

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        return owner._apply_splendor_skill()

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        return bool(rows_with_more_than_four_colors(owner._board))


class PartnerSkillSlot(LahaiSkillSlot):
    def __init__(self) -> None:
        super().__init__(
            PARTNER_SKILL_SLOT,
            name="伙伴",
            avatar_filename="西格莉卡.png",
            cooldown_secs=PARTNER_SKILL_COOLDOWN_SECS,
        )

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        return owner._apply_partner_skill()

    def can_release(self, owner: "LahaiTetrisWidget") -> bool:
        return any(any(cell is not None for cell in row) for row in owner._board)


class EmptySkillSlot(LahaiSkillSlot):
    def __init__(self, slot_index: int) -> None:
        super().__init__(slot_index)

    def apply(self, owner: "LahaiTetrisWidget") -> bool:
        return False
