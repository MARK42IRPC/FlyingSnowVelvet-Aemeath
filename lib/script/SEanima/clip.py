"""SEanima 动画定义与目录解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from config.config import ANIMATION


DEFAULT_START_ANIMATION_FOLDER = "耶比_anima"
DEFAULT_EXIT_ANIMATION_FOLDER = "爱弥斯联合_anima"


def get_project_root() -> str:
    return os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
    )


def get_animation_root() -> str:
    return os.path.join(get_project_root(), "resc", "GIF", "SEanima")


def get_default_animation_folder_name(animation_type: str) -> str:
    if animation_type == "start":
        return DEFAULT_START_ANIMATION_FOLDER
    if animation_type == "exit":
        return DEFAULT_EXIT_ANIMATION_FOLDER
    return str(animation_type or "").strip()


def get_config_key_for_animation_type(animation_type: str) -> str:
    if animation_type == "start":
        return "start_animation_folder"
    if animation_type == "exit":
        return "exit_animation_folder"
    return f"{str(animation_type).strip()}_animation_folder"


def get_configured_animation_folder_name(animation_type: str, config: dict | None = None) -> str:
    values = config if isinstance(config, dict) else ANIMATION
    key = get_config_key_for_animation_type(animation_type)
    configured = str(values.get(key, "")).strip()
    return configured or get_default_animation_folder_name(animation_type)


def _iter_existing_animation_directories() -> list[str]:
    root = get_animation_root()
    if not os.path.isdir(root):
        return []
    return sorted(
        entry.name
        for entry in os.scandir(root)
        if entry.is_dir()
    )


def list_detected_animation_folder_names() -> list[str]:
    return [
        name for name in _iter_existing_animation_directories()
        if name.endswith("_anima")
    ]


def list_animation_folder_choices() -> list[str]:
    detected = list_detected_animation_folder_names()
    choices: list[str] = []
    for name in (
        DEFAULT_START_ANIMATION_FOLDER,
        DEFAULT_EXIT_ANIMATION_FOLDER,
        *detected,
    ):
        if name not in choices:
            choices.append(name)
    return choices


def resolve_animation_folder_path(folder_name: str) -> str:
    normalized = str(folder_name or "").strip()
    root = get_animation_root()
    if not normalized:
        return root

    direct_path = os.path.join(root, normalized)
    return direct_path


@dataclass(frozen=True)
class AnimationClip:
    animation_type: str
    folder_name: str
    folder_path: str
    fps: int
    enabled: bool


def resolve_animation_clip(animation_type: str, config: dict | None = None) -> AnimationClip:
    values = config if isinstance(config, dict) else ANIMATION
    folder_name = get_configured_animation_folder_name(animation_type, values)
    fps = int(values.get("frame_fps", 60) or 60)
    enabled = bool(values.get("start_exit_enabled", True))
    return AnimationClip(
        animation_type=str(animation_type),
        folder_name=folder_name,
        folder_path=resolve_animation_folder_path(folder_name),
        fps=max(1, fps),
        enabled=enabled,
    )
