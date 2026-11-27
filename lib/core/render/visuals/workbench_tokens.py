"""Canonical workbench palette shared by the QSS theme and visual presenters.

The dark/light token values used to live in two places: the QSS builder in
``lib/script/workbench/theme.py`` and the announcement presenter. This module is
the single source of truth so a backend cannot reintroduce its own product
colours, and so the floating panels follow the workbench theme automatically.
"""

from __future__ import annotations

from lib.core.render.popup_design import WORKBENCH_DARK_TOKENS, WORKBENCH_LIGHT_TOKENS
from lib.core.render.visuals.types import Color

#: Token order is the public contract used by both QSS generation and presenters.
WORKBENCH_TOKEN_NAMES = (
    "canvas",
    "navigation",
    "surface",
    "surface_raised",
    "surface_hover",
    "border",
    "border_strong",
    "text",
    "text_muted",
    "text_dim",
    "pink",
    "pink_hover",
    "cyan",
    "warning",
    "danger",
)

def resolve_workbench_mode(mode: str | None = None) -> str:
    """Resolve ``dark``/``light`` from an explicit value or the live UI config."""
    if mode is None:
        try:
            from config.config import UI

            return "light" if bool(UI.get("workbench_light_theme", False)) else "dark"
        except Exception:
            return "dark"
    return "light" if str(mode).strip().lower() == "light" else "dark"


def get_workbench_tokens(mode: str | None = None) -> dict[str, str]:
    """Return the hex palette for the requested (or current) workbench theme."""
    return dict(
        WORKBENCH_LIGHT_TOKENS
        if resolve_workbench_mode(mode) == "light"
        else WORKBENCH_DARK_TOKENS
    )


def hex_to_color(value: str) -> Color:
    """Convert ``#rrggbb`` into the backend-neutral colour value object."""
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid hex colour: {value!r}")
    return Color(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def get_workbench_token_colors(mode: str | None = None) -> dict[str, Color]:
    """Return the palette as backend-neutral colours for presenters."""
    return {name: hex_to_color(value) for name, value in get_workbench_tokens(mode).items()}


__all__ = [
    "WORKBENCH_DARK_TOKENS",
    "WORKBENCH_LIGHT_TOKENS",
    "WORKBENCH_TOKEN_NAMES",
    "get_workbench_token_colors",
    "get_workbench_tokens",
    "hex_to_color",
    "resolve_workbench_mode",
]
