"""音响右键菜单右侧的动感响应频段滑条（竖向、单块）。

外观与共享横向滑条（音乐进度条 / 音量滑条）用同一套 token：黑框 + 青框 + 粉色底，
选中的频段用深青色填充、单块手柄用深粉色；刻度沿用同一批小刻度。拖动时只需要一个块：
块所在位置就是中心频率，频段固定为中心 ±10Hz，中心按 10Hz 吸附（换算见
``lib/core/speaker_band.py``）。Qt 与 DX 两个后端执行同一批命令，几何也由这里唯一决定。
"""

from __future__ import annotations

from dataclasses import dataclass

from config.scale import scale_px
from lib.core.layer import Layer
from lib.core.speaker_band import band_center_ratio, band_ratios

from .commands import DrawBatch, RectCommand
from .media_panel_visuals import PROGRESS_PANEL_HEIGHT
from .palette import COLORS, UI_THEME
from .panel_visuals import (
    SLIDER_HANDLE_ASPECT,
    inset_rect,
    panel_frame_commands,
    panel_inset,
)
from .types import Rect, Size

#: 滑条厚度与横向滑条一致，保证两个滑条看起来是同一套控件。
BAND_SLIDER_WIDTH = PROGRESS_PANEL_HEIGHT
#: 滑条与菜单主体之间的水平间隙。
BAND_SLIDER_GAP = scale_px(4, min_abs=1)
#: 刻度数量（只作参照；吸附粒度由 ``speaker_band.BAND_SNAP_HZ`` 决定）。
BAND_TICK_COUNT = 10
#: 命中判定时向滑条左右各放宽的逻辑像素，细滑条也好点。
BAND_HIT_PADDING = scale_px(4, min_abs=1)


@dataclass(frozen=True, slots=True)
class BandSliderVisual:
    """竖向频段滑条的几何与绘制命令。

    ``band_rect`` 是固定 ±半宽的频段填充，``center_rect`` 是拖动的单块手柄——
    手柄画在频段正中，两者一起把「一块 ±10Hz」表达清楚。
    """

    size: Size
    track_rect: Rect
    band_rect: Rect
    center_rect: Rect
    batch: DrawBatch

    @property
    def handle_rects(self) -> tuple[Rect, ...]:
        return (self.center_rect,)


def band_track_rect(
    *,
    x: int = 0,
    y: int = 0,
    width: int | None = None,
    height: int,
) -> Rect:
    """返回滑条的内轨道矩形（去掉最外层两圈边框）。"""
    width = BAND_SLIDER_WIDTH if width is None else max(1, int(width))
    border = panel_inset() * 2
    total_height = max(1, int(height))
    return Rect(
        int(x) + border,
        int(y) + border,
        max(1, width - border * 2),
        max(1, total_height - border * 2),
    )


def band_ratio_at(track_rect: Rect, y: float) -> float:
    """把滑条内的纵坐标换算成 0.0-1.0 比例；顶端为 1.0（高频）。"""
    height = float(track_rect.height)
    if height <= 0:
        return 0.0
    bottom = float(track_rect.y) + height
    return max(0.0, min(1.0, (bottom - float(y)) / height))


def band_position(track_rect: Rect, ratio: float) -> float:
    """比例换算成滑条内的纵坐标（``band_ratio_at`` 的逆运算）。"""
    value = max(0.0, min(1.0, float(ratio)))
    return float(track_rect.y) + float(track_rect.height) * (1.0 - value)


