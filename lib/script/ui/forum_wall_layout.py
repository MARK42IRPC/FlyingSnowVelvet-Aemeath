"""论坛窗口的布局常量与页签元数据。

批次 3 后续轮次从 `forum_window` 切出：留言墙（`forum_wall.ForumWallMixin`）与窗口本体
（`ForumWindow`）都要用同一组列宽 / 边距 / 页签常量，原先把它们写在 `forum_window` 里会
让 `forum_wall` 与 `forum_window` 互相 import（循环）。这里把**只依赖 `config.scale` 与
核心层**的那一组常量集中成叶子模块，两边都从这里取；`forum_window` 仍按原名重新导出，
既有导入面（含 `tests/test_forum_window.py` 的 `COLUMN_COUNT` / `SCROLL_GAP` 等）不变。

数值与搬出前逐项相同：``COLUMN_COUNT`` / ``WALL_MARGIN`` / ``COLUMN_SPACING`` /
``SCROLL_GAP`` / ``DEFAULT_WINDOW_WIDTH`` / ``FORUM_PAGES`` 等。
"""

from __future__ import annotations

from config.scale import scale_px
from lib.core.forum import FORUM_MAX_NICKNAME
from lib.script.ui.forum_style import FORUM_SCROLLBAR_WIDTH

COLUMN_COUNT = 3
WALL_MARGIN = scale_px(16, min_abs=13)
COLUMN_SPACING = scale_px(12, min_abs=10)
#: 卡片右侧与滚动条之间的空隙：卡片贴着条子会把滚动条读成「卡片右边框」，最后一列像被压住。
SCROLL_GAP = scale_px(10, min_abs=8)
#: 滚动条连同它前面的空隙占掉的横向空间；滚动条一出现，视口就少这么多宽度。
SCROLL_GUTTER = FORUM_SCROLLBAR_WIDTH + SCROLL_GAP
#: 窗口宽度取工作台的一半左右，三列必须仍能并排放下，所以卡片最小宽度随之下调。
CARD_MIN_WIDTH = scale_px(170, min_abs=150)
#: 最小宽度由三列网格 + 滚动条占位推出，避免窗口窄到把卡片挤出行外（滚动条一出现就少一个
#: `SCROLL_GUTTER`，不预先留出来的话三列会被挤到卡片最小宽度以下）。
MIN_WINDOW_WIDTH = (
    COLUMN_COUNT * CARD_MIN_WIDTH
    + (COLUMN_COUNT - 1) * COLUMN_SPACING
    + 2 * WALL_MARGIN
    + SCROLL_GUTTER
)
DEFAULT_WINDOW_WIDTH = max(MIN_WINDOW_WIDTH, scale_px(620, min_abs=580))
DEFAULT_WINDOW_HEIGHT = scale_px(800, min_abs=700)
LOAD_OLDER_THRESHOLD_PX = scale_px(140, min_abs=90)
#: 昵称上限：核心层 `FORUM_MAX_NICKNAME` 是唯一事实源，这里只做别名。
NICKNAME_MAX_LENGTH = FORUM_MAX_NICKNAME
FORUM_NICKNAME_PLACEHOLDER = "输入昵称…（未输入以匿名发送）"

#: 社区页的三个子页面；顺序就是导航条从左到右的顺序。
FORUM_PAGES = (
    ("wall", "留言墙"),
    ("board", "主论坛"),
    ("account", "账号页"),
)
FORUM_DEFAULT_PAGE = "wall"

#: 页眉小字：留言来自社区，不是官方口径。
FORUM_HEADER_NOTICE = "内容来自社区，不一定来自官方，请仔细甄别"


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
