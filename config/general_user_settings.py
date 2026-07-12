"""Sparse overrides for editable general config dictionaries."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import config.config as config_module
from config.shared_storage_paths import get_shared_config_path
from config.user_settings import get_section_overrides, load_section, migrate_section_once, save_section

GENERAL_CONFIG_FILES = {
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

_EXCLUDED_KEYS = {
    ("CLOUD_MUSIC", "cache_dir"),
    ("CLOUD_MUSIC", "default_volume"),
}


def _is_supported(value: object) -> bool:
    basic = (bool, int, float, str)
    if isinstance(value, basic):
        return True
    if isinstance(value, (tuple, list)):
        return all(isinstance(item, basic) for item in value)
    return False


def _capture_defaults() -> dict[str, dict[str, object]]:
    defaults: dict[str, dict[str, object]] = {}
    for dict_name in GENERAL_CONFIG_FILES:
        source = getattr(config_module, dict_name, None)
        if not isinstance(source, dict):
            continue
        defaults[dict_name] = {
            str(key): deepcopy(value)
            for key, value in source.items()
            if _is_supported(value) and (dict_name, str(key)) not in _EXCLUDED_KEYS
        }
    return defaults


_GENERAL_DEFAULTS = _capture_defaults()

_PREVIOUS_MOVEMENT_DEFAULTS = {
    "move_min_speed": (1.0, 7.5),
    "move_acceleration": (0.1, 0.75),
    "move_max_speed": (2.0, 15.0),
}


def get_general_setting_defaults() -> dict[str, dict[str, object]]:
    return deepcopy(_GENERAL_DEFAULTS)


def _read_literal_dicts(path: Path) -> dict[str, dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return {}
    result: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in _GENERAL_DEFAULTS:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            result[target.id] = value
    return result


def _legacy_general_values() -> dict[str, dict[str, object]]:
    values = get_general_setting_defaults()
    by_file: dict[str, list[str]] = {}
    for dict_name, rel_name in GENERAL_CONFIG_FILES.items():
        by_file.setdefault(rel_name, []).append(dict_name)
    for rel_name, dict_names in by_file.items():
        legacy = _read_literal_dicts(get_shared_config_path(rel_name))
        for dict_name in dict_names:
            source = legacy.get(dict_name)
            if not isinstance(source, dict):
                continue
            target = values.get(dict_name, {})
            for key in target:
                if key in source and _is_supported(source[key]):
                    target[key] = source[key]
    return values


def apply_general_values(values_by_dict: dict[str, dict]) -> None:
    for dict_name, items in values_by_dict.items():
        target = getattr(config_module, dict_name, None)
        if isinstance(target, dict) and isinstance(items, dict):
            target.update(items)


def save_general_values(values_by_dict: dict[str, dict]) -> dict:
    effective = load_section("general", _GENERAL_DEFAULTS)
    for dict_name, items in values_by_dict.items():
        if dict_name not in effective or not isinstance(items, dict):
            continue
        effective[dict_name].update(items)
    sparse = save_section("general", effective, _GENERAL_DEFAULTS)
    apply_general_values(effective)
    return sparse


def _migrate_previous_movement_defaults() -> None:
    overrides = get_section_overrides("general")
    behavior_overrides = overrides.get("BEHAVIOR")
    behavior_defaults = _GENERAL_DEFAULTS.get("BEHAVIOR")
    if not isinstance(behavior_overrides, dict) or not isinstance(behavior_defaults, dict):
        return

    legacy_keys = [
        key
        for key, previous_values in _PREVIOUS_MOVEMENT_DEFAULTS.items()
        if behavior_overrides.get(key) in previous_values
    ]
    if not legacy_keys:
        return

    effective = load_section("general", _GENERAL_DEFAULTS)
    effective_behavior = effective.get("BEHAVIOR")
    if not isinstance(effective_behavior, dict):
        return
    for key in legacy_keys:
        effective_behavior[key] = behavior_defaults[key]
    save_section("general", effective, _GENERAL_DEFAULTS)


def initialize_general_user_settings() -> None:
    migrate_section_once(
        "legacy_general_python_v1",
        "general",
        _legacy_general_values(),
        _GENERAL_DEFAULTS,
    )
    _migrate_previous_movement_defaults()
    apply_general_values(load_section("general", _GENERAL_DEFAULTS))
