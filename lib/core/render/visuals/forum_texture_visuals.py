"""论坛卡片底纹的后端中立规格：花纹候选、种子与几何参数。

底纹在 `lib/script/ui/forum_texture.py` 里被画成 `QImage`，但**底纹长得什么样**与 Qt 无关：
它是「按卡片信息的内容哈希挑一组花纹 + 透明度 + 平铺尺寸 + 相位 + 渐变焦点」的纯算术，
只有最后的绘制才用 `QPainter`。本模块因此承接这一半：

- 花纹候选与取值范围（`CARD_TEXTURE_*`）；
- `CardTexture`（一张卡片的底纹规格）；
- `texture_seed()` / `card_texture()`（内容哈希 → 规格）；
- 绘制期要用的一批几何助手（`tile_geometry` / `tile_starts` / `noise` / `focus_distance` /
  `cell_mix` / `stroke_width` / `gradient_weight` / `gradient_alpha`）。

绘制实现（`paint_card_texture` / `_texture_layer` / 各个 `_paint_*`）留在
`lib/script/ui/forum_texture.py`，它按原名字重新导出这里的一切，既有导入面不变。
本模块不 import Qt、不 import `lib.script`。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random

from config.scale import scale_px
from lib.core.forum import ForumMessage

CARD_TEXTURE_PATTERNS = (
    "tiles",
    "bricks",
    "diamonds",
    "hexes",
    "triangles",
    "crosses",
    "dots",
    "rings",
    "polygons",
    "solid_polygons",
    "coarse_rings",
    "coarse_crosses",
    "hatch",
    "radial_dots",
    "fade_stripes",
)
#: 平铺边长；取值偏小，整面墙的纹理更密。
CARD_TEXTURE_TILES = (12, 14, 16, 18, 22)
#: 用粗线画笔绘制的花纹；线宽来自 `CardTexture.stroke` 而不是固定 1px。
CARD_TEXTURE_COARSE_PATTERNS = (
    "polygons",
    "solid_polygons",
    "coarse_rings",
    "coarse_crosses",
    "hatch",
)
#: 粗线多边形边数：三角形到八边形；圆形交给 `coarse_rings`。
CARD_TEXTURE_SIDES = (3, 4, 5, 6, 7, 8)
#: 粗线线宽 = 瓷砖边长 // 这里的取值，越小线越粗（当前取值约是上一版的两倍粗）。
CARD_TEXTURE_STROKE_DIVISORS = (3, 4, 5, 6)
#: 斜纹间隔 = 瓷砖边长 × 这里的系数；系数偏小，斜纹更密。
CARD_TEXTURE_SPACING_RANGE = (0.45, 1.0)
#: 底纹线宽基准：细线族与粗线族的下限都取它（比早期版本的 1px 粗一倍）。
CARD_TEXTURE_STROKE_BASE = scale_px(2, min_abs=2)
#: 底纹透明度：偏深的取值，纹理在卡片底色上看得清楚，但不盖过正文。
CARD_TEXTURE_ALPHA_RANGE = (20, 34)
#: 瓷砖缝隙 = 瓷砖边长 // 这里的取值，保证不同 tile 下缝隙比例都在视野里。
CARD_TEXTURE_GAP_DIVISORS = (3, 4, 5)
#: 线条/点阵类花纹的倾角，几何瓷砖不旋转。
CARD_TEXTURE_ANGLES = (-45, -30, -15, 15, 30, 45)
CARD_TEXTURE_FOCUS_RANGE = (0.2, 0.8)
#: 渐变花纹最少画半径/透明度倍率，避免远端完全消失、近端糊成一块。
CARD_TEXTURE_GRADIENT_FLOOR = 0.45
#: 底纹层缓存条目上限：蜂窝这类花纹一张卡要画上千个图形，缓存成位图后重绘只做一次
#: blit。条目按（花纹 + 卡片尺寸 + 主题色 + 设备像素比）区分，一屏卡片够用。
#: 底纹颜色带卡片色调后，缓存键多了一维配色色调，条目上限相应放宽。
CARD_TEXTURE_LAYER_CACHE = 32

_MIN_TILE = scale_px(12, min_abs=10)
_TEXTURE_SEED_FIELDS = ("id", "nickname", "content", "accent", "created_at")

@dataclass(frozen=True, slots=True)
class CardTexture:
    """一张卡片的底纹规格：花纹、透明度、平铺尺寸、相位与渐变焦点。"""

    pattern: str
    alpha: int
    tile: int
    gap: int = 3
    angle: int = 0
    origin_x: int = 0
    origin_y: int = 0
    focus_x: float = 0.5
    focus_y: float = 0.4
    bias: float = 1.0
    #: 粗线几何专用：边数、线宽、斜纹间隔与噪波盐值，全部来自同一个卡片种子。
    sides: int = 5
    stroke: float = 0.0
    spacing: int = 0
    noise_salt: int = 0


def texture_seed(message) -> int:
    """卡片信息的内容哈希；内容变了底纹就跟着变，跨进程仍然稳定。"""
    if isinstance(message, ForumMessage):
        payload = "\x00".join(
            str(getattr(message, field, "") or "") for field in _TEXTURE_SEED_FIELDS
        )
    else:
        payload = f"id\x00{message}"
    return int.from_bytes(
        hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest(),
        "big",
    )


def card_texture(message) -> CardTexture:
    """按卡片信息内容哈希挑一组底纹；同一张卡片每次重绘都完全一致。"""
    rng = random.Random(texture_seed(message))
    low, high = CARD_TEXTURE_ALPHA_RANGE
    tile = scale_px(rng.choice(CARD_TEXTURE_TILES), min_abs=_MIN_TILE)
    return CardTexture(
        pattern=rng.choice(CARD_TEXTURE_PATTERNS),
        alpha=rng.randint(low, high),
        tile=tile,
        gap=max(1, tile // rng.choice(CARD_TEXTURE_GAP_DIVISORS)),
        angle=rng.choice(CARD_TEXTURE_ANGLES),
        origin_x=rng.randrange(tile),
        origin_y=rng.randrange(tile),
        focus_x=rng.uniform(*CARD_TEXTURE_FOCUS_RANGE),
        focus_y=rng.uniform(*CARD_TEXTURE_FOCUS_RANGE),
        bias=rng.uniform(-1.0, 1.0),
        sides=rng.choice(CARD_TEXTURE_SIDES),
        stroke=max(1.0, tile / rng.choice(CARD_TEXTURE_STROKE_DIVISORS)),
        spacing=max(2, int(tile * rng.uniform(*CARD_TEXTURE_SPACING_RANGE))),
        noise_salt=rng.randrange(1, 1 << 30),
    )


def tile_geometry(texture) -> tuple[int, int]:
    """把底纹的平铺尺寸与缝隙收敛到可用范围。"""
    tile = max(_MIN_TILE, int(texture.tile))
    return tile, max(1, min(int(texture.gap), tile - 1))


def tile_starts(start: float, span: float, phase: float, step: float):
    """返回覆盖 [start, start + span] 的平铺起点；多铺一格保证边缘不留白。"""
    first = int(start + phase - step)
    last = int(start + span + step)
    return range(first, last + 1, int(step))

def stroke_width(texture, tile: float, factor: float = 1.0) -> float:
    """粗线线宽：规格里没给就按瓷砖边长推一个，再乘上噪波系数。"""
    base = float(texture.stroke) if texture.stroke else tile / 4.0
    return max(float(CARD_TEXTURE_STROKE_BASE), base * max(0.35, factor))


def noise(column: int, row: int, salt: int) -> float:
    """坐标 + 种子的确定性噪波（0~1），让同一套花纹里的图形大小不整齐。"""
    mixed = (int(column) & 0xFFFF) * 0x9E3779B1
    mixed ^= (int(row) & 0xFFFF) * 0x85EBCA6B
    mixed ^= (int(salt) & 0xFFFFFFFF) * 0xC2B2AE35
    mixed &= 0xFFFFFFFF
    mixed ^= mixed >> 15
    mixed = (mixed * 0x2545F491) & 0xFFFFFFFF
    mixed ^= mixed >> 13
    return mixed / 0xFFFFFFFF

def focus_distance(rect, texture, center_x: float, center_y: float) -> float:
    """归一化到渐变焦点的距离（0~1），作为噪波之外的第二个变化来源。"""
    focus_x = rect.left() + rect.width() * texture.focus_x
    focus_y = rect.top() + rect.height() * texture.focus_y
    spread = max(
        1.0,
        math.hypot(
            max(focus_x - rect.left(), rect.right() - focus_x),
            max(focus_y - rect.top(), rect.bottom() - focus_y),
        ),
    )
    return min(1.0, math.hypot(center_x - focus_x, center_y - focus_y) / spread)


def cell_mix(texture, weight: float, noise: float) -> float:
    """渐变权重与坐标噪波对半混合：图形大小、线宽、透明度都走这一条。"""
    return max(0.0, min(1.0, 0.5 * (weight + noise)))


def gradient_weight(distance: float, bias: float) -> float:
    """把归一化距离映成 0~1 的渐变权重；bias 变号就翻转渐变方向。"""
    return 1.0 - distance if bias >= 0 else distance


def gradient_alpha(texture, weight: float) -> float:
    floor = CARD_TEXTURE_GRADIENT_FLOOR
    return texture.alpha * (floor + (1.0 - floor) * weight)


__all__ = [
    "CARD_TEXTURE_ALPHA_RANGE",
    "CARD_TEXTURE_ANGLES",
    "CARD_TEXTURE_COARSE_PATTERNS",
    "CARD_TEXTURE_FOCUS_RANGE",
    "CARD_TEXTURE_GAP_DIVISORS",
    "CARD_TEXTURE_GRADIENT_FLOOR",
    "CARD_TEXTURE_LAYER_CACHE",
    "CARD_TEXTURE_PATTERNS",
    "CARD_TEXTURE_SIDES",
    "CARD_TEXTURE_SPACING_RANGE",
    "CARD_TEXTURE_STROKE_BASE",
    "CARD_TEXTURE_STROKE_DIVISORS",
    "CARD_TEXTURE_TILES",
    "CardTexture",
    "card_texture",
    "cell_mix",
    "focus_distance",
    "gradient_alpha",
    "gradient_weight",
    "noise",
    "stroke_width",
    "texture_seed",
    "tile_geometry",
    "tile_starts",
]
