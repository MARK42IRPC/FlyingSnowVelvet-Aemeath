"""雪绒论坛行内标记的解析与渲染片段（产品垫片）。

实现是纯逻辑：解析 `**粗体**` 一族行内标记、`[雪豹]` 效果令牌与颜色/排版令牌，产出
`to_html()` 富文本片段与 `visible_text()` 可见文字，并给发帖框的四个按钮提供
`span_at_cursor()` / `toggle()`。它不导入任何 GUI 库，按《render 层边界契约》第 34.2 节
的 A 类判定下沉到后端中立的 `lib/core/render/visuals/forum_markup.py`；这里按原名再导出，
调用方导入面不变。
"""

from __future__ import annotations

from lib.core.render.visuals.forum_markup import (  # noqa: F401
    FORUM_EFFECT_TOKENS,
    FORUM_MARKUP_FORMATS,
    FORMAT_BY_KEY,
    ForumMarkupFormat,
    effect_tokens,
    escape_text,
    marker_positions,
    span_at_cursor,
    strip_effect_tokens,
    to_html,
    toggle,
    visible_text,
)

__all__ = [
    "FORUM_EFFECT_TOKENS",
    "FORUM_MARKUP_FORMATS",
    "FORMAT_BY_KEY",
    "ForumMarkupFormat",
    "effect_tokens",
    "escape_text",
    "marker_positions",
    "span_at_cursor",
    "strip_effect_tokens",
    "to_html",
    "toggle",
    "visible_text",
]
