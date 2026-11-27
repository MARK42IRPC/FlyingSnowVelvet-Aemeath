"""Popup metrics and workbench colors usable before the app or UI initializes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


WORKBENCH_DARK_TOKENS = {
    "canvas": "#0d0f12",
    "navigation": "#121419",
    "surface": "#17191f",
    "surface_raised": "#1e2128",
    "surface_hover": "#272b33",
    "border": "#353a45",
    "border_strong": "#4a515f",
    "text": "#f4f5f7",
    "text_muted": "#a8adb7",
    "text_dim": "#777e8b",
    "pink": "#ff95bc",
    "pink_hover": "#ffb1cf",
    "cyan": "#8cd2ff",
    "warning": "#f1cf76",
    "danger": "#ff7a92",
}

WORKBENCH_LIGHT_TOKENS = {
    "canvas": "#fff8fb",
    "navigation": "#fff0f5",
    "surface": "#ffffff",
    "surface_raised": "#fff5f8",
    "surface_hover": "#ffe7f0",
    "border": "#e7c5d2",
    "border_strong": "#c99eb0",
    "text": "#20344d",
    "text_muted": "#344863",
    "text_dim": "#4f627b",
    "pink": "#e9689d",
    "pink_hover": "#f58db7",
    "cyan": "#91bdd8",
    "warning": "#a97c36",
    "danger": "#d95e78",
}

# Each entry is (fill, border, text). A missing fill or border is transparent.
POPUP_ACTION_TOKENS = {
    "default": {
        "normal": ("surface_raised", "border", "text"),
        "hover": ("surface_hover", "cyan", "text"),
        "pressed": ("border_strong", "cyan", "text"),
    },
    "primary": {
        "normal": ("pink", "pink", "canvas"),
        "hover": ("pink_hover", "pink_hover", "canvas"),
        "pressed": ("pink_hover", "cyan", "canvas"),
    },
    "accent": {
        "normal": ("cyan", "cyan", "canvas"),
        "hover": ("surface_hover", "cyan", "text"),
        "pressed": ("border_strong", "cyan", "text"),
    },
    "danger": {
        "normal": ("danger", "danger", "canvas"),
        "hover": ("danger", "text", "canvas"),
        "pressed": ("danger", "text", "canvas"),
    },
    "ghost": {
        "normal": (None, None, "text_muted"),
        "hover": ("surface_hover", "border", "text"),
        "pressed": ("border_strong", "border", "text"),
    },
}
POPUP_DISABLED_TOKENS = ("surface", "border", "text_dim")


@dataclass(frozen=True, slots=True)
class PopupChrome:
    """Shared dimensions for Qt, draw batches and the native installer."""

    border: int
    margin_x: int
    margin_top: int
    margin_bottom: int
    row_gap: int
    header_height: int
    header_gap: int
    accent_width: int
    action_height: int
    action_gap: int
    action_pad_x: int
    action_size: int
    body_padding: int
    radius: int
    close_size: int
    title_size: int
    body_size: int
    source_size: int
    channel_size: int
    notice_width: int
    notice_height: int

    @property
    def margins(self) -> tuple[int, int, int, int]:
        return self.margin_x, self.margin_top, self.margin_x, self.margin_bottom


def popup_chrome_dimensions(scale: Callable[[int, int], int] | None = None) -> PopupChrome:
    """Return the 96-DPI baseline or apply a caller-supplied UI scale."""

    def px(value: int, minimum: int) -> int:
        return value if scale is None else scale(value, minimum)

    border = px(1, 1)
    return PopupChrome(
        border=border,
        margin_x=border + px(20, 16),
        margin_top=border + px(18, 14),
        margin_bottom=border + px(17, 14),
        row_gap=px(15, 11),
        header_height=px(42, 36),
        header_gap=px(12, 9),
        accent_width=px(3, 2),
        action_height=px(34, 30),
        action_gap=px(9, 7),
        action_pad_x=px(10, 8),
        action_size=px(12, 11),
        body_padding=px(16, 12),
        radius=px(4, 3),
        close_size=px(30, 26),
        title_size=px(18, 15),
        body_size=px(13, 11),
        source_size=px(10, 9),
        channel_size=px(9, 8),
        notice_width=px(520, 460),
        notice_height=px(290, 250),
    )
