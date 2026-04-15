"""AI 设置面板：编辑并保存 config/ollama_config.py。"""

from __future__ import annotations

import ast
import copy
import ctypes
import json
import math
import os
import random
import re
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QListView,
    QPushButton,
    QCheckBox,
    QApplication,
    QStyleOptionComboBox,
    QStyle,
    QGraphicsOpacityEffect,
    QTabBar,
    QScrollArea,
    QSizePolicy,
    QFileDialog,
    QSlider,
    QMenu,
)
from PyQt5.QtGui import QPainter, QColor, QPolygon, QFontMetrics, QCursor

from config.config import UI_THEME, UI
from config.font_config import get_ui_font, get_digit_font
from config.scale import scale_px
from config.shared_storage import ensure_shared_config_ready, get_shared_config_path
from lib.script.ui.ai_settings_validators import validate_ai_values
from lib.script.ui.ai_settings_storage import load_ai_values, save_ai_values, apply_ai_runtime
from lib.script.ui.ai_settings_tabs import (
    attach_ai_settings_tabs,
    layout_ai_settings_tab_bar,
    show_ai_settings_tab_bar,
    hide_ai_settings_tab_bar,
    layout_ai_settings_tab_panels,
    set_active_ai_settings_tab,
)
from lib.core.anchor_utils import animate_opacity
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger
from lib.script.chat.ollama_registry import get_available_model_names, get_model_list_error
from lib.script.microphone_stt.push_to_talk import parse_hotkey_binding
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.yuanbao_free_api import get_yuanbao_free_api_service
from lib.script.yuanbao_free_api.service import get_yuanbao_free_api_log_path

_logger = get_logger(__name__)


_GPU_MODE_CPU = "cpu"
_GPU_MODE_GPU = "gpu"
_GPU_MODE_AUTO = "auto"

_DEFAULT_VALUES = {
    "api_key": "",
    "force_reply_mode": "4",
    "api_base_url": "http://127.0.0.1:8000/v1",
    "api_model": "deepseek-v3",
    "yuanbao_login_url": "https://yuanbao.tencent.com/chat/naQivTmsDa",
    "yuanbao_free_api_enabled": False,
    "yuanbao_hy_source": "web",
    "yuanbao_hy_user": "",
    "yuanbao_x_uskey": "",
    "yuanbao_agent_id": "naQivTmsDa",
    "yuanbao_chat_id": "",
    "yuanbao_remove_conversation": False,
    "yuanbao_upload_images": True,
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen2.5",
    "num_gpu": -1,
    "num_thread": 0,
    "api_temperature": 0.8,
    "gsv_auto_start": True,
    "gsv_temperature": 1.35,
    "gsv_speed_factor": 1.0,
    "ai_voice_max_chars": 40,
    "gsv_cache_max_files": 20,
    "memory_context_limit": 12,
    "api_enable_thinking": False,
    "auto_companion_enabled": True,
}

_WATERMARK_TEXT = "Aemeath\nAIsetting"
_TITLE_FONT_SIZE = scale_px(19, min_abs=14)   # 14 + 5xp
_CONFIG_FONT_SIZE = scale_px(14, min_abs=10)  # 12 + 2xp
_DROPDOWN_ITEM_FONT_SIZE = max(scale_px(8, min_abs=8), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_PANEL_SCALE = 1.05
_LEFT_WM_SCALE = 2.0 / 3.0
_AI_HINT_TEXT = "保存后会写入本地 AI 配置文件，建议重启程序后完整生效"
_GENERAL_HINT_TEXT = "保存后会写入本地配置文件，建议重启程序后完整生效"
_TITLE_ROW_FIXED_HEIGHT = scale_px(34, min_abs=28)
_HINT_FONT_SIZE = max(scale_px(12, min_abs=9), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_SCROLLBAR_RIGHT_SHIFT = scale_px(10, min_abs=8)
_CONFIG_FIELD_WIDTH = scale_px(320, min_abs=280)
_CONFIG_LABEL_WIDTH = scale_px(176, min_abs=152)
_CONTROL_PANEL_CONTENT_HEIGHT = int(round(scale_px(620, min_abs=540) * 2.0 / 3.0))
_CORE_CONFIG_FILES = (
    "config.py",
    "config_ui.py",
    "config_animation.py",
    "config_entities.py",
    "config_music.py",
    "config_voice.py",
    "config_timeouts.py",
    "config_runtime.py",
)
_SHARED_RESET_FILES = (*_CORE_CONFIG_FILES, "ollama_config.py")

_DICT_TO_CONFIG_FILE = {
    "COLORS": "config_ui.py",
    "UI_THEME": "config_ui.py",
    "WINDOW": "config_ui.py",
    "UI": "config_ui.py",
    "BUBBLE_CONFIG": "config_ui.py",
    "COMMAND_DIALOG": "config_ui.py",
    "ANIMATION": "config_animation.py",
    "BEHAVIOR": "config_animation.py",
    "PARTICLES": "config_animation.py",
    "PHYSICS": "config_animation.py",
    "SNOW_LEOPARD": "config_entities.py",
    "SNOW_PILE": "config_entities.py",
    "SOFA": "config_entities.py",
    "MORTOR": "config_entities.py",
    "CLOCK": "config_entities.py",
    "SPEAKER": "config_entities.py",
    "OBJECTS": "config_entities.py",
    "SNOWBALL": "config_entities.py",
    "SOUND": "config_music.py",
    "SPEAKER_AUDIO": "config_music.py",
    "SPEAKER_SEARCH_UI": "config_music.py",
    "CLOUD_MUSIC": "config_music.py",
    "CHAT": "config_voice.py",
    "VOICE": "config_voice.py",
    "TOOL_DISPATCHER": "config_timeouts.py",
    "TIMEOUTS": "config_timeouts.py",
    "DRAW": "config_runtime.py",
    "STARTUP": "config_runtime.py",
}

_GENERAL_CONFIG_CATEGORIES = [
    {
        "id": "ui_anim",
        "tab": "界面动画",
        "title": "界面与动画配置",
        "sections": [
            ("ANIMATION", "动画"),
            ("UI", "界面"),
            ("COMMAND_DIALOG", "命令框"),
        ],
    },
    {
        "id": "behavior_physics",
        "tab": "行为物理",
        "title": "行为与物理配置",
        "sections": [
            ("PARTICLES", "粒子"),
            ("BEHAVIOR", "行为"),
            ("PHYSICS", "物理"),
        ],
    },
    {
        "id": "audio_music",
        "tab": "音频音乐",
        "title": "音频与音乐配置",
        "sections": [
            ("SOUND", "音量"),
            ("VOICE", "语音"),
            ("SPEAKER_AUDIO", "音频可视化"),
            ("CLOUD_MUSIC", "云音乐"),
        ],
    },
    {
        "id": "scene_objects",
        "tab": "场景对象",
        "title": "场景对象配置",
        "sections": [
            ("SNOW_LEOPARD", "雪豹"),
            ("SNOW_PILE", "雪堆"),
            ("SOFA", "沙发"),
            ("MORTOR", "摩托"),
            ("CLOCK", "闹钟"),
            ("SPEAKER", "音响"),
            ("SNOWBALL", "雪球"),
            ("OBJECTS", "物体"),
        ],
    },
    {
        "id": "system_dispatch",
        "tab": "系统调度",
        "title": "系统与调度配置",
        "sections": [
            ("TIMEOUTS", "超时"),
            ("TOOL_DISPATCHER", "工具调度"),
            ("CLOUD_MUSIC", "鸣潮设置"),
            ("DRAW", "绘制"),
            ("STARTUP", "启动"),
        ],
    },
    {
        "id": "desktop_pet_update",
        "tab": "桌宠更新",
        "title": "桌宠更新管理",
        "sections": [],
    },
]

_CATEGORY_KEY_ALLOWLIST = {
    "ui_anim": {
        "ANIMATION": {"frame_fps", "gif_fps", "start_exit_enabled"},
        "UI": {"pet_opacity", "ui_widget_opacity", "ui_fade_duration", "auto_hide_mouse_distance"},
        "COMMAND_DIALOG": {"idle_timeout_ms"},
    },
    "behavior_physics": {
        "PARTICLES": {"enable_stroke", "fade_threshold"},
        "BEHAVIOR": {
            "wander_near_speaker_radius",
            "double_click_ticks",
            "move_max_speed",
            "move_acceleration",
            "move_min_speed",
        },
        "PHYSICS": {
            "max_bounces",
            "ground_y_pct",
            "air_resistance",
        },
    },
    "scene_objects": {
        "SNOW_LEOPARD": {
            "spawn_y_min",
            "spawn_y_max",
            "interact_radius",
            "natural_spawn_limit",
            "jump_power_min",
            "jump_power_max",
        },
        "SNOW_PILE": {
            "spawn_y_min",
            "spawn_y_max",
            "scale_min",
            "scale_max",
            "batch_interval",
            "batch_size",
            "batch_item_interval",
            "spawn_power_min",
            "spawn_power_max",
        },
        "SOFA": {
            "spawn_y_min",
            "spawn_y_max",
            "protect_radius",
        },
        "MORTOR": {
            "spawn_y_min",
            "spawn_y_max",
            "move_speed_px_per_frame",
            "bgm_enabled",
        },
        "CLOCK": {
            "spawn_y_min",
            "spawn_y_max",
            "countdown_ss",
        },
        "SPEAKER": {
            "spawn_y_min",
            "spawn_y_max",
        },
        "OBJECTS": {
            "object_opacity",
        },
        "SNOWBALL": {
            "max_count",
            "spawn_y_min",
            "spawn_y_max",
            "size_min",
            "size_max",
            "lifetime_min",
            "lifetime_max",
        },
    },
    "audio_music": {
        "SOUND": {
            "master_volume",
            "main_pet_volume",
            "game_object_volume",
        },
        "VOICE": {
            "voice_volume",
            "microphone_push_to_talk_key",
            "microphone_silence_timeout_secs",
            "microphone_speech_rms_threshold",
        },
        "SPEAKER_AUDIO": set(),
        "CLOUD_MUSIC": {
            "provider",
            "default_volume",
            "particle_interval",
            "search_result_limit",
            "cache_dir",
            "local_music_dir",
        },
    },
    "system_dispatch": {
        "TIMEOUTS": {
            "api_list",
            "api_request",
            "login_wait",
            "login_call",
            "cmd_exec",
            "idle_close_ms",
        },
        "TOOL_DISPATCHER": set(),
        "CLOUD_MUSIC": {
            "launch_wuwa_path",
        },
        "DRAW": {
            "scale",
        },
        "STARTUP": {
            "ensure_desktop_shortcut",
        },
    },
    "desktop_pet_update": {},  # 桌宠更新标签页 - 没有配置字段，只有按钮
}

_GENERAL_BOOL_KEYS: set[tuple[str, str]] = {
    ("ANIMATION", "start_exit_enabled"),
    ("PARTICLES", "enable_stroke"),
    ("MORTOR", "bgm_enabled"),
    ("STARTUP", "ensure_desktop_shortcut"),
}

_GENERAL_NUMERIC_RULES: dict[tuple[str, str], tuple[str, float, float]] = {
    ("ANIMATION", "frame_fps"): ("int", 1, 120),
    ("ANIMATION", "gif_fps"): ("int", 1, 60),
    ("UI", "pet_opacity"): ("number", 0.0, 1.0),
    ("UI", "ui_widget_opacity"): ("number", 0.0, 1.0),
    ("UI", "ui_fade_duration"): ("int", 0, 5000),
    ("UI", "auto_hide_mouse_distance"): ("int", 0, 5000),
    ("COMMAND_DIALOG", "idle_timeout_ms"): ("int", 0, 3600000),
    ("PARTICLES", "fade_threshold"): ("number", 0.0, 1.0),
    ("BEHAVIOR", "wander_near_speaker_radius"): ("int", 0, 10000),
    ("BEHAVIOR", "double_click_ticks"): ("int", 1, 60),
    ("BEHAVIOR", "move_min_speed"): ("number", 0.0, 100.0),
    ("BEHAVIOR", "move_acceleration"): ("number", 0.0, 50.0),
    ("BEHAVIOR", "move_max_speed"): ("number", 0.0, 100.0),
    ("PHYSICS", "max_bounces"): ("int", 0, 100),
    ("PHYSICS", "ground_y_pct"): ("number", 0.0, 1.0),
    ("PHYSICS", "air_resistance"): ("number", 0.0, 1.0),
    ("SNOW_LEOPARD", "spawn_y_min"): ("number", 0.0, 1.0),
    ("SNOW_LEOPARD", "spawn_y_max"): ("number", 0.0, 1.0),
    ("SNOW_LEOPARD", "interact_radius"): ("int", 1, 5000),
    ("SNOW_LEOPARD", "natural_spawn_limit"): ("int", 1, 512),
    ("SNOW_LEOPARD", "jump_power_min"): ("number", 0.01, 50.0),
    ("SNOW_LEOPARD", "jump_power_max"): ("number", 0.01, 50.0),
    ("SNOW_PILE", "spawn_y_min"): ("number", 0.0, 1.0),
    ("SNOW_PILE", "spawn_y_max"): ("number", 0.0, 1.0),
    ("SNOW_PILE", "scale_min"): ("number", 0.01, 20.0),
    ("SNOW_PILE", "scale_max"): ("number", 0.01, 20.0),
    ("SNOW_PILE", "spawn_power_min"): ("number", 0.01, 50.0),
    ("SNOW_PILE", "spawn_power_max"): ("number", 0.01, 50.0),
    ("SOFA", "spawn_y_min"): ("number", 0.0, 1.0),
    ("SOFA", "spawn_y_max"): ("number", 0.0, 1.0),
    ("SOFA", "protect_radius"): ("int", 0, 5000),
    ("MORTOR", "spawn_y_min"): ("number", 0.0, 1.0),
    ("MORTOR", "spawn_y_max"): ("number", 0.0, 1.0),
    ("MORTOR", "move_speed_px_per_frame"): ("number", 0.01, 100.0),
    ("CLOCK", "spawn_y_min"): ("number", 0.0, 1.0),
    ("CLOCK", "spawn_y_max"): ("number", 0.0, 1.0),
    ("CLOCK", "countdown_ss"): ("int", 0, 59),
    ("SPEAKER", "spawn_y_min"): ("number", 0.0, 1.0),
    ("SPEAKER", "spawn_y_max"): ("number", 0.0, 1.0),
    ("OBJECTS", "object_opacity"): ("number", 0.0, 1.0),
    ("SNOWBALL", "max_count"): ("int", 1, 512),
    ("SNOWBALL", "spawn_y_min"): ("number", 0.0, 1.0),
    ("SNOWBALL", "spawn_y_max"): ("number", 0.0, 1.0),
    ("SNOWBALL", "size_min"): ("int", 1, 1000),
    ("SNOWBALL", "size_max"): ("int", 1, 1000),
    ("SNOWBALL", "lifetime_min"): ("int", 1, 3600),
    ("SNOWBALL", "lifetime_max"): ("int", 1, 3600),
    ("SOUND", "master_volume"): ("number", 0.0, 1.0),
    ("SOUND", "main_pet_volume"): ("number", 0.0, 1.0),
    ("SOUND", "game_object_volume"): ("number", 0.0, 1.0),
    ("VOICE", "voice_volume"): ("number", 0.0, 1.0),
    ("VOICE", "microphone_silence_timeout_secs"): ("number", 0.5, 10.0),
    ("VOICE", "microphone_speech_rms_threshold"): ("int", 50, 8000),
    ("CLOUD_MUSIC", "default_volume"): ("number", 0.0, 1.0),
    ("CLOUD_MUSIC", "particle_interval"): ("int", 1, 1000),
    ("CLOUD_MUSIC", "search_result_limit"): ("int", 1, 128),
    ("TIMEOUTS", "api_list"): ("int", 1, 600),
    ("TIMEOUTS", "api_request"): ("int", 1, 600),
    ("TIMEOUTS", "login_wait"): ("int", 1, 600),
    ("TIMEOUTS", "login_call"): ("int", 1, 600),
    ("TIMEOUTS", "cmd_exec"): ("int", 1, 600),
    ("TIMEOUTS", "idle_close_ms"): ("int", 100, 3600000),
    ("DRAW", "scale"): ("number", 0.1, 8.0),
}

_GENERAL_TUPLE_INT_RULES: dict[tuple[str, str], tuple[int, int]] = {
    ("SNOW_PILE", "batch_interval"): (1, 3600000),
    ("SNOW_PILE", "batch_size"): (1, 128),
    ("SNOW_PILE", "batch_item_interval"): (1, 3600000),
}

_GENERAL_RANGE_RELATIONS: tuple[tuple[str, str, str], ...] = (
    ("SNOW_LEOPARD", "spawn_y_min", "spawn_y_max"),
    ("SNOW_PILE", "spawn_y_min", "spawn_y_max"),
    ("SOFA", "spawn_y_min", "spawn_y_max"),
    ("MORTOR", "spawn_y_min", "spawn_y_max"),
    ("CLOCK", "spawn_y_min", "spawn_y_max"),
    ("SPEAKER", "spawn_y_min", "spawn_y_max"),
    ("SNOW_LEOPARD", "jump_power_min", "jump_power_max"),
    ("SNOW_PILE", "scale_min", "scale_max"),
    ("SNOW_PILE", "spawn_power_min", "spawn_power_max"),
    ("SNOWBALL", "spawn_y_min", "spawn_y_max"),
    ("SNOWBALL", "size_min", "size_max"),
    ("SNOWBALL", "lifetime_min", "lifetime_max"),
    ("BEHAVIOR", "move_min_speed", "move_max_speed"),
)

_VOLUME_SLIDER_FIELDS: set[tuple[str, str]] = {
    ("SOUND", "master_volume"),
    ("SOUND", "main_pet_volume"),
    ("SOUND", "game_object_volume"),
    ("VOICE", "voice_volume"),
    ("CLOUD_MUSIC", "default_volume"),
}

_DICT_FRIENDLY_NAME = {
    "UI": "界面",
    "ANIMATION": "动画",
    "BUBBLE_CONFIG": "气泡",
    "COMMAND_DIALOG": "命令框",
    "BEHAVIOR": "行为",
    "PHYSICS": "物理",
    "PARTICLES": "粒子",
    "SOUND": "音量",
    "VOICE": "语音",
    "SPEAKER_AUDIO": "音频可视化",
    "CLOUD_MUSIC": "云音乐",
    "SNOW_LEOPARD": "雪豹",
    "SNOW_PILE": "雪堆",
    "SOFA": "沙发",
    "MORTOR": "摩托",
    "CLOCK": "闹钟",
    "SPEAKER": "音响",
    "OBJECTS": "物体",
    "SNOWBALL": "雪球",
    "TIMEOUTS": "超时",
    "TOOL_DISPATCHER": "工具调度",
    "DRAW": "绘制",
    "STARTUP": "启动",
}

_KEY_FRIENDLY_NAME = {
    "UI": {
        "cmd_window_width": "命令框宽度",
        "cmd_window_height": "命令框高度",
        "bubble_max_width": "气泡最大宽度",
        "pet_opacity": "桌宠透明度",
        "ui_widget_opacity": "UI控件透明度",
        "ui_fade_duration": "淡入淡出时长(ms)",
        "auto_hide_mouse_distance": "自动关闭阈值",
    },
    "ANIMATION": {
        "pet_size": "宠物尺寸",
        "gif_fps": "GIF帧率",
        "frame_fps": "帧率",
        "start_exit_enabled": "启动/退出动画",
    },
    "STARTUP": {
        "ensure_desktop_shortcut": "启动时创建快捷方式",
    },
    "BUBBLE_CONFIG": {
        "default_min_ticks": "默认最小显示tick",
        "default_max_ticks": "默认最大显示tick",
        "padding": "气泡内边距",
        "border_width": "气泡边框宽度",
        "default_persona_file": "默认人格文件",
    },
    "COMMAND_DIALOG": {
        "idle_timeout_ms": "自动关闭时间(ms)",
        "offset_x": "水平偏移",
        "offset_y": "垂直偏移",
    },
    "BEHAVIOR": {
        "auto_behavior_interval": "自动行为间隔(ms)",
        "auto_wander_interval": "自动漫游间隔(ms)",
        "wander_near_speaker_radius": "音响漫游半径",
        "random_states": "随机状态列表",
        "double_click_ticks": "双击判定",
        "move_min_speed": "最小移动速度",
        "move_acceleration": "移动加速度",
        "move_max_speed": "最大移动速度",
        "move_decel_distance": "减速距离",
    },
    "PHYSICS": {
        "snow_leopard_jump_vx": "雪豹跳跃水平速度",
        "snow_leopard_jump_vy": "雪豹跳跃垂直速度",
        "max_throw_vx": "最大抛掷水平速度",
        "max_throw_vy": "最大抛掷垂直速度",
        "drag_threshold": "拖拽阈值",
        "max_bounces": "最大弹跳次数",
        "ground_y_pct": "地面高度比例",
        "air_resistance": "空气阻力",
        "min_velocity": "静止速度阈值",
        "fade_step": "淡出步长",
        "fade_interval_ms": "淡出间隔(ms)",
        "flip_interval_min": "自动翻转最小间隔(ms)",
        "flip_interval_max": "自动翻转最大间隔(ms)",
    },
    "PARTICLES": {
        "enable_stroke": "启用粒子描边",
        "fade_threshold": "淡出阈值",
    },
    "SOUND": {
        "master_volume": "总音量",
        "main_pet_volume": "主宠物音量",
        "game_object_volume": "游戏物体音量",
    },
        "VOICE": {
            "voice_volume": "语音音量",
            "microphone_push_to_talk_key": "语聊快捷键(留空禁用)",
            "microphone_silence_timeout_secs": "静音停止时长(s)",
            "microphone_speech_rms_threshold": "说话判定阈值",
        },
    "SPEAKER_AUDIO": {
        "scale_range": "缩放范围",
        "scale_exp": "缩放指数",
        "ema_attack": "EMA攻击系数",
        "ema_decay": "EMA衰减系数",
        "freq_min": "最低频率(Hz)",
        "freq_max": "最高频率(Hz)",
    },
    "CLOUD_MUSIC": {
        "provider": "音乐平台",
        "bitrate_ladder": "音质梯度(bps)",
        "default_volume": "默认音量",
        "pygame_init_wait": "pygame初始化等待(s)",
        "particle_interval": "音符粒子间隔(帧)",
        "search_result_limit": "搜索结果上限(首)",
        "cache_dir": "缓存目录",
        "local_music_dir": "本地音乐文件夹",
        "launch_wuwa_path": "启动鸣潮路径文件",
    },
    "SNOW_LEOPARD": {
        "gif_file": "GIF资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "interact_radius": "交互半径",
        "natural_spawn_limit": "自然生成上限",
        "jump_power_min": "跳跃力度最小倍率",
        "jump_power_max": "跳跃力度最大倍率",
        "anchor_offset_y": "锚点Y偏移",
    },
    "SNOW_PILE": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "scale_min": "随机缩放最小倍率",
        "scale_max": "随机缩放最大倍率",
        "batch_interval": "批次间隔(ms)",
        "batch_size": "批次数量范围",
        "batch_item_interval": "批次内间隔(ms)",
        "spawn_power_min": "生成力度最小倍率",
        "spawn_power_max": "生成力度最大倍率",
    },
    "SOFA": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "protect_radius": "保护半径",
    },
    "MORTOR": {
        "png_file": "PNG资源路径",
        "target_width": "目标宽度",
        "move_speed_px_per_frame": "移动速度(像素/帧)",
        "move_accel_per_tick": "按键加速度",
        "move_decel_per_tick": "松键减速度",
        "move_speed_max": "最大移动速度",
        "jump_vy": "跳跃垂直速度",
        "bgm_enabled": "摩托BGM",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
    },
    "CLOCK": {
        "png_file": "PNG资源路径",
        "target_width": "目标宽度",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "countdown_ss": "默认倒计时秒",
    },
    "SPEAKER": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
    },
    "OBJECTS": {
        "object_opacity": "物体透明度",
    },
    "SNOWBALL": {
        "png_file": "PNG资源路径",
        "max_count": "最大存在数量",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "size_min": "最小直径(px)",
        "size_max": "最大直径(px)",
        "lifetime_min": "最短寿命(秒)",
        "lifetime_max": "最长寿命(秒)",
    },
    "TIMEOUTS": {
        "api_list": "API模型列表超时(s)",
        "api_request": "API请求超时(s)",
        "login_wait": "登录等待超时(s)",
        "login_call": "登录调用超时(s)",
        "cmd_exec": "命令执行超时(s)",
        "idle_close_ms": "空闲关闭(ms)",
    },
    "TOOL_DISPATCHER": {
        "tool_pattern": "工具触发正则",
        "play_index": "搜索结果播放索引",
        "auto_spawn_speaker_count": "自动生成音响数量",
    },
    "DRAW": {
        "scale": "绘制缩放",
        "screen_width": "屏幕宽度",
        "screen_height": "屏幕高度",
        "scale_rule": "缩放规则",
    },
}


