"""UI ????????"""

from __future__ import annotations

import config.config_runtime as _config_runtime

from config.scale import scale_px
from config.font_config import FONT
from lib.core.graphics.palette import COLORS, UI_THEME


WINDOW = {}
UI = {
    'cmd_window_width':        scale_px(240),   # CMD窗口宽度（包含2px黑边）
    'cmd_window_height':       scale_px(36),    # CMD窗口高度（包含2px黑边）
    'bubble_max_width':        scale_px(360),   # 气泡最大宽度（像素）
    'pet_opacity':             1.0,             # 主宠物透明度（0.0-1.0）
    'ui_widget_opacity':       1.0,             # UI控件透明度（0.0-1.0）
    'tooltip_opacity':         0.8,             # 鼠标悬浮说明透明度（0.0-1.0）
    'ui_fade_duration':        200,  # UI淡入/淡出持续时间（毫秒）
    'auto_hide_mouse_distance': 300,  # 右键相关UI自动关闭距离阈值（xp）
    'workbench_light_theme': False,   # 工作台亮色主题开关
    'render_backend': 'qt',           # 渲染后端（重启后生效）
}
BUBBLE_CONFIG = {
    'default_min_ticks':  4,      # 默认最小显示 tick 数
    'default_max_ticks': 200,     # 默认最大显示 tick 数
    'padding':            scale_px(12),  # 气泡内边距（像素）
    'border_width':       scale_px(2),   # 边框宽度（像素）
    # 默认人格文件路径
    'default_persona_file': 'resc/persona.txt',
}
COMMAND_DIALOG = {
    'idle_timeout_ms': 10000,     # 空闲超时自动关闭时间（毫秒）
    'offset_x':            scale_px(6),  # 相对主宠物的水平偏移（像素）
    'offset_y':            scale_px(0),  # 相对主宠物的垂直偏移（像素）
}
