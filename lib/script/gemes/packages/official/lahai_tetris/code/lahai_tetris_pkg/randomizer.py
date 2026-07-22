"""Piece randomizer for Lahai Tetris."""

from __future__ import annotations

import random


class LahaiPieceRandomizer:
    """Weighted piece randomizer with short-term memory."""

    def __init__(
        self,
        kinds: list[str] | tuple[str, ...],
        rng: random.Random | None = None,
        *,
        reset_interval: int = 14,
        initial_weight: float = 1.0,
        selected_delta: float = -0.2,
        other_delta: float = 0.05,
        min_weight: float = 0.0,
    ) -> None:
        self._kinds = [str(kind) for kind in kinds]
        if not self._kinds:
            raise ValueError("kinds must not be empty")
        self._rng = rng or random.Random()
        self._reset_interval = max(1, int(reset_interval))
        self._initial_weight = max(0.0, float(initial_weight))
        self._selected_delta = float(selected_delta)
        self._other_delta = float(other_delta)
        self._min_weight = max(0.0, float(min_weight))
        self._generated_count = 0
        self._weights: dict[str, float] = {}
        self.reset()

    def reset(self) -> None:
        self._weights = {kind: self._initial_weight for kind in self._kinds}
        self._generated_count = 0

    def reset_with_weights(
        self,
        overrides: dict[str, float] | None = None,
        *,
        default_weight: float | None = None,
    ) -> None:
        self.reset()
        if default_weight is not None:
            clamped_default = max(self._min_weight, float(default_weight))
            self._weights = {kind: clamped_default for kind in self._kinds}
        if not overrides:
            return
        for kind, weight in overrides.items():
            if kind not in self._weights:
                continue
            self._weights[kind] = max(self._min_weight, float(weight))

    def next_kind(self) -> str:
        if self._generated_count > 0 and self._generated_count % self._reset_interval == 0:
            self.reset()
        selected = self._weighted_choice()
        self._generated_count += 1
        self._apply_selection(selected)
        return selected

    def weights_snapshot(self) -> dict[str, float]:
        return dict(self._weights)

    @property
    def generated_count(self) -> int:
        return self._generated_count

    def _weighted_choice(self) -> str:
        total = sum(max(0.0, self._weights.get(kind, 0.0)) for kind in self._kinds)
        if total <= 0:
            return self._rng.choice(self._kinds)
        needle = self._rng.random() * total
        cumulative = 0.0
        for kind in self._kinds:
            cumulative += max(0.0, self._weights.get(kind, 0.0))
            if needle < cumulative:
                return kind
        return self._kinds[-1]

    def _apply_selection(self, selected: str) -> None:
        for kind in self._kinds:
            delta = self._selected_delta if kind == selected else self._other_delta
            self._weights[kind] = max(self._min_weight, self._weights.get(kind, self._initial_weight) + delta)