def _friendly_section_name(dict_name: str, fallback: str = "") -> str:
    if fallback and fallback != dict_name:
        return fallback
    return _DICT_FRIENDLY_NAME.get(dict_name, fallback or dict_name)


def _friendly_field_section_name(dict_name: str, key: str) -> str:
    if str(dict_name) == "CLOUD_MUSIC" and str(key) == "launch_wuwa_path":
        return "鸣潮设置"
    return _friendly_section_name(dict_name, dict_name)


def _friendly_key_name(dict_name: str, key: str) -> str:
    return _KEY_FRIENDLY_NAME.get(dict_name, {}).get(key, key)


def _range_pair_signature(key: str) -> tuple[str, str] | None:
    k = str(key)
    if "_min_" in k:
        return k.replace("_min_", "_range_"), "min"
    if "_max_" in k:
        return k.replace("_max_", "_range_"), "max"
    if "_lower_" in k:
        return k.replace("_lower_", "_range_"), "lower"
    if "_upper_" in k:
        return k.replace("_upper_", "_range_"), "upper"
    if k.endswith("_min"):
        return k[:-4], "min"
    if k.endswith("_max"):
        return k[:-4], "max"
    if k.endswith("_lower"):
        return k[:-6], "lower"
    if k.endswith("_upper"):
        return k[:-6], "upper"
    return None


def _friendly_range_name(dict_name: str, left_key: str, right_key: str) -> str:
    left_name = _friendly_key_name(dict_name, left_key)
    right_name = _friendly_key_name(dict_name, right_key)
    replace_rules = (
        ("下限", "上限"),
        ("最小", "最大"),
        ("最低", "最高"),
        ("Lower", "Upper"),
        ("Min", "Max"),
    )
    for l_token, r_token in replace_rules:
        if l_token in left_name and r_token in right_name:
            l_stem = left_name.replace(l_token, "")
            r_stem = right_name.replace(r_token, "")
            if l_stem == r_stem and l_stem.strip():
                return f"{l_stem}范围"
    return f"{left_name} / {right_name}"


def _config_rel_path_for_dict(dict_name: str) -> str:
    rel_name = _DICT_TO_CONFIG_FILE.get(str(dict_name))
    if not rel_name:
        raise ValueError(f"未找到配置文件映射: {dict_name}")
    return rel_name


def _config_path_for_dict(dict_name: str) -> Path:
    return _project_root() / "config" / _config_rel_path_for_dict(dict_name)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _mirror_config_text_to_shared(rel_name: str, text: str) -> None:
    try:
        ensure_shared_config_ready()
        shared_path = get_shared_config_path(rel_name)
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(shared_path, text)
    except Exception as e:
        _logger.warning("镜像写入外部配置失败(%s): %s", rel_name, e)


def _reset_shared_core_configs_from_project() -> None:
    """将 C 盘共享配置中的核心配置文件重置为项目当前版本。"""
    try:
        ensure_shared_config_ready()
        project_cfg_dir = _project_root() / "config"
        for rel_name in _SHARED_RESET_FILES:
            src = project_cfg_dir / rel_name
            if not src.exists():
                continue
            text = src.read_text(encoding="utf-8")
            dst = get_shared_config_path(rel_name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            _write_text_atomic(dst, text)
    except Exception as e:
        _logger.warning("重置C盘本地配置失败: %s", e)


def _is_supported_config_value(value) -> bool:
    basic_types = (bool, int, float, str)
    if isinstance(value, basic_types):
        return True
    if isinstance(value, tuple):
        return all(isinstance(item, basic_types) for item in value)
    if isinstance(value, list):
        return all(isinstance(item, basic_types) for item in value)
    return False


def _format_config_editor_value(value) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


def _py_literal_any(value) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float, str)):
        return repr(value)
    if isinstance(value, tuple):
        inner = ", ".join(_py_literal_any(item) for item in value)
        if len(value) == 1:
            inner += ","
        return f"({inner})"
    if isinstance(value, list):
        inner = ", ".join(_py_literal_any(item) for item in value)
        return f"[{inner}]"
    return repr(value)


def _replace_config_dict_item(text: str, dict_name: str, key: str, py_literal: str) -> str:
    block_pattern = re.compile(
        rf"(?ms)^(\s*{re.escape(dict_name)}\s*=\s*\{{)(.*?)(^\s*\}})",
    )
    block_match = block_pattern.search(text)
    if not block_match:
        raise ValueError(f"未找到配置字典: {dict_name}")

    body = block_match.group(2)
    item_pattern = re.compile(rf"(?m)^(\s*'{re.escape(key)}'\s*:\s*).*(,\s*(?:#.*)?)$")
    if not item_pattern.search(body):
        raise ValueError(f"未找到配置项: {dict_name}.{key}")
    new_body = item_pattern.sub(lambda m: f"{m.group(1)}{py_literal}{m.group(2)}", body, count=1)
    return text[:block_match.start(2)] + new_body + text[block_match.end(2):]


def _save_general_config(values_by_dict: dict[str, dict]) -> None:
    file_texts: dict[str, str] = {}
    for dict_name, items in values_by_dict.items():
        cfg_path = _config_path_for_dict(dict_name)
        rel_name = cfg_path.name
        text = file_texts.get(rel_name)
        if text is None:
            text = cfg_path.read_text(encoding="utf-8")
        for key, value in items.items():
            text = _replace_config_dict_item(text, dict_name, key, _py_literal_any(value))
        file_texts[rel_name] = text

    for rel_name, text in file_texts.items():
        cfg_path = _project_root() / "config" / rel_name
        _write_text_atomic(cfg_path, text)
        _mirror_config_text_to_shared(rel_name, text)


def _apply_general_runtime(values_by_dict: dict[str, dict]) -> None:
    import config.config as cc

    for dict_name, items in values_by_dict.items():
        target = getattr(cc, dict_name, None)
        if isinstance(target, dict):
            target.update(items)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _decode_process_output(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-16", "utf-16le", "utf-16be", "gb18030", "cp936", "cp1252"):
        try:
            return raw.decode(encoding).replace("\x00", "")
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")


def _run_capture_text(cmd: list[str], timeout: int = 2) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)
    stdout = _decode_process_output(result.stdout or b"")
    stderr = _decode_process_output(result.stderr or b"")
    return result.returncode, stdout, stderr


def _get_powershell_executable() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    ps_exe = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return ps_exe if os.path.exists(ps_exe) else "powershell"


def _to_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]

    def __init__(self):
        super().__init__()
        self.dwLength = ctypes.sizeof(self)


def _get_total_memory_bytes() -> int:
    try:
        memory_status = _MEMORYSTATUSEX()
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
            total = int(memory_status.ullTotalPhys)
            if total > 0:
                return total
    except Exception:
        pass
    return 0


def _is_virtual_or_software_gpu(name: str) -> bool:
    text = str(name or '').strip().lower()
    if not text:
        return True
    virtual_keywords = (
        'microsoft basic',
        'basic render',
        'indirect display',
        'idd',
        'displaylink',
        'mirror driver',
        'remote display',
        'virtual',
        'vmware',
        'hyper-v',
        'virtio',
        'citrix',
        'parsec',
        'asklink',
    )
    return any(keyword in text for keyword in virtual_keywords)


def _gpu_pick_score(item: dict) -> tuple[int, int, int]:
    name = str(item.get('Name') or '').strip().lower()
    ram = _to_int(item.get('AdapterRAM'))
    if _is_virtual_or_software_gpu(name):
        return 0, 0, ram
    if any(keyword in name for keyword in ('nvidia', 'geforce', 'rtx', 'gtx', 'quadro', 'tesla')):
        vendor_rank = 3
    elif any(keyword in name for keyword in ('amd', 'radeon', 'rx ', 'vega', 'firepro')):
        vendor_rank = 2
    elif any(keyword in name for keyword in ('intel', 'arc', 'iris', 'uhd', 'hd graphics')):
        vendor_rank = 1
    else:
        vendor_rank = 1 if name else 0
    return 1, vendor_rank, ram


def _format_gb_text(byte_value: int | None) -> str:
    value = int(byte_value or 0)
    gib = value / (1024 ** 3)
    return f"{gib:.2f} GB"


def _query_hardware_watermark_lines() -> tuple[str, str]:
    """返回两行硬件水印文本。"""
    fallback_line1 = "UnKnow GPU 0.00 GB"
    fallback_line2 = "RAM 0.00 GB"
    try:
        total_memory = _get_total_memory_bytes()
        cmd = [
            _get_powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "$ErrorActionPreference='SilentlyContinue'; "
            "$g=Get-CimInstance Win32_VideoController | Where-Object { $_.Name } | "
            "Select-Object Name,@{Name='AdapterRAM';Expression={[UInt64]($_.AdapterRAM)}}; "
            "@{gpus=$g} | ConvertTo-Json -Compress",
        ]
        rc, stdout, _stderr = _run_capture_text(cmd, timeout=5)
        if rc != 0:
            ram_text = _format_gb_text(total_memory)
            return fallback_line1, f"RAM {ram_text}"
        payload = (stdout or "").strip()
        if not payload:
            ram_text = _format_gb_text(total_memory)
            return fallback_line1, f"RAM {ram_text}"

        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            ram_text = _format_gb_text(total_memory)
            return fallback_line1, f"RAM {ram_text}"

        raw_items = parsed.get("gpus")
        if isinstance(raw_items, dict):
            items = [raw_items]
        elif isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
        else:
            items = []
        if not items:
            ram_text = _format_gb_text(total_memory)
            return fallback_line1, f"RAM {ram_text}"

        filtered_items = [item for item in items if _gpu_pick_score(item)[0] > 0]
        picked = max(filtered_items or items, key=_gpu_pick_score)
        model = str(picked.get("Name") or "").strip() or "UnKnow GPU"
        vram_text = _format_gb_text(_to_int(picked.get("AdapterRAM")))
        ram_text = _format_gb_text(total_memory)
        return f"{model} {vram_text}", f"RAM {ram_text}"
    except Exception:
        return fallback_line1, fallback_line2


