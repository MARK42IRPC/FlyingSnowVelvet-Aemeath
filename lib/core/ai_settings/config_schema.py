"""AI 设置面板的通用配置 schema 与取值格式化（无 Qt）。

从 `ai_settings_panel.py` 拆出的纯数据 + 纯函数：

- schema 表：`CATEGORY_KEY_ALLOWLIST`（各分类允许出现哪些字典的哪些键）、
  `GENERAL_BOOL_KEYS` / `GENERAL_NUMERIC_RULES` / `GENERAL_TUPLE_INT_RULES` /
  `GENERAL_RANGE_RELATIONS` / `VOLUME_SLIDER_FIELDS` / `GENERAL_DECIMAL_SLIDER_SPECS` /
  `GENERAL_CHOICE_FIELD_OPTIONS`、`GENERAL_CONFIG_DEFAULTS`、`GENERAL_MIXED_SECTION_FIELDS`；
- 取值处理函数：`category_section_entries` / `choice_label_for_value` /
  `hardcoded_general_default` / `range_pair_signature` / `friendly_range_name` /
  `is_supported_config_value` / `format_config_editor_value`。

它不碰 Qt、不碰 UI 装配；引用的 config 常量（`LAYER_VALUES`、动画目录默认值）与后端
描述符表（`get_backend_descriptors`）来自后端中立来源，面板与新模块读的是同一份。

面板按原名（加前导下划线）再导入这些符号，既有调用面不变；本模块导出的是去掉下划线的公开名。
"""

from __future__ import annotations

import copy

from config.config_layer import LAYER_VALUES
from lib.core.render.router import get_backend_descriptors
from config.config_animation import ANIMATION as _ANIMATION_CONFIG
from lib.core.ai_settings.labels import friendly_key_name as _friendly_key_name

#: 动画目录默认值与 `lib/script/SEanima/clip.py` 读的是同一份 `config.config_animation`；
#: 原实现从 SEanima 再取一层，core 不得 import 产品包，故直接取 config 源值（逐字符相同）。
DEFAULT_START_ANIMATION_FOLDER = str(_ANIMATION_CONFIG["start_animation_folder"])
DEFAULT_EXIT_ANIMATION_FOLDER = str(_ANIMATION_CONFIG["exit_animation_folder"])


