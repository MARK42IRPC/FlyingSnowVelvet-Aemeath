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
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

import requests

from PyQt5.QtCore import Qt, QPoint, QSize, QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal
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
    QGraphicsOpacityEffect,
    QScrollArea,
    QSizePolicy,
    QFileDialog,
    QSlider,
    QMenu,
)
from PyQt5.QtGui import QPainter, QColor, QCursor, QPixmap

from config.config import UI
from lib.core.qt_bridge.colors import UI_THEME
from lib.core.qt_bridge.font import get_ui_font, get_digit_font
from lib.core.backend_router import get_backend_descriptors
from config.general_user_settings import save_general_values
from config.ollama_config import (
    AI_VOICE_MAX_CHARS_DEFAULT,
    AI_VOICE_MAX_CHARS_MAX,
    AI_VOICE_MAX_CHARS_MIN,
)
from config.scale import scale_px
from lib.script.ui.ai_settings_validators import validate_ai_values
from lib.script.ui.ai_settings_storage import load_ai_values, save_ai_values, apply_ai_runtime
from lib.script.ui.announcement_dialog import (
    load_announcement_preferences,
    set_announcement_forever_suppressed,
)
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.ai_settings_tabs import (
    attach_ai_settings_tabs,
    layout_ai_settings_tab_bar,
    show_ai_settings_tab_bar,
    hide_ai_settings_tab_bar,
    layout_ai_settings_tab_panels,
    set_active_ai_settings_tab,
)
from lib.core.anchor_utils import animate_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.script.app.startup_probe import load_saved_watermark_payload as _load_saved_watermark_payload
from lib.script.SEanima.clip import (
    DEFAULT_EXIT_ANIMATION_FOLDER,
    DEFAULT_START_ANIMATION_FOLDER,
    list_animation_folder_choices,
)
from lib.script.chat.ollama_registry import get_available_model_names, get_model_list_error
from lib.script.chat.network_policy import API_TIMEOUT_SECS
from lib.script.chat.persona_storage import ensure_user_persona_file
from lib.script.microphone_stt.push_to_talk import parse_hotkey_binding
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.ui.voice_package_installer import (
    VoicePackageInstallBanner,
    VoicePackageInstallerDialog,
    VoicePackageManagementBar,
)
from lib.script.workbench.settings import (
    GENERAL_CONFIG_CATEGORIES,
    SettingsPageScaffold,
    create_settings_form,
)
from lib.script.workbench.theme import COLORS as WORKBENCH_COLORS, get_workbench_colors
from lib.script.yuanbao_free_api import get_yuanbao_free_api_service
from lib.script.yuanbao_free_api.service import get_yuanbao_free_api_log_path
from lib.script.gsvmove import get_voice_package_status

_logger = get_logger(__name__)


_GPU_MODE_CPU = "cpu"
_GPU_MODE_GPU = "gpu"
_GPU_MODE_AUTO = "auto"
_DROPDOWN_POPUP_LAYER = 601
_EXTERNAL_CONFIG_FIELD_KINDS = {
    "external_autostart",
    "external_announcement_suppression",
}

