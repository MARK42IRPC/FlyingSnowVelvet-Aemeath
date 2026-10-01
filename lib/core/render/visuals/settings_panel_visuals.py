"""Backend-neutral visuals for the AI settings panel chrome."""

from __future__ import annotations

from dataclasses import dataclass

from config.font_config import get_digit_font_family
from config.scale import scale_px
from lib.core.layer import Layer

from .commands import DrawBatch, TextAlignment, TextCommand
from .palette import UI_THEME
from .panel_visuals import panel_inset, panel_shell_commands
from .types import Color, FontSpec, Rect, Size

#: Left watermark is drawn at two thirds of the top watermark design size.
LEFT_WATERMARK_SCALE = 2.0 / 3.0
WATERMARK_ALPHA = 220


def _watermark_color() -> Color:
    base = UI_THEME["deep_pink"]
    return Color(base.red, base.green, base.blue, WATERMARK_ALPHA)


@dataclass(frozen=True, slots=True)
class SettingsPanelVisual:
    size: Size
    content_rect: Rect
    top_watermark_rect: Rect
    side_watermark_rect: Rect
    batch: DrawBatch


def build_ai_settings_panel_visual(
    size: Size,
    *,
    top_watermark_text: str = "",
    side_watermark_text: str = "",
    inset: int | None = None,
    layer: int = int(Layer.PANEL),
    alpha: float = 1.0,
) -> SettingsPanelVisual:
    """Build the layered settings panel shell plus its two GPU/RAM watermarks."""
    inset = panel_inset() if inset is None else max(1, int(inset))
    border = inset * 2
    width = float(size.width)
    height = float(size.height)
    outer = Rect(0, 0, width, height)
    commands, content = panel_shell_commands(outer, inset=inset, layer=layer, alpha=alpha)

    family = get_digit_font_family()
    color = _watermark_color()

    # Top watermark: a third of the design size, pinned to the top edge.
    top_size = max(scale_px(8, min_abs=6), scale_px(46, min_abs=24) // 3)
    top_rect = Rect(
        border + scale_px(6),
        border + scale_px(1),
        width - border * 2 - scale_px(6) * 2,
        max(scale_px(42, min_abs=18), int(height * 0.14)) - 1,
    )
    commands.append(TextCommand(
        str(top_watermark_text or ""),
        FontSpec(family, max(1, int(top_size)), True),
        color,
        top_rect,
        alignment=int(TextAlignment.HCENTER | TextAlignment.TOP),
        alpha=alpha,
        layer=layer,
        z=3,
    ))

    # Bottom-left watermark: two thirds of the design size.
    side_size = max(
        scale_px(12, min_abs=10),
        int(round(scale_px(46, min_abs=24) * LEFT_WATERMARK_SCALE)),
    )
    side_width = max(scale_px(80, min_abs=1), int(round((width // 2) * LEFT_WATERMARK_SCALE)))
    side_height = max(scale_px(80, min_abs=1), int(round((height * 0.42) * LEFT_WATERMARK_SCALE)))
    shift_right = scale_px(30, min_abs=24)
    side_rect = Rect(
        border + scale_px(8) + shift_right,
        height - side_height - border - scale_px(6),
        side_width,
        side_height,
    )
    commands.append(TextCommand(
        str(side_watermark_text or ""),
        FontSpec(family, max(1, int(side_size)), True),
        color,
        side_rect,
        alignment=int(TextAlignment.LEFT | TextAlignment.BOTTOM),
        alpha=alpha,
        layer=layer,
        z=3,
    ))

    return SettingsPanelVisual(
        Size(width, height),
        content,
        top_rect,
        side_rect,
        DrawBatch(tuple(commands)),
    )


__all__ = [
    "LEFT_WATERMARK_SCALE",
    "WATERMARK_ALPHA",
    "SettingsPanelVisual",
    "build_ai_settings_panel_visual",
]
