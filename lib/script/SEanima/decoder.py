"""SEanima 帧解码与播放计划。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image

from config.config import ANIMATION
from lib.script.SEanima.clip import AnimationClip
from lib.script.SEanima.effects import compose_exit_shadow_frame, resolve_exit_shadow_metrics

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS

_ANIMATION_DEFAULT_SIZE_MULTIPLIER = 2.0


@dataclass(frozen=True)
class AnimationPlaybackPlan:
    clip: AnimationClip
    files: list[str]
    base_w: int
    base_h: int
    target_w: int
    target_h: int
    visual_anchor_x: float
    visual_anchor_y: float
    shadow_metrics: dict | None


def select_playback_files(
    files: list[str], *, target_seconds: float | None = None, fps: int, speed_multiplier: float | None = None
) -> list[str]:
    """按目标时长或倍速均匀抽帧；减速时保留完整帧序列。"""
    source = list(files)
    if speed_multiplier is not None:
        speed = max(0.5, min(2.0, float(speed_multiplier)))
        if speed <= 1.0:
            return source
        target_seconds = len(source) / max(1, int(fps)) / speed
    if len(source) <= 1 or target_seconds is None or float(target_seconds) <= 0:
        return source
    target_count = min(len(source), max(1, int(round(float(target_seconds) * max(1, int(fps))))))
    if target_count >= len(source):
        return source
    if target_count == 1:
        return [source[0]]
    return [source[int(round(index * (len(source) - 1) / (target_count - 1)))] for index in range(target_count)]


def playback_duration_seconds(
    frame_count: int,
    *,
    fps: int,
    target_seconds: float | None = None,
    speed_multiplier: float | None = None,
) -> float:
    """Return the actual duration after applying the same speed rule."""
    count = max(0, int(frame_count))
    rate = max(1, int(fps))
    if count <= 0:
        return 0.0
    if speed_multiplier is not None:
        speed = max(0.5, min(2.0, float(speed_multiplier)))
        return (count / rate) / speed
    if target_seconds is not None and float(target_seconds) > 0:
        count = min(count, max(1, int(round(float(target_seconds) * rate))))
    return count / float(rate)


def scan_animation_frame_files(folder_path: str) -> list[str]:
    if not os.path.isdir(folder_path):
        return []
    return sorted(
        file_name
        for file_name in os.listdir(folder_path)
        if file_name.endswith((".png", ".webp"))
    )


def build_playback_plan(clip: AnimationClip) -> AnimationPlaybackPlan | None:
    files = scan_animation_frame_files(clip.folder_path)
    if not files:
        return None

    first_frame_path = os.path.join(clip.folder_path, files[0])
    with Image.open(first_frame_path) as probe:
        orig_w, orig_h = probe.size

    pet_w, pet_h = ANIMATION.get("pet_size", (150, 150))
    target_box_w = max(1, int(round(max(1, int(pet_w)) * _ANIMATION_DEFAULT_SIZE_MULTIPLIER)))
    target_box_h = max(1, int(round(max(1, int(pet_h)) * _ANIMATION_DEFAULT_SIZE_MULTIPLIER)))
    fit_scale = min(target_box_w / max(1, orig_w), target_box_h / max(1, orig_h))
    base_w = max(1, int(orig_w * fit_scale))
    base_h = max(1, int(orig_h * fit_scale))

    target_w = base_w
    target_h = base_h
    visual_anchor_x = target_w / 2.0
    visual_anchor_y = target_h / 2.0
    shadow_metrics = None

    if clip.animation_type == "exit":
        shadow_metrics = resolve_exit_shadow_metrics(base_w, base_h)
        if shadow_metrics is not None:
            target_w = int(shadow_metrics["canvas_w"])
            target_h = int(shadow_metrics["canvas_h"])
            visual_anchor_x = float(shadow_metrics["frame_x"]) + (base_w / 2.0)
            visual_anchor_y = float(shadow_metrics["frame_y"]) + (base_h / 2.0)

    return AnimationPlaybackPlan(
        clip=clip,
        files=files,
        base_w=base_w,
        base_h=base_h,
        target_w=target_w,
        target_h=target_h,
        visual_anchor_x=visual_anchor_x,
        visual_anchor_y=visual_anchor_y,
        shadow_metrics=shadow_metrics,
    )


def decode_frame_to_bytes(frame_path: str, plan: AnimationPlaybackPlan) -> bytes:
    with Image.open(frame_path) as img:
        img = img.convert("RGBA").resize((plan.base_w, plan.base_h), _LANCZOS)
        if plan.shadow_metrics is not None:
            img = compose_exit_shadow_frame(img, plan.shadow_metrics)
        return img.tobytes()
