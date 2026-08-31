"""Qt-equivalent opacity animation for native DirectX presenters."""
from __future__ import annotations

from collections.abc import Callable

from config.config import UI
from lib.core.anchor_utils import apply_ui_opacity

from .loop import DxLoopContext, DxScheduledCall


class DxOpacityAnimator:
    """Animate one presenter's batch opacity on the owning DX loop."""

    def __init__(
        self,
        context: DxLoopContext,
        repaint: Callable[[], None],
        *,
        duration_ms: int | None = None,
    ) -> None:
        self._context = context
        self._repaint = repaint
        self._duration_ms = max(0, int(
            UI.get("ui_fade_duration", 200) if duration_ms is None else duration_ms
        ))
        self._value = 0.0
        self._start_value = 0.0
        self._target = 0.0
        self._started_at = 0.0
        self._call: DxScheduledCall | None = None
        self._finished: Callable[[], None] | None = None

    @property
    def value(self) -> float:
        return self._value

    @property
    def running(self) -> bool:
        return self._call is not None

    def fade_in(self) -> None:
        self._animate(apply_ui_opacity(1.0), None)

    def fade_out(self, finished: Callable[[], None]) -> None:
        self._animate(0.0, finished)

    def cancel(self) -> None:
        call, self._call = self._call, None
        if call is not None:
            call.cancel()
        self._finished = None

    def _animate(self, target: float, finished: Callable[[], None] | None) -> None:
        self.cancel()
        self._start_value = self._value
        self._target = max(0.0, min(1.0, float(target)))
        self._started_at = self._context.now()
        self._finished = finished
        if self._duration_ms == 0 or self._start_value == self._target:
            self._value = self._target
            self._repaint()
            self._complete()
            return
        self._repaint()
        self._call = self._context.call_later(16, self._advance)

    def _advance(self) -> None:
        self._call = None
        elapsed_ms = max(0.0, (self._context.now() - self._started_at) * 1000.0)
        progress = min(1.0, elapsed_ms / self._duration_ms)
        eased = (
            2.0 * progress * progress
            if progress < 0.5
            else 1.0 - ((-2.0 * progress + 2.0) ** 2) / 2.0
        )
        self._value = self._start_value + (self._target - self._start_value) * eased
        self._repaint()
        if progress >= 1.0:
            self._complete()
        else:
            self._call = self._context.call_later(16, self._advance)

    def _complete(self) -> None:
        callback, self._finished = self._finished, None
        if callback is not None:
            callback()


__all__ = ["DxOpacityAnimator"]
