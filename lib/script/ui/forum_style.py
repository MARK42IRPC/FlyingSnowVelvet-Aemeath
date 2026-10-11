"""雪绒论坛共享视觉语言（产品垫片）。

配色与样式表的实现已下沉到后端中立的 `lib/core/render/visuals/forum_style.py`：它只用
`workbench_tokens` 的 token 与论坛自己的 accent 配色，不再 import 产品包。这里按原名再导出，
既有导入面（`forum_window` / `forum_body` / `forum_color_*` / `forum_wall_layout` 等）不变。
"""

from __future__ import annotations

from lib.core.render.visuals.forum_style import (  # noqa: F401
    FORUM_ACCENTS,
    FORUM_ACCENT_LABELS,
    FORUM_CARD_RADIUS,
    FORUM_SCROLLBAR_WIDTH,
    FORUM_CARD_TEXT_SCALE_RANGE,
    FORUM_CARD_TEXT_LONG_LENGTH,
    FORUM_CARD_TEXT_SHORT_LENGTH,
    FORUM_TEXTURE_TINT_RATIO,
    get_workbench_colors,
    window_button_stylesheet,
    forum_card_text_scale,
    forum_card_text_size,
    forum_card_text_color,
    forum_muted_text_color,
    forum_texture_color,
    forum_accent_color,
    forum_picker_track_color,
    forum_stylesheet,
    FORUM_IMAGE_FRAME,
)

__all__ = [
    "FORUM_ACCENTS",
    "FORUM_ACCENT_LABELS",
    "FORUM_CARD_RADIUS",
    "FORUM_SCROLLBAR_WIDTH",
    "FORUM_CARD_TEXT_SCALE_RANGE",
    "FORUM_CARD_TEXT_LONG_LENGTH",
    "FORUM_CARD_TEXT_SHORT_LENGTH",
    "FORUM_TEXTURE_TINT_RATIO",
    "get_workbench_colors",
    "window_button_stylesheet",
    "forum_card_text_scale",
    "forum_card_text_size",
    "forum_card_text_color",
    "forum_muted_text_color",
    "forum_texture_color",
    "forum_accent_color",
    "forum_picker_track_color",
    "forum_stylesheet",
    "FORUM_IMAGE_FRAME",
]
