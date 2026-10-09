"""AI 设置面板配置字段的「说明文本」生成（无 Qt）。

从 `ai_settings_panel.py` 拆出的四个纯函数：`build_config_single_description` /
`build_config_range_description` 拼装字段说明（分节名 · 友好名 / 配置键 / 类型 / 默认值），
`description_value_type` 把取值映射成中文类型名，`description_preview_value` 做默认值的
截断预览。

它们只读 `ai_settings_config_schema` 与 `ai_settings_labels` 两张已抽离的事实源表，
不碰 Qt、不碰控件，因此没有理由留在面板里。面板按原名（加前导下划线）再导出，
既有调用面与测试不变。
"""

from __future__ import annotations

from lib.script.ui.ai_settings_config_schema import (
    choice_label_for_value,
    format_config_editor_value,
)
from lib.script.ui.ai_settings_labels import (
    friendly_field_section_name,
    friendly_section_name,
)

__all__ = [
    "build_config_range_description",
    "build_config_single_description",
    "description_preview_value",
    "description_value_type",
]

def description_preview_value(value, max_len: int = 72) -> str:
    text = format_config_editor_value(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def description_value_type(value) -> str:
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


def build_config_single_description(
    dict_name: str,
    key: str,
    value,
    friendly_name: str,
) -> str:
    section_name = friendly_field_section_name(dict_name, key)
    value_type = description_value_type(value)
    preview = description_preview_value(value)
    choice_label = choice_label_for_value(dict_name, key, value)
    if choice_label and choice_label != preview:
        preview = f"{choice_label} ({preview})"
    recommendation = ""
    if (dict_name, key) in {
        ("ANIMATION", "start_animation_duration"),
        ("ANIMATION", "exit_animation_duration"),
    }:
        try:
            preview = f"{float(value):.1f}x"
        except (TypeError, ValueError):
            preview = "1.0x"
        recommendation = (
            "，推荐3.0s"
            if key == "start_animation_duration"
            else "，推荐默认"
        )
    elif (dict_name, key) == ("STARTUP", "ui_cache_preload"):
        recommendation = "，启动等待期预绘制常用窗口，缓存上限30MB"
    return (
        f"{section_name} · {friendly_name}\n"
        f"配置键: {dict_name}.{key}\n"
        f"类型: {value_type}\n"
        f"默认值: {preview}{recommendation}"
    )


def build_config_range_description(
    dict_name: str,
    left_key: str,
    right_key: str,
    left_value,
    right_value,
    friendly_name: str,
) -> str:
    section_name = friendly_section_name(dict_name, dict_name)
    left_preview = description_preview_value(left_value)
    right_preview = description_preview_value(right_value)
    return (
        f"{section_name} · {friendly_name}\n"
        f"配置键: {dict_name}.{left_key} / {dict_name}.{right_key}\n"
        f"类型: 数值范围\n"
        f"默认值: {left_preview} ~ {right_preview}"
    )
