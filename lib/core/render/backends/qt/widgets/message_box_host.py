"""Render the shared announcement-style prompts in a modal spec window."""

from __future__ import annotations

from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.spec_host import build_spec_window
from lib.core.render.visuals.message_box import (
    ICON_INFORMATION,
    ICON_WARNING,
    message_box_window_spec,
)
from lib.core.render.visuals.window_spec import WindowSpec


def _build_message_box(parent, spec: WindowSpec):
    return build_spec_window(
        spec,
        parent=parent,
        font_factory=tier_d_seams.ui_font,
        presentation_host=tier_d_seams.presentation_host(),
    )


def _run_message_box(parent, spec: WindowSpec, *, layer_name: str) -> str:
    window = _build_message_box(parent, spec)
    try:
        return window.exec_modal(layer_name=layer_name)
    finally:
        window.cleanup()


def ask_confirmation(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    destructive: bool = False,
    icon: str = ICON_WARNING,
    layer_name: str = "WorkbenchConfirmation",
) -> bool:
    """Return True only for the explicit confirmation action."""
    spec = message_box_window_spec(
        title=title, text=text, informative_text=informative_text, primary_text=confirm_text,
        cancel_text=cancel_text, destructive=destructive, icon=icon,
        fade=True, center_on_parent=True,
    )
    return _run_message_box(parent, spec, layer_name=layer_name) == "accept"


def show_message(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    ok_text: str = "知道了",
    icon: str = ICON_INFORMATION,
    layer_name: str = "WorkbenchNotice",
) -> None:
    """Show an announcement-style notice with one dismissal button."""
    spec = message_box_window_spec(
        title=title, text=text, informative_text=informative_text, primary_text=ok_text, icon=icon,
        modal=True, fade=True, center_on_parent=True,
    )
    _run_message_box(parent, spec, layer_name=layer_name)


def post_message(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    ok_text: str = "知道了",
    icon: str = ICON_INFORMATION,
    layer_name: str = "WorkbenchNotice",
):
    """Display a persistent notice from a UI callback without a nested event loop."""
    spec = message_box_window_spec(
        title=title, text=text, informative_text=informative_text,
        primary_text=ok_text, icon=icon, modal=False, fade=True, center_on_parent=True,
    )
    window = _build_message_box(parent, spec)
    window.show_window(layer_name=layer_name)
    return window


__all__ = ["ask_confirmation", "post_message", "show_message"]
