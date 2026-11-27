"""Workbench floating-window data: theme-change detection and shared style entry.

Floating windows (QR login, voice-package installer) and the announcement,
confirmation, help, update and approval pop-ups share the single QSS in
``announcement_chrome.announcement_stylesheet``. This module keeps the historic
import names and the theme-change payload check; it owns no stylesheet copy.
"""

from __future__ import annotations

from config.scale import scale_px
from lib.core.render.visuals.announcement_chrome import announcement_stylesheet
from lib.core.render.visuals.workbench_tokens import (
    get_workbench_tokens,
    resolve_workbench_mode,
)

FLOATING_WINDOW_OBJECT_NAME = "WorkbenchFloatingWindow"


def floating_window_stylesheet(mode: str | None = None) -> str:
    """Return the shared pop-up QSS for the given theme."""

    return announcement_stylesheet(resolve_workbench_mode(mode))


def _window_button_stylesheet(resolved_mode: str) -> str:
    """Mirror of the workbench window-button QSS using the neutral token map.

    Still used by the main workbench window and the office page (not pop-ups).
    """

    c = get_workbench_tokens(resolved_mode)
    radius = scale_px(4, min_abs=3)
    control_height = scale_px(32, min_abs=28)
    return f"""
    QToolButton#WorkbenchWindowButton {{
        background: transparent;
        border: none;
        border-radius: {radius}px;
        min-width: {control_height}px;
        min-height: {control_height}px;
        max-width: {control_height}px;
        max-height: {control_height}px;
        padding: 0px;
    }}
    QToolButton#WorkbenchWindowButton:hover {{
        background: {c['surface_hover']};
    }}
    QToolButton#WorkbenchWindowButton[danger="true"]:hover {{
        background: {c['danger']};
    }}
    """


def voice_installer_stylesheet(mode: str | None = None) -> str:
    """Voice-installer QSS is the shared pop-up QSS; keep the historic name."""

    return announcement_stylesheet(resolve_workbench_mode(mode))


def is_workbench_theme_change(event: object) -> bool:
    """Return True when a CONFIG_UPDATED payload switched the workbench theme."""
    data = getattr(event, "data", None)
    if not isinstance(data, dict):
        return False
    values = data.get("values")
    if not isinstance(values, dict):
        return False
    ui_values = values.get("UI")
    return isinstance(ui_values, dict) and "workbench_light_theme" in ui_values


__all__ = [
    "FLOATING_WINDOW_OBJECT_NAME",
    "floating_window_stylesheet",
    "is_workbench_theme_change",
    "voice_installer_stylesheet",
]
