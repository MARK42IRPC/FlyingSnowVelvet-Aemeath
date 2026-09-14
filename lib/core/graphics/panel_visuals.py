"""Backend-neutral visuals for the pet's layered panels and action buttons.

The pet UI draws the same "black frame -> cyan mid -> pink background" shell in
many places (command hint, tooltips, playlist, progress bar, CMD window, AI
settings tab bar). Those duplicated pixel recipes used to live inside the Qt
hosts, which made every backend re-implement the product look. They are exposed
here once so hosts only execute the resulting :class:`DrawBatch`.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.scale import scale_px
from lib.core.layer import Layer

from .commands import (
    DrawBatch,
    RectCommand,
    TextAlignment,
    TextCommand,
    TransformPop,
    TransformPush,
)
from .palette import COLORS, UI_THEME
from .types import Color, FontSpec, Rect, Size


def panel_inset() -> int:
    """Return the canonical panel frame thickness (2 logical pixels)."""
    return scale_px(2, min_abs=1)


def inset_rect(rect: Rect, inset: float) -> Rect:
    """Return ``rect`` shrunk by ``inset`` logical pixels on every side."""
    return Rect(
        rect.x + inset,
        rect.y + inset,
        rect.width - inset * 2,
        rect.height - inset * 2,
    )


def panel_frame_commands(
    rect: Rect,
    *,
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
    color: Color | None = None,
) -> list[object]:
    """Build the outer frame strips.

    Panels whose content can reach into the frame (for example a slider handle
    parked on the first pixel) draw the frame *after* the content, so the frame
    is expressed as four strips instead of one covering fill.
    """
    inset = panel_inset() if inset is None else max(1, int(inset))
    fill = color or COLORS["black"]
    return [
        RectCommand(
            Rect(rect.x, rect.y, rect.width, inset),
            fill=fill, alpha=alpha, layer=layer, z=z,
        ),
        RectCommand(
            Rect(rect.x, rect.y + rect.height - inset, rect.width, inset),
            fill=fill, alpha=alpha, layer=layer, z=z,
        ),
        RectCommand(
            Rect(rect.x, rect.y, inset, rect.height),
            fill=fill, alpha=alpha, layer=layer, z=z,
        ),
        RectCommand(
            Rect(rect.x + rect.width - inset, rect.y, inset, rect.height),
            fill=fill, alpha=alpha, layer=layer, z=z,
        ),
    ]


def panel_shell_commands(
    rect: Rect,
    *,
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
    border: Color | None = None,
    mid: Color | None = None,
    bg: Color | None = None,
    content_inset: int | None = None,
) -> tuple[list[object], Rect]:
    """Build the three-layer panel shell and return ``(commands, content_rect)``.

    ``border``/``mid``/``bg`` default to the black / cyan / pink product tokens;
    ``inset`` is the per-layer thickness and ``content_inset`` overrides the
    inner rectangle offset (used by asymmetric panels such as the tab bar).
    """
    inset = panel_inset() if inset is None else max(1, int(inset))
    inner_inset = inset * 2 if content_inset is None else int(content_inset)
    commands: list[object] = [
        RectCommand(rect, fill=border or COLORS["black"], alpha=alpha, layer=layer, z=z),
        RectCommand(
            inset_rect(rect, inset),
            fill=mid or COLORS["cyan"],
            alpha=alpha,
            layer=layer,
            z=z + 1,
        ),
        RectCommand(
            inset_rect(rect, inner_inset),
            fill=bg or COLORS["pink"],
            alpha=alpha,
            layer=layer,
            z=z + 2,
        ),
    ]
    return commands, inset_rect(rect, inner_inset)


@dataclass(frozen=True, slots=True)
class PanelVisual:
    """Resolved shell geometry and draw commands for one layered panel."""

    size: Size
    rect: Rect
    content_rect: Rect
    batch: DrawBatch


def build_panel_shell_visual(
    rect: Rect,
    *,
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
    border: Color | None = None,
    mid: Color | None = None,
    bg: Color | None = None,
    content_inset: int | None = None,
) -> PanelVisual:
    commands, content = panel_shell_commands(
        rect,
        inset=inset,
        layer=layer,
        z=z,
        alpha=alpha,
        border=border,
        mid=mid,
        bg=bg,
        content_inset=content_inset,
    )
    return PanelVisual(
        Size(rect.width, rect.height),
        rect,
        content,
        DrawBatch(tuple(commands)),
    )


def action_button_commands(
    rect: Rect,
    label: str | None,
    font: FontSpec | None = None,
    *,
    state: str = "normal",
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> tuple[list[object], Rect]:
    """Build the shared action button look and return ``(commands, content)``.

    ``state`` is one of ``normal`` / ``hover`` / ``pressed`` / ``pressed_flat``;
    hover expands the deep-pink ring while the pressed states swap the inner
    fill for the highlight token. ``pressed_flat`` keeps the normal ring (used
    when a button is held but the pointer has already left it).
    """
    inset = panel_inset() if inset is None else max(1, int(inset))
    commands: list[object] = [
        RectCommand(rect, fill=COLORS["black"], alpha=alpha, layer=layer, z=z),
        RectCommand(
            inset_rect(rect, inset),
            fill=COLORS["cyan"],
            alpha=alpha,
            layer=layer,
            z=z + 1,
        ),
    ]
    if state in {"hover", "pressed"}:
        commands.append(RectCommand(
            inset_rect(rect, inset * 2),
            fill=UI_THEME["deep_pink"],
            alpha=alpha,
            layer=layer,
            z=z + 2,
        ))
        content = inset_rect(rect, inset * 3)
        content_z = z + 3
    else:
        content = inset_rect(rect, inset * 2)
        content_z = z + 2
    commands.append(RectCommand(
        content,
        fill=UI_THEME["highlight"] if state in {"pressed", "pressed_flat"} else COLORS["pink"],
        alpha=alpha,
        layer=layer,
        z=content_z,
    ))
    if label is not None:
        commands.append(TextCommand(
            label,
            font,
            COLORS["black"],
            content,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            alpha=alpha,
            layer=layer,
            z=content_z + 1,
        ))
    return commands, content


@dataclass(frozen=True, slots=True)
class ActionButtonVisual:
    """Resolved geometry and draw commands for one layered action button."""

    size: Size
    rect: Rect
    content_rect: Rect
    batch: DrawBatch


def build_action_button_visual(
    rect: Rect,
    label: str,
    font: FontSpec,
    *,
    state: str = "normal",
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> ActionButtonVisual:
    commands, content = action_button_commands(
        rect,
        label,
        font,
        state=state,
        inset=inset,
        layer=layer,
        z=z,
        alpha=alpha,
    )
    return ActionButtonVisual(
        Size(rect.width, rect.height),
        rect,
        content,
        DrawBatch(tuple(commands)),
    )


def rotated_square_commands(
    center_x: float,
    center_y: float,
    half_diagonal: float,
    fill: Color,
    *,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> list[object]:
    """Build a diamond (a square rotated 45 degrees) centred on one point."""
    radius = max(0.5, float(half_diagonal))
    half_side = radius / 1.4142135623730951
    diagonal = 0.7071067811865476
    return [
        TransformPush((
            diagonal,
            diagonal,
            -diagonal,
            diagonal,
            float(center_x),
            float(center_y),
        )),
        RectCommand(
            Rect(-half_side, -half_side, half_side * 2, half_side * 2),
            fill=fill,
            alpha=alpha,
            layer=layer,
            z=z,
        ),
        TransformPop(),
    ]


#: Portrait slider handle aspect (height / width), roughly 4:3.
SLIDER_HANDLE_ASPECT = 4.0 / 3.0


def slider_handle_commands(
    center_x: float,
    track_rect: Rect,
    fill: Color,
    *,
    aspect: float = SLIDER_HANDLE_ASPECT,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> tuple[list[object], Rect]:
    """Build the shared portrait slider handle and return ``(commands, rect)``.

    The handle is a vertical rectangle (about 4:3, taller than wide) centred on
    the requested position and clamped inside the track. It replaces the old
    rotated square so the playback progress bar and the speaker volume slider
    share one look that matches the pink/cyan panel language.
    """
    height = max(2.0, float(track_rect.height))
    width = max(2.0, float(round(height / max(1.0, float(aspect)))))
    left = float(center_x) - width / 2.0
    left = max(
        float(track_rect.x),
        min(float(track_rect.x) + float(track_rect.width) - width, left),
    )
    rect = Rect(left, float(track_rect.y), width, height)
    return [
        RectCommand(rect, fill=fill, alpha=alpha, layer=layer, z=z),
    ], rect


@dataclass(frozen=True, slots=True)
class TabBarVisual:
    """Resolved geometry and draw commands for the AI settings tab strip."""

    size: Size
    content_rect: Rect
    batch: DrawBatch


def build_tab_bar_visual(
    size: Size,
    *,
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> TabBarVisual:
    """Reproduce the asymmetric tab strip: pink fill open to the right edge."""
    inset = panel_inset() if inset is None else max(1, int(inset))
    border = inset * 2
    width = size.width
    height = size.height
    content = Rect(border, border, width - border, height - border * 2)
    commands: list[object] = [
        RectCommand(content, fill=UI_THEME["bg"], alpha=alpha, layer=layer, z=z),
        # cyan mid frame (left/top/bottom/right strips)
        RectCommand(
            Rect(0, inset, inset, height - inset * 2),
            fill=UI_THEME["mid"], alpha=alpha, layer=layer, z=z + 1,
        ),
        RectCommand(
            Rect(border, 0, width - border, inset),
            fill=UI_THEME["mid"], alpha=alpha, layer=layer, z=z + 1,
        ),
        RectCommand(
            Rect(border, height - inset, width - border, inset),
            fill=UI_THEME["mid"], alpha=alpha, layer=layer, z=z + 1,
        ),
        RectCommand(
            Rect(width - inset, inset, inset, height - inset * 2),
            fill=UI_THEME["mid"], alpha=alpha, layer=layer, z=z + 1,
        ),
        # black outer frame (left/top/bottom strips, matching the Qt baseline)
        RectCommand(
            Rect(0, 0, inset, height),
            fill=UI_THEME["border"], alpha=alpha, layer=layer, z=z + 2,
        ),
        RectCommand(
            Rect(border, 0, width - border, inset),
            fill=UI_THEME["border"], alpha=alpha, layer=layer, z=z + 2,
        ),
        RectCommand(
            Rect(border, height - inset, width - border, inset),
            fill=UI_THEME["border"], alpha=alpha, layer=layer, z=z + 2,
        ),
    ]
    return TabBarVisual(size, content, DrawBatch(tuple(commands)))


__all__ = [
    "ActionButtonVisual",
    "PanelVisual",
    "SLIDER_HANDLE_ASPECT",
    "TabBarVisual",
    "action_button_commands",
    "build_action_button_visual",
    "build_panel_shell_visual",
    "build_tab_bar_visual",
    "inset_rect",
    "panel_frame_commands",
    "panel_inset",
    "panel_shell_commands",
    "rotated_square_commands",
    "slider_handle_commands",
]