def band_handle_commands(
    center_y: float,
    track_rect: Rect,
    fill,
    *,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> tuple[list[object], Rect]:
    """横向块（共享纵向把手旋转 90 度），返回 ``(命令, 矩形)``。"""
    width = max(2.0, float(track_rect.width))
    height = max(2.0, float(round(width / max(1.0, float(SLIDER_HANDLE_ASPECT)))))
    top = float(center_y) - height / 2.0
    top = max(
        float(track_rect.y),
        min(float(track_rect.y) + float(track_rect.height) - height, top),
    )
    rect = Rect(float(track_rect.x), top, width, height)
    return [RectCommand(rect, fill=fill, alpha=alpha, layer=layer, z=z)], rect


def band_hit_test(track_rect: Rect, handle_rect: Rect, x: float, y: float) -> str:
    """返回命中的动作（``band``）；不在滑条内返回空串。

    只有一个块，整条滑条都可按下：按哪儿都把块拖到哪儿。
    """
    del handle_rect
    left = float(track_rect.x) - BAND_HIT_PADDING
    right = float(track_rect.x) + float(track_rect.width) + BAND_HIT_PADDING
    if not (left <= float(x) <= right):
        return ""
    # 只放宽横向：滑条本身够高，往下放宽会压到结果框的第一行。
    top = float(track_rect.y)
    bottom = top + float(track_rect.height)
    if not (top <= float(y) <= bottom):
        return ""
    return "band"


def build_band_slider_visual(
    *,
    band: tuple[float, float],
    height: int,
    x: int = 0,
    y: int = 0,
    width: int | None = None,
    ticks: int = BAND_TICK_COUNT,
    layer: int = int(Layer.PET_UI),
    z: int = 0,
    alpha: float = 1.0,
) -> BandSliderVisual:
    """构建竖向频段滑条：外壳 -> 频段填充 -> 刻度 -> 单块手柄 -> 外框。"""
    width = BAND_SLIDER_WIDTH if width is None else max(1, int(width))
    total_height = max(1, int(height))
    origin_x = int(x)
    origin_y = int(y)
    inset = panel_inset()
    border = inset * 2
    outer = Rect(origin_x, origin_y, width, total_height)

    low, high = band_ratios(band)
    center = band_center_ratio(band)

    commands: list[object] = [
        RectCommand(outer, fill=COLORS["black"], alpha=alpha, layer=layer, z=z),
        RectCommand(
            inset_rect(outer, inset), fill=COLORS["cyan"], alpha=alpha, layer=layer, z=z + 1,
        ),
        RectCommand(
            inset_rect(outer, border), fill=COLORS["pink"], alpha=alpha, layer=layer, z=z + 2,
        ),
    ]

    track = band_track_rect(
        x=origin_x, y=origin_y, width=width, height=total_height,
    )
    top_y = band_position(track, high)
    bottom_y = band_position(track, low)
    fill_top = max(float(track.y), min(top_y, bottom_y))
    fill_height = int(round(abs(bottom_y - top_y)))
    if fill_height > 0:
        commands.append(RectCommand(
            Rect(track.x, fill_top, track.width, fill_height),
            fill=UI_THEME["deep_cyan"],
            alpha=alpha,
            layer=layer,
            z=z + 3,
        ))

    count = max(0, int(ticks))
    if count:
        tick_height = scale_px(1, min_abs=1)
        tick_width = max(2, int(round(track.width * 0.4)))
        tick_x = track.x + (track.width - tick_width) // 2
        for index in range(1, count + 1):
            tick_y = track.y + int(round(index * track.height / count))
            tick_y = min(tick_y, track.y + track.height - tick_height)
            commands.append(RectCommand(
                Rect(tick_x, tick_y, tick_width, tick_height),
                fill=COLORS["black"],
                alpha=0.55 * alpha,
                layer=layer,
                z=z + 3,
            ))

    handle_commands, center_rect = band_handle_commands(
        band_position(track, center), track, UI_THEME["deep_pink"],
        layer=layer, z=z + 4, alpha=alpha,
    )
    commands.extend(handle_commands)
    commands.extend(panel_frame_commands(outer, inset=inset, layer=layer, z=z + 6, alpha=alpha))

    return BandSliderVisual(
        Size(width, total_height),
        track,
        Rect(track.x, fill_top, track.width, max(0, fill_height)),
        center_rect,
        DrawBatch(tuple(commands)),
    )


__all__ = [
    "BAND_HIT_PADDING",
    "BAND_SLIDER_GAP",
    "BAND_SLIDER_WIDTH",
    "BAND_TICK_COUNT",
    "BandSliderVisual",
    "band_handle_commands",
    "band_hit_test",
    "band_position",
    "band_ratio_at",
    "band_track_rect",
    "build_band_slider_visual",
]