CATEGORY_KEY_ALLOWLIST = {
    "ui_anim": {
        "ANIMATION": {
            "frame_fps",
            "gif_fps",
            "start_exit_enabled",
            "start_animation_folder",
            "start_animation_duration",
            "exit_animation_folder",
            "exit_animation_duration",
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
        "LAYER_VALUES": {
            "BACKGROUND",
            "WORLD_OBJECT",
            "MAIN_PET",
            "PET_EFFECT_BELOW",
            "PARTICLE",
            "EFFECT",
            "PET_UI",
            "PANEL",
            "DIALOG",
            "TOOLTIP",
            "SYSTEM_MODAL",
        },
        "STARTUP": {
            "ensure_desktop_shortcut",
            "log_retention_count",
            "ui_cache_preload",
        },
    },
    "live_stream": {},  # 直播设置 - 字段由 LIVE_STREAM 分组自动生成
    "desktop_pet_update": {},  # 桌宠更新标签页 - 没有配置字段，只有按钮
}

GENERAL_BOOL_KEYS: set[tuple[str, str]] = {
    ("ANIMATION", "start_exit_enabled"),
    ("PARTICLES", "enable_stroke"),
    ("MORTOR", "bgm_enabled"),
    ("STARTUP", "ensure_desktop_shortcut"),
    ("STARTUP", "ui_cache_preload"),
    ("LIVE_STREAM", "live_mode_enabled"),
    ("LIVE_STREAM", "enabled"),
    ("LIVE_STREAM", "notify_on_enqueue"),
    ("GIFT_ALERT", "enabled"),
    ("GIFT_ALERT", "notify_in_chat"),
    ("GIFT_ALERT", "speak"),
}

GENERAL_NUMERIC_RULES: dict[tuple[str, str], tuple[str, float, float]] = {
    ("ANIMATION", "start_animation_duration"): ("number", 0.5, 2.0),
    ("ANIMATION", "exit_animation_duration"): ("number", 0.5, 2.0),
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
    ("GIFT_ALERT", "min_price"): ("int", 0, 1000000),
    ("LIVE_STREAM", "request_cooldown_seconds"): ("int", 0, 600),
    ("LIVE_STREAM", "duplicate_window_seconds"): ("int", 0, 86400),
    ("LIVE_STREAM", "pending_limit"): ("int", 1, 100),
    ("LIVE_STREAM", "max_queue_size"): ("int", 1, 5000),
    ("LIVE_STREAM", "song_title_max_length"): ("int", 1, 200),
    ("LIVE_STREAM", "artist_max_length"): ("int", 0, 200),
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
    ("LAYER_VALUES", "BACKGROUND"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "WORLD_OBJECT"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "MAIN_PET"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "PET_EFFECT_BELOW"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "PARTICLE"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "EFFECT"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "PET_UI"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "PANEL"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "DIALOG"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "TOOLTIP"): ("int", -1000000, 1000000),
    ("LAYER_VALUES", "SYSTEM_MODAL"): ("int", -1000000, 1000000),
    ("STARTUP", "log_retention_count"): ("int", 1, 200),
}

GENERAL_TUPLE_INT_RULES: dict[tuple[str, str], tuple[int, int]] = {
    ("SNOW_PILE", "batch_interval"): (1, 3600000),
    ("SNOW_PILE", "batch_size"): (1, 128),
    ("SNOW_PILE", "batch_item_interval"): (1, 3600000),
}

GENERAL_RANGE_RELATIONS: tuple[tuple[str, str, str], ...] = (
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

VOLUME_SLIDER_FIELDS: set[tuple[str, str]] = {
    ("SOUND", "master_volume"),
    ("SOUND", "main_pet_volume"),
    ("SOUND", "game_object_volume"),
    ("VOICE", "voice_volume"),
    ("CLOUD_MUSIC", "default_volume"),
}

GENERAL_DECIMAL_SLIDER_SPECS: dict[tuple[str, str], tuple[float, float, float, int]] = {
    ("ANIMATION", "start_animation_duration"): (0.5, 2.0, 0.1, 1),
    ("ANIMATION", "exit_animation_duration"): (0.5, 2.0, 0.1, 1),
    ("UI", "pet_opacity"): (0.0, 1.0, 0.05, 2),
    ("UI", "ui_widget_opacity"): (0.0, 1.0, 0.05, 2),
    ("UI", "tooltip_opacity"): (0.0, 1.0, 0.05, 2),
    ("OBJECTS", "object_opacity"): (0.0, 1.0, 0.05, 2),
    ("ANIMATION", "exit_shadow_strength"): (0.0, 255.0, 1.0, 0),
    ("ANIMATION", "exit_shadow_blur_radius"): (0.0, 128.0, 1.0, 0),
    ("VOICE", "microphone_denoise_strength"): (0.0, 1.0, 0.05, 2),
}

GENERAL_CHOICE_FIELD_OPTIONS: dict[tuple[str, str], list[tuple[str, str]]] = {
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

GENERAL_CONFIG_DEFAULTS: dict[str, dict[str, object]] = {
    "ANIMATION": {
        "frame_fps": 60,
        "gif_fps": 16,
        "start_exit_enabled": True,
        "start_animation_folder": DEFAULT_START_ANIMATION_FOLDER,
        "start_animation_duration": 0.5,
        "exit_animation_folder": DEFAULT_EXIT_ANIMATION_FOLDER,
        "exit_animation_duration": 0.5,
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
    "LIVE_STREAM": {
        "live_mode_enabled": False,
        "enabled": False,
        "room_id": "",
        "command_prefix": "点歌",
        "request_cooldown_seconds": 10,
        "duplicate_window_seconds": 120,
        "pending_limit": 10,
        "max_queue_size": 200,
        "song_title_max_length": 50,
        "artist_max_length": 30,
        "notify_on_enqueue": True,
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
    "LAYER_VALUES": dict(LAYER_VALUES),
    "STARTUP": {
        "ensure_desktop_shortcut": True,
        "log_retention_count": 20,
        "ui_cache_preload": False,
    },
}

#: 「一个分区只取同一个配置字典里的部分键」的路由表：`分区名 -> (字典名, 该分区的键)`。
#: 直播页把 `LIVE_STREAM` 拆成两张卡片——「直播间设置」（总开关 + 房间号）与「弹幕点歌」
#: （点歌参数），两者仍写回同一份 `LIVE_STREAM` 配置，保存语义不变。
GENERAL_SECTION_KEY_ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {

    "LIVE_STREAM_SONG": (
        "LIVE_STREAM",
        (
            "command_prefix",
            "request_cooldown_seconds",
            "duplicate_window_seconds",
            "pending_limit",
            "max_queue_size",
            "song_title_max_length",
            "artist_max_length",
            "notify_on_enqueue",
        ),
    ),
    #: 礼物提醒与直播点歌平行，用的是独立配置字典 `GIFT_ALERT`。
    #: 总开关 `enabled` 放在「直播间设置」卡片里（与「启用弹幕点歌」同一处），
    #: 这张卡片只放启用后才需要看的参数。
    "GIFT_ALERT": (
        "GIFT_ALERT",
        ("template", "min_price", "notify_in_chat", "speak"),
    ),
}


GENERAL_MIXED_SECTION_FIELDS: dict[str, list[tuple[str, str]]] = {
    #: 「直播间设置」卡片的行序：直播模式 → 房间号 → 礼物提醒 → 直播点歌。
    #: 「直播模式」是连接总开关，礼物提醒与弹幕点歌共用同一条弹幕连接，
    #: 两个子开关都排在它后面，从属关系才看得出来。
    #: 「连接检查」按钮行由页面在礼物开关之后插入，见 `_move_row_before_gift_switch`。
    "LIVE_STREAM": [
        ("LIVE_STREAM", "live_mode_enabled"),
        ("LIVE_STREAM", "room_id"),
        ("GIFT_ALERT", "enabled"),
        ("LIVE_STREAM", "enabled"),
    ],
    "AUDIO_VOLUMES": [
        ("SOUND", "master_volume"),
        ("SOUND", "main_pet_volume"),
        ("SOUND", "game_object_volume"),
        ("VOICE", "voice_volume"),
        ("VOICE", "lahai_skill_release_volume"),
        ("CLOUD_MUSIC", "default_volume"),
    ],
}


def category_section_entries(category_id: str, section_name: str, cc_module, category_allow_map: dict) -> list[tuple[str, str, object]]:
    route = GENERAL_SECTION_KEY_ROUTES.get(str(section_name))
    if route is not None:
        dict_name, routed_keys = route
        section_obj = getattr(cc_module, str(dict_name), None)
        if not isinstance(section_obj, dict):
            return []
        entries: list[tuple[str, str, object]] = []
        for key in routed_keys:
            if key not in section_obj:
                continue
            value = section_obj.get(key)
            if not is_supported_config_value(value):
                continue
            entries.append((str(dict_name), str(key), value))
        return entries

    mixed_fields = GENERAL_MIXED_SECTION_FIELDS.get(str(section_name))
    if mixed_fields is not None:
        entries: list[tuple[str, str, object]] = []
        for dict_name, key in mixed_fields:
            section_obj = getattr(cc_module, str(dict_name), None)
            if not isinstance(section_obj, dict):
                continue
            if key not in section_obj:
                continue
            value = section_obj.get(key)
            if not is_supported_config_value(value):
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
        if not is_supported_config_value(value):
            continue
        entries.append((str(section_name), key_text, value))
    return entries


def choice_label_for_value(dict_name: str, key: str, value) -> str | None:
    options = GENERAL_CHOICE_FIELD_OPTIONS.get((str(dict_name), str(key)))
    if not options:
        return None
    for label, option_value in options:
        if option_value == value:
            return label
    return None


def hardcoded_general_default(dict_name: str, key: str, fallback):
    section = GENERAL_CONFIG_DEFAULTS.get(str(dict_name))
    if isinstance(section, dict) and key in section:
        return copy.deepcopy(section[key])
    return copy.deepcopy(fallback)


def range_pair_signature(key: str) -> tuple[str, str] | None:
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


def friendly_range_name(dict_name: str, left_key: str, right_key: str) -> str:
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


def is_supported_config_value(value) -> bool:
    basic_types = (bool, int, float, str)
    if isinstance(value, basic_types):
        return True
    if isinstance(value, tuple):
        return all(isinstance(item, basic_types) for item in value)
    if isinstance(value, list):
        return all(isinstance(item, basic_types) for item in value)
    return False


def format_config_editor_value(value) -> str:
    if isinstance(value, str):
        return value
    return repr(value)
