"""AI 设置面板「通用配置」的取值校验（无 Qt）。

从 `ai_settings_panel.py` 拆出的纯校验逻辑：整段入口 `validate_general_config_values`，
逐键的 `validate_general_config_value`、跨键的 `validate_general_config_relations`，以及
三个内部助手（`get_choice_field_options` / `raise_config_value_error` /
`validate_general_numeric`）。原先它们是面板上的 `self.xxx` 方法：逻辑本身与控件无关，
唯一的例外是 `get_choice_field_options` 需要动画目录的可用值——那份"有哪些目录"的答案
（显示名 + 目录名）改由调用方注入，本模块因此既不 import `PyQt5`、也不 import
`lib.script.SEanima`。

校验失败抛 `ConfigValueError`（`ValueError` 的子类）。面板的历史契约是抛 `ValueError`，
调用点与测试都按它捕获；面板方法显式把 `ConfigValueError` 转换回 `ValueError`，
异常类型与消息逐字符不变。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Callable

from lib.script.microphone_stt.push_to_talk import parse_hotkey_binding
from lib.script.ui.ai_settings_config_schema import (
    GENERAL_BOOL_KEYS,
    GENERAL_CHOICE_FIELD_OPTIONS,
    GENERAL_NUMERIC_RULES,
    GENERAL_RANGE_RELATIONS,
    GENERAL_TUPLE_INT_RULES,
)
from lib.script.ui.ai_settings_labels import friendly_key_name

#: 动画目录选项的注入签名：返回 `[(显示名, 目录名), ...]`。
FolderChoices = Callable[[], list[tuple[str, str]]]

__all__ = [
    "ConfigValueError",
    "get_choice_field_options",
    "raise_config_value_error",
    "validate_general_config_value",
    "validate_general_config_relations",
    "validate_general_config_values",
]


class ConfigValueError(ValueError):
    """配置取值校验失败；面板按历史契约转换回 `ValueError`。"""


def validate_general_config_values(
    values_by_dict: dict[str, dict],
    *,
    choice_options: Callable[[str, str], list[tuple[str, str]] | None] | None = None,
    project_root: Path | None = None,
) -> None:
    for dict_name, section in values_by_dict.items():
        if not isinstance(section, dict):
            raise ConfigValueError(f"{dict_name} 配置结构无效")
        for key, value in section.items():
            validate_general_config_value(
                str(dict_name),
                str(key),
                value,
                choice_options=choice_options,
                project_root=project_root,
            )
    validate_general_config_relations(values_by_dict)


def get_choice_field_options(
    dict_name: str,
    key: str,
    *,
    folder_options: FolderChoices | None = None,
) -> list[tuple[str, str]] | None:
    pair = (str(dict_name), str(key))
    static_options = GENERAL_CHOICE_FIELD_OPTIONS.get(pair)
    if static_options is not None:
        return static_options
    if pair in {
        ("ANIMATION", "start_animation_folder"),
        ("ANIMATION", "exit_animation_folder"),
    }:
        if folder_options is None:
            return None
        return list(folder_options())
    return None


def raise_config_value_error(dict_name: str, key: str, reason: str) -> None:
    friendly = friendly_key_name(dict_name, key)
    raise ConfigValueError(f"{dict_name}.{key}（{friendly}）{reason}")


def validate_general_numeric(
    dict_name: str,
    key: str,
    value,
    kind: str,
    min_val: float,
    max_val: float,
) -> None:
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise_config_value_error(dict_name, key, "必须为整数")
        if value < int(min_val) or value > int(max_val):
            raise_config_value_error(dict_name, key, f"必须在 {int(min_val)}~{int(max_val)} 范围内")
        return

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise_config_value_error(dict_name, key, "必须为数字")
    try:
        num = float(value)
    except Exception:
        raise_config_value_error(dict_name, key, "必须为数字")
        return
    if not math.isfinite(num):
        raise_config_value_error(dict_name, key, "必须为有限数字")
    if num < min_val or num > max_val:
        raise_config_value_error(dict_name, key, f"必须在 {min_val}~{max_val} 范围内")


def validate_general_config_value(
    dict_name: str,
    key: str,
    value,
    *,
    choice_options: Callable[[str, str], list[tuple[str, str]] | None] | None = None,
    project_root: Path | None = None,
) -> None:
    pair = (dict_name, key)

    if pair in GENERAL_BOOL_KEYS:
        if not isinstance(value, bool):
            raise_config_value_error(dict_name, key, "必须为开关值")
        return

    if pair in GENERAL_TUPLE_INT_RULES:
        min_item, max_item = GENERAL_TUPLE_INT_RULES[pair]
        if not isinstance(value, tuple) or len(value) != 2:
            raise_config_value_error(dict_name, key, "必须为长度为 2 的整数元组")
        left, right = value
        if isinstance(left, bool) or not isinstance(left, int):
            raise_config_value_error(dict_name, key, "左值必须为整数")
        if isinstance(right, bool) or not isinstance(right, int):
            raise_config_value_error(dict_name, key, "右值必须为整数")
        if left < min_item or left > max_item or right < min_item or right > max_item:
            raise_config_value_error(dict_name, key, f"每项必须在 {min_item}~{max_item} 范围内")
        if left > right:
            raise_config_value_error(dict_name, key, "最小值不能大于最大值")
        return

    if pair == ("CLOUD_MUSIC", "cache_dir"):
        if not isinstance(value, str):
            raise_config_value_error(dict_name, key, "必须为文本路径")
        normalized = value.strip()
        if not normalized:
            raise_config_value_error(dict_name, key, "不能为空")
        if Path(normalized).is_absolute():
            raise_config_value_error(dict_name, key, "必须使用相对路径")
        if "\n" in normalized or "\r" in normalized:
            raise_config_value_error(dict_name, key, "路径包含非法换行字符")
        return

    if pair == ("CLOUD_MUSIC", "local_music_dir"):
        if not isinstance(value, str):
            raise_config_value_error(dict_name, key, "必须为文本路径")
        normalized = value.strip()
        if not normalized:
            return
        if "\n" in normalized or "\r" in normalized:
            raise_config_value_error(dict_name, key, "路径包含非法换行字符")

        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = (project_root or Path.cwd()) / normalized
        if candidate.exists() and not candidate.is_dir():
            raise_config_value_error(dict_name, key, "必须指向文件夹路径")
        return

    if pair == ("CLOUD_MUSIC", "launch_wuwa_path"):
        if not isinstance(value, str):
            raise_config_value_error(dict_name, key, "必须为文本路径")
        normalized = value.strip()
        if not normalized:
            return
        if "\n" in normalized or "\r" in normalized:
            raise_config_value_error(dict_name, key, "路径包含非法换行字符")

        expanded = os.path.expandvars(os.path.expanduser(normalized))
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = (project_root or Path.cwd()) / candidate

        ext = candidate.suffix.lower()
        if ext not in (".exe", ".bat", ".lnk"):
            raise_config_value_error(dict_name, key, "仅支持 .exe / .bat / .lnk 文件")
        if candidate.exists() and not candidate.is_file():
            raise_config_value_error(dict_name, key, "必须指向文件路径")
        return

    if pair == ("VOICE", "microphone_push_to_talk_key"):
        if not isinstance(value, str):
            raise_config_value_error(dict_name, key, "必须为文本内容")
        normalized = value.strip()
        if not normalized:
            return
        if "\n" in normalized or "\r" in normalized:
            raise_config_value_error(dict_name, key, "内容包含非法换行字符")
        if parse_hotkey_binding(normalized) is None:
            raise_config_value_error(dict_name, key, "格式无效，示例：Ctrl+Shift+V")
        return

    current_options = (
        choice_options(dict_name, key) if choice_options is not None else None
    )
    if current_options is not None:
        if not isinstance(value, str):
            raise_config_value_error(dict_name, key, "必须为文本选项")
        allowed_values = [option_value for _label, option_value in current_options]
        if value not in allowed_values:
            joined = " / ".join(str(option_value) for option_value in allowed_values)
            raise_config_value_error(dict_name, key, f"必须为以下之一：{joined}")
        return

    numeric_rule = GENERAL_NUMERIC_RULES.get(pair)
    if numeric_rule is not None:
        kind, min_val, max_val = numeric_rule
        validate_general_numeric(dict_name, key, value, kind, min_val, max_val)


def validate_general_config_relations(values_by_dict: dict[str, dict]) -> None:
    for dict_name, left_key, right_key in GENERAL_RANGE_RELATIONS:
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
            raise_config_value_error(dict_name, left_key, "与关联上限比较失败")
            return
        if left_num > right_num:
            left_name = friendly_key_name(dict_name, left_key)
            right_name = friendly_key_name(dict_name, right_key)
            raise ConfigValueError(
                f"{dict_name} 配置无效：{left_name} 不能大于 {right_name}"
            )
