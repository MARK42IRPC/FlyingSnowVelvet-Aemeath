"""论坛窗口布局常量与页签元数据（产品垫片）。

实现已下沉到后端中立的 `lib/core/render/visuals/forum_wall_layout.py`（只依赖
`config.scale`、核心层与 `visuals/forum_style.py`）；这里按原名再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.render.visuals.forum_wall_layout import (  # noqa: F401
    CARD_MIN_WIDTH,
    COLUMN_COUNT,
    COLUMN_SPACING,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    FORUM_DEFAULT_PAGE,
    FORUM_HEADER_NOTICE,
    FORUM_NICKNAME_PLACEHOLDER,
    FORUM_PAGES,
    LOAD_OLDER_THRESHOLD_PX,
    MIN_WINDOW_WIDTH,
    NICKNAME_MAX_LENGTH,
    SCROLL_GAP,
    SCROLL_GUTTER,
    WALL_MARGIN,
)

__all__ = [
    "CARD_MIN_WIDTH",
    "COLUMN_COUNT",
    "COLUMN_SPACING",
    "DEFAULT_WINDOW_HEIGHT",
    "DEFAULT_WINDOW_WIDTH",
    "FORUM_DEFAULT_PAGE",
    "FORUM_HEADER_NOTICE",
    "FORUM_NICKNAME_PLACEHOLDER",
    "FORUM_PAGES",
    "LOAD_OLDER_THRESHOLD_PX",
    "MIN_WINDOW_WIDTH",
    "NICKNAME_MAX_LENGTH",
    "SCROLL_GAP",
    "SCROLL_GUTTER",
    "WALL_MARGIN",
]
