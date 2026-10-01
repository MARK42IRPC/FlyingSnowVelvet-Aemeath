"""Presentational timing and linger values shared by every backend.

These are not pixels, so they do not belong in ``lib/core/render/visuals``; they are
product numbers that both bridges used to resolve on their own. Two real
divergences had accumulated:

* the UI fade duration fell back to ``180`` in ``qt_bridge/workbench_page`` but
  to ``200`` in ``dx_bridge/opacity``; the config key exists and holds ``200``,
  so both bridges always saw the same value at runtime, and the fallbacks only
  disagreed on paper -- exactly the kind of drift that becomes a visible
  difference the moment the key is dropped from a user's config;
* the overlay hide linger lived inside ``qt_bridge/overlay_policy`` even though
  the policy itself (no-activate plus lingured hiding) is backend-neutral.

Both are resolved here now, so a future backend cannot pick a different default.
"""
from __future__ import annotations

from config.config import PARTICLES, UI

#: Matches ``config_ui.py``'s documented default for ``ui_fade_duration``.
DEFAULT_UI_FADE_DURATION_MS = 200
#: Legacy per-bridge fallbacks kept for the record; they disagreed with each other.
LEGACY_UI_FADE_DURATION_FALLBACKS = (180, 200)
DEFAULT_OVERLAY_HIDE_LINGER_MS = 500


def ui_fade_duration_ms(value: object = None) -> int:
    """Resolve the shared UI fade duration in milliseconds.

    ``value`` overrides the configuration, which is what tests and callers that
    already own a duration use. A negative or unparsable value falls back to the
    documented default rather than raising.
    """
    if value is None:
        value = UI.get("ui_fade_duration", DEFAULT_UI_FADE_DURATION_MS)
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_UI_FADE_DURATION_MS


def overlay_hide_linger_ms(value: object = None) -> int:
    """Resolve how long an emptied overlay stays hidden before hiding.

    ``0`` keeps the old immediate-hide behaviour.
    """
    if value is None:
        value = PARTICLES.get(
            "overlay_hide_linger_ms", DEFAULT_OVERLAY_HIDE_LINGER_MS
        )
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_OVERLAY_HIDE_LINGER_MS


__all__ = [
    "DEFAULT_OVERLAY_HIDE_LINGER_MS",
    "DEFAULT_UI_FADE_DURATION_MS",
    "LEGACY_UI_FADE_DURATION_FALLBACKS",
    "overlay_hide_linger_ms",
    "ui_fade_duration_ms",
]
