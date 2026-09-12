"""Canonical product palette shared by the pet panels and the config layer.

These colour tables used to live in ``config/config_ui.py`` while the backend
neutral presenters imported them from there, which made core graphics depend on
the configuration package. The palette now lives here, next to the other pure
graphics contracts, and ``config/config_ui.py`` simply re-exports the same dict
objects so user overrides keep landing on the dicts the presenters read.
"""

from __future__ import annotations

from .types import Color

#: Base palette for the pet / command style widgets.
COLORS = {
    "pink": Color(255, 182, 193),   # 淡粉色 #FFB6C1
    "cyan": Color(173, 216, 230),   # 浅青色 #ADD8E6
    "deep_blue": Color(35, 76, 128),  # 深蓝色 #234C80
    "black": Color(0, 0, 0),
    "text": Color(51, 51, 51),
}

#: Multi-layer panel palette (black frame / cyan mid / pink background).
UI_THEME = {
    "border": Color(0, 0, 0),         # 黑色外框
    "mid": Color(173, 216, 230),      # 浅青色中框（主宠物同款）
    "bg": Color(255, 182, 193),       # 淡粉色背景（主宠物粉色）
    "text": Color(0, 0, 0),           # 黑色字体
    "icon": Color(0, 0, 0),           # 黑色图标
    "highlight": Color(255, 200, 210),  # 高亮选中（稍亮的粉色）
    # 深色版本（饱和度提高15%）
    "deep_cyan": Color(129, 198, 221),  # 深青色（浅青色饱和度+15%）
    "deep_pink": Color(255, 149, 164),  # 深粉色（浅粉色饱和度+15%）
    "deep_blue": Color(35, 76, 128),    # 深蓝色
}

__all__ = ["COLORS", "UI_THEME"]