_MANUAL_API_PROVIDER_PRESETS = (
    ("自定义地址", ""),
    ("OpenAI", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Kimi", "https://api.moonshot.cn/v1"),
    ("智谱 AI", "https://open.bigmodel.cn/api/paas/v4"),
    ("阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("硅基流动", "https://api.siliconflow.cn/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
)

_DEFAULT_VALUES = {
    "api_key": "",
    "force_reply_mode": "1",
    "welfare_intelligence_boost": False,
    "api_base_url": "",
    "api_model": "gpt-5.4",
    "yuanbao_login_url": "https://yuanbao.tencent.com/chat/naQivTmsDa",
    "yuanbao_hy_source": "web",
    "yuanbao_hy_user": "",
    "yuanbao_x_uskey": "",
    "yuanbao_agent_id": "naQivTmsDa",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen2.5",
    "num_gpu": -1,
    "num_thread": 0,
    "api_temperature": 1.35,
    "model_vision": 0,
    "gsv_auto_start": False,
    "gsv_gpu_hybrid": False,
    "gsv_temperature": 1.0,
    "gsv_top_k": 15,
    "gsv_top_p": 1.0,
    "gsv_repetition_penalty": 1.35,
    "gsv_speed_factor": 1.0,
    "gsv_text_split_method": "cut5",
    "gsv_fragment_interval": 0.3,
    "gsv_seed": -1,
    "gsv_max_steps": 500,
    "ai_voice_max_chars": AI_VOICE_MAX_CHARS_DEFAULT,
    "gsv_cache_max_files": 20,
    "memory_context_limit": 12,
    "memory_recall_count": 30,
    "api_enable_thinking": False,
    "auto_companion_enabled": True,
    "auto_companion_interval_minutes": 2,
}

_WATERMARK_TEXT = "Aemeath\nAIsetting"
_TITLE_FONT_SIZE = scale_px(23, min_abs=17)
_CONFIG_FONT_SIZE = scale_px(17, min_abs=12)
_DROPDOWN_ITEM_FONT_SIZE = max(scale_px(8, min_abs=8), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_PANEL_SCALE = 1.05
_LEFT_WM_SCALE = 2.0 / 3.0
_AI_HINT_TEXT = "保存后会写入本地 AI 配置文件，建议重启程序后完整生效"
_GENERAL_HINT_TEXT = "保存后会写入本地配置文件，建议重启程序后完整生效"
_HINT_FONT_SIZE = max(scale_px(12, min_abs=9), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_UPDATE_BUTTON_ROW_GAP = scale_px(10, min_abs=10)
_QUARK_UPDATE_URL = "https://pan.quark.cn/s/9158e62439e2"
_SPONSOR_AUTHOR_URL = "https://afdian.com/a/fxxrdeskpet"
_CONTRIBUTION_IGNORED_TITLE_PARTS = {"保留所有权利"}
_CONTRIBUTION_HIDDEN_ROLES = {"安装教程指引"}
_MANUAL_CONTRIBUTION_RECORDS = [
    {
        "insert_at": 1,
        "name": "猫咪",
        "role": "配音（千咲，达妮娅，莫宁）",
        "url": "https://space.bilibili.com/1838261330",
    },
    {
        "insert_at": 2,
        "name": "TDSI服务器",
        "role": "服务器支持",
        "url": "https://tdsi.top",
    },
    {
        "insert_at": 999,
        "name": "鸣潮",
        "role": "素材/形象来源",
        "url": "https://mc.kurogames.com/",
    },
]
_CONTRIBUTION_ROLE_OVERRIDES = {
    "https://github.com/chenwr727/yuanbao-free-api": "元宝OpenAI中转集成",
}
_GENERAL_CONFIG_CATEGORIES = GENERAL_CONFIG_CATEGORIES

_CATEGORY_KEY_ALLOWLIST = {
    "ui_anim": {
        "ANIMATION": {
            "frame_fps",
            "gif_fps",
            "start_exit_enabled",
            "start_animation_folder",
            "exit_animation_folder",
            "exit_shadow_strength",
            "exit_shadow_blur_radius",
            "exit_shadow_offset_direction",
        },
        "UI": {
            "pet_opacity",
            "ui_widget_opacity",
            "tooltip_opacity",
            "ui_fade_duration",
            "auto_hide_mouse_distance",
            "render_backend",
        },
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
        "AUDIO_VOLUMES": set(),
        "VOICE": {
            "microphone_push_to_talk_key",
            "microphone_silence_timeout_secs",
            "microphone_speech_rms_threshold",
            "microphone_denoise_enabled",
            "microphone_denoise_strength",
            "microphone_noise_gate_threshold",
        },
        "SPEAKER_AUDIO": set(),
        "CLOUD_MUSIC": {
            "provider",
            "particle_interval",
            "search_result_limit",
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
            "log_retention_count",
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
    ("ANIMATION", "exit_shadow_strength"): ("int", 0, 255),
    ("ANIMATION", "exit_shadow_blur_radius"): ("int", 0, 128),
    ("UI", "pet_opacity"): ("number", 0.0, 1.0),
    ("UI", "ui_widget_opacity"): ("number", 0.0, 1.0),
    ("UI", "tooltip_opacity"): ("number", 0.0, 1.0),
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
    ("VOICE", "lahai_skill_release_volume"): ("number", 0.0, 1.0),
    ("VOICE", "microphone_silence_timeout_secs"): ("number", 0.5, 10.0),
    ("VOICE", "microphone_speech_rms_threshold"): ("int", 50, 8000),
    ("VOICE", "microphone_noise_gate_threshold"): ("int", 0, 4000),
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
    ("STARTUP", "log_retention_count"): ("int", 1, 200),
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

_GENERAL_DECIMAL_SLIDER_SPECS: dict[tuple[str, str], tuple[float, float, float, int]] = {
    ("UI", "pet_opacity"): (0.0, 1.0, 0.05, 2),
    ("UI", "ui_widget_opacity"): (0.0, 1.0, 0.05, 2),
    ("UI", "tooltip_opacity"): (0.0, 1.0, 0.05, 2),
    ("OBJECTS", "object_opacity"): (0.0, 1.0, 0.05, 2),
    ("ANIMATION", "exit_shadow_strength"): (0.0, 255.0, 1.0, 0),
    ("ANIMATION", "exit_shadow_blur_radius"): (0.0, 128.0, 1.0, 0),
    ("VOICE", "microphone_denoise_strength"): (0.0, 1.0, 0.05, 2),
}

_GENERAL_CHOICE_FIELD_OPTIONS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("ANIMATION", "exit_shadow_offset_direction"): [
        ("向下", "down"),
        ("向上", "up"),
        ("向右", "right"),
        ("向左", "left"),
        ("右下", "down_right"),
        ("左下", "down_left"),
        ("右上", "up_right"),
        ("左上", "up_left"),
        ("不偏移", "center"),
    ],
    ("UI", "render_backend"): [
        (
            f"{descriptor.display_name}（{('实验性功能' if descriptor.experimental else '当前可用') if descriptor.available else '尚未接入'}）",
            descriptor.backend_id,
        )
        for descriptor in get_backend_descriptors()
    ],
}

_GENERAL_CONFIG_DEFAULTS: dict[str, dict[str, object]] = {
    "ANIMATION": {
        "frame_fps": 60,
        "gif_fps": 16,
        "start_exit_enabled": True,
        "start_animation_folder": DEFAULT_START_ANIMATION_FOLDER,
        "exit_animation_folder": DEFAULT_EXIT_ANIMATION_FOLDER,
        "exit_shadow_strength": 230,
        "exit_shadow_blur_radius": 10,
        "exit_shadow_offset_direction": "down_right",
    },
    "UI": {
        "pet_opacity": 1.0,
        "ui_widget_opacity": 1.0,
        "tooltip_opacity": 0.8,
        "ui_fade_duration": 200,
        "auto_hide_mouse_distance": 300,
        "workbench_light_theme": False,
        "render_backend": "qt",
    },
    "COMMAND_DIALOG": {
        "idle_timeout_ms": 10000,
    },
    "PARTICLES": {
        "enable_stroke": False,
        "fade_threshold": 0.75,
    },
    "BEHAVIOR": {
        "wander_near_speaker_radius": 150,
        "double_click_ticks": 4,
        "move_max_speed": 5.0,
        "move_acceleration": 0.25,
        "move_min_speed": 2.5,
    },
    "PHYSICS": {
        "max_bounces": 5,
        "ground_y_pct": 0.9,
        "air_resistance": 0.95,
    },
    "SNOW_LEOPARD": {
        "spawn_y_min": 0.95,
        "spawn_y_max": 0.99,
        "interact_radius": 50,
        "natural_spawn_limit": 12,
        "jump_power_min": 2,
        "jump_power_max": 2.5,
    },
    "SNOW_PILE": {
        "spawn_y_min": 0.82,
        "spawn_y_max": 0.93,
        "scale_min": 1.2,
        "scale_max": 1.5,
        "batch_interval": (10000, 20000),
        "batch_size": (1, 2),
        "batch_item_interval": (3000, 5000),
        "spawn_power_min": 3,
        "spawn_power_max": 5,
    },
    "SOFA": {
        "spawn_y_min": 0.8,
        "spawn_y_max": 0.9,
        "protect_radius": 10,
    },
    "MORTOR": {
        "spawn_y_min": 0.8,
        "spawn_y_max": 0.9,
        "move_speed_px_per_frame": 2.0,
        "bgm_enabled": True,
    },
    "CLOCK": {
        "spawn_y_min": 0.8,
        "spawn_y_max": 0.9,
        "countdown_ss": 30,
    },
    "SPEAKER": {
        "spawn_y_min": 0.8,
        "spawn_y_max": 0.9,
    },
    "OBJECTS": {
        "object_opacity": 1.0,
    },
    "SNOWBALL": {
        "max_count": 16,
        "spawn_y_min": 0.85,
        "spawn_y_max": 0.95,
        "size_min": 24,
        "size_max": 48,
        "lifetime_min": 10,
        "lifetime_max": 15,
    },
    "SOUND": {
        "master_volume": 0.68,
        "main_pet_volume": 0.4,
        "game_object_volume": 0.9,
    },
    "VOICE": {
        "voice_volume": 1.0,
        "lahai_skill_release_volume": 0.7,
        "microphone_push_to_talk_key": "V",
        "microphone_silence_timeout_secs": 3.0,
        "microphone_speech_rms_threshold": 550,
        "microphone_denoise_enabled": True,
        "microphone_denoise_strength": 0.65,
        "microphone_noise_gate_threshold": 180,
    },
    "CLOUD_MUSIC": {
        "provider": "netease",
        "default_volume": 0.3,
        "particle_interval": 60,
        "search_result_limit": 128,
        "local_music_dir": "",
        "launch_wuwa_path": "",
    },
    "TIMEOUTS": {
        "api_list": 2,
        "api_request": 10,
        "login_wait": 30,
        "login_call": 20,
        "cmd_exec": 30,
        "idle_close_ms": 10000,
    },
    "DRAW": {
        "scale": 1.0,
    },
    "STARTUP": {
        "ensure_desktop_shortcut": True,
        "log_retention_count": 20,
    },
}

_GENERAL_MIXED_SECTION_FIELDS: dict[str, list[tuple[str, str]]] = {
    "AUDIO_VOLUMES": [
        ("SOUND", "master_volume"),
        ("SOUND", "main_pet_volume"),
        ("SOUND", "game_object_volume"),
        ("VOICE", "voice_volume"),
        ("VOICE", "lahai_skill_release_volume"),
        ("CLOUD_MUSIC", "default_volume"),
    ],
}

_DICT_FRIENDLY_NAME = {
    "UI": "界面",
    "ANIMATION": "动画",
    "BUBBLE_CONFIG": "气泡",
    "COMMAND_DIALOG": "命令框",
    "BEHAVIOR": "行为",
    "PHYSICS": "物理",
    "PARTICLES": "粒子",
    "AUDIO_VOLUMES": "音量控制",
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
        "tooltip_opacity": "悬浮说明透明度",
        "ui_fade_duration": "淡入淡出时长(ms)",
        "auto_hide_mouse_distance": "自动关闭阈值",
        "render_backend": "渲染后端",
    },
    "ANIMATION": {
        "pet_size": "宠物尺寸",
        "gif_fps": "GIF帧率",
        "frame_fps": "帧率",
        "start_exit_enabled": "启动/退出动画",
        "start_animation_folder": "启动序列帧目录",
        "exit_animation_folder": "退出序列帧目录",
        "exit_shadow_strength": "退出阴影强度",
        "exit_shadow_blur_radius": "退出阴影模糊半径(px)",
        "exit_shadow_offset_direction": "退出阴影偏移方向",
    },
    "STARTUP": {
        "ensure_desktop_shortcut": "启动时创建快捷方式",
        "log_retention_count": "日志保留数量",
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
        "main_pet_volume": "主宠物语音音量",
        "game_object_volume": "特效音量",
    },
    "VOICE": {
        "voice_volume": "AI语音音量",
        "lahai_skill_release_volume": "拉海洛技能语音音量",
        "microphone_push_to_talk_key": "语聊快捷键(留空禁用)",
        "microphone_silence_timeout_secs": "静音停止时长(s)",
        "microphone_speech_rms_threshold": "说话判定阈值",
        "microphone_denoise_enabled": "启用语音降噪",
        "microphone_denoise_strength": "降噪强度",
        "microphone_noise_gate_threshold": "噪声门阈值",
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
        "default_volume": "音乐音量",
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


def _category_section_entries(category_id: str, section_name: str, cc_module, category_allow_map: dict) -> list[tuple[str, str, object]]:
    mixed_fields = _GENERAL_MIXED_SECTION_FIELDS.get(str(section_name))
    if mixed_fields is not None:
        entries: list[tuple[str, str, object]] = []
        for dict_name, key in mixed_fields:
            section_obj = getattr(cc_module, str(dict_name), None)
            if not isinstance(section_obj, dict):
                continue
            if key not in section_obj:
                continue
            value = section_obj.get(key)
            if not _is_supported_config_value(value):
                continue
            entries.append((str(dict_name), str(key), value))
        return entries

    section_obj = getattr(cc_module, str(section_name), None)
    if not isinstance(section_obj, dict):
        return []

    allowed_keys = category_allow_map.get(str(section_name))
    entries = []
    for key, value in section_obj.items():
        key_text = str(key)
        if allowed_keys is not None and key_text not in allowed_keys:
            continue
        if not _is_supported_config_value(value):
            continue
        entries.append((str(section_name), key_text, value))
    return entries


def _animation_folder_display_name(folder_name: str) -> str:
    text = str(folder_name or "").strip()
    if text.endswith("_anima"):
        text = text[:-6]
    return text or str(folder_name or "")


def _choice_label_for_value(dict_name: str, key: str, value) -> str | None:
    options = _GENERAL_CHOICE_FIELD_OPTIONS.get((str(dict_name), str(key)))
    if not options:
        return None
    for label, option_value in options:
        if option_value == value:
            return label
    return None


def _hardcoded_general_default(dict_name: str, key: str, fallback):
    section = _GENERAL_CONFIG_DEFAULTS.get(str(dict_name))
    if isinstance(section, dict) and key in section:
        return copy.deepcopy(section[key])
    return copy.deepcopy(fallback)


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


def _save_general_config(values_by_dict: dict[str, dict]) -> None:
    values_to_save = copy.deepcopy(values_by_dict)
    cloud_music = values_to_save.get("CLOUD_MUSIC")
    if isinstance(cloud_music, dict) and "default_volume" in cloud_music:
        from config.music.volume_config import get_volume_config

        get_volume_config().set_volume(float(cloud_music.pop("default_volume")))
    save_general_values(values_to_save)


def _apply_general_runtime(values_by_dict: dict[str, dict]) -> None:
    import config.config as cc

    for dict_name, items in values_by_dict.items():
        target = getattr(cc, dict_name, None)
        if isinstance(target, dict):
            target.update(items)
        if dict_name == "CLOUD_MUSIC" and "provider" in items:
            try:
                from lib.script.music import get_music_service

                get_music_service().set_provider(str(items.get("provider") or ""), persist=False)
            except Exception as exc:
                _logger.warning("热重载音乐平台失败: %s", exc)

    try:
        get_event_center().publish(Event(EventType.CONFIG_UPDATED, {
            "source": "general",
            "values": values_by_dict,
        }))
    except Exception as exc:
        _logger.debug("发布通用配置热重载事件失败: %s", exc)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sponsor_author_image_path() -> Path:
    return (
        _project_root()
        / "doc"
        / "贡献名单和主播的狗盆"
        / "如果想给作者买鸡腿饭的话"
        / "喵-感谢支持喵-欢迎工单喵.jpg"
    )


def _contribution_list_path() -> Path:
    return (
        _project_root()
        / "doc"
        / "贡献名单和主播的狗盆"
        / "开发贡献.txt"
    )


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            pass
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://\S+", str(text or ""))
    return match.group(0).strip() if match else ""


def _normalize_contribution_name(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    value = value.strip("-=:： \t")
    return value


def _guess_contribution_fallback_name(text: str) -> str:
    candidate = _normalize_contribution_name(text)
    if not candidate:
        return ""
    if len(candidate) > 20:
        return ""
    blocked_tokens = ("感谢", "谢谢", "喜欢", "更新", "测试版", "版权", "侵权", "删除")
    if any(token in candidate for token in blocked_tokens):
        return ""
    return candidate


def _split_contribution_header(header: str) -> tuple[str, str]:
    text = _normalize_contribution_name(header)
    parts = [_normalize_contribution_name(part) for part in text.split("-") if _normalize_contribution_name(part)]
    if len(parts) < 2:
        return text, ""

    picked_index = -1
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part in _CONTRIBUTION_IGNORED_TITLE_PARTS:
            continue
        if index == 0:
            continue
        picked_index = index
        break

    if picked_index < 0:
        return text, ""

    name = parts[picked_index]
    role_parts = [
        part
        for index, part in enumerate(parts)
        if index != picked_index and part not in _CONTRIBUTION_IGNORED_TITLE_PARTS
    ]
    role = "-".join(role_parts).strip("- ") or text
    return role, name


def _parse_contribution_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current_role = ""
    current_default_name = ""
    current_fallback_name = ""
    current_has_record = False

    def flush_pending() -> None:
        nonlocal current_role, current_default_name, current_fallback_name, current_has_record
        if current_role and not current_has_record:
            name = current_fallback_name or current_default_name
            if name:
                records.append({
                    "name": name,
                    "role": current_role,
                    "url": "",
                })
        current_role = ""
        current_default_name = ""
        current_fallback_name = ""
        current_has_record = False

    for raw_line in str(text or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("贡献:"):
            flush_pending()
            current_role, current_default_name = _split_contribution_header(line[3:].strip())
            current_fallback_name = current_default_name
            continue
        if not current_role or not line.startswith("==="):
            continue

        detail = _normalize_contribution_name(line[3:].strip())
        if not detail:
            continue
        url = _extract_first_url(detail)
        if url:
            prefix = _normalize_contribution_name(detail.split(url, 1)[0])
            name = prefix or current_default_name or current_fallback_name or "未命名贡献者"
            records.append({
                "name": name,
                "role": current_role,
                "url": url,
            })
            current_has_record = True
            continue

        fallback_name = _guess_contribution_fallback_name(detail)
        if fallback_name:
            current_fallback_name = fallback_name

    flush_pending()
    return records


def _load_contribution_records() -> list[dict[str, str]]:
    path = _contribution_list_path()
    if not path.exists():
        records = []
    else:
        try:
            records = _parse_contribution_records(_read_text_with_fallback(path))
        except Exception as exc:
            _logger.warning("读取贡献名单失败: %s", exc)
            records = []
    try:
        filtered_records: list[dict[str, str]] = []
        for record in records:
            role = str(record.get("role") or "").strip()
            if role in _CONTRIBUTION_HIDDEN_ROLES:
                continue
            url = str(record.get("url") or "").strip()
            override_role = _CONTRIBUTION_ROLE_OVERRIDES.get(url)
            if override_role:
                record["role"] = override_role
            filtered_records.append(record)

        for manual in _MANUAL_CONTRIBUTION_RECORDS:
            manual_url = str(manual.get("url") or "").strip()
            if not manual_url:
                continue
            filtered_records = [
                record for record in filtered_records
                if str(record.get("url") or "").strip() != manual_url
            ]
            insert_at = int(manual.get("insert_at", len(filtered_records)))
            insert_at = max(0, min(insert_at, len(filtered_records)))
            filtered_records.insert(insert_at, {
                "name": str(manual.get("name") or "未命名贡献者").strip(),
                "role": str(manual.get("role") or "贡献者").strip(),
                "url": manual_url,
            })

        return filtered_records
    except Exception as exc:
        _logger.warning("整理贡献名单失败: %s", exc)
        return []


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
    """Combo box with guarded refresh and workbench popup layering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._before_popup_callback: Callable[[], None] | None = None
        self._popup_refreshing = False
        self._popup_window_instance = None

    def set_before_popup_callback(self, callback: Callable[[], None] | None) -> None:
        self._before_popup_callback = callback

    def _popup_window(self):
        if self._popup_window_instance is not None:
            return self._popup_window_instance
        view = self.view()
        return view.window() if view is not None else None

    def _unregister_popup_layer(self) -> None:
        popup = self._popup_window()
        if popup is not None:
            get_layer_manager().unregister(popup)

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
        popup = self._popup_window()
        if popup is not None:
            self._popup_window_instance = popup
            popup.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            popup.show()
            layer_manager = get_layer_manager()
            layer_manager.register(
                popup,
                _DROPDOWN_POPUP_LAYER,
                name="AISettingsDropdownPopup",
            )
            layer_manager.enforce_burst()
            popup.raise_()
            popup.activateWindow()

    def hidePopup(self) -> None:
        self._unregister_popup_layer()
        try:
            super().hidePopup()
        finally:
            self._popup_window_instance = None

    def wheelEvent(self, event) -> None:
        # 下拉框不消费滚轮，让外层设置页面独占滚动手势。
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
        suffix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._step = max(float(step), 0.0001)
        self._decimals = max(0, int(decimals))
        self._suffix = str(suffix or "")

        total_steps = max(1, int(round((self._maximum - self._minimum) / self._step)))

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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
        formatted = text.rstrip("0").rstrip(".") if "." in text else text
        return f"{formatted}{self._suffix}"

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


class _ContributionCardButton(QPushButton):
    """Contribution entry with a compact action hint."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watermark_label: QLabel | None = None
        self._watermark_font = get_ui_font(size=max(scale_px(11, min_abs=9), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
        self._watermark_font.setBold(True)

    def bind_watermark_label(self, label: QLabel) -> None:
        self._watermark_label = label
        label.setProperty("preserveCustomFont", True)
        self._apply_watermark(False)

    def _layout_aware_size_hint(self, hint: QSize) -> QSize:
        card_layout = self.layout()
        if card_layout is not None:
            hint.setHeight(max(hint.height(), card_layout.minimumSize().height()))
        return hint

    def sizeHint(self) -> QSize:
        return self._layout_aware_size_hint(super().sizeHint())

    def minimumSizeHint(self) -> QSize:
        return self._layout_aware_size_hint(super().minimumSizeHint())

    def _apply_watermark(self, hovered: bool) -> None:
        if self._watermark_label is None:
            return
        colors = get_workbench_colors()
        self._watermark_label.setFont(self._watermark_font)
        self._watermark_label.setText("打开" if hovered else "主页")
        self._watermark_label.setStyleSheet(
            f"color: {colors.cyan if hovered else colors.text_dim};"
        )

    def enterEvent(self, event) -> None:
        self._apply_watermark(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._apply_watermark(False)
        super().leaveEvent(event)


class AISettingsPanel(QWidget):
    """托盘入口 AI 设置面板。"""

    _ui_thread_call = pyqtSignal(object)

    def __init__(self, parent=None, *, lazy_workbench_pages: bool = False):
        super().__init__(parent)
        self._lazy_workbench_pages = bool(lazy_workbench_pages)
        self._workbench_pages: dict[str, QWidget] = {}
        self._ui_thread_call.connect(self._invoke_ui_callable)
        self._ec = get_event_center()
        self._yuanbao_login_status_generation = 0
        self._yuanbao_login_status_subscribed = False
        self._subscribe_yuanbao_login_events()
        self._autostart_checkbox = None
        self._announcement_suppression_checkbox = None
        self._autostart_status_subscribed = False
        self._update_dialog: DesktopPetUpdateDialog | None = None
        self._voice_installer_dialog: VoicePackageInstallerDialog | None = None
        self._qq_group_dialog: QQGroupDialog | None = None
        self._subscribe_autostart_events()
        self.setWindowTitle("控制面板")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        get_layer_manager().register(self, Layer.PANEL, name='AISettingsPanel')
        self.setMinimumWidth(int(round(scale_px(520) * _PANEL_SCALE)))
        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2
        self._visible = False
        self._external_close_callback = None
        self._workbench_attached = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._gpu_watermark_text = "UnKnow GPU 0.00 GB\nRAM 0.00 GB"
        self._panel_watermark_text = _WATERMARK_TEXT
        self._tick_counter = 0
        self._tick_subscribed = False

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
        self._stable_window_size: tuple[int, int] | None = None
        self._save_task_pending = False
        self._save_completion_action: Callable[[], None] | None = None

        self._build_ui()
        self._apply_project_fonts()
        self._apply_style()
        self._cache_stable_window_size()
        self.load_values()
        self._refresh_hardware_watermark_async()

    def _refresh_hardware_watermark_async(self) -> None:
        def worker() -> None:
            payload = _load_saved_watermark_payload()
            hardware_lines = payload.get("hardware", ("UnKnow GPU 0.00 GB", "RAM 0.00 GB"))
            panel_lines = payload.get("control_panel", ("Aemeath", "AIsetting"))

            def apply_result() -> None:
                self._gpu_watermark_text = "\n".join(hardware_lines)
                self._panel_watermark_text = "\n".join(panel_lines)
                try:
                    self.update()
                except RuntimeError:
                    pass

            self._ui_thread_call.emit(apply_result)

        future = get_compute_hub().submit_latest(
            "ai_settings_hardware_watermark",
            worker,
            executor="io",
        )
        if future is None:
            _logger.debug("硬件水印查询任务仍在运行，跳过重复提交")

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

    @staticmethod
    def _create_field_row_group(spacing: int = 0):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(spacing))
        return group, row

    def _create_config_line_edit(
        self,
        value=...,
        *,
        placeholder_text: str = "",
        expanding: bool = False,
    ) -> QLineEdit:
        editor = QLineEdit()
        if placeholder_text:
            editor.setPlaceholderText(placeholder_text)
        if value is not ...:
            self._set_config_editor_value(editor, value)
        if expanding:
            editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return editor

    @staticmethod
    def _create_config_choice_editor(options: list[tuple[str, str]]) -> QComboBox:
        editor = _WatermarkComboBox()
        editor.setView(QListView(editor))
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for label, value in options:
            editor.addItem(str(label), value)
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
        choice_label = _choice_label_for_value(dict_name, key, value)
        if choice_label and choice_label != preview:
            preview = f"{choice_label} ({preview})"
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
            self._border + scale_px(10, min_abs=8),
            self._border + scale_px(8, min_abs=6),
            self._border + scale_px(10, min_abs=8),
            self._border + scale_px(10, min_abs=8),
        )
        root_layout.setSpacing(0)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)
        self._center_row = center_row
        content_panel = QWidget(self)
        content_panel.setMinimumSize(scale_px(600, min_abs=560), scale_px(420, min_abs=380))
        content_panel.setMaximumSize(16777215, 16777215)
        content_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center_row.addWidget(content_panel, 1)
        root_layout.addLayout(center_row, 1)
        self._ai_panel = content_panel
        self._tab_pages = [self._ai_panel]

        scaffold = SettingsPageScaffold(
            content_panel,
            "AI设置",
            _AI_HINT_TEXT,
            scroll_factory=_SmoothScrollArea,
        )
        self._ai_scaffold = scaffold
        self._title_label = scaffold.title_label
        self._hint_label = scaffold.description_label

        self._voice_package_status = get_voice_package_status()
        self._voice_package_banner = VoicePackageInstallBanner(scaffold.content)
        self._voice_package_banner.install_requested.connect(self._on_install_voice_package)
        self._voice_package_banner.set_package_status(self._voice_package_status)
        scaffold.content_layout.addWidget(self._voice_package_banner)
        self._voice_package_management = VoicePackageManagementBar(scaffold.content)
        self._voice_package_management.package_removed.connect(self._on_voice_package_removed)
        self._voice_package_management.removal_failed.connect(self._on_voice_package_removal_failed)
        self._voice_package_management.set_package_status(self._voice_package_status)
        scaffold.content_layout.addWidget(self._voice_package_management)

        interface_section = scaffold.add_section(
            "回复模式",
            "选择本次保存后固定使用的回复来源。",
        )
        form = create_settings_form()
        interface_section.body_layout.addLayout(form)

        self._force_mode = _WatermarkComboBox()
        self._force_mode.setView(QListView(self._force_mode))
        self._force_mode.addItem('福利 API', '1')
        self._force_mode.addItem('手动 API', '0')
        self._force_mode.addItem('本地 Ollama', '2')
        self._force_mode.addItem('规则回复', '3')
        self._force_mode.addItem('元宝', '4')
        form.addRow("回复模式", self._force_mode)
        self._set_form_row_description(
            form,
            self._force_mode,
            "回复只走选中的来源，失败时不会切换到其他来源。",
        )

        self._auto_companion_enabled = QCheckBox("启用自动陪伴")
        self._auto_companion_enabled.setChecked(True)
        form.addRow("", self._auto_companion_enabled)
        self._set_form_row_description(
            form,
            self._auto_companion_enabled,
            "开启后会按设定间隔自动触发陪伴对话。",
        )

        self._auto_companion_interval_minutes = _DecimalSliderField(
            1,
            20,
            1,
            value=_DEFAULT_VALUES["auto_companion_interval_minutes"],
            decimals=0,
            suffix=" 分钟",
        )
        form.addRow("陪伴间隔", self._auto_companion_interval_minutes)
        self._set_form_row_description(
            form,
            self._auto_companion_interval_minutes,
            "自动陪伴两次观察之间的时间，范围 1~20 分钟。",
        )
        self._auto_companion_enabled.toggled.connect(self._auto_companion_interval_minutes.setEnabled)

        persona_row, persona_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._open_persona_file_btn = QPushButton("设置人格词")
        self._open_persona_file_btn.setFixedWidth(scale_px(110, min_abs=92))
        self._open_persona_file_btn.clicked.connect(self._on_open_persona_file)
        persona_layout.addWidget(self._open_persona_file_btn, 0)
        persona_layout.addStretch(1)
        form.addRow("人格配置", persona_row)
        self._set_form_row_description(
            form,
            persona_row,
            "使用系统默认程序打开当前生效的人格 txt，直接编辑系统 prompt。",
        )
        self._set_widget_description(self._open_persona_file_btn, "使用系统默认程序打开当前生效的人格 txt。")

        self._welfare_section = scaffold.add_section(
            "福利 API 配置",
            "仅在回复模式选择福利 API 时显示。",
        )
        form = create_settings_form()
        self._welfare_section.body_layout.addLayout(form)
        self._welfare_intelligence_boost = QCheckBox("智力提升")
        form.addRow("", self._welfare_intelligence_boost)
        self._set_form_row_description(
            form,
            self._welfare_intelligence_boost,
            "关闭时使用 Agnes 2.0 Flash；开启后使用 Agnes 2.5 Flash。",
        )

        self._manual_api_section = scaffold.add_section(
            "手动 API 配置",
            "仅在回复模式选择手动 API 时显示。",
        )
        form = create_settings_form()
        self._manual_api_section.body_layout.addLayout(form)

        self._api_key = _ApiKeyLineEdit()
        form.addRow("接口密钥", self._api_key)
        self._set_form_row_description(
            form,
            self._api_key,
            "OpenAI 兼容接口密钥，单独保存在用户密钥文件中。",
        )

        self._manual_api_provider = _WatermarkComboBox()
        self._manual_api_provider.setView(QListView(self._manual_api_provider))
        for label, base_url in _MANUAL_API_PROVIDER_PRESETS:
            self._manual_api_provider.addItem(label, base_url)
        self._manual_api_provider.currentIndexChanged.connect(self._on_manual_api_provider_changed)
        form.addRow("常用提供商", self._manual_api_provider)
        self._set_form_row_description(
            form,
            self._manual_api_provider,
            "选择后自动填入该提供商的 OpenAI 兼容接口地址；自定义地址仍可直接填写。",
        )

        self._api_base_url = QLineEdit()
        self._api_base_url.textChanged.connect(self._sync_manual_api_provider_selection)
        self._api_base_url.editingFinished.connect(self._normalize_manual_api_base_url_input)
        form.addRow("接口地址", self._api_base_url)
        self._set_form_row_description(
            form,
            self._api_base_url,
            "外部接口地址，通常填写兼容 OpenAI 的基地址；若直接填写完整的 `/chat/completions` 或 `/v1/chat/completions` 端点也可兼容。",
        )

        api_model_row, api_model_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._api_model = _WatermarkComboBox()
        self._api_model.setView(QListView(self._api_model))
        self._api_model.setEditable(True)
        self._api_model.setInsertPolicy(QComboBox.NoInsert)
        self._api_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if self._api_model.lineEdit():
            self._api_model.lineEdit().setPlaceholderText("输入或探测接口模型")
        api_model_layout.addWidget(self._api_model, 1)
        self._probe_manual_api_models_btn = QPushButton("探测模型")
        self._probe_manual_api_models_btn.setFixedWidth(scale_px(100, min_abs=84))
        self._probe_manual_api_models_btn.clicked.connect(self._on_probe_manual_api_models)
        api_model_layout.addWidget(self._probe_manual_api_models_btn, 0)
        form.addRow("接口模型", api_model_row)
        self._set_form_row_description(
            form,
            api_model_row,
            "外部接口模型名，例如 qwen3.5-plus。可探测 OpenAI 兼容接口的 /models 列表，也可直接手动输入。",
        )
        self._set_widget_description(self._probe_manual_api_models_btn, "使用当前填写的接口地址和密钥探测可用模型列表。")

        self._set_hidden_yuanbao_values(_DEFAULT_VALUES)

        self._yuanbao_section = scaffold.add_section(
            "元宝登录",
            "仅在回复模式选择元宝时显示。",
        )
        form = create_settings_form()
        self._yuanbao_section.body_layout.addLayout(form)
        yuanbao_login_row, yuanbao_login_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._start_yuanbao_wechat_login_btn = QPushButton("微信登录元宝")
        self._start_yuanbao_wechat_login_btn.setFixedWidth(scale_px(126, min_abs=108))
        self._start_yuanbao_wechat_login_btn.clicked.connect(self._on_start_yuanbao_wechat_login)
        yuanbao_login_layout.addWidget(self._start_yuanbao_wechat_login_btn, 0)
        self._stop_yuanbao_login_btn = QPushButton("退出元宝登录")
        self._stop_yuanbao_login_btn.setFixedWidth(scale_px(126, min_abs=108))
        self._stop_yuanbao_login_btn.clicked.connect(self._on_stop_yuanbao_login)
        yuanbao_login_layout.addWidget(self._stop_yuanbao_login_btn, 0)
        yuanbao_login_layout.addStretch(1)
        form.addRow("元宝登录", yuanbao_login_row)
        self._set_widget_description(self._start_yuanbao_wechat_login_btn, "启动本地 YuanBao-Free-API 服务，并使用微信扫码方式登录元宝；程序会固定使用内置 loopback 地址、占位密钥和默认模型。")
        self._set_widget_description(self._stop_yuanbao_login_btn, "停止元宝登录流程并关闭本地元宝服务。")
        self._set_yuanbao_login_actions(logged_in=False)

        self._ollama_section = scaffold.add_section(
            "Ollama 配置",
            "仅在回复模式选择本地 Ollama 时显示。",
        )
        form = create_settings_form()
        self._ollama_section.body_layout.addLayout(form)

        base_row, base_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
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

        generation_section = scaffold.add_section(
            "生成参数",
            "调整大模型输出与图片输入。",
        )
        form = create_settings_form()
        generation_section.body_layout.addLayout(form)

        self._api_temperature = _DecimalSliderField(0.0, 2.0, 0.05, value=_DEFAULT_VALUES["api_temperature"])
        form.addRow("大模型温度", self._api_temperature)
        self._set_form_row_description(
            form,
            self._api_temperature,
            "大模型采样温度范围 0~2，越高回复越发散。",
        )

        self._model_vision = _DecimalSliderField(0, 100, 1, value=_DEFAULT_VALUES["model_vision"], decimals=0)
        form.addRow("模型视力", self._model_vision)
        self._set_form_row_description(
            form,
            self._model_vision,
            "视力越高，token消耗越高，看图越清晰。100 为不压缩，0 为压缩到 720p。",
        )

        self._force_mode.currentIndexChanged.connect(self._update_reply_mode_sections)
        self._update_reply_mode_sections()

        self._gsv_launcher_available = not self._voice_package_status.install_required
        self._voice_section = scaffold.add_section(
            "语音合成",
            "控制 ONNX 语音模型、采样、节奏和本地缓存。",
        )
        form = create_settings_form()
        self._voice_section.body_layout.addLayout(form)

        self._gsv_auto_start = QCheckBox("自动启用ONNX语音模块")
        self._gsv_auto_start.setChecked(_DEFAULT_VALUES["gsv_auto_start"])
        form.addRow("", self._gsv_auto_start)
        self._set_form_row_description(
            form,
            self._gsv_auto_start,
            "开启后，桌宠启动时会在后台加载并预热 ONNX 语音模型。",
        )

        self._gsv_gpu_hybrid = QCheckBox("使用gpu混合推理（可能会提高显存占用）")
        self._gsv_gpu_hybrid.setChecked(_DEFAULT_VALUES["gsv_gpu_hybrid"])
        form.addRow("", self._gsv_gpu_hybrid)

        self._gsv_temperature = _DecimalSliderField(0.01, 2.0, 0.01, value=_DEFAULT_VALUES["gsv_temperature"])
        form.addRow("采样温度", self._gsv_temperature)
        self._set_form_row_description(
            form,
            self._gsv_temperature,
            "T2S 采样温度；越高变化越多，过高可能使语调不稳定。",
        )

        self._gsv_top_k = _DecimalSliderField(1, 1025, 1, value=_DEFAULT_VALUES["gsv_top_k"], decimals=0)
        form.addRow("Top-K", self._gsv_top_k)
        self._set_form_row_description(form, self._gsv_top_k, "每一步保留概率最高的候选数量，默认 15。")

        self._gsv_top_p = _DecimalSliderField(0.01, 1.0, 0.01, value=_DEFAULT_VALUES["gsv_top_p"])
        form.addRow("Top-P", self._gsv_top_p)
        self._set_form_row_description(form, self._gsv_top_p, "限制累计概率候选范围，1.0 表示不额外截断。")

        self._gsv_repetition_penalty = _DecimalSliderField(
            0.1,
            2.0,
            0.01,
            value=_DEFAULT_VALUES["gsv_repetition_penalty"],
        )
        form.addRow("重复惩罚", self._gsv_repetition_penalty)
        self._set_form_row_description(form, self._gsv_repetition_penalty, "抑制语义 token 重复，默认 1.35。")

        self._gsv_speed_factor = _DecimalSliderField(0.5, 2.0, 0.05, value=_DEFAULT_VALUES["gsv_speed_factor"])
        form.addRow("ONNX语速", self._gsv_speed_factor)
        self._set_form_row_description(
            form,
            self._gsv_speed_factor,
            "模型内部语速，1.0 为原速；不会通过重采样改变音高。",
        )

        self._gsv_text_split_method = _WatermarkComboBox()
        self._gsv_text_split_method.setView(QListView(self._gsv_text_split_method))
        for label, value in (
            ("按全部标点分句", "cut5"),
            ("不自动分句", "cut0"),
            ("每四句一段", "cut1"),
            ("每约 50 字一段", "cut2"),
            ("按中文句号分句", "cut3"),
            ("按英文句号分句", "cut4"),
        ):
            self._gsv_text_split_method.addItem(label, value)
        form.addRow("长文本分句", self._gsv_text_split_method)
        self._set_form_row_description(form, self._gsv_text_split_method, "控制长回复如何拆成多个独立语音片段。")

        self._gsv_fragment_interval = _DecimalSliderField(
            0.0,
            5.0,
            0.05,
            value=_DEFAULT_VALUES["gsv_fragment_interval"],
        )
        form.addRow("片段停顿(秒)", self._gsv_fragment_interval)
        self._set_form_row_description(form, self._gsv_fragment_interval, "分句片段之间插入的静音时长，默认 0.3 秒。")

        self._gsv_seed = QLineEdit(str(_DEFAULT_VALUES["gsv_seed"]))
        self._gsv_seed.setPlaceholderText("-1 表示每次随机")
        form.addRow("随机种子", self._gsv_seed)
        self._set_form_row_description(form, self._gsv_seed, "-1 为随机；固定非负整数可复现 T2S 采样结果。")

        self._gsv_max_steps = _DecimalSliderField(64, 1200, 1, value=_DEFAULT_VALUES["gsv_max_steps"], decimals=0)
        form.addRow("最大解码步数", self._gsv_max_steps)
        self._set_form_row_description(form, self._gsv_max_steps, "语义解码保护上限；过低可能截断，默认 500。")

        self._ai_voice_max_chars = _DecimalSliderField(
            AI_VOICE_MAX_CHARS_MIN,
            AI_VOICE_MAX_CHARS_MAX,
            1,
            value=_DEFAULT_VALUES["ai_voice_max_chars"],
        )
        form.addRow("语音字数限制", self._ai_voice_max_chars)
        self._set_form_row_description(
            form,
            self._ai_voice_max_chars,
            "ONNX 语音合成最大文本长度，超过此长度的回复不会转为语音。",
        )

        self._gsv_cache_max_files = _DecimalSliderField(1, 128, 1, value=_DEFAULT_VALUES["gsv_cache_max_files"], decimals=0)
        form.addRow("语音缓存上限", self._gsv_cache_max_files)
        self._set_form_row_description(
            form,
            self._gsv_cache_max_files,
            "保留最近生成的 ONNX 语音条数，超出后按时间自动删除旧缓存。",
        )

        gsv_cache_row, gsv_cache_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._open_gsv_cache_dir_btn = QPushButton("打开缓存文件夹")
        self._open_gsv_cache_dir_btn.setFixedWidth(scale_px(132, min_abs=112))
        self._open_gsv_cache_dir_btn.clicked.connect(self._on_open_gsv_cache_dir)
        gsv_cache_layout.addWidget(self._open_gsv_cache_dir_btn, 0)
        gsv_cache_layout.addStretch(1)
        form.addRow("语音缓存", gsv_cache_row)
        self._set_form_row_description(
            form,
            gsv_cache_row,
            "打开 ONNX 语音缓存目录。",
        )
        self._set_widget_description(self._open_gsv_cache_dir_btn, "打开 ONNX 语音缓存目录。")
        self._update_gsv_settings_visibility()

        memory_section = scaffold.add_section(
            "记忆与陪伴",
            "调整会话记忆规模和自动陪伴行为。",
        )
        form = create_settings_form()
        memory_section.body_layout.addLayout(form)

        self._memory_context_limit = _DecimalSliderField(0, 48, 1, value=_DEFAULT_VALUES["memory_context_limit"])
        form.addRow("记忆上下文条数", self._memory_context_limit)
        self._set_form_row_description(
            form,
            self._memory_context_limit,
            "附带给 AI 的 recent memory 条数，0 表示不附带，范围 0~48。",
        )

        self._memory_recall_count = _DecimalSliderField(5, 50, 1, value=_DEFAULT_VALUES["memory_recall_count"])
        form.addRow("回忆提取条数", self._memory_recall_count)
        self._set_form_row_description(
            form,
            self._memory_recall_count,
            "回忆工具单次提取的记忆条数，范围 5~50。",
        )

        self._api_enable_thinking = QCheckBox("启用思考模式(外部接口可用)")
        form.addRow("", self._api_enable_thinking)
        self._set_form_row_description(
            form,
            self._api_enable_thinking,
            "开启后，支持思考模式的外部接口将返回推理链路。",
        )

        scaffold.finish()
        self._reload_btn = scaffold.add_action("恢复本页默认", self._on_restore_ai_defaults)
        self._save_exit_btn = scaffold.add_action("保存更改", self._on_save_ai_action, primary=True)
        self._save_restart_btn = scaffold.add_action("保存并重启", self._on_save_and_restart)
        self._save_restart_btn.setObjectName("SettingsRestartAction")
        self._save_restart_btn.setProperty("restartAction", True)

        if self._lazy_workbench_pages:
            self._tab_pages = [self._ai_panel]
        else:
            attach_ai_settings_tabs(self, _GENERAL_CONFIG_CATEGORIES)
        self._ensure_config_defaults_integrity()

    def set_external_close_callback(self, callback) -> None:
        self._external_close_callback = callback

    def get_workbench_page_specs(self) -> list[tuple[str, str]]:
        return [('ai', 'AI 设置')] + [
            (category.page_id, category.tab_title)
            for category in _GENERAL_CONFIG_CATEGORIES
        ]

    def create_workbench_page(self, page_id: str) -> QWidget:
        cached = self._workbench_pages.get(page_id)
        if cached is not None:
            return cached
        if not self._workbench_attached:
            self._workbench_attached = True
            self._visible = False
            self._anim.stop()
            self._opacity.setOpacity(1.0)
            self._hide_floating_tab()
            get_layer_manager().unregister(self)
            if self._tab_floating is not None:
                get_layer_manager().unregister(self._tab_floating)
                self._tab_floating.deleteLater()
                self._tab_floating = None

        if page_id == 'ai':
            self._center_row.removeWidget(self._ai_panel)
            page = self._ai_panel
        else:
            spec = next(
                (item for item in _GENERAL_CONFIG_CATEGORIES if item.page_id == page_id),
                None,
            )
            if spec is None:
                raise KeyError(f'unknown workbench settings page: {page_id}')
            page = self._build_config_category_panel(spec)
            self._load_config_tab_values()
            self._ensure_config_defaults_integrity()

        page.hide()
        page.setProperty('workbenchEmbedded', True)
        page.setMinimumSize(0, 0)
        page.setMaximumSize(16777215, 16777215)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        page_title = page.findChild(QLabel, 'SettingsPageTitle')
        if page_title is not None:
            page_title.hide()
        self._workbench_pages[page_id] = page
        return page

    def _build_config_category_panel(self, category) -> QWidget:
        import config.config as cc

        category_id = category.page_id
        category_title = category.title
        panel = QWidget(self)
        scaffold = SettingsPageScaffold(
            panel,
            category_title,
            category.description or _GENERAL_HINT_TEXT,
            scroll_factory=_SmoothScrollArea,
        )
        title_label = scaffold.title_label
        hint_label = scaffold.description_label

        # 特殊处理：桌宠更新标签页 - 只有按钮，没有配置字段
        if category_id == "desktop_pet_update":
            return self._build_desktop_pet_update_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        if category_id == "sponsor_author":
            return self._build_sponsor_author_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        if category_id == "contribution_list":
            return self._build_contribution_list_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        fields: list[dict] = []
        defaults: dict[str, dict] = {}
        category_allow_map = _CATEGORY_KEY_ALLOWLIST.get(category_id, {})

        for section_spec in category.sections:
            dict_name, section_title = section_spec.config_key, section_spec.title
            section_entries = _category_section_entries(category_id, str(dict_name), cc, category_allow_map)
            if not section_entries:
                continue

            section_fields_added = False
            section = scaffold.add_section(_friendly_section_name(str(dict_name), str(section_title)))
            section_label = section.title_label
            self._set_widget_description(
                section_label,
                f"{_friendly_section_name(str(dict_name), str(section_title))} 配置分组",
            )
            form = create_settings_form()

            consumed_keys: set[str] = set()
            for entry_dict_name, key, value in section_entries:
                entry_id = f"{entry_dict_name}.{key}"
                if entry_id in consumed_keys:
                    continue

                signature = _range_pair_signature(key)
                if signature is not None:
                    pair_key = None
                    pair_value = None
                    pair_dict_name = None
                    sign_base, sign_type = signature
                    for other_dict_name, other_key, other_value in section_entries:
                        other_entry_id = f"{other_dict_name}.{other_key}"
                        if other_key == key or other_entry_id in consumed_keys:
                            continue
                        if other_dict_name != entry_dict_name:
                            continue
                        other_sig = _range_pair_signature(other_key)
                        if other_sig is None:
                            continue
                        if other_sig[0] != sign_base:
                            continue
                        pair_dict_name = other_dict_name
                        pair_key = other_key
                        pair_value = other_value
                        break
                    if pair_key is not None and pair_value is not None and pair_dict_name is not None:
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
                        )
                        friendly_name = _friendly_range_name(str(dict_name), left_key, right_key)
                        description = self._build_config_range_description(
                            str(entry_dict_name),
                            left_key,
                            right_key,
                            left_value,
                            right_value,
                            friendly_name,
                        )
                        label = self._create_form_label(friendly_name)
                        self._set_widget_description(label, description)
                        self._set_widget_description(pair_widget, description)
                        self._set_widget_description(left_editor, description)
                        self._set_widget_description(right_editor, description)
                        form.addRow(label, pair_widget)
                        fields.append({
                            "kind": "range_pair",
                            "dict_name": str(entry_dict_name),
                            "keys": [left_key, right_key],
                            "editors": [left_editor, right_editor],
                            "templates": [copy.deepcopy(left_value), copy.deepcopy(right_value)],
                        })
                        defaults.setdefault(str(entry_dict_name), {})[left_key] = _hardcoded_general_default(
                            str(entry_dict_name), left_key, left_value
                        )
                        defaults.setdefault(str(entry_dict_name), {})[right_key] = _hardcoded_general_default(
                            str(entry_dict_name), right_key, right_value
                        )
                        consumed_keys.add(f"{entry_dict_name}.{left_key}")
                        consumed_keys.add(f"{entry_dict_name}.{right_key}")
                        section_fields_added = True
                        continue

                if isinstance(value, (tuple, list)):
                    editors, group_widget = self._create_sequence_editor(value)
                    friendly_name = _friendly_key_name(str(entry_dict_name), key)
                    description = self._build_config_single_description(str(entry_dict_name), key, value, friendly_name)
                    label = self._create_form_label(friendly_name)
                    self._set_widget_description(label, description)
                    self._set_widget_description(group_widget, description)
                    for editor in editors:
                        self._set_widget_description(editor, description)
                    form.addRow(label, group_widget)
                    fields.append({
                        "kind": "sequence",
                        "dict_name": str(entry_dict_name),
                        "key": key,
                        "editors": editors,
                        "template": copy.deepcopy(value),
                    })
                    defaults.setdefault(str(entry_dict_name), {})[key] = _hardcoded_general_default(
                        str(entry_dict_name), key, value
                    )
                    consumed_keys.add(entry_id)
                    section_fields_added = True
                    continue

                open_dir_btn = None
                extra_widgets: list[QWidget] = []
                slider_spec = self._get_decimal_slider_spec(str(entry_dict_name), key, value)
                choice_options = self._get_choice_field_options(str(entry_dict_name), key)
                if self._is_volume_slider_field(str(entry_dict_name), key, value):
                    editor, percent_label, row_widget = self._create_volume_slider_editor(value)
                    extra_widgets.append(percent_label)
                elif slider_spec is not None:
                    minimum, maximum, step, decimals = slider_spec
                    editor = _DecimalSliderField(
                        minimum,
                        maximum,
                        step,
                        value=float(value),
                        decimals=decimals,
                    )
                    row_widget = editor
                elif choice_options is not None:
                    editor = self._create_config_choice_editor(choice_options)
                    row_widget = self._wrap_field_widget(editor)
                elif self._is_local_music_path_field(str(entry_dict_name), key) and isinstance(value, str):
                    editor, open_dir_btn, row_widget = self._create_path_editor_with_open_button(
                        str(entry_dict_name),
                        str(key),
                        value,
                    )
                elif isinstance(value, bool):
                    editor = QCheckBox()
                    row_widget = self._wrap_field_widget(editor)
                else:
                    editor = self._create_config_line_edit(expanding=True)
                    row_widget = editor
                self._set_config_editor_value(editor, value)
                friendly_name = _friendly_key_name(str(entry_dict_name), key)
                description = self._build_config_single_description(str(entry_dict_name), key, value, friendly_name)
                label = self._create_form_label(friendly_name)
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
                        if self._is_volume_slider_field(str(entry_dict_name), key, value)
                        else "decimal_slider"
                        if slider_spec is not None
                        else "single"
                    ),
                    "dict_name": str(entry_dict_name),
                    "key": key,
                    "editor": editor,
                    "template": copy.deepcopy(value),
                })
                defaults.setdefault(str(entry_dict_name), {})[key] = _hardcoded_general_default(
                    str(entry_dict_name), key, value
                )
                consumed_keys.add(entry_id)
                section_fields_added = True

                if category_id == "system_dispatch" and str(entry_dict_name) == "STARTUP" and key == "ensure_desktop_shortcut":
                    self._append_autostart_field(form, fields)
                    section_fields_added = True

            if category_id == "ui_anim" and str(dict_name) == "UI":
                self._append_announcement_suppression_field(form, fields)
                section_fields_added = True

            if section_fields_added:
                section.body_layout.addLayout(form)
            else:
                section.hide()

        scaffold.finish()
        scaffold.add_action(
            "恢复本页默认",
            lambda _checked=False, target=category_id: self._on_restore_config_category(target),
        )
        scaffold.add_action(
            "保存更改",
            lambda _checked=False, target=category_id: self._on_save_config_category(target),
            primary=True,
        )

        self._config_tab_meta[category_id] = {
            "panel": panel,
            "fields": fields,
            "defaults": defaults,
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
        }
        return panel

    def _build_desktop_pet_update_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        stable_section = scaffold.add_section(
            "稳定版本",
            "检查最新分发包。下载完成后桌宠会退出，由独立更新进程覆盖安装并重新启动。",
        )
        stable_row = QHBoxLayout()
        stable_row.setContentsMargins(0, 0, 0, 0)
        stable_row.setSpacing(scale_px(8, min_abs=6))
        check_update_btn = QPushButton("检查新版本", stable_section)
        check_update_btn.setObjectName("checkUpdateButton")
        check_update_btn.setProperty("primary", True)
        check_update_btn.clicked.connect(self._on_check_updates)
        stable_row.addWidget(check_update_btn, 1)
        stable_section.body_layout.addLayout(stable_row)

        dev_section = scaffold.add_section(
            "开发版本",
            "面向需要跟随远端代码的使用场景。同步前请先确认本地改动已妥善保存。",
        )
        sync_dev_btn = QPushButton("同步开发版", dev_section)
        sync_dev_btn.setObjectName("syncDevButton")
        sync_dev_btn.clicked.connect(self._on_sync_dev_build)
        dev_section.body_layout.addWidget(sync_dev_btn)

        manual_section = scaffold.add_section(
            "手动获取",
            "自动更新不可用时，可通过网盘或 QQ 群获取完整安装包。",
        )
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.setSpacing(_UPDATE_BUTTON_ROW_GAP)
        quark_update_btn = QPushButton("打开夸克网盘", manual_section)
        quark_update_btn.setObjectName("quarkManualUpdateButton")
        quark_update_btn.clicked.connect(self._open_quark_manual_update)
        manual_row.addWidget(quark_update_btn, 1)
        qq_group_btn = QPushButton("查看 QQ 群", manual_section)
        qq_group_btn.setObjectName("qqGroupUpdateButton")
        qq_group_btn.clicked.connect(self._show_qq_group_qrcode)
        manual_row.addWidget(qq_group_btn, 1)
        manual_section.body_layout.addLayout(manual_row)
        scaffold.finish()

        self._config_tab_meta["desktop_pet_update"] = {
            "panel": panel,
            "fields": [],
            "defaults": {},
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
            "section_title_labels": [
                stable_section.title_label,
                dev_section.title_label,
                manual_section.title_label,
            ],
            "section_hint_labels": [
                stable_section.description_label,
                dev_section.description_label,
                manual_section.description_label,
            ],
            "buttons": [check_update_btn, sync_dev_btn, quark_update_btn, qq_group_btn],
        }

        return panel

    def _build_sponsor_author_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        section = scaffold.add_section(
            "支持作者",
            "扫描赞助码，或通过下方按钮前往爱发电。",
        )

        card = QWidget(section)
        card.setObjectName("sponsorAuthorCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(scale_px(10, min_abs=8))

        image_frame = QWidget(card)
        image_frame.setObjectName("sponsorAuthorImageFrame")
        image_frame_layout = QVBoxLayout(image_frame)
        image_frame_layout.setContentsMargins(
            scale_px(10, min_abs=8),
            scale_px(10, min_abs=8),
            scale_px(10, min_abs=8),
            scale_px(10, min_abs=8),
        )
        image_frame_layout.setSpacing(0)
        image_frame.setMaximumWidth(scale_px(380, min_abs=320))

        image_label = QLabel(image_frame)
        image_label.setObjectName("sponsorAuthorImage")
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setWordWrap(True)
        image_frame_layout.addWidget(image_label, 0, Qt.AlignCenter)
        self._set_sponsor_author_image(image_label)

        card_layout.addWidget(image_frame, 0, Qt.AlignHCenter)

        sponsor_button = QPushButton("前往爱发电给作者买鸡腿饭", card)
        sponsor_button.setObjectName("sponsorAuthorButton")
        sponsor_button.setCursor(Qt.PointingHandCursor)
        sponsor_button.setFixedHeight(scale_px(36, min_abs=32))
        sponsor_button.setMaximumWidth(scale_px(380, min_abs=320))
        sponsor_button.clicked.connect(self._open_sponsor_author_link)
        card_layout.addWidget(sponsor_button, 0, Qt.AlignHCenter)

        section.body_layout.addWidget(card)
        scaffold.finish()

        self._config_tab_meta["sponsor_author"] = {
            "panel": panel,
            "fields": [],
            "defaults": {},
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
            "section_title_labels": [section.title_label],
            "section_hint_labels": [section.description_label],
            "buttons": [sponsor_button],
        }

        return panel

    def _build_contribution_list_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        records = [
            record
            for record in _load_contribution_records()
            if str(record.get("url") or "").strip()
        ]
        total_count = len(records)
        section = scaffold.add_section(
            f"贡献者 ({total_count})",
            "选择条目可打开对应开发者主页。",
        )

        buttons: list[QPushButton] = []
        if records:
            name_font = get_ui_font(size=max(scale_px(15, min_abs=12), _CONFIG_FONT_SIZE + scale_px(1, min_abs=1)))
            name_font.setBold(True)
            role_font = get_ui_font(size=max(scale_px(10, min_abs=9), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
            for record in records:
                name = str(record.get("name") or "未命名贡献者").strip()
                role = str(record.get("role") or "贡献者").strip()
                url = str(record.get("url") or "").strip()
                button = _ContributionCardButton(section)
                button.setObjectName("ContributionCardButton")
                button.setCursor(Qt.PointingHandCursor)
                button.setMinimumHeight(scale_px(74, min_abs=66))
                button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                button_layout = QHBoxLayout(button)
                button_layout.setContentsMargins(
                    scale_px(14, min_abs=12),
                    scale_px(12, min_abs=10),
                    scale_px(14, min_abs=12),
                    scale_px(12, min_abs=10),
                )
                button_layout.setSpacing(scale_px(12, min_abs=10))

                accent = QWidget(button)
                accent.setObjectName("ContributionCardAccent")
                accent.setFixedWidth(scale_px(3, min_abs=2))
                accent.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                button_layout.addWidget(accent, 0)

                text_wrap = QWidget(button)
                text_layout = QVBoxLayout(text_wrap)
                text_layout.setContentsMargins(0, 0, 0, 0)
                text_layout.setSpacing(scale_px(4, min_abs=2))
                text_wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)

                name_label = QLabel(name, text_wrap)
                name_label.setFont(name_font)
                name_label.setObjectName("ContributionCardName")
                name_label.setProperty("preserveCustomFont", True)
                name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                text_layout.addWidget(name_label, 0, Qt.AlignLeft)

                role_label = QLabel(role, text_wrap)
                role_label.setFont(role_font)
                role_label.setObjectName("ContributionCardRole")
                role_label.setProperty("preserveCustomFont", True)
                role_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                role_label.setWordWrap(True)
                role_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                text_layout.addWidget(role_label, 0, Qt.AlignLeft)

                button_layout.addWidget(text_wrap, 1)

                watermark_wrap = QWidget(button)
                watermark_wrap.setFixedWidth(scale_px(64, min_abs=56))
                watermark_wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                watermark_layout = QVBoxLayout(watermark_wrap)
                watermark_layout.setContentsMargins(0, 0, 0, 0)
                watermark_layout.setSpacing(0)
                watermark_layout.addStretch(1)

                watermark_label = QLabel("主页", watermark_wrap)
                watermark_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                watermark_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                button.bind_watermark_label(watermark_label)
                watermark_layout.addWidget(watermark_label, 0, Qt.AlignRight | Qt.AlignVCenter)
                button_layout.addWidget(watermark_wrap, 0)

                section.body_layout.addWidget(button)
                buttons.append(button)
                button.clicked.connect(lambda _checked=False, entry_name=name, entry_url=url: self._open_contribution_link(entry_name, entry_url))
        else:
            empty_label = QLabel(f"未读取到贡献名单，请检查文件是否存在：{_contribution_list_path()}", section)
            empty_label.setWordWrap(True)
            section.body_layout.addWidget(empty_label)

        scaffold.finish()

        self._config_tab_meta["contribution_list"] = {
            "panel": panel,
            "fields": [],
            "defaults": {},
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
            "section_title_labels": [section.title_label],
            "section_hint_labels": [section.description_label],
            "buttons": buttons,
        }

        return panel

    def _set_sponsor_author_image(self, label: QLabel) -> None:
        image_path = _sponsor_author_image_path()
        if not image_path.exists():
            label.setText(f"未找到赞助图片：\n{image_path}")
            label.setPixmap(QPixmap())
            return

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            label.setText(f"赞助图片加载失败：\n{image_path.name}")
            label.setPixmap(QPixmap())
            return

        max_width = scale_px(340, min_abs=280)
        scaled = pixmap.scaledToWidth(max_width, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setText("")

    def _open_sponsor_author_link(self) -> None:
        try:
            opened = webbrowser.open(_SPONSOR_AUTHOR_URL)
        except Exception as exc:
            self._show_info_message(f"打开爱发电链接失败：{exc}")
            return
        if not opened:
            self._show_info_message(f"未能自动打开链接，请手动访问：{_SPONSOR_AUTHOR_URL}")

    def _open_contribution_link(self, name: str, url: str) -> None:
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            self._show_info_message(f"打开 {name} 的主页失败：{exc}")
            return
        if not opened:
            self._show_info_message(f"未能自动打开 {name} 的主页，请手动访问：{url}")

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
    ):
        group, row = self._create_field_row_group(spacing=scale_px(10))

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
    def _create_form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('ConfigFormLabel')
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

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
        return pair in _GENERAL_DECIMAL_SLIDER_SPECS

    @staticmethod
    def _get_decimal_slider_spec(dict_name: str, key: str, value) -> tuple[float, float, float, int] | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return _GENERAL_DECIMAL_SLIDER_SPECS.get((str(dict_name), str(key)))

    @staticmethod
    def _get_choice_field_options(dict_name: str, key: str) -> list[tuple[str, str]] | None:
        pair = (str(dict_name), str(key))
        static_options = _GENERAL_CHOICE_FIELD_OPTIONS.get(pair)
        if static_options is not None:
            return static_options
        if pair in {
            ("ANIMATION", "start_animation_folder"),
            ("ANIMATION", "exit_animation_folder"),
        }:
            return [(_animation_folder_display_name(name), name) for name in list_animation_folder_choices()]
        return None

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

    def _create_volume_slider_editor(self, value):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
    ):
        group, row = self._create_field_row_group(spacing=scale_px(8, min_abs=6))

        editor = self._create_config_line_edit(value, expanding=True)
        row.addWidget(editor, 1)

        open_btn = QPushButton("浏览")
        open_btn.setFixedWidth(scale_px(52, min_abs=46))
        if self._is_launch_wuwa_path_field(dict_name, key):
            open_btn.clicked.connect(lambda _=False, line=editor: self._browse_launch_wuwa_file(line))
        elif self._is_local_music_path_field(dict_name, key):
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

    @staticmethod
    def _open_path_with_system_default(path: Path) -> None:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], shell=False)
            return
        if os.name == "posix":
            subprocess.Popen(["xdg-open", str(path)], shell=False)
            return
        subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)

    def _on_open_persona_file(self) -> None:
        try:
            candidate = ensure_user_persona_file()
            self._open_path_with_system_default(candidate)
            self._emit_info(f"已打开人格文件：{candidate.name}", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开人格文件失败: %s", e)
            self._emit_info(f"打开人格文件失败: {e}", min_tick=20, max_tick=180)

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
            self._emit_info("已打开 ONNX 语音缓存文件夹。", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开 ONNX 语音缓存文件夹失败: %s", e)
            self._emit_info(f"打开 ONNX 语音缓存文件夹失败: {e}", min_tick=20, max_tick=180)

    def _ensure_voice_installer_dialog(self) -> VoicePackageInstallerDialog:
        if self._voice_installer_dialog is None:
            dialog = VoicePackageInstallerDialog()
            dialog.install_succeeded.connect(self._on_voice_package_installed)
            self._voice_installer_dialog = dialog
        return self._voice_installer_dialog

    def _on_install_voice_package(self) -> None:
        dialog = self._ensure_voice_installer_dialog()
        if dialog.is_busy():
            self.fade_out()
            delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))
            QTimer.singleShot(delay_ms, dialog.show_dialog)
            return
        self.fade_out()
        delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))
        QTimer.singleShot(delay_ms, dialog.show_dialog)

    def _on_voice_package_installed(self, _result=None) -> None:
        values = load_ai_values(_DEFAULT_VALUES)
        values["gsv_auto_start"] = True
        save_ai_values(values, _DEFAULT_VALUES)
        apply_ai_runtime(values, _DEFAULT_VALUES)
        self._gsv_auto_start.setChecked(True)
        self._refresh_voice_package_ui()
        try:
            from lib.script.gsvmove import get_gsvmove_service

            get_gsvmove_service().reload_voice_package()
        except Exception as exc:
            _logger.warning("安装后预热 ONNX 语音包失败: %s", exc)

    def _on_voice_package_removed(self, _package_root=None) -> None:
        self._refresh_voice_package_ui()
        self._emit_info("ONNX 语音包已删除，可通过安装入口重新安装。", min_tick=12, max_tick=120)

    def _on_voice_package_removal_failed(self, message: str) -> None:
        self._refresh_voice_package_ui()
        if self._voice_package_status.kind == "installed":
            try:
                from lib.script.gsvmove import get_gsvmove_service

                get_gsvmove_service().reload_voice_package()
            except Exception as exc:
                _logger.warning("删除失败后恢复 ONNX 语音包失败: %s", exc)
        self._emit_info(f"删除 ONNX 语音包失败：{message}", min_tick=20, max_tick=180)

    @staticmethod
    def _yuanbao_login_provider_label(provider: str) -> str:
        return "手机QQ" if str(provider).strip().lower() == "qq" else "微信"

    def _set_yuanbao_login_actions(self, *, logged_in: bool) -> None:
        """登录按钮保持单一可执行动作，避免未登录时出现无效的退出入口。"""
        self._start_yuanbao_wechat_login_btn.setVisible(not logged_in)
        self._stop_yuanbao_login_btn.setVisible(logged_in)

    def _refresh_yuanbao_login_actions(self) -> None:
        """后台读取已运行服务的登录态；不启动服务、不触发浏览器登录。"""
        self._yuanbao_login_status_generation += 1
        generation = self._yuanbao_login_status_generation

        def worker() -> None:
            try:
                status = get_yuanbao_free_api_service().peek_service_status()
                logged_in = bool((status or {}).get("logged_in"))
            except Exception as exc:
                _logger.debug("读取元宝登录状态失败: %s", exc)
                logged_in = False

            def apply_result() -> None:
                if generation != self._yuanbao_login_status_generation:
                    return
                self._set_yuanbao_login_actions(logged_in=logged_in)

            self._run_on_ui_thread(apply_result)

        try:
            get_compute_hub().submit_io(worker)
        except RuntimeError as exc:
            _logger.debug("提交元宝登录状态读取任务失败: %s", exc)

    def _on_start_yuanbao_wechat_login(self) -> None:
        self._on_start_yuanbao_login("wechat")

    def _on_start_yuanbao_qq_login(self) -> None:
        self._on_start_yuanbao_login("qq")

    def _on_start_yuanbao_login(self, provider: str = "wechat") -> None:
        provider_name = str(provider or "wechat").strip().lower()
        provider_label = self._yuanbao_login_provider_label(provider_name)
        import config.ollama_config as oc
        oc.YUANBAO_FREE_API["login_url"] = str(getattr(self, "_yuanbao_login_url_value", _DEFAULT_VALUES.get("yuanbao_login_url", "")) or "")
        oc.YUANBAO_FREE_API["agent_id"] = str(getattr(self, "_yuanbao_agent_id_value", _DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa")) or "")

        def worker() -> None:
            try:
                svc = get_yuanbao_free_api_service()
                result = svc.begin_login_flow(provider=provider_name)
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
                    self._run_on_ui_thread(
                        lambda: self._set_yuanbao_login_actions(logged_in=True)
                    )
                    self._emit_info("元宝已登录，本地服务可直接使用。", min_tick=14, max_tick=120)
                elif qrcode_ready:
                    self._run_on_ui_thread(
                        lambda: self._set_yuanbao_login_actions(logged_in=False)
                    )
                    self._emit_info(f"元宝二维码已生成，请使用{provider_label}扫码登录。", min_tick=16, max_tick=180)
                elif login_in_progress or (not last_error and stage_in_progress):
                    self._run_on_ui_thread(
                        lambda: self._set_yuanbao_login_actions(logged_in=False)
                    )
                    detail = stage or message or '正在继续初始化元宝登录流程'
                    self._emit_info(f"元宝登录流程已启动：{detail}", min_tick=14, max_tick=180)
                else:
                    self._run_on_ui_thread(self._refresh_yuanbao_login_actions)
                    log_path = get_yuanbao_free_api_log_path()
                    detail = last_error or stage or message or f'请查看 {log_path.name}'
                    self._emit_info(f"元宝登录未能启动：{detail}", min_tick=18, max_tick=260)
            except Exception as exc:
                _logger.error("Start YuanBao login failed: %s", exc)
                self._run_on_ui_thread(self._refresh_yuanbao_login_actions)
                self._emit_info(f"启动元宝登录失败: {exc}", min_tick=18, max_tick=220)

        try:
            from lib.script.ui.yuanbao_login_dialog import init_yuanbao_login_dialog
            init_yuanbao_login_dialog()
        except Exception as exc:
            _logger.debug("Init YuanBao login dialog failed: %s", exc)
        self._ec.publish(Event(EventType.YUANBAO_LOGIN_QR_SHOW, {
            'title': f'{provider_label}登录元宝',
            'status': f'正在启动元宝服务并准备{provider_label}登录二维码，请稍候...',
            'qr_png': None,
        }))
        self._emit_info(f"正在启动元宝服务并准备{provider_label}登录二维码；本地回环地址、占位密钥与模型名均由程序内部管理。", min_tick=12, max_tick=200)
        get_compute_hub().submit_interactive_io(worker)

    def _on_stop_yuanbao_login(self) -> None:
        def worker() -> None:
            try:
                svc = get_yuanbao_free_api_service()
                svc.stop_login_flow()
                self._run_on_ui_thread(
                    lambda: self._set_yuanbao_login_actions(logged_in=False)
                )
                self._emit_info("已退出元宝登录，并关闭本地元宝服务。", min_tick=12, max_tick=140)
            except Exception as exc:
                _logger.error("Stop YuanBao login failed: %s", exc)
                self._run_on_ui_thread(self._refresh_yuanbao_login_actions)
                self._emit_info(f"退出元宝登录失败: {exc}", min_tick=18, max_tick=220)

        self._emit_info("正在退出元宝登录并关闭本地元宝服务...", min_tick=10, max_tick=120)
        get_compute_hub().submit_interactive_io(worker)

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
    def _wrap_field_widget(widget: QWidget) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(widget, 1, Qt.AlignVCenter)
        return wrap

    def _create_sequence_editor(self, value):
        group, row = self._create_field_row_group(spacing=scale_px(10))

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
        if isinstance(editor, QComboBox):
            index = editor.findData(value)
            if index < 0:
                index = editor.findText(str(value))
            if index >= 0:
                editor.setCurrentIndex(index)
            elif editor.count() > 0:
                editor.setCurrentIndex(0)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(_format_config_editor_value(value))

    @staticmethod
    def _get_autostart_enabled() -> bool:
        try:
            from lib.core.qt_bridge.tray_icon import get_tray_icon
            tray = get_tray_icon()
            return bool(tray._is_autostart_enabled())
        except Exception:
            return False

    def _set_autostart_enabled(self, enabled: bool) -> None:
        try:
            from lib.core.qt_bridge.tray_icon import get_tray_icon
            tray = get_tray_icon()
            target = bool(enabled)
            tray._on_toggle_autostart(target, source="panel")
            actual = bool(tray._is_autostart_enabled())
            self._set_autostart_checkbox_checked(actual)
            if actual != target:
                raise ValueError("开机启动设置未生效，请检查用户 Startup 文件夹和日志")
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
    ) -> None:
        editor = QCheckBox()
        default_enabled = self._get_autostart_enabled()
        editor.setChecked(default_enabled)
        self._autostart_checkbox = editor
        row_widget = self._wrap_field_widget(editor)
        label = self._create_form_label("开机启动")
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

    @staticmethod
    def _get_announcement_forever_suppressed() -> bool:
        return bool(load_announcement_preferences().suppress_forever)

    @staticmethod
    def _set_announcement_forever_suppressed(enabled: bool) -> None:
        set_announcement_forever_suppressed(bool(enabled))

    def _append_announcement_suppression_field(
        self,
        form: QFormLayout,
        fields: list[dict],
    ) -> None:
        editor = QCheckBox()
        editor.setObjectName("AnnouncementSuppressionCheckbox")
        editor.setChecked(self._get_announcement_forever_suppressed())
        self._announcement_suppression_checkbox = editor
        row_widget = self._wrap_field_widget(editor)
        label = self._create_form_label("不显示公告")
        description = (
            "控制启动时是否自动显示公告；托盘“桌宠公告”仍可手动打开。"
            "取消勾选只解除永久抑制，不清除当日抑制状态。"
        )
        self._set_widget_description(label, description)
        self._set_widget_description(row_widget, description)
        self._set_widget_description(editor, description)
        form.addRow(label, row_widget)
        fields.append({
            "kind": "external_announcement_suppression",
            "dict_name": "UI",
            "key": "announcement_suppress_forever",
            "editor": editor,
            "template": False,
            "default": False,
        })

    def _parse_editor_value(self, field: dict) -> dict[str, object]:
        kind = str(field.get("kind") or "single")
        if kind in _EXTERNAL_CONFIG_FIELD_KINDS:
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
        if isinstance(editor, QComboBox):
            selected = editor.currentData()
            if selected is None:
                selected = editor.currentText().strip()
            if isinstance(template, str):
                return {key: str(selected)}
            if template is not None and isinstance(selected, type(template)):
                return {key: selected}
            return {key: self._parse_text_by_template(str(selected), template)}
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

        choice_options = self._get_choice_field_options(dict_name, key)
        if choice_options is not None:
            if not isinstance(value, str):
                self._raise_config_value_error(dict_name, key, "必须为文本选项")
            allowed_values = [option_value for _label, option_value in choice_options]
            if value not in allowed_values:
                joined = " / ".join(str(option_value) for option_value in allowed_values)
                self._raise_config_value_error(dict_name, key, f"必须为以下之一：{joined}")
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
                if kind == "external_announcement_suppression":
                    editor = field.get("editor")
                    if isinstance(editor, QCheckBox):
                        editor.setChecked(self._get_announcement_forever_suppressed())
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
            category_id = category.page_id
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
            category_id = category.page_id
            if not category_id:
                continue
            self._apply_external_category_fields(category_id)

    def _ensure_config_defaults_integrity(self) -> None:
        """兜底检查默认值映射，避免“恢复默认”因缺项而失效。"""
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
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
                if kind == "external_announcement_suppression":
                    field.setdefault("default", False)
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
                                bucket[key] = _hardcoded_general_default(dict_name, key, templates[idx])
                    continue
                key = str(field.get("key") or "")
                if not key:
                    continue
                if key in bucket:
                    continue
                if kind == "sequence":
                    bucket[key] = _hardcoded_general_default(dict_name, key, field.get("template", []))
                else:
                    bucket[key] = _hardcoded_general_default(dict_name, key, field.get("template"))

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
            if kind == "external_announcement_suppression":
                editor = field.get("editor")
                if isinstance(editor, QCheckBox):
                    editor.setChecked(bool(field.get("default", False)))
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
            editor = field.get("editor")
            if not isinstance(editor, QCheckBox):
                continue
            if kind == "external_autostart":
                self._set_autostart_enabled(bool(editor.isChecked()))
            elif kind == "external_announcement_suppression":
                self._set_announcement_forever_suppressed(bool(editor.isChecked()))

    def _submit_save_task(
        self,
        worker: Callable[[], None],
        completion: Callable[[], None],
    ) -> bool:
        """把配置文件写入和缓存清理移出 Qt 主线程。"""
        if self._save_task_pending:
            return False
        self._save_task_pending = True

        def finish(error: Exception | None = None) -> None:
            self._save_task_pending = False
            if error is not None:
                _logger.error("后台保存控制面板设置失败: %s", error)
                self._emit_info(f"保存失败: {error}", min_tick=20, max_tick=180)
                self._save_completion_action = None
                return
            try:
                completion()
            except Exception as exc:
                _logger.error("应用已保存控制面板设置失败: %s", exc)
                self._emit_info(f"应用保存结果失败: {exc}", min_tick=20, max_tick=180)
                self._save_completion_action = None
                return
            action = self._save_completion_action
            self._save_completion_action = None
            if callable(action):
                action()

        def on_done(future) -> None:
            try:
                future.result()
            except Exception as exc:
                self._run_on_ui_thread(lambda error=exc: finish(error))
            else:
                self._run_on_ui_thread(finish)

        try:
            future = get_compute_hub().submit_interactive_io(worker)
            future.add_done_callback(on_done)
        except Exception as exc:
            self._save_task_pending = False
            self._save_completion_action = None
            _logger.error("提交后台保存任务失败: %s", exc)
            self._emit_info(f"保存失败: {exc}", min_tick=20, max_tick=180)
            return False
        return True

    def _on_save_config_category(self, category_id: str) -> bool:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return False
        try:
            values = self._collect_config_category_values(category_id)
            if self._save_task_pending:
                self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
                return False

            def persist() -> None:
                _save_general_config(copy.deepcopy(values))

            def completed() -> None:
                _apply_general_runtime(values)
                self._apply_external_category_fields(category_id)
                message = f"{meta.get('title', '配置')}已保存。"
                if "render_backend" in values.get("UI", {}):
                    message += " 渲染后端将在重启后生效。"
                self._emit_info(message)

            return self._submit_save_task(persist, completed)
        except Exception as e:
            _logger.error("保存配置分类失败(%s): %s", category_id, e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_config_category_and_exit(self, category_id: str) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = self.fade_out
        saved = self._on_save_config_category(category_id)
        if not saved:
            self._save_completion_action = None
        elif not self._save_task_pending and callable(self._save_completion_action):
            action = self._save_completion_action
            self._save_completion_action = None
            action()

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
            if widget.property("preserveCustomFont"):
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
            section_title_labels = meta.get("section_title_labels") or []
            section_hint_labels = meta.get("section_hint_labels") or []
            section_title_font = get_ui_font(size=max(scale_px(12, min_abs=10), _CONFIG_FONT_SIZE))
            section_title_font.setBold(True)
            for widget in section_title_labels:
                if isinstance(widget, QLabel):
                    widget.setFont(section_title_font)
            for widget in section_hint_labels:
                if isinstance(widget, QLabel):
                    widget.setFont(hint_font)

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
        about_c = get_workbench_colors()
        menu_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        menu_font.setBold(True)
        menu_font_family = str(menu_font.family() or "").replace("'", "\\'")
        menu_font_size = max(scale_px(12, min_abs=10), _CONFIG_FONT_SIZE)
        combo_drop_w = scale_px(32, min_abs=28)
        combo_right_pad = combo_drop_w + scale_px(8, min_abs=6)
        scroll_w = scale_px(14, min_abs=12)
        scroll_handle_min_h = scale_px(28, min_abs=20)

        self.setStyleSheet(
            f"""
            QWidget {{
                background: transparent;
                color: {text};
            }}
            QLabel#ConfigSectionLabel {{
                background: {highlight};
                color: {text};
                border: 2px solid {border};
                padding: {scale_px(5, min_abs=4)}px {scale_px(10, min_abs=8)}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-weight: 700;
            }}
            QLabel#ConfigFormLabel {{
                color: {text};
                padding: 0px {scale_px(4, min_abs=3)}px;
                font-weight: 700;
            }}
            QWidget#sponsorAuthorCard {{
                background: transparent;
                border: none;
            }}
            QWidget#sponsorAuthorImageFrame {{
                background: {about_c.surface_raised};
                border: 1px solid {about_c.border};
                border-radius: {scale_px(4, min_abs=3)}px;
            }}
            QLabel#sponsorAuthorImage {{
                background: transparent;
                color: {about_c.text};
                padding: {scale_px(6, min_abs=4)}px;
            }}
            QPushButton#sponsorAuthorButton {{
                background: {about_c.cyan};
                color: {about_c.canvas};
                border: 1px solid {about_c.cyan};
                border-radius: {scale_px(4, min_abs=3)}px;
                min-height: {scale_px(34, min_abs=30)}px;
                padding: 0px {scale_px(12, min_abs=10)}px;
                font-weight: 700;
            }}
            QPushButton#sponsorAuthorButton:hover {{
                background: {about_c.pink_hover};
                color: {about_c.canvas};
                border-color: {about_c.pink_hover};
            }}
            QPushButton#sponsorAuthorButton:pressed {{
                background: {about_c.pink};
                color: {about_c.canvas};
                border-color: {about_c.pink};
            }}
            QPushButton#ContributionCardButton {{
                background: {about_c.surface_raised};
                color: {about_c.text};
                border: 1px solid {about_c.border};
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: 0px;
                min-height: {scale_px(66, min_abs=58)}px;
            }}
            QPushButton#ContributionCardButton:hover {{
                background: {about_c.surface_hover};
                border-color: {about_c.cyan};
            }}
            QPushButton#ContributionCardButton:pressed {{
                background: {about_c.surface};
                border-color: {about_c.pink};
            }}
            QWidget#ContributionCardAccent {{
                background: {about_c.cyan};
                border: none;
                border-radius: {scale_px(1, min_abs=1)}px;
            }}
            QLabel#ContributionCardName {{
                background: transparent;
                color: {about_c.text};
            }}
            QLabel#ContributionCardRole {{
                background: transparent;
                color: {about_c.text_muted};
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
                padding: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-size: {_CONFIG_FONT_SIZE}px;
                font-weight: 700;
            }}
            QComboBox {{
                background: rgba(255, 255, 255, 128);
                color: {text};
                border: 1px solid {border};
                border-radius: 0px;
                padding: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
                padding-right: {combo_right_pad}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-size: {_CONFIG_FONT_SIZE}px;
                font-weight: 700;
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
                image: url(resc/ui/combo_down_arrow.svg);
                width: {scale_px(12, min_abs=10)}px;
                height: {scale_px(8, min_abs=6)}px;
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
                padding: {scale_px(5, min_abs=4)}px {scale_px(12, min_abs=9)}px;
                min-height: {scale_px(32, min_abs=28)}px;
                font-size: {_CONFIG_FONT_SIZE}px;
                font-weight: 700;
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
                width: {scale_px(18, min_abs=15)}px;
                height: {scale_px(18, min_abs=15)}px;
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
                height: {scale_px(8, min_abs=6)}px;
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
                width: {scale_px(13, min_abs=11)}px;
                margin: -5px 0px;
                border-radius: 0px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {highlight};
            }}
            QSlider::handle:horizontal:pressed {{
                background: {text};
            }}
            """
        )

    def refresh_workbench_theme(self) -> None:
        """重新应用工作台主题到面板自有样式和自定义水印控件。"""
        self._apply_style()
        for button in self.findChildren(_ContributionCardButton):
            button._apply_watermark(button.underMouse())
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
        self._set_values_to_form(load_ai_values(_DEFAULT_VALUES))
        try:
            import config.config as cc
            from config.music.volume_config import get_volume_config

            cc.CLOUD_MUSIC["default_volume"] = get_volume_config().get_volume()
        except Exception as exc:
            _logger.debug("加载音乐音量用户配置失败: %s", exc)
        self._load_config_tab_values()

    def show_centered(self) -> None:
        self.load_values()
        self._refresh_voice_package_ui()
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
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._animate(1.0)

    def fade_out(self) -> None:
        if self._external_close_callback is not None:
            self._external_close_callback()
            return
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
            self._subscribe_border_effect_events()
            self._show_floating_tab()
            get_layer_manager().enforce_burst()

    def hideEvent(self, event) -> None:
        self._unsubscribe_border_effect_events()
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

    def _subscribe_yuanbao_login_events(self) -> None:
        if not self._yuanbao_login_status_subscribed:
            self._ec.subscribe(EventType.YUANBAO_LOGIN_QR_STATUS, self._on_yuanbao_login_status_event)
            self._yuanbao_login_status_subscribed = True

    def _unsubscribe_yuanbao_login_events(self) -> None:
        if self._yuanbao_login_status_subscribed:
            self._ec.unsubscribe(EventType.YUANBAO_LOGIN_QR_STATUS, self._on_yuanbao_login_status_event)
            self._yuanbao_login_status_subscribed = False

    def _on_yuanbao_login_status_event(self, event: Event) -> None:
        payload = event.data or {}
        if "logged_in" not in payload:
            return
        logged_in = bool(payload.get("logged_in"))
        self._run_on_ui_thread(
            lambda: self._set_yuanbao_login_actions(logged_in=logged_in)
        )

    def deleteLater(self) -> None:
        self._unsubscribe_border_effect_events()
        self._unsubscribe_yuanbao_login_events()
        self._unsubscribe_autostart_events()
        self._hide_floating_tab()
        if self._tab_floating is not None:
            self._tab_floating.deleteLater()
            self._tab_floating = None
        try:
            get_layer_manager().unregister(self)
        except (AttributeError, RuntimeError):
            pass
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
        interactive_types = (QLineEdit, QComboBox, QPushButton, QCheckBox, QSlider, QListView, QScrollArea)
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
        if force_mode not in ("0", "1", "2", "3", "4"):
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
            model_vision = int(float(self._model_vision.text().strip() or "0"))
        except ValueError as e:
            raise ValueError("模型视力必须是整数") from e
        if not (0 <= model_vision <= 100):
            raise ValueError("模型视力范围应为 0~100")

        try:
            gsv_temperature = float(self._gsv_temperature.text().strip() or "1.0")
        except ValueError as e:
            raise ValueError("GSV服务温度必须是数字") from e
        if not (0.01 <= gsv_temperature <= 2.0):
            raise ValueError("GSV服务温度范围应为 0.01~2")

        gsv_top_k = int(float(self._gsv_top_k.text().strip() or "15"))
        gsv_top_p = float(self._gsv_top_p.text().strip() or "1.0")
        gsv_repetition_penalty = float(self._gsv_repetition_penalty.text().strip() or "1.35")

        try:
            gsv_speed_factor = float(self._gsv_speed_factor.text().strip() or "1.0")
        except ValueError as e:
            raise ValueError("GSV语速必须是数字") from e
        if not (0.5 <= gsv_speed_factor <= 2.0):
            raise ValueError("GSV语速范围应为 0.5~2.0")

        gsv_text_split_method = str(self._gsv_text_split_method.currentData() or "cut5")
        gsv_fragment_interval = float(self._gsv_fragment_interval.text().strip() or "0.3")
        try:
            gsv_seed = int(self._gsv_seed.text().strip() or "-1")
        except ValueError as e:
            raise ValueError("GSV随机种子必须是整数") from e
        gsv_max_steps = int(float(self._gsv_max_steps.text().strip() or "500"))

        try:
            ai_voice_max_chars = int(float(
                self._ai_voice_max_chars.text().strip()
                or str(AI_VOICE_MAX_CHARS_DEFAULT)
            ))
        except ValueError as e:
            raise ValueError("GSV语音字数限制必须是整数") from e
        if not (
            AI_VOICE_MAX_CHARS_MIN
            <= ai_voice_max_chars
            <= AI_VOICE_MAX_CHARS_MAX
        ):
            raise ValueError(
                f"GSV语音字数限制范围应为 "
                f"{AI_VOICE_MAX_CHARS_MIN}~{AI_VOICE_MAX_CHARS_MAX}"
            )

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

        try:
            memory_recall_count = int(float(self._memory_recall_count.text().strip() or "5"))
        except ValueError as e:
            raise ValueError("回忆提取条数必须是整数") from e
        if not (5 <= memory_recall_count <= 50):
            raise ValueError("回忆提取条数范围应为 5~50")

        values = {
            "api_key": self._api_key.raw_text(),
            "force_reply_mode": force_mode,
            "welfare_intelligence_boost": bool(self._welfare_intelligence_boost.isChecked()),
            "api_base_url": AISettingsPanel._normalize_manual_api_base_url(self._api_base_url.text()),
            "api_model": self._api_model.currentText().strip(),
            "yuanbao_free_api_enabled": force_mode == "4",
            "ollama_base_url": self._ollama_base_url.text().strip(),
            "ollama_model": self._ollama_model.currentText().strip(),
            "num_gpu": num_gpu,
            "num_thread": num_thread,
            "api_temperature": api_temperature,
            "model_vision": model_vision,
            "gsv_auto_start": bool(self._gsv_auto_start.isChecked()),
            "gsv_gpu_hybrid": bool(self._gsv_gpu_hybrid.isChecked()),
            "gsv_temperature": gsv_temperature,
            "gsv_top_k": gsv_top_k,
            "gsv_top_p": gsv_top_p,
            "gsv_repetition_penalty": gsv_repetition_penalty,
            "gsv_speed_factor": gsv_speed_factor,
            "gsv_text_split_method": gsv_text_split_method,
            "gsv_fragment_interval": gsv_fragment_interval,
            "gsv_seed": gsv_seed,
            "gsv_max_steps": gsv_max_steps,
            "ai_voice_max_chars": ai_voice_max_chars,
            "gsv_cache_max_files": gsv_cache_max_files,
            "memory_context_limit": memory_context_limit,
            "memory_recall_count": memory_recall_count,
            "api_enable_thinking": bool(self._api_enable_thinking.isChecked()),
            "auto_companion_enabled": bool(self._auto_companion_enabled.isChecked()),
            "auto_companion_interval_minutes": int(self._auto_companion_interval_minutes.value()),
        }
        values.update(self._collect_hidden_yuanbao_values())
        self._validate_ai_values(values)
        return values

    def _set_hidden_yuanbao_values(self, values: dict | None) -> None:
        source = values or {}
        self._yuanbao_login_url_value = str(source.get("yuanbao_login_url", _DEFAULT_VALUES.get("yuanbao_login_url", "")) or "")
        self._yuanbao_hy_source_value = str(source.get("yuanbao_hy_source", _DEFAULT_VALUES.get("yuanbao_hy_source", "web")) or "")
        self._yuanbao_hy_user_value = str(source.get("yuanbao_hy_user", _DEFAULT_VALUES.get("yuanbao_hy_user", "")) or "")
        self._yuanbao_x_uskey_value = str(source.get("yuanbao_x_uskey", _DEFAULT_VALUES.get("yuanbao_x_uskey", "")) or "")
        self._yuanbao_agent_id_value = str(source.get("yuanbao_agent_id", _DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa")) or "")

    def _collect_hidden_yuanbao_values(self) -> dict:
        return {
            "yuanbao_login_url": str(getattr(self, "_yuanbao_login_url_value", _DEFAULT_VALUES.get("yuanbao_login_url", "")) or "").strip(),
            "yuanbao_hy_source": str(getattr(self, "_yuanbao_hy_source_value", _DEFAULT_VALUES.get("yuanbao_hy_source", "web")) or "").strip(),
            "yuanbao_hy_user": str(getattr(self, "_yuanbao_hy_user_value", _DEFAULT_VALUES.get("yuanbao_hy_user", "")) or "").strip(),
            "yuanbao_x_uskey": str(getattr(self, "_yuanbao_x_uskey_value", _DEFAULT_VALUES.get("yuanbao_x_uskey", "")) or "").strip(),
            "yuanbao_agent_id": str(getattr(self, "_yuanbao_agent_id_value", _DEFAULT_VALUES.get("yuanbao_agent_id", "naQivTmsDa")) or "").strip(),
        }

    def _set_values_to_form(self, values: dict) -> None:
        self._api_key.set_raw_text(str(values.get("api_key", "")))
        self._welfare_intelligence_boost.setChecked(bool(values.get("welfare_intelligence_boost", False)))
        self._api_base_url.setText(str(values.get("api_base_url", "")))
        self._sync_manual_api_provider_selection()
        self._refresh_manual_api_model_choices(str(values.get("api_model", "")))
        self._set_hidden_yuanbao_values(values)
        self._ollama_base_url.setText(str(values.get("ollama_base_url", "")))
        self._refresh_ollama_model_choices(str(values.get("ollama_model", "")))
        gpu_mode = _gpu_mode_from_num_gpu(values.get("num_gpu", -1))
        gpu_idx = self._gpu_mode.findData(gpu_mode)
        self._gpu_mode.setCurrentIndex(max(0, gpu_idx))
        self._num_thread.setText(str(values.get("num_thread", 0)))
        self._api_temperature.setText(str(values.get("api_temperature", 0.8)))
        self._model_vision.setText(str(values.get("model_vision", 0)))
        self._gsv_auto_start.setChecked(bool(values.get("gsv_auto_start", True)))
        self._gsv_gpu_hybrid.setChecked(bool(values.get("gsv_gpu_hybrid", False)))
        self._gsv_temperature.setText(str(values.get("gsv_temperature", 1.0)))
        self._gsv_top_k.setText(str(values.get("gsv_top_k", 15)))
        self._gsv_top_p.setText(str(values.get("gsv_top_p", 1.0)))
        self._gsv_repetition_penalty.setText(str(values.get("gsv_repetition_penalty", 1.35)))
        self._gsv_speed_factor.setText(str(values.get("gsv_speed_factor", 1.0)))
        split_method = str(values.get("gsv_text_split_method", "cut5"))
        split_index = self._gsv_text_split_method.findData(split_method)
        self._gsv_text_split_method.setCurrentIndex(max(0, split_index))
        self._gsv_fragment_interval.setText(str(values.get("gsv_fragment_interval", 0.3)))
        self._gsv_seed.setText(str(values.get("gsv_seed", -1)))
        self._gsv_max_steps.setText(str(values.get("gsv_max_steps", 500)))
        self._ai_voice_max_chars.setText(str(values.get(
            "ai_voice_max_chars",
            AI_VOICE_MAX_CHARS_DEFAULT,
        )))
        self._gsv_cache_max_files.setText(str(values.get("gsv_cache_max_files", 20)))
        self._memory_context_limit.setText(str(values.get("memory_context_limit", 12)))
        self._memory_recall_count.setText(str(values.get("memory_recall_count", 5)))
        self._api_enable_thinking.setChecked(bool(values.get("api_enable_thinking", False)))
        self._auto_companion_enabled.setChecked(bool(values.get("auto_companion_enabled", True)))
        self._auto_companion_interval_minutes.set_value(values.get("auto_companion_interval_minutes", 2))
        self._auto_companion_interval_minutes.setEnabled(self._auto_companion_enabled.isChecked())

        mode_value = str(values.get("force_reply_mode", "") or "").strip()
        idx = self._force_mode.findData(mode_value)
        self._force_mode.setCurrentIndex(max(0, idx))
        self._update_reply_mode_sections()

    def _update_reply_mode_sections(self, *_args) -> None:
        mode = str(self._force_mode.currentData() or "1").strip()
        self._welfare_section.setVisible(mode == "1")
        self._manual_api_section.setVisible(mode == "0")
        self._ollama_section.setVisible(mode == "2")
        self._yuanbao_section.setVisible(mode == "4")
        if mode == "4":
            self._refresh_yuanbao_login_actions()

    def _update_gsv_settings_visibility(self) -> None:
        self._voice_section.setVisible(bool(self._gsv_launcher_available))

    def _refresh_voice_package_ui(self) -> None:
        status = get_voice_package_status()
        self._voice_package_status = status
        self._gsv_launcher_available = not status.install_required
        self._voice_package_banner.set_package_status(status)
        self._voice_package_management.set_package_status(status)
        self._update_gsv_settings_visibility()

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

    @staticmethod
    def _normalize_manual_api_base_url(raw_url: str) -> str:
        """补全手动 OpenAI 兼容地址的协议，保留用户填写的路径。"""
        text = str(raw_url or "").strip()
        if not text:
            return ""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
            return text.rstrip("/")
        if text.startswith("//"):
            return f"https:{text}".rstrip("/")

        host = text.split("/", 1)[0].lower()
        is_local = (
            host == "localhost"
            or host.startswith("localhost:")
            or host.startswith("127.")
            or host.startswith("0.0.0.0")
            or host.startswith("[::1]")
            or host == "::1"
        )
        scheme = "http" if is_local else "https"
        return f"{scheme}://{text}".rstrip("/")

    def _normalize_manual_api_base_url_input(self) -> None:
        normalized = self._normalize_manual_api_base_url(self._api_base_url.text())
        if normalized != self._api_base_url.text().strip():
            self._api_base_url.setText(normalized)

    def _on_manual_api_provider_changed(self, _index: int) -> None:
        base_url = str(self._manual_api_provider.currentData() or "").strip()
        if base_url:
            self._api_base_url.setText(base_url)

    def _sync_manual_api_provider_selection(self, *_args) -> None:
        current_base_url = self._normalize_manual_api_base_url(self._api_base_url.text())
        matched_index = 0
        for index in range(1, self._manual_api_provider.count()):
            preset_url = self._normalize_manual_api_base_url(
                str(self._manual_api_provider.itemData(index) or "")
            )
            if current_base_url and current_base_url == preset_url:
                matched_index = index
                break
        if self._manual_api_provider.currentIndex() != matched_index:
            self._manual_api_provider.blockSignals(True)
            self._manual_api_provider.setCurrentIndex(matched_index)
            self._manual_api_provider.blockSignals(False)

    @classmethod
    def _manual_api_models_url(cls, base_url: str) -> str:
        root = cls._normalize_manual_api_base_url(base_url)
        root = root.rstrip("/")
        for suffix in ("/chat/completions",):
            if root.lower().endswith(suffix):
                root = root[:-len(suffix)].rstrip("/")
                break
        return f"{root}/models" if root else ""

    @staticmethod
    def _parse_manual_api_models(payload) -> list[str]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("接口没有返回兼容的模型列表")
        models = {
            str(item.get("id", "")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        return sorted(models, key=str.casefold)

    @classmethod
    def _probe_manual_api_models(cls, base_url: str, api_key: str) -> list[str]:
        models_url = cls._manual_api_models_url(base_url)
        if not models_url:
            raise ValueError("请先填写接口地址")
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("请先填写接口密钥")
        response = requests.get(
            models_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=API_TIMEOUT_SECS,
        )
        response.raise_for_status()
        models = cls._parse_manual_api_models(response.json())
        if not models:
            raise ValueError("接口未返回可用模型")
        return models

    def _refresh_manual_api_model_choices(self, selected_model: str = "", models: list[str] | None = None) -> None:
        if not isinstance(self._api_model, QComboBox):
            return
        selected_text = str(selected_model or "").strip()
        choices = list(models or [])
        self._api_model.blockSignals(True)
        self._api_model.clear()
        for model in choices:
            self._api_model.addItem(model, model)
        if selected_text:
            index = self._api_model.findData(selected_text)
            if index >= 0:
                self._api_model.setCurrentIndex(index)
            else:
                self._api_model.setEditText(selected_text)
        elif choices:
            self._api_model.setCurrentIndex(0)
        self._api_model.blockSignals(False)

    def _on_probe_manual_api_models(self) -> None:
        base_url = self._normalize_manual_api_base_url(self._api_base_url.text())
        api_key = self._api_key.raw_text()
        if not base_url or not api_key:
            self._emit_info("请先填写接口地址和接口密钥。", min_tick=10, max_tick=100)
            return
        self._api_base_url.setText(base_url)
        selected_model = self._api_model.currentText().strip()
        self._probe_manual_api_models_btn.setEnabled(False)
        self._probe_manual_api_models_btn.setText("探测中...")

        def worker() -> None:
            try:
                models = self._probe_manual_api_models(base_url, api_key)
            except Exception as exc:
                _logger.warning("手动 API 模型探测失败: %s", exc)

                def apply_failure() -> None:
                    self._probe_manual_api_models_btn.setEnabled(True)
                    self._probe_manual_api_models_btn.setText("探测模型")
                    self._emit_info("模型探测失败，请检查接口地址、密钥和服务兼容性。", min_tick=12, max_tick=140)

                self._run_on_ui_thread(apply_failure)
                return

            def apply_success() -> None:
                self._refresh_manual_api_model_choices(selected_model, models)
                self._probe_manual_api_models_btn.setEnabled(True)
                self._probe_manual_api_models_btn.setText("探测模型")
                self._emit_info(f"已探测到 {len(models)} 个模型。", min_tick=10, max_tick=100)

            self._run_on_ui_thread(apply_success)

        future = get_compute_hub().submit_latest(
            "ai_settings_manual_api_model_probe",
            worker,
            executor="io",
        )
        if future is None:
            self._probe_manual_api_models_btn.setEnabled(True)
            self._probe_manual_api_models_btn.setText("探测模型")
            self._emit_info("模型探测正在进行，请稍候。", min_tick=10, max_tick=100)

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

    def _ensure_qq_group_dialog(self) -> QQGroupDialog:
        if self._qq_group_dialog is None:
            image_path = _project_root() / "resc" / "GIF" / "QQqrc.png"
            self._qq_group_dialog = QQGroupDialog(image_path)
        return self._qq_group_dialog

    def _open_quark_manual_update(self) -> None:
        try:
            opened = webbrowser.open(_QUARK_UPDATE_URL)
        except Exception as exc:
            self._show_info_message(f"打开夸克更新链接失败：{exc}")
            return
        if not opened:
            self._show_info_message(f"未能调用系统默认浏览器，请手动打开：{_QUARK_UPDATE_URL}")

    def _show_qq_group_qrcode(self) -> None:
        self._ensure_qq_group_dialog().show_dialog()

    def _on_restore_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._ensure_config_defaults_integrity()
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id:
                continue
            self._on_restore_config_category(category_id, emit_message=False)
        self._emit_info("已恢复默认配置，保存后会移除对应用户覆盖。", min_tick=10, max_tick=90)

    def _on_restore_ai_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._emit_info("AI 设置已恢复默认值，保存后生效。", min_tick=10, max_tick=90)

    def _on_save_ai_action(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = None if self._workbench_attached else self.fade_out
        if not self._on_save():
            self._save_completion_action = None
        elif not self._save_task_pending and callable(self._save_completion_action):
            action = self._save_completion_action
            self._save_completion_action = None
            action()

    def _on_save_and_restart(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        restart_action = lambda: self._ec.publish(Event(
            EventType.APP_QUIT,
            {"exit_code": 0, "restart": True},
        ))
        self._save_completion_action = restart_action
        if not self._on_save(apply_runtime=False):
            self._save_completion_action = None
            return
        # Keep compatibility with lightweight test doubles and integrations
        # that replace _on_save with a synchronous implementation.
        if not self._save_task_pending:
            action = self._save_completion_action
            self._save_completion_action = None
            if callable(action):
                action()

    def _on_save(self, *, apply_runtime: bool = True) -> bool:
        try:
            ai_values = self._collect_values()
            general_values = self._collect_all_general_config_values()
            if self._save_task_pending:
                self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
                return False

            def persist() -> None:
                save_ai_values(copy.deepcopy(ai_values), _DEFAULT_VALUES)
                _save_general_config(copy.deepcopy(general_values))
                if apply_runtime:
                    try:
                        from lib.script.gsvmove import get_gsvmove_service

                        get_gsvmove_service().cleanup_saved_audio_cache()
                    except Exception as trim_exc:
                        _logger.warning("应用 GSV 语音缓存上限失败: %s", trim_exc)

            def completed() -> None:
                if apply_runtime:
                    apply_ai_runtime(ai_values, _DEFAULT_VALUES)
                    _apply_general_runtime(general_values)
                self._apply_all_external_config_fields()
                self._emit_info("控制面板设置已保存，重启程序后完整生效。")

            return self._submit_save_task(persist, completed)
        except Exception as e:
            _logger.error("保存控制面板设置失败: %s", e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_and_exit(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = self.fade_out
        if self._on_save():
            if not self._save_task_pending and callable(self._save_completion_action):
                action = self._save_completion_action
                self._save_completion_action = None
                action()
            return
        self._save_completion_action = None

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
        painter.drawText(wm_rect, Qt.AlignLeft | Qt.AlignBottom, self._panel_watermark_text)