def _gpu_mode_from_num_gpu(num_gpu_value) -> str:
    try:
        num_gpu = int(num_gpu_value)
    except (TypeError, ValueError):
        return _GPU_MODE_AUTO
    if num_gpu == 0:
        return _GPU_MODE_CPU
    if num_gpu > 0:
        return _GPU_MODE_GPU
    return _GPU_MODE_AUTO


def _num_gpu_from_mode(mode: str) -> int:
    if mode == _GPU_MODE_CPU:
        return 0
    if mode == _GPU_MODE_GPU:
        # 使用较大层数，尽量将更多层卸载到 GPU。
        return 999
    return -1


class _WatermarkComboBox(QComboBox):
    """项目风格下拉框：自绘按钮内三角与 List 水印。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._before_popup_callback: Callable[[], None] | None = None
        self._popup_refreshing = False

    def set_before_popup_callback(self, callback: Callable[[], None] | None) -> None:
        self._before_popup_callback = callback

    def showPopup(self) -> None:
        callback = self._before_popup_callback
        if callable(callback) and not self._popup_refreshing:
            self._popup_refreshing = True
            try:
                callback()
            finally:
                self._popup_refreshing = False

        if self.count() <= 0:
            return

        super().showPopup()
        popup = self.view().window() if self.view() is not None else None
        if popup is not None:
            popup.raise_()
            popup.activateWindow()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        arrow_rect = self.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxArrow, self)
        if not arrow_rect.isValid() or arrow_rect.width() <= 0 or arrow_rect.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        wm_font = get_digit_font(
            size=max(scale_px(10, min_abs=8), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1) + scale_px(2, min_abs=2))
        )
        wm_font.setBold(True)
        painter.setFont(wm_font)

        local_pos = self.mapFromGlobal(QCursor.pos())
        arrow_hovered = self.underMouse() and arrow_rect.contains(local_pos)
        deep_cyan_color = QColor(UI_THEME.get("deep_cyan", UI_THEME.get("text", QColor(0, 0, 0))))
        light_cyan_color = QColor(UI_THEME.get("cyan", UI_THEME.get("mid", deep_cyan_color.lighter(120))))
        wm_color = light_cyan_color if arrow_hovered else deep_cyan_color
        wm_color.setAlpha(220)
        painter.setPen(wm_color)

        text_rect = arrow_rect.adjusted(scale_px(2), 0, -scale_px(6, min_abs=4), 0)
        watermark = "List"
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, watermark)

        fm = QFontMetrics(wm_font)
        text_w = fm.horizontalAdvance(watermark)
        text_right = text_rect.right()
        text_left = text_right - text_w + 1

        tri_cx = text_left - scale_px(6, min_abs=4) - scale_px(10, min_abs=10)
        tri_cy = arrow_rect.center().y()
        tri_half_w = scale_px(4, min_abs=3)
        tri_h = scale_px(5, min_abs=4)
        tri = QPolygon([
            QPoint(tri_cx - tri_half_w, tri_cy - tri_h // 2),
            QPoint(tri_cx + tri_half_w, tri_cy - tri_h // 2),
            QPoint(tri_cx, tri_cy + tri_h // 2),
        ])

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(UI_THEME["text"]))
        painter.drawPolygon(tri)

    def wheelEvent(self, event) -> None:
        # 未展开时屏蔽滚轮，避免滚动页面时误操作；展开后允许滚轮选择。
        view = self.view() if hasattr(self, "view") else None
        if view is not None and view.isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoWheelSlider(QSlider):
    """屏蔽滚轮事件的水平滑条，避免滚动页面时误操作。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _SmoothScrollArea(QScrollArea):
    """滚轮平滑滚动容器：将离散滚动步进转换为短动画过渡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_target_value = 0
        self._wheel_pending_px = 0.0
        self._wheel_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._wheel_anim.setEasingCurve(QEasingCurve.OutQuart)
        self._wheel_anim.setDuration(160)
        bar = self.verticalScrollBar()
        bar.setSingleStep(scale_px(24, min_abs=18))
        bar.setPageStep(scale_px(120, min_abs=96))
        bar.rangeChanged.connect(self._on_scroll_range_changed)

    def _on_scroll_range_changed(self, minimum: int, maximum: int) -> None:
        self._wheel_target_value = max(minimum, min(maximum, self._wheel_target_value))

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            super().wheelEvent(event)
            return

        if not event.pixelDelta().isNull():
            delta_px = float(event.pixelDelta().y())
        else:
            angle_y = int(event.angleDelta().y())
            if angle_y == 0:
                super().wheelEvent(event)
                return
            delta_px = float(angle_y) / 120.0 * float(scale_px(48, min_abs=36))

        if abs(delta_px) < 1e-6:
            event.accept()
            return

        self._wheel_pending_px += delta_px
        scroll_delta = int(self._wheel_pending_px)
        if scroll_delta == 0:
            event.accept()
            return
        self._wheel_pending_px -= float(scroll_delta)

        current = int(bar.value())
        base = self._wheel_target_value if self._wheel_anim.state() == QPropertyAnimation.Running else current
        target = int(round(base - scroll_delta))
        target = max(bar.minimum(), min(bar.maximum(), target))
        if target == current:
            self._wheel_pending_px = 0.0
            event.accept()
            return

        distance = abs(target - current)
        duration = max(110, min(280, int(120 + distance * 0.45)))

        self._wheel_target_value = target
        self._wheel_anim.stop()
        self._wheel_anim.setDuration(duration)
        self._wheel_anim.setStartValue(current)
        self._wheel_anim.setEndValue(target)
        self._wheel_anim.start()
        event.accept()


class _ApiKeyLineEdit(QLineEdit):
    """接口密钥输入框：展示时脱敏（前7后4，中间 *），编辑时显示原文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_text = ""
        self._masked = True
        self._updating = False
        self.editingFinished.connect(self._on_editing_finished)

    @staticmethod
    def _mask_text(raw_text: str) -> str:
        text = str(raw_text or "")
        if len(text) <= 11:
            return text
        return f"{text[:7]}{'*' * (len(text) - 11)}{text[-4:]}"

    def set_raw_text(self, raw_text: str) -> None:
        self._raw_text = str(raw_text or "").strip()
        self._apply_masked_text()

    def raw_text(self) -> str:
        if not self._masked and not self._updating:
            self._raw_text = self.text().strip()
        return self._raw_text

    def _apply_masked_text(self) -> None:
        self._masked = True
        self._updating = True
        self.setText(self._mask_text(self._raw_text))
        self._updating = False

    def _apply_plain_text(self) -> None:
        self._masked = False
        self._updating = True
        self.setText(self._raw_text)
        self._updating = False

    def _on_editing_finished(self) -> None:
        if self._updating:
            return
        if not self._masked:
            self._raw_text = self.text().strip()
        self._apply_masked_text()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._apply_plain_text()
        self.selectAll()

    def focusOutEvent(self, event) -> None:
        if not self._masked and not self._updating:
            self._raw_text = self.text().strip()
        self._apply_masked_text()
        super().focusOutEvent(event)


class _DecimalSliderField(QWidget):
    """带数值显示的小数滑块字段。"""

    def __init__(
        self,
        minimum: float,
        maximum: float,
        step: float,
        *,
        value: float,
        decimals: int = 2,
        field_width: int = _CONFIG_FIELD_WIDTH,
        parent=None,
    ):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._step = max(float(step), 0.0001)
        self._decimals = max(0, int(decimals))

        total_steps = max(1, int(round((self._maximum - self._minimum) / self._step)))

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedWidth(int(field_width))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(10))

        self._slider = _NoWheelSlider(Qt.Horizontal, self)
        self._slider.setRange(0, total_steps)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(max(1, total_steps // 10))
        self._slider.setTickInterval(max(1, total_steps // 10))
        self._slider.setTickPosition(QSlider.NoTicks)
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self._slider, 1)

        self._value_label = QLabel(self)
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_label.setFixedWidth(scale_px(56, min_abs=48))
        value_font = get_digit_font(size=max(scale_px(13, min_abs=10), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
        value_font.setBold(True)
        self._value_label.setFont(value_font)
        row.addWidget(self._value_label, 0)

        self._slider.valueChanged.connect(self._sync_value_label)
        self.setFocusProxy(self._slider)
        self.setText(str(value))

    def _clamp(self, raw_value: float) -> float:
        return max(self._minimum, min(self._maximum, raw_value))

    def _value_from_slider(self, slider_value: int) -> float:
        return self._minimum + float(slider_value) * self._step

    def _slider_from_value(self, raw_value: float) -> int:
        value = self._clamp(raw_value)
        slider_value = int(round((value - self._minimum) / self._step))
        return max(self._slider.minimum(), min(self._slider.maximum(), slider_value))

    def _format_value(self, raw_value: float) -> str:
        text = f"{self._clamp(raw_value):.{self._decimals}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _sync_value_label(self, _slider_value: int) -> None:
        self._value_label.setText(self.text())

    def value(self) -> float:
        return self._clamp(self._value_from_slider(self._slider.value()))

    def set_value(self, raw_value) -> None:
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            numeric = self._minimum
        slider_value = self._slider_from_value(numeric)
        self._slider.setValue(slider_value)
        if self._slider.value() == slider_value:
            self._sync_value_label(slider_value)

    def text(self) -> str:
        return self._format_value(self.value())

    def setText(self, text) -> None:
        self.set_value(text)


class AISettingsPanel(QWidget):
    """托盘入口 AI 设置面板。"""

    _ui_thread_call = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ui_thread_call.connect(self._invoke_ui_callable)
        self._ec = get_event_center()
        self._autostart_checkbox = None
        self._autostart_status_subscribed = False
        self._update_dialog: DesktopPetUpdateDialog | None = None
        self._subscribe_autostart_events()
        self.setWindowTitle("控制面板")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(int(round(scale_px(520) * _PANEL_SCALE)))
        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2
        self._visible = False
        self._dragging = False
        self._drag_offset = QPoint()
        gpu_line1, gpu_line2 = _query_hardware_watermark_lines()
        self._gpu_watermark_text = f"{gpu_line1}\n{gpu_line2}"
        self._tick_counter = 0
        self._tick_subscribed = False
        self._subscribe_border_effect_events()

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI.get("ui_fade_duration", 180))
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)
        self._tab_floating = None
        self._tab_pages: list[QWidget] = []
        self._config_tab_meta: dict[str, dict] = {}
        self._content_width = scale_px(470, min_abs=420)
        self._content_height = _CONTROL_PANEL_CONTENT_HEIGHT
        self._reset_shared_on_next_save = False
        self._stable_window_size: tuple[int, int] | None = None

        self._build_ui()
        self._apply_project_fonts()
        self._apply_style()
        self._cache_stable_window_size()
        self.load_values()

    def _add_panel_title_row(self, layout: QVBoxLayout, title_text: str, hint_text: str) -> tuple[QLabel, QLabel]:
        header_widget = QWidget()
        header_widget.setFixedHeight(_TITLE_ROW_FIXED_HEIGHT)
        title_row = QHBoxLayout(header_widget)
        title_row.setContentsMargins(scale_px(20, min_abs=16), 0, 0, 0)
        title_row.setSpacing(scale_px(8, min_abs=6))

        title_label = QLabel(title_text)
        title_label.setFont(self._build_title_font())
        title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        title_row.addWidget(title_label, 0, Qt.AlignLeft | Qt.AlignBottom)
        title_row.addStretch(1)

        hint_label = QLabel(hint_text)
        hint_label.setWordWrap(False)
        hint_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        hint_label.setFont(self._build_hint_font())
        hint_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title_row.addWidget(hint_label, 1, Qt.AlignRight | Qt.AlignBottom)
        layout.addWidget(header_widget, 0)
        return title_label, hint_label

    @staticmethod
    def _build_title_font():
        title_font = get_ui_font(size=_TITLE_FONT_SIZE)
        title_font.setBold(True)
        return title_font

    @staticmethod
    def _build_hint_font():
        return get_ui_font(size=_HINT_FONT_SIZE)

    @staticmethod
    def _set_widget_description(widget: QWidget | None, text: str) -> None:
        if widget is None:
            return
        desc = str(text or "").strip()
        if not desc:
            return
        setattr(widget, "_description", desc)

    def _set_form_row_description(self, form: QFormLayout, field_widget: QWidget, text: str) -> None:
        self._set_widget_description(field_widget, text)
        self._set_widget_description(form.labelForField(field_widget), text)

    def _invoke_ui_callable(self, func) -> None:
        if callable(func):
            func()

    def _run_on_ui_thread(self, func: Callable[[], None]) -> None:
        if threading.current_thread() is threading.main_thread():
            func()
        else:
            self._ui_thread_call.emit(func)

    def _create_action_button_row(self, *button_specs):
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, scale_px(20, min_abs=16), 0, 0)
        button_row.addStretch(1)
        buttons = []
        for label_text, callback in button_specs:
            button = QPushButton(label_text)
            button.clicked.connect(callback)
            button_row.addWidget(button)
            buttons.append(button)
        return button_row, buttons

    @staticmethod
    def _create_fixed_width_row_group(field_width: int = _CONFIG_FIELD_WIDTH, spacing: int = 0):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        group.setFixedWidth(int(field_width))
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(spacing))
        return group, row

    def _create_config_line_edit(
        self,
        value=...,
        *,
        placeholder_text: str = "",
        fixed_width: int | None = None,
        expanding: bool = False,
    ) -> QLineEdit:
        editor = QLineEdit()
        if placeholder_text:
            editor.setPlaceholderText(placeholder_text)
        if value is not ...:
            self._set_config_editor_value(editor, value)
        if fixed_width is not None:
            editor.setFixedWidth(int(fixed_width))
        if expanding:
            editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return editor

    @staticmethod
    def _description_preview_value(value, max_len: int = 72) -> str:
        text = _format_config_editor_value(value)
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    @staticmethod
    def _description_value_type(value) -> str:
        if isinstance(value, bool):
            return "布尔"
        if isinstance(value, int):
            return "整数"
        if isinstance(value, float):
            return "小数"
        if isinstance(value, str):
            return "文本"
        if isinstance(value, tuple):
            return "元组"
        if isinstance(value, list):
            return "列表"
        return "配置值"

    def _build_config_single_description(self, dict_name: str, key: str, value, friendly_name: str) -> str:
        section_name = _friendly_field_section_name(dict_name, key)
        value_type = self._description_value_type(value)
        preview = self._description_preview_value(value)
        return (
            f"{section_name} · {friendly_name}\n"
            f"配置键: {dict_name}.{key}\n"
            f"类型: {value_type}\n"
            f"默认值: {preview}"
        )

    def _build_config_range_description(
        self,
        dict_name: str,
        left_key: str,
        right_key: str,
        left_value,
        right_value,
        friendly_name: str,
    ) -> str:
        section_name = _friendly_section_name(dict_name, dict_name)
        left_preview = self._description_preview_value(left_value)
        right_preview = self._description_preview_value(right_value)
        return (
            f"{section_name} · {friendly_name}\n"
            f"配置键: {dict_name}.{left_key} / {dict_name}.{right_key}\n"
            f"类型: 数值范围\n"
            f"默认值: {left_preview} ~ {right_preview}"
        )

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(
            self._border + scale_px(20),
            self._border + scale_px(16),
            self._border + scale_px(20),
            self._border + scale_px(26),
        )
        root_layout.setSpacing(scale_px(10))

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)
        center_row.addStretch(1)

        content_panel = QWidget(self)
        content_panel.setMinimumWidth(self._content_width)
        content_panel.setMaximumWidth(self._content_width)
        content_panel.setMinimumHeight(self._content_height)
        content_panel.setMaximumHeight(self._content_height)
        center_row.addWidget(content_panel, 0, Qt.AlignHCenter)
        center_row.addStretch(1)
        root_layout.addLayout(center_row, 1)
        self._ai_panel = content_panel
        self._tab_pages = [self._ai_panel]

        layout = QVBoxLayout(content_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(10))
        layout.addSpacing(scale_px(14, min_abs=10))

        self._title_label, self._hint_label = self._add_panel_title_row(
            layout,
            "AI设置",
            _AI_HINT_TEXT,
        )

        scroll = _SmoothScrollArea(content_panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setViewportMargins(0, 0, _SCROLLBAR_RIGHT_SHIFT, 0)
        scroll_content = QWidget(scroll)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(scale_px(10))
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(scale_px(10))
        form.setVerticalSpacing(scale_px(6))
        scroll_layout.addLayout(form)

        self._api_key = _ApiKeyLineEdit()
        form.addRow("接口密钥", self._api_key)
        self._set_form_row_description(
            form,
            self._api_key,
            "外部接口密钥；接入内置 YuanBao-Free-API 时，这里填写你为本地服务设置的访问密钥。",
        )

        self._force_mode = _WatermarkComboBox()
        self._force_mode.setView(QListView(self._force_mode))
        self._force_mode.addItem("优先走元宝web(默认)", "4")
        self._force_mode.addItem("自动选择", "")
        self._force_mode.addItem("仅使用手动接口密钥", "0")
        self._force_mode.addItem("仅使用本地 Ollama", "2")
        self._force_mode.addItem("仅使用规则回复", "3")
        form.addRow("回复模式", self._force_mode)
        self._set_form_row_description(
            form,
            self._force_mode,
            "强制指定回复来源；自动模式会按可用性在多来源间切换。",
        )

        self._api_base_url = QLineEdit()
        form.addRow("接口地址", self._api_base_url)
        self._set_form_row_description(
            form,
            self._api_base_url,
            "外部接口地址，通常填写兼容 OpenAI 的基地址；若直接填写完整的 `/chat/completions` 或 `/v1/chat/completions` 端点也可兼容。启用 YuanBao-Free-API 时，这里应填写你的中转 API 地址，而不是腾讯元宝网页地址。",
        )

        self._api_model = QLineEdit()
        form.addRow("接口模型", self._api_model)
        self._set_form_row_description(
            form,
            self._api_model,
            "外部接口模型名，例如 qwen3.5-plus。",
        )

        self._yuanbao_login_url_value = str(_DEFAULT_VALUES.get("yuanbao_login_url", ""))
        self._yuanbao_hy_source_value = str(_DEFAULT_VALUES.get("yuanbao_hy_source", "web"))
        self._yuanbao_hy_user_value = str(_DEFAULT_VALUES.get("yuanbao_hy_user", ""))
        self._yuanbao_x_uskey_value = str(_DEFAULT_VALUES.get("yuanbao_x_uskey", ""))
        self._yuanbao_agent_id_value = str(_DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa"))

        yuanbao_login_row, yuanbao_login_layout = self._create_fixed_width_row_group(
            field_width=_CONFIG_FIELD_WIDTH,
            spacing=scale_px(8, min_abs=6),
        )
        self._start_yuanbao_login_btn = QPushButton("登录元宝AI")
        self._start_yuanbao_login_btn.setFixedWidth(scale_px(126, min_abs=108))
        self._start_yuanbao_login_btn.clicked.connect(self._on_start_yuanbao_login)
        yuanbao_login_layout.addWidget(self._start_yuanbao_login_btn, 0)
        self._stop_yuanbao_login_btn = QPushButton("退出元宝登录")
        self._stop_yuanbao_login_btn.setFixedWidth(scale_px(126, min_abs=108))
        self._stop_yuanbao_login_btn.clicked.connect(self._on_stop_yuanbao_login)
        yuanbao_login_layout.addWidget(self._stop_yuanbao_login_btn, 0)
        yuanbao_login_layout.addStretch(1)
        form.addRow("元宝登录", yuanbao_login_row)
        self._set_widget_description(self._start_yuanbao_login_btn, "启动本地 YuanBao-Free-API 服务，弹出二维码面板并等待扫码登录。")
        self._set_widget_description(self._stop_yuanbao_login_btn, "停止元宝登录流程并关闭本地元宝服务。")

        self._yuanbao_free_api_enabled = QCheckBox("启用 YuanBao-Free-API 附加参数")
        form.addRow("元宝Free-API", self._yuanbao_free_api_enabled)
        self._set_form_row_description(
            form,
            self._yuanbao_free_api_enabled,
            "启用后会自动附加元宝兼容参数，并可按选项复用会话或上传图片；登录态由扫码流程自动处理，无需手填。",
        )

        self._yuanbao_chat_id = self._create_config_line_edit(expanding=True)
        form.addRow("chat_id", self._yuanbao_chat_id)
        self._set_form_row_description(
            form,
            self._yuanbao_chat_id,
            "可选；留空时由服务端创建新会话，填写则尽量复用指定会话。",
        )

        yuanbao_flags = QWidget()
        yuanbao_flags_layout = QHBoxLayout(yuanbao_flags)
        yuanbao_flags_layout.setContentsMargins(0, 0, 0, 0)
        yuanbao_flags_layout.setSpacing(scale_px(12, min_abs=8))
        self._yuanbao_remove_conversation = QCheckBox("请求后删除会话")
        self._yuanbao_upload_images = QCheckBox("图片先走 /upload")
        yuanbao_flags_layout.addWidget(self._yuanbao_remove_conversation, 0)
        yuanbao_flags_layout.addWidget(self._yuanbao_upload_images, 0)
        yuanbao_flags_layout.addStretch(1)
        form.addRow("附加开关", yuanbao_flags)
        self._set_form_row_description(
            form,
            yuanbao_flags,
            "启用图片上传后，桌宠截图会先发到 /upload 生成 multimedia，再随聊天请求一并提交。",
        )

        base_row, base_layout = self._create_fixed_width_row_group(
            field_width=_CONFIG_FIELD_WIDTH,
            spacing=scale_px(8, min_abs=6),
        )
        self._ollama_base_url = self._create_config_line_edit(expanding=True)
        base_layout.addWidget(self._ollama_base_url, 1)
        self._open_ollama_app_btn = QPushButton("打开Ollama")
        self._open_ollama_app_btn.setFixedWidth(scale_px(110, min_abs=92))
        self._open_ollama_app_btn.clicked.connect(self._on_open_ollama_app)
        base_layout.addWidget(self._open_ollama_app_btn, 0)
        form.addRow("Ollama地址", base_row)
        self._set_form_row_description(
            form,
            base_row,
            "本地 Ollama 服务地址，默认 http://localhost:11434。",
        )
        self._set_widget_description(self._open_ollama_app_btn, "打开 Ollama 应用或下载页，便于获取/管理模型。")

        self._ollama_model = _WatermarkComboBox()
        self._ollama_model.setView(QListView(self._ollama_model))
        self._ollama_model.setEditable(True)
        self._ollama_model.setInsertPolicy(QComboBox.NoInsert)
        self._ollama_model.set_before_popup_callback(self._refresh_ollama_model_dropdown)
        if self._ollama_model.lineEdit():
            self._ollama_model.lineEdit().setPlaceholderText("自动检测本地模型")
        form.addRow("Ollama模型", self._ollama_model)
        self._set_form_row_description(
            form,
            self._ollama_model,
            "本地 Ollama 使用的模型名，从检测到的模型列表中选择。",
        )

        self._gpu_mode = _WatermarkComboBox()
        self._gpu_mode.setView(QListView(self._gpu_mode))
        self._gpu_mode.addItem("CPU优先", _GPU_MODE_CPU)
        self._gpu_mode.addItem("GPU优先", _GPU_MODE_GPU)
        self._gpu_mode.addItem("自动", _GPU_MODE_AUTO)
        form.addRow("推理模式", self._gpu_mode)
        self._set_form_row_description(
            form,
            self._gpu_mode,
            "控制推理设备偏好；自动模式会按环境能力选择。",
        )

        self._num_thread = QLineEdit()
        form.addRow("CPU线程数", self._num_thread)
        self._set_form_row_description(
            form,
            self._num_thread,
            "CPU 推理线程数，0 表示使用框架默认值。",
        )

        self._api_temperature = _DecimalSliderField(0.0, 2.0, 0.05, value=_DEFAULT_VALUES["api_temperature"])
        form.addRow("大模型温度", self._api_temperature)
        self._set_form_row_description(
            form,
            self._api_temperature,
            "大模型采样温度范围 0~2，越高回复越发散。",
        )

        self._gsv_auto_start = QCheckBox("自动启用GSV语音模块")
        self._gsv_auto_start.setChecked(_DEFAULT_VALUES["gsv_auto_start"])
        form.addRow("", self._gsv_auto_start)
        self._set_form_row_description(
            form,
            self._gsv_auto_start,
            "开启后，桌宠启动时会在后台预拉起并预热 GSV 服务；关闭后不自动拉起，也不处理 AI 文本语音。",
        )

        self._gsv_temperature = _DecimalSliderField(0.0, 2.0, 0.05, value=_DEFAULT_VALUES["gsv_temperature"])
        form.addRow("GSV服务温度", self._gsv_temperature)
        self._set_form_row_description(
            form,
            self._gsv_temperature,
            "GSV 文本转语音温度范围 0~2，越高表达越活跃。",
        )

        self._gsv_speed_factor = _DecimalSliderField(0.5, 2.0, 0.05, value=_DEFAULT_VALUES["gsv_speed_factor"])
        form.addRow("GSV语速", self._gsv_speed_factor)
        self._set_form_row_description(
            form,
            self._gsv_speed_factor,
            "GSV 文本转语音语速，1.0 为默认语速，越大越快。",
        )

        self._ai_voice_max_chars = _DecimalSliderField(20, 80, 1, value=_DEFAULT_VALUES["ai_voice_max_chars"])
        form.addRow("GSV语音字数限制", self._ai_voice_max_chars)
        self._set_form_row_description(
            form,
            self._ai_voice_max_chars,
            "GSV 语音合成最大文本长度，超过此长度的回复不会转为语音。",
        )

        self._gsv_cache_max_files = _DecimalSliderField(1, 128, 1, value=_DEFAULT_VALUES["gsv_cache_max_files"], decimals=0)
        form.addRow("GSV缓存上限", self._gsv_cache_max_files)
        self._set_form_row_description(
            form,
            self._gsv_cache_max_files,
            "保留最近生成的 GSV 语音条数，超出后按时间自动删除旧缓存，默认 20，范围 1~128。",
        )

        gsv_cache_row, gsv_cache_layout = self._create_fixed_width_row_group(
            field_width=_CONFIG_FIELD_WIDTH,
            spacing=scale_px(8, min_abs=6),
        )
        self._open_gsv_cache_dir_btn = QPushButton("打开缓存文件夹")
        self._open_gsv_cache_dir_btn.setFixedWidth(scale_px(132, min_abs=112))
        self._open_gsv_cache_dir_btn.clicked.connect(self._on_open_gsv_cache_dir)
        gsv_cache_layout.addWidget(self._open_gsv_cache_dir_btn, 0)
        gsv_cache_layout.addStretch(1)
        form.addRow("语音缓存", gsv_cache_row)
        self._set_form_row_description(
            form,
            gsv_cache_row,
            "打开 GSV 语音缓存目录，方便查看并保留最近生成的喜欢音频。",
        )
        self._set_widget_description(self._open_gsv_cache_dir_btn, "打开 GSV 语音缓存目录。")

        self._memory_context_limit = _DecimalSliderField(0, 48, 1, value=_DEFAULT_VALUES["memory_context_limit"])
        form.addRow("记忆上下文条数", self._memory_context_limit)
        self._set_form_row_description(
            form,
            self._memory_context_limit,
            "附带给 AI 的 recent memory 条数，0 表示不附带，范围 0~48。",
        )

        self._api_enable_thinking = QCheckBox("启用思考模式(外部接口可用)")
        form.addRow("", self._api_enable_thinking)
        self._set_form_row_description(
            form,
            self._api_enable_thinking,
            "开启后，支持思考模式的外部接口将返回推理链路。",
        )

        self._auto_companion_enabled = QCheckBox("启用自动陪伴")
        self._auto_companion_enabled.setChecked(True)
        form.addRow("", self._auto_companion_enabled)
        self._set_form_row_description(
            form,
            self._auto_companion_enabled,
            "开启后会按系统逻辑自动触发陪伴对话。",
        )

        scroll_layout.addStretch(1)

        btn_row, root_buttons = self._create_action_button_row(
            ("恢复默认", self._on_restore_defaults),
            ("保存并退出", self._on_save_and_exit),
        )
        self._reload_btn, self._save_exit_btn = root_buttons
        layout.addLayout(btn_row)

        attach_ai_settings_tabs(self, _GENERAL_CONFIG_CATEGORIES)
        self._ensure_config_defaults_integrity()

    def _build_config_category_panel(self, category: dict) -> QWidget:
        import config.config as cc

        category_id = str(category.get("id") or "")
        category_title = str(category.get("title") or category_id)
        panel = QWidget(self)
        panel.setMinimumWidth(self._content_width)
        panel.setMaximumWidth(self._content_width)
        panel.setMinimumHeight(self._content_height)
        panel.setMaximumHeight(self._content_height)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(10))
        layout.addSpacing(scale_px(14, min_abs=10))

        title_label, hint_label = self._add_panel_title_row(
            layout,
            category_title,
            _GENERAL_HINT_TEXT,
        )

        # 特殊处理：桌宠更新标签页 - 只有按钮，没有配置字段
        if category_id == "desktop_pet_update":
            return self._build_desktop_pet_update_panel(panel, layout, category_title)

        scroll = _SmoothScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setViewportMargins(0, 0, _SCROLLBAR_RIGHT_SHIFT, 0)
        scroll_content = QWidget(scroll)
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(scale_px(10))
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        fields: list[dict] = []
        defaults: dict[str, dict] = {}
        category_allow_map = _CATEGORY_KEY_ALLOWLIST.get(category_id, {})
        category_label_width = self._estimate_category_label_width(category, cc, category_allow_map)
        category_field_width = self._estimate_category_field_width(category_label_width)

        for dict_name, section_title in category.get("sections", []):
            section_obj = getattr(cc, str(dict_name), None)
            if not isinstance(section_obj, dict):
                continue
            allowed_keys = category_allow_map.get(str(dict_name))

            section_fields_added = False
            section_label = QLabel(_friendly_section_name(str(dict_name), str(section_title)))
            section_font = get_ui_font(size=max(scale_px(12, min_abs=10), _CONFIG_FONT_SIZE))
            section_font.setBold(True)
            section_label.setFont(section_font)
            self._set_widget_description(
                section_label,
                f"{_friendly_section_name(str(dict_name), str(section_title))} 配置分组",
            )
            content_layout.addWidget(section_label, 0, Qt.AlignLeft)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(scale_px(10))
            form.setVerticalSpacing(scale_px(6))
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

            section_entries: list[tuple[str, object]] = []
            for key, value in section_obj.items():
                key_text = str(key)
                if allowed_keys is not None and key_text not in allowed_keys:
                    continue
                if not _is_supported_config_value(value):
                    continue
                section_entries.append((key_text, value))

            consumed_keys: set[str] = set()
            for key, value in section_entries:
                if key in consumed_keys:
                    continue

                signature = _range_pair_signature(key)
                if signature is not None:
                    pair_key = None
                    pair_value = None
                    sign_base, sign_type = signature
                    for other_key, other_value in section_entries:
                        if other_key == key or other_key in consumed_keys:
                            continue
                        other_sig = _range_pair_signature(other_key)
                        if other_sig is None:
                            continue
                        if other_sig[0] != sign_base:
                            continue
                        pair_key = other_key
                        pair_value = other_value
                        break
                    if pair_key is not None and pair_value is not None:
                        if sign_type in ("max", "upper"):
                            left_key, left_value = pair_key, pair_value
                            right_key, right_value = key, value
                        else:
                            left_key, left_value = key, value
                            right_key, right_value = pair_key, pair_value
                        left_editor, right_editor, pair_widget = self._create_compact_pair_editor(
                            left_value,
                            right_value,
                            left_hint="最小",
                            right_hint="最大",
                            field_width=category_field_width,
                        )
                        friendly_name = _friendly_range_name(str(dict_name), left_key, right_key)
                        description = self._build_config_range_description(
                            str(dict_name),
                            left_key,
                            right_key,
                            left_value,
                            right_value,
                            friendly_name,
                        )
                        label = self._create_form_label(
                            friendly_name,
                            category_label_width,
                        )
                        self._set_widget_description(label, description)
                        self._set_widget_description(pair_widget, description)
                        self._set_widget_description(left_editor, description)
                        self._set_widget_description(right_editor, description)
                        form.addRow(label, pair_widget)
                        fields.append({
                            "kind": "range_pair",
                            "dict_name": str(dict_name),
                            "keys": [left_key, right_key],
                            "editors": [left_editor, right_editor],
                            "templates": [copy.deepcopy(left_value), copy.deepcopy(right_value)],
                        })
                        defaults.setdefault(str(dict_name), {})[left_key] = copy.deepcopy(left_value)
                        defaults.setdefault(str(dict_name), {})[right_key] = copy.deepcopy(right_value)
                        consumed_keys.add(left_key)
                        consumed_keys.add(right_key)
                        section_fields_added = True
                        continue

                if isinstance(value, (tuple, list)):
                    editors, group_widget = self._create_sequence_editor(value, field_width=category_field_width)
                    friendly_name = _friendly_key_name(str(dict_name), key)
                    description = self._build_config_single_description(str(dict_name), key, value, friendly_name)
                    label = self._create_form_label(friendly_name, category_label_width)
                    self._set_widget_description(label, description)
                    self._set_widget_description(group_widget, description)
                    for editor in editors:
                        self._set_widget_description(editor, description)
                    form.addRow(label, group_widget)
                    fields.append({
                        "kind": "sequence",
                        "dict_name": str(dict_name),
                        "key": key,
                        "editors": editors,
                        "template": copy.deepcopy(value),
                    })
                    defaults.setdefault(str(dict_name), {})[key] = copy.deepcopy(value)
                    consumed_keys.add(key)
                    section_fields_added = True
                    continue

                open_dir_btn = None
                extra_widgets: list[QWidget] = []
                if self._is_volume_slider_field(str(dict_name), key, value):
                    editor, percent_label, row_widget = self._create_volume_slider_editor(
                        value,
                        field_width=category_field_width,
                    )
                    extra_widgets.append(percent_label)
                elif self._is_decimal_slider_field(str(dict_name), key, value):
                    editor = _DecimalSliderField(0.0, 1.0, 0.05, value=float(value), field_width=category_field_width)
                    row_widget = editor
                elif self._is_local_music_path_field(str(dict_name), key) and isinstance(value, str):
                    editor, open_dir_btn, row_widget = self._create_path_editor_with_open_button(
                        str(dict_name),
                        str(key),
                        value,
                        field_width=category_field_width,
                    )
                elif isinstance(value, bool):
                    editor = QCheckBox()
                    row_widget = self._wrap_fixed_width_field(editor, field_width=category_field_width)
                else:
                    editor = self._create_config_line_edit(fixed_width=category_field_width)
                    row_widget = editor
                self._set_config_editor_value(editor, value)
                friendly_name = _friendly_key_name(str(dict_name), key)
                description = self._build_config_single_description(str(dict_name), key, value, friendly_name)
                label = self._create_form_label(friendly_name, category_label_width)
                self._set_widget_description(label, description)
                self._set_widget_description(row_widget, description)
                self._set_widget_description(editor, description)
                if isinstance(open_dir_btn, QPushButton):
                    self._set_widget_description(open_dir_btn, description)
                for extra in extra_widgets:
                    self._set_widget_description(extra, description)
                form.addRow(label, row_widget)
                fields.append({
                    "kind": (
                        "volume_slider"
                        if self._is_volume_slider_field(str(dict_name), key, value)
                        else "decimal_slider"
                        if self._is_decimal_slider_field(str(dict_name), key, value)
                        else "single"
                    ),
                    "dict_name": str(dict_name),
                    "key": key,
                    "editor": editor,
                    "template": copy.deepcopy(value),
                })
                defaults.setdefault(str(dict_name), {})[key] = copy.deepcopy(value)
                consumed_keys.add(key)
                section_fields_added = True

                if category_id == "system_dispatch" and str(dict_name) == "STARTUP" and key == "ensure_desktop_shortcut":
                    self._append_autostart_field(
                        form,
                        fields,
                        category_label_width,
                        category_field_width,
                    )
                    section_fields_added = True

            if section_fields_added:
                content_layout.addLayout(form)
            else:
                section_label.hide()
            content_layout.addSpacing(scale_px(4, min_abs=2))

        content_layout.addStretch(1)

        btn_row, _buttons = self._create_action_button_row(
            ("恢复默认", self._on_restore_defaults),
            ("保存并退出", self._on_save_and_exit),
        )
        layout.addLayout(btn_row)

        self._config_tab_meta[category_id] = {
            "panel": panel,
            "fields": fields,
            "defaults": defaults,
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
        }
        return panel

    def _build_desktop_pet_update_panel(self, panel: QWidget, layout: QVBoxLayout, category_title: str) -> QWidget:
        """构建桌宠更新标签页 - 只有按钮，没有配置字段"""
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QWidget

        # 添加说明文本
        description_label = QLabel("在这里可以检查桌宠更新和同步开发版本。")
        description_font = get_ui_font(size=scale_px(12, min_abs=10))
        description_label.setFont(description_font)
        description_label.setWordWrap(True)
        description_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        description_label.setContentsMargins(0, 0, 0, scale_px(16))
        layout.addWidget(description_label)

        # 创建按钮容器
        button_container = QWidget()
        button_layout = QVBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(scale_px(16))

        # 创建"检查新版本"按钮
        check_update_btn = QPushButton("检查新版本")
        check_update_btn.setObjectName("checkUpdateButton")
        check_update_btn.setFixedHeight(scale_px(36, min_abs=30))
        # 使用项目主题颜色
        border_color = UI_THEME["border"].name()
        bg_color = UI_THEME["bg"].name()
        mid_color = UI_THEME["mid"].name()
        highlight_color = UI_THEME["deep_cyan"].name()
        text_color = UI_THEME["text"].name()
        check_update_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_color};
                color: {text_color};
                border: 2px solid {border_color};
                border-radius: 0px;
                font-weight: bold;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: {mid_color};
            }}
            QPushButton:pressed {{
                background-color: {highlight_color};
            }}
        """)
        check_update_btn.clicked.connect(self._on_check_updates)

        # 创建"同步开发版"按钮
        sync_dev_btn = QPushButton("同步开发版")
        sync_dev_btn.setObjectName("syncDevButton")
        sync_dev_btn.setFixedHeight(scale_px(36, min_abs=30))
        sync_dev_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_color};
                color: {text_color};
                border: 2px solid {border_color};
                border-radius: 0px;
                font-weight: bold;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: {mid_color};
            }}
            QPushButton:pressed {{
                background-color: {highlight_color};
            }}
        """)
        sync_dev_btn.clicked.connect(self._on_sync_dev_build)

        # 添加按钮到布局
        button_layout.addWidget(check_update_btn)
        button_layout.addWidget(sync_dev_btn)
        button_layout.addStretch(1)

        layout.addWidget(button_container, 1)

        # 添加底部间距
        layout.addStretch(1)

        # 保存元数据（保持与其他标签页一致）
        self._config_tab_meta["desktop_pet_update"] = {
            "panel": panel,
            "fields": [],
            "defaults": {},
            "title": category_title,
            "title_label": None,  # 标题已经在_add_panel_title_row中创建
            "hint_label": None,
        }

        return panel

    def _show_info_message(self, message: str):
        """显示信息消息框"""
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "提示", message)

    def _create_compact_pair_editor(
        self,
        left_value,
        right_value,
        *,
        left_hint: str = "",
        right_hint: str = "",
        field_width: int = _CONFIG_FIELD_WIDTH,
    ):
        group, row = self._create_fixed_width_row_group(
            field_width=field_width,
            spacing=scale_px(10),
        )

        left = self._create_config_line_edit(
            left_value,
            placeholder_text=left_hint,
            expanding=True,
        )
        row.addWidget(left, 1)

        right = self._create_config_line_edit(
            right_value,
            placeholder_text=right_hint,
            expanding=True,
        )
        row.addWidget(right, 1)
        return left, right, group

    @staticmethod
    def _create_form_label(text: str, label_width: int = _CONFIG_LABEL_WIDTH) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setFixedWidth(int(label_width))
        return label

    def _estimate_category_label_width(self, category: dict, cc_module, category_allow_map: dict) -> int:
        font = get_ui_font(size=_CONFIG_FONT_SIZE)
        font.setBold(True)
        fm = QFontMetrics(font)
        min_w = scale_px(108, min_abs=92)
        max_w = scale_px(188, min_abs=164)
        measured = min_w

        for dict_name, _section_title in category.get("sections", []):
            section_obj = getattr(cc_module, str(dict_name), None)
            if not isinstance(section_obj, dict):
                continue
            allowed_keys = category_allow_map.get(str(dict_name))
            entries = []
            for key, value in section_obj.items():
                key_text = str(key)
                if allowed_keys is not None and key_text not in allowed_keys:
                    continue
                if not _is_supported_config_value(value):
                    continue
                entries.append((key_text, value))

            consumed: set[str] = set()
            for key, _value in entries:
                if key in consumed:
                    continue
                signature = _range_pair_signature(key)
                if signature is not None:
                    sign_base, sign_type = signature
                    pair_key = None
                    for other_key, _other_value in entries:
                        if other_key == key or other_key in consumed:
                            continue
                        other_sig = _range_pair_signature(other_key)
                        if other_sig is None or other_sig[0] != sign_base:
                            continue
                        pair_key = other_key
                        break
                    if pair_key is not None:
                        if sign_type in ("max", "upper"):
                            left_key, right_key = pair_key, key
                        else:
                            left_key, right_key = key, pair_key
                        label_text = _friendly_range_name(str(dict_name), left_key, right_key)
                        consumed.add(left_key)
                        consumed.add(right_key)
                    else:
                        label_text = _friendly_key_name(str(dict_name), key)
                        consumed.add(key)
                else:
                    label_text = _friendly_key_name(str(dict_name), key)
                    consumed.add(key)
                measured = max(measured, fm.horizontalAdvance(label_text) + scale_px(12, min_abs=10))

        return int(max(min_w, min(max_w, measured)))

    def _estimate_category_field_width(self, label_width: int) -> int:
        # 预留 label-字段间距、滚动条占位与右侧安全边距，避免字段被右侧裁切。
        reserve = scale_px(10) + _SCROLLBAR_RIGHT_SHIFT + scale_px(24, min_abs=20)
        available = int(self._content_width - int(label_width) - int(reserve))
        min_w = scale_px(176, min_abs=148)
        min_fallback = scale_px(112, min_abs=96)
        max_w = _CONFIG_FIELD_WIDTH
        if available <= 0:
            return int(min_fallback)
        preferred = min(max_w, available)
        if preferred < min_w:
            return int(max(min_fallback, preferred))
        return int(preferred)

    @staticmethod
    def _is_local_music_path_field(dict_name: str, key: str) -> bool:
        pair = (str(dict_name), str(key))
        return pair in {
            ("CLOUD_MUSIC", "local_music_dir"),
            ("CLOUD_MUSIC", "launch_wuwa_path"),
        }

    @staticmethod
    def _is_launch_wuwa_path_field(dict_name: str, key: str) -> bool:
        return str(dict_name) == "CLOUD_MUSIC" and str(key) == "launch_wuwa_path"

    @staticmethod
    def _is_volume_slider_field(dict_name: str, key: str, value) -> bool:
        pair = (str(dict_name), str(key))
        if pair not in _VOLUME_SLIDER_FIELDS:
            return False
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float))

    @staticmethod
    def _is_decimal_slider_field(dict_name: str, key: str, value) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        pair = (str(dict_name), str(key))
        return pair in {
            ("UI", "pet_opacity"),
            ("UI", "ui_widget_opacity"),
            ("OBJECTS", "object_opacity"),
        }

    @staticmethod
    def _volume_percent_from_value(value) -> int:
        try:
            v = float(value)
        except Exception:
            v = 0.0
        v = max(0.0, min(1.0, v))
        return int(round(v * 100))

    @staticmethod
    def _volume_value_from_percent(percent: int) -> float:
        p = max(0, min(100, int(percent)))
        # 步进按 1% 固定，避免浮点误差导致显示与落盘不一致。
        return round(p / 100.0, 2)

    def _create_volume_slider_editor(self, value, *, field_width: int = _CONFIG_FIELD_WIDTH):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        group.setFixedWidth(int(field_width))
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(8, min_abs=6))

        slider = _NoWheelSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(1)
        slider.setTickInterval(10)
        slider.setTickPosition(QSlider.NoTicks)
        slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        label = QLabel()
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setFixedWidth(scale_px(44, min_abs=38))

        percent = self._volume_percent_from_value(value)
        slider.setValue(percent)
        label.setText(f"{percent}%")
        slider.valueChanged.connect(lambda v, lbl=label: lbl.setText(f"{int(v)}%"))

        row.addWidget(slider, 1)
        row.addWidget(label, 0)
        return slider, label, group

    def _create_path_editor_with_open_button(
        self,
        dict_name: str,
        key: str,
        value,
        *,
        field_width: int = _CONFIG_FIELD_WIDTH,
    ):
        group, row = self._create_fixed_width_row_group(
            field_width=field_width,
            spacing=scale_px(8, min_abs=6),
        )

        editor = self._create_config_line_edit(value, expanding=True)
        row.addWidget(editor, 1)

        open_btn = QPushButton("浏览")
        open_btn.setFixedWidth(scale_px(52, min_abs=46))
        if self._is_launch_wuwa_path_field(dict_name, key):
            open_btn.clicked.connect(lambda _=False, line=editor: self._browse_launch_wuwa_file(line))
        else:
            open_btn.clicked.connect(lambda _=False, line=editor: self._browse_local_music_dir(line))
        row.addWidget(open_btn, 0)
        return editor, open_btn, group

    def _browse_local_music_dir(self, editor: QLineEdit) -> None:
        start_dir = _project_root()
        current_text = str(editor.text() or "").strip()
        if current_text:
            expanded = os.path.expandvars(os.path.expanduser(current_text))
            candidate = Path(expanded)
            if not candidate.is_absolute():
                candidate = _project_root() / candidate
            if candidate.is_file():
                candidate = candidate.parent
            if candidate.exists() and candidate.is_dir():
                start_dir = candidate
            elif candidate.parent.exists() and candidate.parent.is_dir():
                start_dir = candidate.parent

        selected = QFileDialog.getExistingDirectory(
            self,
            "选择本地音乐文件夹",
            str(start_dir),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if selected:
            editor.setText(os.path.normpath(selected))

    def _browse_launch_wuwa_file(self, editor: QLineEdit) -> None:
        start_dir = _project_root()
        current_text = str(editor.text() or "").strip()
        if current_text:
            expanded = os.path.expandvars(os.path.expanduser(current_text))
            candidate = Path(expanded)
            if not candidate.is_absolute():
                candidate = _project_root() / candidate
            if candidate.exists():
                start_dir = candidate.parent if candidate.is_file() else candidate
            elif candidate.parent.exists() and candidate.parent.is_dir():
                start_dir = candidate.parent

        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择鸣潮启动文件",
            str(start_dir),
            "启动文件 (*.exe *.bat *.lnk);;可执行文件 (*.exe);;批处理 (*.bat);;快捷方式 (*.lnk);;所有文件 (*.*)",
        )
        if selected:
            editor.setText(os.path.normpath(selected))

    def _on_open_ollama_app(self) -> None:
        candidates: list[Path] = []
        local_app = os.getenv("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Programs" / "Ollama" / "Ollama.exe")
        program_files = os.getenv("PROGRAMFILES")
        if program_files:
            candidates.append(Path(program_files) / "Ollama" / "Ollama.exe")
        program_files_x86 = os.getenv("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(Path(program_files_x86) / "Ollama" / "Ollama.exe")

        for candidate in candidates:
            if candidate and candidate.exists():
                try:
                    if hasattr(os, "startfile"):
                        os.startfile(str(candidate))  # type: ignore[attr-defined]
                    else:
                        subprocess.Popen([str(candidate)], shell=False)
                    self._emit_info("已尝试打开 Ollama 应用，请在其中下载或管理模型。", min_tick=10, max_tick=90)
                    return
                except Exception as e:
                    _logger.error("打开 Ollama 应用失败: %s", e)
                    break

        try:
            webbrowser.open("https://ollama.com/download")
            self._emit_info("未找到本地 Ollama 应用，已打开 Ollama 下载页面。", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开 Ollama 下载页面失败: %s", e)
            self._emit_info(f"打开 Ollama 页面失败: {e}", min_tick=20, max_tick=180)

    def _on_open_gsv_cache_dir(self) -> None:
        try:
            from lib.script.gsvmove import get_gsvmove_service

            cache_dir = get_gsvmove_service().get_saved_audio_cache_root()
            if hasattr(os, "startfile"):
                os.startfile(str(cache_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", str(cache_dir)], shell=False)
            self._emit_info("已打开 GSV 语音缓存文件夹。", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开 GSV 语音缓存文件夹失败: %s", e)
            self._emit_info(f"打开 GSV 语音缓存文件夹失败: {e}", min_tick=20, max_tick=180)

    def _on_start_yuanbao_login(self) -> None:
        api_key = self._api_key.raw_text().strip()

        self._yuanbao_free_api_enabled.setChecked(True)
        if not self._api_base_url.text().strip():
            self._api_base_url.setText("http://127.0.0.1:8000/v1")
        if not self._api_model.text().strip():
            self._api_model.setText("deepseek-v3")

        import config.ollama_config as oc
        oc.API_KEY = api_key
        oc.API_BASE_URL = self._api_base_url.text().strip() or "http://127.0.0.1:8000/v1"
        oc.API_MODEL = self._api_model.text().strip() or "deepseek-v3"
        oc.FORCE_REPLY_MODE = str(self._force_mode.currentData() or self._force_mode.currentText() or "4").strip() or "4"
        oc.YUANBAO_FREE_API["enabled"] = True
        oc.YUANBAO_FREE_API["login_url"] = str(getattr(self, "_yuanbao_login_url_value", _DEFAULT_VALUES.get("yuanbao_login_url", "")) or "")
        oc.YUANBAO_FREE_API["agent_id"] = str(getattr(self, "_yuanbao_agent_id_value", _DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa")) or "")

        def worker() -> None:
            try:
                svc = get_yuanbao_free_api_service()
                result = svc.begin_login_flow()
                status = result.get('status') if isinstance(result, dict) else {}
                status = status if isinstance(status, dict) else {}
                logged_in = bool(result.get('logged_in') or status.get('logged_in')) if isinstance(result, dict) else False
                qrcode_ready = bool(result.get('qrcode_exists') or status.get('qrcode_exists')) if isinstance(result, dict) else False
                login_in_progress = bool(result.get('login_in_progress') or status.get('login_in_progress')) if isinstance(result, dict) else False
                message = str((result or {}).get('message') or '').strip() if isinstance(result, dict) else ''
                last_error = str(status.get('last_error') or '').strip()
                raw_stage = str(status.get('last_message') or '').strip()
                stage = self._describe_yuanbao_stage(raw_stage)
                stage_in_progress = raw_stage in {
                    'starting_login',
                    'starting_playwright',
                    'launching_browser',
                    'creating_page',
                    'page_loading',
                    'page_loaded',
                    'browser_initialized',
                    'dismissing_dialog',
                    'resolving_login_button',
                    'waiting_login_button',
                    'clicking_login_button',
                    'login_button_clicked',
                    'waiting_qrcode',
                    'refreshing_qrcode',
                    'waiting_scan_confirm',
                }

                if logged_in:
                    self._emit_info("元宝已登录，本地服务可直接使用。", min_tick=14, max_tick=120)
                elif qrcode_ready:
                    self._emit_info("元宝二维码已生成，请在弹出的二维码窗口中扫码登录。", min_tick=16, max_tick=180)
                elif login_in_progress or (not last_error and stage_in_progress):
                    detail = stage or message or '正在继续初始化元宝登录流程'
                    self._emit_info(f"元宝登录流程已启动：{detail}", min_tick=14, max_tick=180)
                else:
                    log_path = get_yuanbao_free_api_log_path()
                    detail = last_error or stage or message or f'请查看 {log_path.name}'
                    self._emit_info(f"元宝登录未能启动：{detail}", min_tick=18, max_tick=260)
            except Exception as exc:
                _logger.error("Start YuanBao login failed: %s", exc)
                self._emit_info(f"启动元宝登录失败: {exc}", min_tick=18, max_tick=220)

        try:
            from lib.script.ui.yuanbao_login_dialog import init_yuanbao_login_dialog
            init_yuanbao_login_dialog()
        except Exception as exc:
            _logger.debug("Init YuanBao login dialog failed: %s", exc)
        self._ec.publish(Event(EventType.YUANBAO_LOGIN_QR_SHOW, {
            'title': '元宝扫码登录',
            'status': '正在启动元宝服务并等待二维码生成，请稍候...',
            'qr_png': None,
        }))
        self._emit_info("正在启动元宝服务并准备登录二维码，请稍候...", min_tick=12, max_tick=180)
        threading.Thread(target=worker, daemon=True, name="yuanbao-login-start").start()

    def _on_stop_yuanbao_login(self) -> None:
        def worker() -> None:
            try:
                svc = get_yuanbao_free_api_service()
                svc.stop_login_flow()
                self._emit_info("已退出元宝登录，并关闭本地元宝服务。", min_tick=12, max_tick=140)
            except Exception as exc:
                _logger.error("Stop YuanBao login failed: %s", exc)
                self._emit_info(f"退出元宝登录失败: {exc}", min_tick=18, max_tick=220)

        self._emit_info("正在退出元宝登录并关闭本地元宝服务...", min_tick=10, max_tick=120)
        threading.Thread(target=worker, daemon=True, name="yuanbao-login-stop").start()

    @staticmethod
    def _describe_yuanbao_stage(stage: str) -> str:
        mapping = {
            'starting_login': '正在初始化登录流程',
            'starting_playwright': '正在启动浏览器驱动',
            'launching_browser': '正在启动浏览器',
            'creating_page': '正在创建页面',
            'page_loading': '正在打开元宝页面',
            'page_loaded': '元宝页面已打开，正在继续登录',
            'browser_initialized': '浏览器已就绪，正在继续登录',
            'dismissing_dialog': '正在关闭页面弹窗',
            'resolving_login_button': '正在定位登录入口',
            'waiting_login_button': '正在等待登录入口出现',
            'clicking_login_button': '正在点击登录入口',
            'login_button_clicked': '登录入口已点击，正在等待二维码',
            'login_button_not_found': '未找到登录入口',
            'waiting_qrcode': '正在等待二维码出现',
            'qrcode_ready': '二维码已生成',
            'waiting_scan_confirm': '二维码已生成，正在等待扫码确认',
            'refreshing_qrcode': '二维码已过期，正在尝试刷新',
            'qrcode_container_not_found': '未找到二维码容器',
            'login_success': '登录成功',
            'login_timeout': '扫码超时',
            'browser_init_failed': '浏览器初始化失败',
            'login_failed': '登录失败',
            'login_button_not_found_assume_logged_in': '未找到登录入口，疑似已登录',
            'already_logged_in': '已登录',
            'browser_closed': '浏览器已关闭',
        }
        return mapping.get(stage, stage)

    @staticmethod
    def _wrap_fixed_width_field(widget: QWidget, field_width: int = _CONFIG_FIELD_WIDTH) -> QWidget:
        wrap = QWidget()
        wrap.setFixedWidth(int(field_width))
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(widget, 0, Qt.AlignLeft | Qt.AlignVCenter)
        row.addStretch(1)
        return wrap

    def _create_sequence_editor(self, value, *, field_width: int = _CONFIG_FIELD_WIDTH):
        group, row = self._create_fixed_width_row_group(
            field_width=field_width,
            spacing=scale_px(10),
        )

        items = list(value) if isinstance(value, (tuple, list)) else [value]
        editors: list[QLineEdit] = []
        for item in items:
            editor = self._create_config_line_edit(item, expanding=True)
            editors.append(editor)
            row.addWidget(editor, 1)
        return editors, group

    @staticmethod
    def _set_sequence_editor_values(editors, value) -> None:
        if not isinstance(value, (tuple, list)):
            return
        for idx, editor in enumerate(editors):
            if idx >= len(value):
                break
            if isinstance(editor, QLineEdit):
                editor.setText(_format_config_editor_value(value[idx]))

    @staticmethod
    def _parse_text_by_template(text: str, template):
        if isinstance(template, str):
            return text
        if isinstance(template, bool):
            return bool(text.lower() in ("1", "true", "yes", "on"))
        if isinstance(template, int):
            return int(text)
        if isinstance(template, float):
            return float(text)
        return ast.literal_eval(text)

    @staticmethod
    def _set_config_editor_value(editor, value) -> None:
        if isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
            return
        if isinstance(editor, QSlider):
            editor.setValue(AISettingsPanel._volume_percent_from_value(value))
            return
        if isinstance(editor, _DecimalSliderField):
            editor.set_value(value)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(_format_config_editor_value(value))

    @staticmethod
    def _get_autostart_enabled() -> bool:
        try:
            from lib.core.tray_icon import get_tray_icon
            tray = get_tray_icon()
            return bool(tray._is_autostart_enabled())
        except Exception:
            return False

    def _set_autostart_enabled(self, enabled: bool) -> None:
        try:
            from lib.core.tray_icon import get_tray_icon
            tray = get_tray_icon()
            target = bool(enabled)
            tray._on_toggle_autostart(target, source="panel")
            actual = bool(tray._is_autostart_enabled())
            self._set_autostart_checkbox_checked(actual)
            if actual != target:
                raise ValueError("开机启动设置未生效，请检查“启动程序.bat”是否存在")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"开机启动设置失败: {e}") from e

    def _subscribe_autostart_events(self) -> None:
        if self._autostart_status_subscribed:
            return
        self._ec.subscribe(EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change)
        self._autostart_status_subscribed = True

    def _unsubscribe_autostart_events(self) -> None:
        if not self._autostart_status_subscribed:
            return
        self._ec.unsubscribe(EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change)
        self._autostart_status_subscribed = False

    def _set_autostart_checkbox_checked(self, enabled: bool) -> None:
        if not isinstance(self._autostart_checkbox, QCheckBox):
            return
        target = bool(enabled)
        if self._autostart_checkbox.isChecked() == target:
            return
        blocked = self._autostart_checkbox.blockSignals(True)
        try:
            self._autostart_checkbox.setChecked(target)
        finally:
            self._autostart_checkbox.blockSignals(blocked)

    def _on_autostart_status_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get("enabled", self._get_autostart_enabled()))
        self._set_autostart_checkbox_checked(enabled)

    def _append_autostart_field(
        self,
        form: QFormLayout,
        fields: list[dict],
        label_width: int,
        field_width: int,
    ) -> None:
        editor = QCheckBox()
        default_enabled = self._get_autostart_enabled()
        editor.setChecked(default_enabled)
        self._autostart_checkbox = editor
        row_widget = self._wrap_fixed_width_field(editor, field_width=field_width)
        label = self._create_form_label("开机启动", label_width)
        description = "桌宠随系统启动；保存时复用系统托盘开机启动逻辑。"
        self._set_widget_description(label, description)
        self._set_widget_description(row_widget, description)
        self._set_widget_description(editor, description)
        form.addRow(label, row_widget)
        fields.append({
            "kind": "external_autostart",
            "dict_name": "ANIMATION",
            "key": "autostart_enabled",
            "editor": editor,
            "template": True,
            "default": bool(default_enabled),
        })

    def _parse_editor_value(self, field: dict) -> dict[str, object]:
        kind = str(field.get("kind") or "single")
        if kind == "external_autostart":
            return {}
        if kind == "range_pair":
            keys = field.get("keys") or []
            editors = field.get("editors") or []
            templates = field.get("templates") or []
            if len(keys) != 2 or len(editors) != 2 or len(templates) != 2:
                raise ValueError("范围配置结构无效")
            result = {}
            for idx in range(2):
                editor = editors[idx]
                if not isinstance(editor, QLineEdit):
                    raise ValueError("范围配置编辑控件无效")
                text = editor.text().strip()
                result[str(keys[idx])] = self._parse_text_by_template(text, templates[idx])
            return result

        if kind == "sequence":
            key = str(field.get("key") or "")
            editors = field.get("editors") or []
            template = field.get("template")
            if not isinstance(template, (tuple, list)):
                raise ValueError("数组配置模板无效")
            if len(editors) != len(template):
                raise ValueError("数组配置长度不一致")
            parsed_items = []
            for idx, editor in enumerate(editors):
                if not isinstance(editor, QLineEdit):
                    raise ValueError("数组配置编辑控件无效")
                text = editor.text().strip()
                parsed_items.append(self._parse_text_by_template(text, template[idx]))
            if isinstance(template, tuple):
                return {key: tuple(parsed_items)}
            return {key: list(parsed_items)}

        if kind == "volume_slider":
            key = str(field.get("key") or "")
            editor = field.get("editor")
            if not isinstance(editor, QSlider):
                raise ValueError("音量滑块控件无效")
            return {key: self._volume_value_from_percent(editor.value())}

        if kind == "decimal_slider":
            key = str(field.get("key") or "")
            editor = field.get("editor")
            template = field.get("template")
            if not isinstance(editor, _DecimalSliderField):
                raise ValueError("小数滑块控件无效")
            return {key: self._parse_text_by_template(editor.text().strip(), template)}

        key = str(field.get("key") or "")
        editor = field.get("editor")
        template = field.get("template")
        if isinstance(editor, QCheckBox):
            return {key: bool(editor.isChecked())}
        if not isinstance(editor, QLineEdit):
            raise ValueError("不支持的配置编辑控件")
        text = editor.text().strip()
        return {key: self._parse_text_by_template(text, template)}

    def _raise_config_value_error(self, dict_name: str, key: str, reason: str) -> None:
        friendly = _friendly_key_name(dict_name, key)
        raise ValueError(f"{dict_name}.{key}（{friendly}）{reason}")

    def _validate_general_numeric(self, dict_name: str, key: str, value, kind: str, min_val: float, max_val: float) -> None:
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                self._raise_config_value_error(dict_name, key, "必须为整数")
            if value < int(min_val) or value > int(max_val):
                self._raise_config_value_error(dict_name, key, f"必须在 {int(min_val)}~{int(max_val)} 范围内")
            return

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._raise_config_value_error(dict_name, key, "必须为数字")
        try:
            num = float(value)
        except Exception:
            self._raise_config_value_error(dict_name, key, "必须为数字")
            return
        if not math.isfinite(num):
            self._raise_config_value_error(dict_name, key, "必须为有限数字")
        if num < min_val or num > max_val:
            self._raise_config_value_error(dict_name, key, f"必须在 {min_val}~{max_val} 范围内")

    def _validate_general_config_value(self, dict_name: str, key: str, value) -> None:
        pair = (dict_name, key)

        if pair in _GENERAL_BOOL_KEYS:
            if not isinstance(value, bool):
                self._raise_config_value_error(dict_name, key, "必须为开关值")
            return

        if pair in _GENERAL_TUPLE_INT_RULES:
            min_item, max_item = _GENERAL_TUPLE_INT_RULES[pair]
            if not isinstance(value, tuple) or len(value) != 2:
                self._raise_config_value_error(dict_name, key, "必须为长度为 2 的整数元组")
            left, right = value
            if isinstance(left, bool) or not isinstance(left, int):
                self._raise_config_value_error(dict_name, key, "左值必须为整数")
            if isinstance(right, bool) or not isinstance(right, int):
                self._raise_config_value_error(dict_name, key, "右值必须为整数")
            if left < min_item or left > max_item or right < min_item or right > max_item:
                self._raise_config_value_error(dict_name, key, f"每项必须在 {min_item}~{max_item} 范围内")
            if left > right:
                self._raise_config_value_error(dict_name, key, "最小值不能大于最大值")
            return

        if pair == ("CLOUD_MUSIC", "cache_dir"):
            if not isinstance(value, str):
                self._raise_config_value_error(dict_name, key, "必须为文本路径")
            normalized = value.strip()
            if not normalized:
                self._raise_config_value_error(dict_name, key, "不能为空")
            if Path(normalized).is_absolute():
                self._raise_config_value_error(dict_name, key, "必须使用相对路径")
            if "\n" in normalized or "\r" in normalized:
                self._raise_config_value_error(dict_name, key, "路径包含非法换行字符")
            return

        if pair == ("CLOUD_MUSIC", "local_music_dir"):
            if not isinstance(value, str):
                self._raise_config_value_error(dict_name, key, "必须为文本路径")
            normalized = value.strip()
            if not normalized:
                return
            if "\n" in normalized or "\r" in normalized:
                self._raise_config_value_error(dict_name, key, "路径包含非法换行字符")

            candidate = Path(normalized)
            if not candidate.is_absolute():
                candidate = _project_root() / normalized
            if candidate.exists() and not candidate.is_dir():
                self._raise_config_value_error(dict_name, key, "必须指向文件夹路径")
            return

        if pair == ("CLOUD_MUSIC", "launch_wuwa_path"):
            if not isinstance(value, str):
                self._raise_config_value_error(dict_name, key, "必须为文本路径")
            normalized = value.strip()
            if not normalized:
                return
            if "\n" in normalized or "\r" in normalized:
                self._raise_config_value_error(dict_name, key, "路径包含非法换行字符")

            expanded = os.path.expandvars(os.path.expanduser(normalized))
            candidate = Path(expanded)
            if not candidate.is_absolute():
                candidate = _project_root() / candidate

            ext = candidate.suffix.lower()
            if ext not in (".exe", ".bat", ".lnk"):
                self._raise_config_value_error(dict_name, key, "仅支持 .exe / .bat / .lnk 文件")
            if candidate.exists() and not candidate.is_file():
                self._raise_config_value_error(dict_name, key, "必须指向文件路径")
            return

        if pair == ("VOICE", "microphone_push_to_talk_key"):
            if not isinstance(value, str):
                self._raise_config_value_error(dict_name, key, "必须为文本内容")
            normalized = value.strip()
            if not normalized:
                return
            if "\n" in normalized or "\r" in normalized:
                self._raise_config_value_error(dict_name, key, "内容包含非法换行字符")
            if parse_hotkey_binding(normalized) is None:
                self._raise_config_value_error(dict_name, key, "格式无效，示例：Ctrl+Shift+V")
            return

        numeric_rule = _GENERAL_NUMERIC_RULES.get(pair)
        if numeric_rule is not None:
            kind, min_val, max_val = numeric_rule
            self._validate_general_numeric(dict_name, key, value, kind, min_val, max_val)

    def _validate_general_config_relations(self, values_by_dict: dict[str, dict]) -> None:
        for dict_name, left_key, right_key in _GENERAL_RANGE_RELATIONS:
            section = values_by_dict.get(dict_name)
            if not isinstance(section, dict):
                continue
            if left_key not in section or right_key not in section:
                continue
            left = section[left_key]
            right = section[right_key]
            try:
                left_num = float(left)
                right_num = float(right)
            except Exception:
                self._raise_config_value_error(dict_name, left_key, "与关联上限比较失败")
                return
            if left_num > right_num:
                left_name = _friendly_key_name(dict_name, left_key)
                right_name = _friendly_key_name(dict_name, right_key)
                raise ValueError(f"{dict_name} 配置无效：{left_name} 不能大于 {right_name}")

    def _validate_general_config_values(self, values_by_dict: dict[str, dict]) -> None:
        for dict_name, section in values_by_dict.items():
            if not isinstance(section, dict):
                raise ValueError(f"{dict_name} 配置结构无效")
            for key, value in section.items():
                self._validate_general_config_value(str(dict_name), str(key), value)
        self._validate_general_config_relations(values_by_dict)

    def _validate_ai_values(self, values: dict) -> None:
        validate_ai_values(values)

    def _load_config_tab_values(self) -> None:
        import config.config as cc

        for meta in self._config_tab_meta.values():
            for field in meta.get("fields", []):
                kind = str(field.get("kind") or "single")
                if kind == "external_autostart":
                    editor = field.get("editor")
                    if isinstance(editor, QCheckBox):
                        editor.setChecked(self._get_autostart_enabled())
                    continue

                dict_name = str(field.get("dict_name") or "")
                section = getattr(cc, dict_name, None)
                if not isinstance(section, dict):
                    continue
                if kind == "range_pair":
                    keys = field.get("keys") or []
                    editors = field.get("editors") or []
                    if len(keys) == 2 and len(editors) == 2:
                        for idx in range(2):
                            key = str(keys[idx])
                            if key in section:
                                self._set_config_editor_value(editors[idx], section[key])
                    continue
                if kind == "sequence":
                    key = str(field.get("key") or "")
                    if key in section:
                        self._set_sequence_editor_values(field.get("editors") or [], section[key])
                    continue
                key = str(field.get("key") or "")
                if key in section:
                    self._set_config_editor_value(field.get("editor"), section[key])

    def _collect_config_category_values(self, category_id: str) -> dict[str, dict]:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            raise ValueError("未找到配置分类")

        values: dict[str, dict] = {}
        for field in meta.get("fields", []):
            dict_name = str(field.get("dict_name") or "")
            try:
                parsed_items = self._parse_editor_value(field)
            except Exception as e:
                key_text = str(field.get("key") or ",".join(field.get("keys") or []))
                raise ValueError(f"{dict_name}.{key_text} 格式错误: {e}") from e
            target_dict = values.setdefault(dict_name, {})
            for key, parsed in parsed_items.items():
                target_dict[str(key)] = parsed
        self._validate_general_config_values(values)
        return values

    def _collect_all_general_config_values(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = str(category.get("id") or "")
            if not category_id or category_id not in self._config_tab_meta:
                continue
            values = self._collect_config_category_values(category_id)
            for dict_name, items in values.items():
                target = merged.setdefault(str(dict_name), {})
                target.update(items)
        self._validate_general_config_values(merged)
        return merged

    def _apply_all_external_config_fields(self) -> None:
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = str(category.get("id") or "")
            if not category_id:
                continue
            self._apply_external_category_fields(category_id)

    def _ensure_config_defaults_integrity(self) -> None:
        """兜底检查默认值映射，避免“恢复默认”因缺项而失效。"""
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = str(category.get("id") or "")
            if not category_id:
                continue
            meta = self._config_tab_meta.get(category_id)
            if not isinstance(meta, dict):
                continue
            defaults = meta.setdefault("defaults", {})
            fields = meta.get("fields", [])
            for field in fields:
                kind = str(field.get("kind") or "single")
                dict_name = str(field.get("dict_name") or "")
                if kind == "external_autostart":
                    if "default" not in field:
                        field["default"] = bool(self._get_autostart_enabled())
                    continue
                if not dict_name:
                    continue
                bucket = defaults.setdefault(dict_name, {})
                if kind == "range_pair":
                    keys = field.get("keys") or []
                    templates = field.get("templates") or []
                    if len(keys) == 2 and len(templates) == 2:
                        for idx in range(2):
                            key = str(keys[idx] or "")
                            if key and key not in bucket:
                                bucket[key] = copy.deepcopy(templates[idx])
                    continue
                key = str(field.get("key") or "")
                if not key:
                    continue
                if key in bucket:
                    continue
                if kind == "sequence":
                    bucket[key] = copy.deepcopy(field.get("template", []))
                else:
                    bucket[key] = copy.deepcopy(field.get("template"))

    def _on_restore_config_category(self, category_id: str, *, emit_message: bool = True) -> None:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return
        for field in meta.get("fields", []):
            kind = str(field.get("kind") or "single")
            if kind == "external_autostart":
                editor = field.get("editor")
                if isinstance(editor, QCheckBox):
                    editor.setChecked(bool(field.get("default", self._get_autostart_enabled())))
                continue

            dict_name = str(field.get("dict_name") or "")
            defaults = meta.get("defaults", {})
            if dict_name not in defaults:
                continue
            if kind == "range_pair":
                keys = field.get("keys") or []
                editors = field.get("editors") or []
                if len(keys) == 2 and len(editors) == 2:
                    for idx in range(2):
                        key = str(keys[idx])
                        if key in defaults[dict_name]:
                            self._set_config_editor_value(editors[idx], defaults[dict_name][key])
                continue
            if kind == "sequence":
                key = str(field.get("key") or "")
                if key in defaults[dict_name]:
                    self._set_sequence_editor_values(field.get("editors") or [], defaults[dict_name][key])
                continue
            key = str(field.get("key") or "")
            if key in defaults[dict_name]:
                self._set_config_editor_value(field.get("editor"), defaults[dict_name][key])
        if emit_message:
            self._emit_info(f"{meta.get('title', '配置')}已恢复默认，点击“保存并退出”后生效。", min_tick=10, max_tick=90)

    def _apply_external_category_fields(self, category_id: str) -> None:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return
        for field in meta.get("fields", []):
            kind = str(field.get("kind") or "single")
            if kind != "external_autostart":
                continue
            editor = field.get("editor")
            if not isinstance(editor, QCheckBox):
                continue
            self._set_autostart_enabled(bool(editor.isChecked()))

    def _on_save_config_category(self, category_id: str) -> bool:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return False
        try:
            values = self._collect_config_category_values(category_id)
            _save_general_config(values)
            _apply_general_runtime(values)
            self._apply_external_category_fields(category_id)
            self._emit_info(f"{meta.get('title', '配置')}已保存。")
            return True
        except Exception as e:
            _logger.error("保存配置分类失败(%s): %s", category_id, e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_config_category_and_exit(self, category_id: str) -> None:
        if self._on_save_config_category(category_id):
            self.fade_out()

    def _install_line_edit_context_menus(self) -> None:
        for edit in self.findChildren(QLineEdit):
            if bool(getattr(edit, "_cn_context_menu_bound", False)):
                continue
            edit.setContextMenuPolicy(Qt.CustomContextMenu)
            edit.customContextMenuRequested.connect(
                lambda pos, target=edit: self._show_line_edit_context_menu(target, pos)
            )
            setattr(edit, "_cn_context_menu_bound", True)

    def _show_line_edit_context_menu(self, edit: QLineEdit, pos: QPoint) -> None:
        if not isinstance(edit, QLineEdit):
            return

        menu = QMenu(edit)
        font = get_ui_font(size=_CONFIG_FONT_SIZE)
        font.setBold(True)
        menu.setFont(font)

        can_edit = not bool(edit.isReadOnly())
        has_selection = bool(edit.hasSelectedText())
        can_paste = can_edit and bool(QApplication.clipboard().text())

        action_cut = menu.addAction("剪切")
        action_copy = menu.addAction("复制")
        action_paste = menu.addAction("粘贴")

        action_cut.setEnabled(can_edit and has_selection)
        action_copy.setEnabled(has_selection)
        action_paste.setEnabled(can_paste)

        chosen = menu.exec_(edit.mapToGlobal(pos))
        if chosen is action_cut:
            edit.cut()
        elif chosen is action_copy:
            edit.copy()
        elif chosen is action_paste:
            edit.paste()

    def _apply_project_fonts(self) -> None:
        """将面板及子控件字体统一为项目字体。"""
        base_font = get_ui_font()
        config_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        config_font.setBold(True)
        self.setFont(base_font)

        # 配置项与配置内容：统一粗体并放大 2xp。
        for widget in self.findChildren(QLabel):
            if widget is self._title_label or widget is self._hint_label:
                continue
            widget.setFont(config_font)
        for widget_type in (QLineEdit, QComboBox, QPushButton, QCheckBox):
            for widget in self.findChildren(widget_type):
                widget.setFont(config_font)

        # 下拉弹层是独立视图，需要显式设置字体。
        dropdown_font = get_ui_font(size=_DROPDOWN_ITEM_FONT_SIZE)
        dropdown_font.setBold(True)
        for combo in (self._force_mode, self._gpu_mode):
            view = combo.view()
            if view is not None:
                view.setFont(dropdown_font)

        # 标题与标题右侧说明保持统一样式。
        title_font = self._build_title_font()
        hint_font = self._build_hint_font()
        self._title_label.setFont(title_font)
        self._hint_label.setFont(hint_font)

        for meta in self._config_tab_meta.values():
            title_label = meta.get("title_label")
            hint_label = meta.get("hint_label")
            if isinstance(title_label, QLabel):
                title_label.setFont(title_font)
            if isinstance(hint_label, QLabel):
                hint_label.setFont(hint_font)

        tab_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        tab_font.setBold(True)
        # 设置标签按钮字体
        if hasattr(self, '_tab_buttons') and self._tab_buttons:
            for btn in self._tab_buttons:
                btn.setFont(tab_font)
        self._install_line_edit_context_menus()

    def _apply_style(self) -> None:
        border = UI_THEME["border"].name()
        mid = UI_THEME["mid"].name()
        bg = UI_THEME["bg"].name()
        text = UI_THEME["text"].name()
        highlight = UI_THEME["deep_cyan"].name()
        menu_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        menu_font.setBold(True)
        menu_font_family = str(menu_font.family() or "").replace("'", "\\'")
        menu_font_size = max(scale_px(12, min_abs=10), _CONFIG_FONT_SIZE)
        combo_drop_w = scale_px(72, min_abs=64)
        combo_right_pad = combo_drop_w + scale_px(8, min_abs=6)
        tab_min_w = max(scale_px(56, min_abs=52), int(round(scale_px(96, min_abs=82) * 2.0 / 3.0)))
        tab_pad_y = max(scale_px(3, min_abs=2), int(round(scale_px(5, min_abs=4) * 2.0 / 3.0)))
        tab_pad_x = max(scale_px(8, min_abs=6), int(round(scale_px(12, min_abs=10) * 2.0 / 3.0)))
        tab_margin_r = max(scale_px(2, min_abs=1), int(round(scale_px(3, min_abs=2) * 2.0 / 3.0)))
        tab_min_h = scale_px(24, min_abs=18)
        scroll_w = scale_px(14, min_abs=12)
        scroll_handle_min_h = scale_px(28, min_abs=20)

        self.setStyleSheet(
            f"""
            QWidget {{
                background: transparent;
                color: {text};
            }}
            QScrollArea {{
                border: 0px;
                background: transparent;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QMenu {{
                background: {bg};
                color: {text};
                border: 2px solid {border};
                border-radius: 0px;
                padding: {scale_px(3, min_abs=2)}px 0px;
                font-family: '{menu_font_family}';
                font-size: {menu_font_size}px;
                font-weight: 700;
            }}
            QMenu::item {{
                background: {bg};
                color: {text};
                padding: {scale_px(4, min_abs=3)}px {scale_px(18, min_abs=12)}px;
                margin: 0px;
                border: 0px;
            }}
            QMenu::item:selected {{
                background: {mid};
                color: {text};
            }}
            QMenu::item:pressed {{
                background: {highlight};
                color: {text};
            }}
            QMenu::separator {{
                height: 1px;
                background: {border};
                margin: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
            }}
            QScrollBar:vertical {{
                background: {bg};
                width: {scroll_w}px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {mid};
                border: 1px solid {border};
                min-height: {scroll_handle_min_h}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {highlight};
            }}
            QScrollBar::handle:vertical:pressed {{
                background: {text};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QLineEdit {{
                background: rgba(255, 255, 255, 128);
                color: {text};
                border: 1px solid {border};
                border-radius: 0px;
                padding: 2px 6px;
                min-height: {scale_px(22)}px;
            }}
            QComboBox {{
                background: rgba(255, 255, 255, 128);
                color: {text};
                border: 1px solid {border};
                border-radius: 0px;
                padding: 2px 6px;
                padding-right: {combo_right_pad}px;
                min-height: {scale_px(22)}px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {combo_drop_w}px;
                border-left: 1px solid {border};
                border-top: 0px;
                border-right: 0px;
                border-bottom: 0px;
                border-radius: 0px;
                background: {mid};
            }}
            QComboBox::drop-down:hover {{
                background: {highlight};
            }}
            QComboBox::drop-down:pressed {{
                background: {bg};
            }}
            QComboBox::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
            }}
            QComboBox QAbstractItemView {{
                background: {bg};
                color: {text};
                font-size: {_DROPDOWN_ITEM_FONT_SIZE}px;
                font-weight: 700;
                selection-background-color: {mid};
                selection-color: {text};
                border: 1px solid {border};
                border-radius: 0px;
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item {{
                background: {bg};
                color: {text};
                font-size: {_DROPDOWN_ITEM_FONT_SIZE}px;
                font-weight: 700;
                border: 0px;
                border-radius: 0px;
                padding: 4px 8px;
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {mid};
                color: {text};
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {mid};
                color: {text};
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:focus {{
                border: 0px;
                outline: 0px;
            }}
            QPushButton {{
                background: {bg};
                color: {text};
                border: 2px solid {border};
                border-radius: 0px;
                padding: 4px 10px;
                min-height: {scale_px(24)}px;
            }}
            QPushButton:hover {{
                background: {mid};
            }}
            QPushButton:pressed {{
                background: {highlight};
            }}
            QCheckBox {{
                spacing: {scale_px(6, min_abs=4)}px;
                color: {text};
                background: transparent;
                padding: 1px 0px;
            }}
            QCheckBox::indicator {{
                width: {scale_px(14, min_abs=12)}px;
                height: {scale_px(14, min_abs=12)}px;
                border: 1px solid {border};
                border-radius: 0px;
                background: {bg};
            }}
            QCheckBox::indicator:hover {{
                background: {mid};
            }}
            QCheckBox::indicator:checked {{
                border: 1px solid {border};
                border-radius: 0px;
                background: {mid};
            }}
            QCheckBox::indicator:checked:hover {{
                background: {highlight};
            }}
            QSlider::groove:horizontal {{
                border: 1px solid {border};
                background: rgba(255, 255, 255, 128);
                height: {scale_px(6, min_abs=5)}px;
                border-radius: 0px;
            }}
            QSlider::sub-page:horizontal {{
                background: {mid};
                border: 0px;
            }}
            QSlider::add-page:horizontal {{
                background: rgba(255, 255, 255, 128);
                border: 0px;
            }}
            QSlider::handle:horizontal {{
                background: {bg};
                border: 2px solid {border};
                width: {scale_px(10, min_abs=9)}px;
                margin: -4px 0px;
                border-radius: 0px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {highlight};
            }}
            QSlider::handle:horizontal:pressed {{
                background: {text};
            }}
            QTabBar::tab {{
                background: {bg};
                color: {text};
                border: 2px solid {border};
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                min-width: {tab_min_w}px;
                min-height: {tab_min_h}px;
                padding: {tab_pad_y}px {tab_pad_x}px;
                margin-right: {tab_margin_r}px;
            }}
            QTabBar::tab:selected {{
                background: {mid};
            }}
            QTabBar::tab:hover:!selected {{
                background: {mid};
            }}
            QTabBar::tab:pressed {{
                background: {highlight};
            }}
            """
        )
        # 垂直标签栏样式已在 ai_settings_tabs.py 中通过按钮样式设置

    def _layout_top_tab_bar(self) -> None:
        layout_ai_settings_tab_bar(self)

    def _show_floating_tab(self) -> None:
        show_ai_settings_tab_bar(self)

    def _hide_floating_tab(self) -> None:
        hide_ai_settings_tab_bar(self)

    def _layout_config_panels(self) -> None:
        layout_ai_settings_tab_panels(self)

    def _on_top_tab_changed(self, index: int) -> None:
        set_active_ai_settings_tab(self, index)

    def _cache_stable_window_size(self) -> tuple[int, int]:
        ai_panel = getattr(self, "_ai_panel", None)
        restore_visible = bool(ai_panel is not None and ai_panel.isVisible())
        if ai_panel is not None:
            ai_panel.show()

        self.adjustSize()
        target_w = max(self.minimumWidth(), int(round(self.width() * _PANEL_SCALE)))
        target_h = max(self.minimumHeight(), int(round(self.height() * _PANEL_SCALE)))
        self._stable_window_size = (target_w, target_h)

        if ai_panel is not None and not restore_visible:
            ai_panel.hide()
        return self._stable_window_size

    def load_values(self) -> None:
        self._reset_shared_on_next_save = False
        self._set_values_to_form(load_ai_values(_DEFAULT_VALUES))
        self._load_config_tab_values()

    def show_centered(self) -> None:
        self.load_values()
        current_index = 0
        # 获取当前选中的标签索引（从按钮组或按钮列表）
        if hasattr(self, '_tab_button_group') and self._tab_button_group is not None:
            current_index = max(0, self._tab_button_group.checkedId())
        elif hasattr(self, '_tab_buttons') and self._tab_buttons:
            for i, btn in enumerate(self._tab_buttons):
                if btn.isChecked():
                    current_index = i
                    break
        target_panel = None
        if 0 <= current_index < len(self._tab_pages):
            target_panel = self._tab_pages[current_index]
        if target_panel is None:
            target_panel = self._ai_panel
        if target_panel is not None:
            # 在布局测量前确保目标面板可见，避免上次停留在其它标签页后被隐藏导致尺寸被压缩。
            target_panel.show()
        target_w, target_h = self._stable_window_size or self._cache_stable_window_size()
        self.resize(target_w, target_h)

        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        if screen is not None:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)
        self._on_top_tab_changed(current_index)

        self._visible = True
        self.show()
        self._show_floating_tab()
        self._layout_config_panels()
        self.raise_()
        self.activateWindow()
        self._animate(1.0)

    def fade_out(self) -> None:
        self._visible = False
        self._hide_floating_tab()
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        animate_opacity(self._anim, self._opacity, target)

    def _on_anim_finished(self) -> None:
        if not self._visible:
            self._hide_floating_tab()
            self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_top_tab_bar()
        self._layout_config_panels()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._layout_top_tab_bar()
        self._layout_config_panels()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._visible:
            self._show_floating_tab()

    def hideEvent(self, event) -> None:
        self._hide_floating_tab()
        super().hideEvent(event)

    def _subscribe_border_effect_events(self) -> None:
        if not self._tick_subscribed:
            self._ec.subscribe(EventType.TICK, self._on_tick)
            self._tick_subscribed = True

    def _unsubscribe_border_effect_events(self) -> None:
        if self._tick_subscribed:
            self._ec.unsubscribe(EventType.TICK, self._on_tick)
            self._tick_subscribed = False

    def deleteLater(self) -> None:
        self._unsubscribe_border_effect_events()
        self._unsubscribe_autostart_events()
        self._hide_floating_tab()
        if self._tab_floating is not None:
            self._tab_floating.deleteLater()
            self._tab_floating = None
        super().deleteLater()

    def _random_border_spawn_point(self) -> tuple[int, int] | None:
        w = int(self.width())
        h = int(self.height())
        if w <= 0 or h <= 0:
            return None

        gx = int(self.x())
        gy = int(self.y())
        band = max(1, int(self._layer))
        edge = random.choice(("top", "bottom", "left", "right"))

        if edge == "top":
            x = random.randint(gx, gx + w - 1)
            y = random.randint(gy, min(gy + band - 1, gy + h - 1))
        elif edge == "bottom":
            x = random.randint(gx, gx + w - 1)
            y = random.randint(max(gy, gy + h - band), gy + h - 1)
        elif edge == "left":
            x = random.randint(gx, min(gx + band - 1, gx + w - 1))
            y = random.randint(gy, gy + h - 1)
        else:
            x = random.randint(max(gx, gx + w - band), gx + w - 1)
            y = random.randint(gy, gy + h - 1)
        return x, y

    def _request_border_flicker(self) -> None:
        pos = self._random_border_spawn_point()
        if pos is None:
            return
        self._ec.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "flicker_data",
            "area_type": "point",
            "area_data": pos,
        }))

    def _on_tick(self, event: Event) -> None:
        if not self.isVisible():
            return
        try:
            tick_count = int((event.data or {}).get("tick_count", 0))
        except Exception:
            tick_count = 0
        if tick_count <= 0:
            self._tick_counter += 1
            tick_count = self._tick_counter
        else:
            self._tick_counter = tick_count

        if tick_count % 5 == 0:
            for _ in range(random.randint(2, 4)):
                self._request_border_flicker()

    def _is_interactive_widget(self, widget) -> bool:
        interactive_types = (QLineEdit, QComboBox, QPushButton, QCheckBox, QSlider, QListView, QTabBar, QScrollArea)
        cur = widget
        while cur is not None and cur is not self:
            if isinstance(cur, interactive_types):
                return True
            cur = cur.parentWidget()
        return False

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)

        if event.button() == Qt.LeftButton:
            hit = self.childAt(event.pos())
            if not self._is_interactive_widget(hit):
                self._dragging = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _collect_values(self) -> dict:
        force_mode = str(self._force_mode.currentData() or "").strip()
        if force_mode not in ("", "0", "2", "3", "4"):
            raise ValueError("回复模式值无效")

        gpu_mode = str(self._gpu_mode.currentData() or _GPU_MODE_AUTO)
        num_gpu = _num_gpu_from_mode(gpu_mode)

        try:
            num_thread = int(self._num_thread.text().strip() or "0")
        except ValueError as e:
            raise ValueError("CPU线程数必须是整数") from e
        if num_thread < 0:
            raise ValueError("CPU线程数不能小于 0")

        try:
            api_temperature = float(self._api_temperature.text().strip() or "0.8")
        except ValueError as e:
            raise ValueError("采样温度必须是数字") from e
        if not (0.0 <= api_temperature <= 2.0):
            raise ValueError("采样温度范围应为 0~2")

        try:
            gsv_temperature = float(self._gsv_temperature.text().strip() or "1.35")
        except ValueError as e:
            raise ValueError("GSV服务温度必须是数字") from e
        if not (0.0 <= gsv_temperature <= 2.0):
            raise ValueError("GSV服务温度范围应为 0~2")

        try:
            gsv_speed_factor = float(self._gsv_speed_factor.text().strip() or "1.0")
        except ValueError as e:
            raise ValueError("GSV语速必须是数字") from e
        if not (0.5 <= gsv_speed_factor <= 2.0):
            raise ValueError("GSV语速范围应为 0.5~2.0")

        try:
            ai_voice_max_chars = int(float(self._ai_voice_max_chars.text().strip() or "40"))
        except ValueError as e:
            raise ValueError("GSV语音字数限制必须是整数") from e
        if not (20 <= ai_voice_max_chars <= 80):
            raise ValueError("GSV语音字数限制范围应为 20~80")

        try:
            gsv_cache_max_files = int(float(self._gsv_cache_max_files.text().strip() or "20"))
        except ValueError as e:
            raise ValueError("GSV缓存上限必须是整数") from e
        if not (1 <= gsv_cache_max_files <= 128):
            raise ValueError("GSV缓存上限范围应为 1~128")

        try:
            memory_context_limit = int(float(self._memory_context_limit.text().strip() or "12"))
        except ValueError as e:
            raise ValueError("记忆上下文条数必须是整数") from e
        if not (0 <= memory_context_limit <= 48):
            raise ValueError("记忆上下文条数范围应为 0~48")

        values = {
            "api_key": self._api_key.raw_text(),
            "force_reply_mode": force_mode,
            "api_base_url": self._api_base_url.text().strip(),
            "api_model": self._api_model.text().strip(),
            "yuanbao_login_url": str(_DEFAULT_VALUES.get("yuanbao_login_url", "") or "").strip(),
            "yuanbao_free_api_enabled": bool(self._yuanbao_free_api_enabled.isChecked()),
            "yuanbao_hy_source": str(_DEFAULT_VALUES.get("yuanbao_hy_source", "web") or "").strip(),
            "yuanbao_hy_user": "",
            "yuanbao_x_uskey": "",
            "yuanbao_agent_id": str(_DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa") or "").strip(),
            "yuanbao_chat_id": self._yuanbao_chat_id.text().strip(),
            "yuanbao_remove_conversation": bool(self._yuanbao_remove_conversation.isChecked()),
            "yuanbao_upload_images": bool(self._yuanbao_upload_images.isChecked()),
            "ollama_base_url": self._ollama_base_url.text().strip(),
            "ollama_model": self._ollama_model.currentText().strip(),
            "num_gpu": num_gpu,
            "num_thread": num_thread,
            "api_temperature": api_temperature,
            "gsv_auto_start": bool(self._gsv_auto_start.isChecked()),
            "gsv_temperature": gsv_temperature,
            "gsv_speed_factor": gsv_speed_factor,
            "ai_voice_max_chars": ai_voice_max_chars,
            "gsv_cache_max_files": gsv_cache_max_files,
            "memory_context_limit": memory_context_limit,
            "api_enable_thinking": bool(self._api_enable_thinking.isChecked()),
            "auto_companion_enabled": bool(self._auto_companion_enabled.isChecked()),
        }
        self._validate_ai_values(values)
        return values

    def _set_values_to_form(self, values: dict) -> None:
        self._api_key.set_raw_text(str(values.get("api_key", "")))
        self._api_base_url.setText(str(values.get("api_base_url", "")))
        self._api_model.setText(str(values.get("api_model", "")))
        self._yuanbao_login_url_value = str(_DEFAULT_VALUES.get("yuanbao_login_url", ""))
        self._yuanbao_free_api_enabled.setChecked(bool(values.get("yuanbao_free_api_enabled", False)))
        self._yuanbao_hy_source_value = str(_DEFAULT_VALUES.get("yuanbao_hy_source", "web"))
        self._yuanbao_hy_user_value = ""
        self._yuanbao_x_uskey_value = ""
        self._yuanbao_agent_id_value = str(_DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa"))
        self._yuanbao_chat_id.setText(str(values.get("yuanbao_chat_id", "")))
        self._yuanbao_remove_conversation.setChecked(bool(values.get("yuanbao_remove_conversation", False)))
        self._yuanbao_upload_images.setChecked(bool(values.get("yuanbao_upload_images", True)))
        self._ollama_base_url.setText(str(values.get("ollama_base_url", "")))
        self._refresh_ollama_model_choices(str(values.get("ollama_model", "")))
        gpu_mode = _gpu_mode_from_num_gpu(values.get("num_gpu", -1))
        gpu_idx = self._gpu_mode.findData(gpu_mode)
        self._gpu_mode.setCurrentIndex(max(0, gpu_idx))
        self._num_thread.setText(str(values.get("num_thread", 0)))
        self._api_temperature.setText(str(values.get("api_temperature", 0.8)))
        self._gsv_auto_start.setChecked(bool(values.get("gsv_auto_start", True)))
        self._gsv_temperature.setText(str(values.get("gsv_temperature", 1.35)))
        self._gsv_speed_factor.setText(str(values.get("gsv_speed_factor", 1.0)))
        self._ai_voice_max_chars.setText(str(values.get("ai_voice_max_chars", 40)))
        self._gsv_cache_max_files.setText(str(values.get("gsv_cache_max_files", 20)))
        self._memory_context_limit.setText(str(values.get("memory_context_limit", 12)))
        self._api_enable_thinking.setChecked(bool(values.get("api_enable_thinking", False)))
        self._auto_companion_enabled.setChecked(bool(values.get("auto_companion_enabled", True)))

        mode_value = str(values.get("force_reply_mode", "") or "").strip()
        idx = self._force_mode.findData(mode_value)
        self._force_mode.setCurrentIndex(max(0, idx))

    def _ollama_model_placeholder_message(self) -> str:
        error = get_model_list_error()
        return f"未检测到 Ollama 模型（{error}）" if error else "未检测到 Ollama 模型"

    def _refresh_ollama_model_choices(self, selected_model: str = "") -> None:
        if not isinstance(self._ollama_model, QComboBox):
            return
        models = get_available_model_names()
        selected_text = str(selected_model or "").strip()
        self._ollama_model.blockSignals(True)
        self._ollama_model.clear()
        if models:
            for model in models:
                self._ollama_model.addItem(model, model)
            if selected_text:
                idx = self._ollama_model.findData(selected_text)
                if idx >= 0:
                    self._ollama_model.setCurrentIndex(idx)
                else:
                    self._ollama_model.setEditText(selected_text)
            else:
                self._ollama_model.setCurrentIndex(0)
            if self._ollama_model.lineEdit():
                self._ollama_model.lineEdit().setPlaceholderText("")
            self._ollama_model.setToolTip(f"检测到 {len(models)} 个 Ollama 模型")
        else:
            placeholder = self._ollama_model_placeholder_message()
            if self._ollama_model.lineEdit():
                self._ollama_model.lineEdit().clear()
                self._ollama_model.lineEdit().setPlaceholderText(placeholder)
            self._ollama_model.setToolTip(placeholder)
            if selected_text:
                self._ollama_model.setEditText(selected_text)
        self._ollama_model.blockSignals(False)

    def _refresh_ollama_model_dropdown(self) -> None:
        if not isinstance(self._ollama_model, QComboBox):
            return
        selected_text = self._ollama_model.currentText().strip()
        self._refresh_ollama_model_choices(selected_text)

    def _emit_info(self, text: str, min_tick: int = 12, max_tick: int = 140) -> None:
        self._ec.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": min_tick,
            "max": max_tick,
        }))

    def _ensure_update_dialog(self) -> DesktopPetUpdateDialog:
        if self._update_dialog is None:
            self._update_dialog = DesktopPetUpdateDialog()
        return self._update_dialog

    def _open_update_dialog(self, mode: str) -> None:
        dialog = self._ensure_update_dialog()
        if dialog.is_busy():
            self._emit_info("更新窗口正在处理任务，请稍候。", min_tick=12, max_tick=160)
            return

        self.fade_out()
        delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))

        def show_dialog() -> None:
            started = (
                dialog.begin_release_check()
                if mode == "release"
                else dialog.begin_git_sync_check()
            )
            if not started:
                self._emit_info("更新窗口正在处理任务，请稍候。", min_tick=12, max_tick=160)

        QTimer.singleShot(delay_ms, show_dialog)

    def _on_check_updates(self) -> None:
        self._open_update_dialog("release")

    def _on_sync_dev_build(self) -> None:
        self._open_update_dialog("git")

    def _on_restore_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._ensure_config_defaults_integrity()
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = str(category.get("id") or "")
            if not category_id:
                continue
            self._on_restore_config_category(category_id, emit_message=False)
        self._reset_shared_on_next_save = True
        self._emit_info("已恢复默认配置，点击“保存并退出”后将同时重置C盘本地配置。", min_tick=10, max_tick=90)

    def _on_save(self) -> bool:
        try:
            ai_values = self._collect_values()
            general_values = self._collect_all_general_config_values()
            save_ai_values(ai_values, _DEFAULT_VALUES)
            _save_general_config(general_values)
            if self._reset_shared_on_next_save:
                _reset_shared_core_configs_from_project()
            apply_ai_runtime(ai_values, _DEFAULT_VALUES)
            _apply_general_runtime(general_values)
            self._apply_all_external_config_fields()
            try:
                from lib.script.gsvmove import get_gsvmove_service

                get_gsvmove_service().cleanup_saved_audio_cache()
            except Exception as trim_exc:
                _logger.warning("应用 GSV 语音缓存上限失败: %s", trim_exc)
            self._reset_shared_on_next_save = False
            self._emit_info("控制面板设置已保存，重启程序后完整生效。")
            return True
        except Exception as e:
            _logger.error("保存控制面板设置失败: %s", e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_and_exit(self) -> None:
        if self._on_save():
            self.fade_out()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.fillRect(rect, UI_THEME["border"])
        painter.fillRect(
            rect.adjusted(self._layer, self._layer, -self._layer, -self._layer),
            UI_THEME["mid"],
        )
        painter.fillRect(
            rect.adjusted(self._border, self._border, -self._border, -self._border),
            UI_THEME["bg"],
        )

        wm_color = QColor(UI_THEME["deep_pink"])
        wm_color.setAlpha(220)
        painter.setPen(wm_color)

        # 顶部硬件水印：字号缩小为左下水印的 1/3，贴顶并水平居中。
        wm_font_small = get_digit_font(size=max(scale_px(8, min_abs=6), scale_px(46, min_abs=24) // 3))
        wm_font_small.setBold(True)
        painter.setFont(wm_font_small)
        top_wm_h = max(scale_px(42, min_abs=18), int(self.height() * 0.14))
        top_wm_rect = rect.adjusted(
            self._border + scale_px(6),
            self._border + scale_px(1),
            -self._border - scale_px(6),
            -(self.height() - top_wm_h - self._border - scale_px(1)),
        )
        painter.drawText(top_wm_rect, Qt.AlignHCenter | Qt.AlignTop, self._gpu_watermark_text)

        wm_font = get_digit_font(
            size=max(scale_px(12, min_abs=10), int(round(scale_px(46, min_abs=24) * _LEFT_WM_SCALE)))
        )
        wm_font.setBold(True)
        painter.setFont(wm_font)
        wm_width = max(scale_px(80, min_abs=1), int(round((self.width() // 2) * _LEFT_WM_SCALE)))
        wm_height = max(scale_px(80, min_abs=1), int(round((self.height() * 0.42) * _LEFT_WM_SCALE)))
        wm_shift_right = scale_px(30, min_abs=24)
        wm_rect = rect.adjusted(
            self._border + scale_px(8) + wm_shift_right,
            self.height() - wm_height - self._border - scale_px(6),
            -(self.width() - wm_width - self._border - scale_px(8) - wm_shift_right),
            -self._border - scale_px(6),
        )
        painter.drawText(wm_rect, Qt.AlignLeft | Qt.AlignBottom, _WATERMARK_TEXT)
