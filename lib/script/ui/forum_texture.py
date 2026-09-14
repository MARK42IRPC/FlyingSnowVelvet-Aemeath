"""雪绒论坛卡片底纹：按卡片信息内容哈希稳定生成的几何瓷砖与渐变纹理。

底纹颜色由 `forum_style.forum_texture_color()` 给出：主题中性色（深色白 / 浅色黑）按
`FORUM_TEXTURE_TINT_RATIO` 掺上卡片 accent，所以每张卡的底纹带上自己的色调，不再只是
一层灰白；透明度取 `CARD_TEXTURE_ALPHA_RANGE`（偏深），线宽基准是
`CARD_TEXTURE_STROKE_BASE`，平铺边长取 `CARD_TEXTURE_TILES`（偏小）。种子是卡片信息
（id / 昵称 / 正文 / 配色 / 时间）的内容哈希而不是进程内随机的 `hash()`，所以同一张卡片
在任何一次运行、任何一次重绘里都会拿到同一套花纹。

花纹分四族：

- 几何间隙拼贴：`tiles` / `bricks` / `crosses`，瓷砖块之间留缝，靠错缝与留白制造拼贴感。
- 几何图形瓷砖平铺：`diamonds` / `hexes` / `triangles` / `dots` / `rings`，单一几何形按
  点阵或蜂窝平铺。
- 粗线几何：`polygons` / `solid_polygons` / `coarse_rings` / `coarse_crosses` / `hatch`，
  线宽不再是固定 1px；多边形边数、线宽、图形尺寸与斜纹间隔都由种子在范围里取值，再叠一层
  坐标噪波与焦点渐变，让平铺不至于像一张规整的网。
- 艺术渐变：`radial_dots` / `fade_stripes`，点半径或线透明度沿到焦点的距离渐变。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
import random

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

from config.scale import scale_px
from lib.core.forum import ForumMessage

#: 可供挑选的花纹；每一条都必须有对应的绘制函数（`_TEXTURE_PAINTERS`）。
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


def paint_card_texture(painter: QPainter, rect: QRectF, texture, color, *, radius=0.0) -> None:
    """在 rect 内平铺 texture；调用方负责给出主题中性色与卡片圆角。"""
    if texture is None:
        return
    area = QRectF(rect)
    base = QColor(color)
    if not base.isValid() or area.width() <= 0 or area.height() <= 0:
        return

    painter.save()
    try:
        clip = QPainterPath()
        clip.addRoundedRect(area, radius, radius)
        painter.setClipPath(clip, Qt.IntersectClip)
        layer = _texture_layer(
            texture,
            int(math.ceil(area.width())),
            int(math.ceil(area.height())),
            base.name(QColor.HexRgb),
            _device_scale(painter),
        )
        painter.drawImage(area.topLeft(), layer)
    finally:
        painter.restore()


def _device_scale(painter: QPainter) -> float:
    """取绘制目标的设备像素比，缓存位图要和屏幕像素对上才不糊。"""
    device = painter.device()
    ratio = getattr(device, "devicePixelRatioF", None)
    if not callable(ratio):
        return 1.0
    try:
        return round(max(1.0, float(ratio())), 2)
    except (TypeError, ValueError):
        return 1.0


@lru_cache(maxsize=CARD_TEXTURE_LAYER_CACHE)
def _texture_layer(texture, width: int, height: int, color: str, scale: float) -> QImage:
    """把底纹画进一张与卡片等大的透明位图；同一档底纹只画一次。"""
    width = max(1, int(width))
    height = max(1, int(height))
    pixel_scale = max(1.0, float(scale))
    image = QImage(
        int(math.ceil(width * pixel_scale)),
        int(math.ceil(height * pixel_scale)),
        QImage.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(pixel_scale)
    image.fill(Qt.transparent)
    tile, gap = _tile_geometry(texture)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(Qt.NoPen)
        _TEXTURE_PAINTERS[str(texture.pattern)](
            painter,
            QRectF(0.0, 0.0, float(width), float(height)),
            texture,
            QColor(color),
            tile,
            gap,
        )
    finally:
        painter.end()
    return image


def _tile_geometry(texture) -> tuple[int, int]:
    """把底纹的平铺尺寸与缝隙收敛到可用范围。"""
    tile = max(_MIN_TILE, int(texture.tile))
    return tile, max(1, min(int(texture.gap), tile - 1))


# ── 平铺与着色助手 ───────────────────────────────────────────────────

def _tile_starts(start: float, span: float, phase: float, step: float):
    """返回覆盖 [start, start + span] 的平铺起点；多铺一格保证边缘不留白。"""
    first = int(start + phase - step)
    last = int(start + span + step)
    return range(first, last + 1, int(step))


def _color(base: QColor, alpha: float) -> QColor:
    color = QColor(base)
    color.setAlpha(max(0, min(255, int(round(alpha)))))
    return color


def _pen(base: QColor, alpha: float) -> QPen:
    pen = QPen(_color(base, alpha))
    pen.setWidthF(CARD_TEXTURE_STROKE_BASE)
    pen.setJoinStyle(Qt.MiterJoin)
    return pen


def _coarse_pen(base: QColor, alpha: float, width: float) -> QPen:
    """粗线画笔：线宽由种子或噪波给出，但不会细过底纹线宽基准。"""
    pen = QPen(_color(base, alpha))
    pen.setWidthF(max(float(CARD_TEXTURE_STROKE_BASE), float(width)))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _stroke_width(texture, tile: float, factor: float = 1.0) -> float:
    """粗线线宽：规格里没给就按瓷砖边长推一个，再乘上噪波系数。"""
    base = float(texture.stroke) if texture.stroke else tile / 4.0
    return max(float(CARD_TEXTURE_STROKE_BASE), base * max(0.35, factor))


def _noise(column: int, row: int, salt: int) -> float:
    """坐标 + 种子的确定性噪波（0~1），让同一套花纹里的图形大小不整齐。"""
    mixed = (int(column) & 0xFFFF) * 0x9E3779B1
    mixed ^= (int(row) & 0xFFFF) * 0x85EBCA6B
    mixed ^= (int(salt) & 0xFFFFFFFF) * 0xC2B2AE35
    mixed &= 0xFFFFFFFF
    mixed ^= mixed >> 15
    mixed = (mixed * 0x2545F491) & 0xFFFFFFFF
    mixed ^= mixed >> 13
    return mixed / 0xFFFFFFFF


def _focus_distance(rect, texture, center_x: float, center_y: float) -> float:
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


def _cell_mix(texture, weight: float, noise: float) -> float:
    """渐变权重与坐标噪波对半混合：图形大小、线宽、透明度都走这一条。"""
    return max(0.0, min(1.0, 0.5 * (weight + noise)))


def _polygon_points(center_x, center_y, radius, sides, angle_deg=0.0) -> QPolygonF:
    """以 (center_x, center_y) 为心、radius 为外接圆半径的常规多边形。"""
    count = max(3, int(sides))
    step = 360.0 / count
    return QPolygonF(
        [
            QPointF(
                center_x + radius * math.cos(math.radians(angle_deg + step * index)),
                center_y + radius * math.sin(math.radians(angle_deg + step * index)),
            )
            for index in range(count)
        ]
    )


def _gradient_weight(distance: float, bias: float) -> float:
    """把归一化距离映成 0~1 的渐变权重；bias 变号就翻转渐变方向。"""
    return 1.0 - distance if bias >= 0 else distance


def _gradient_alpha(texture, weight: float) -> float:
    floor = CARD_TEXTURE_GRADIENT_FLOOR
    return texture.alpha * (floor + (1.0 - floor) * weight)


# ── 几何间隙拼贴 ─────────────────────────────────────────────────────

def _paint_tiles(painter, rect, texture, base, tile, gap):
    """圆角矩形瓷砖对齐平铺，瓷砖之间留缝。"""
    painter.setPen(_pen(base, texture.alpha))
    inset = gap / 2.0
    radius = max(1.0, gap)
    for y in _tile_starts(rect.top(), rect.height(), texture.origin_y, tile):
        for x in _tile_starts(rect.left(), rect.width(), texture.origin_x, tile):
            painter.drawRoundedRect(
                QRectF(x + inset, y + inset, tile - gap, tile - gap), radius, radius
            )


def _paint_bricks(painter, rect, texture, base, tile, gap):
    """错缝长砖：每行水平偏移半块，砖与砖之间留缝。"""
    painter.setPen(_pen(base, texture.alpha))
    brick_w = tile * 2
    half = gap / 2.0
    for row, y in enumerate(
        _tile_starts(rect.top(), rect.height(), texture.origin_y, tile)
    ):
        phase = texture.origin_x + (brick_w // 2 if row % 2 else 0)
        for x in _tile_starts(rect.left(), rect.width(), phase, brick_w):
            painter.drawRect(QRectF(x + half, y + half, brick_w - gap, tile - gap))


def _paint_crosses(painter, rect, texture, base, tile, gap):
    """十字瓷砖：加号形瓷砖逐格平铺，格与格之间留缝。"""
    painter.setPen(_pen(base, texture.alpha))
    arm = max(1.0, (tile - gap) / 3.0)
    for y in _tile_starts(rect.top(), rect.height(), texture.origin_y, tile):
        for x in _tile_starts(rect.left(), rect.width(), texture.origin_x, tile):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            painter.drawRect(
                QRectF(center_x - arm * 1.5, center_y - arm / 2.0, arm * 3.0, arm)
            )
            painter.drawRect(
                QRectF(center_x - arm / 2.0, center_y - arm * 1.5, arm, arm * 3.0)
            )


# ── 几何图形瓷砖平铺 ─────────────────────────────────────────────────

def _paint_diamonds(painter, rect, texture, base, tile, gap):
    """菱形瓷砖：旋转 45 度的方形瓷砖按点阵平铺。"""
    painter.setPen(_pen(base, texture.alpha))
    half = (tile - gap) / 2.0
    for y in _tile_starts(rect.top(), rect.height(), texture.origin_y, tile):
        for x in _tile_starts(rect.left(), rect.width(), texture.origin_x, tile):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            painter.drawPolygon(QPolygonF([
                QPointF(center_x, center_y - half),
                QPointF(center_x + half, center_y),
                QPointF(center_x, center_y + half),
                QPointF(center_x - half, center_y),
            ]))


def _paint_hexes(painter, rect, texture, base, tile, gap):
    """六边蜂窝瓷砖：三角点阵上平铺正六边形，边与边之间留缝。"""
    painter.setPen(_pen(base, texture.alpha))
    radius = (tile - gap) / 2.0
    column_step = radius * 1.5
    row_step = radius * math.sqrt(3.0)
    for column, x in enumerate(
        _tile_starts(rect.left(), rect.width(), texture.origin_x, column_step)
    ):
        phase = texture.origin_y + (row_step / 2.0 if column % 2 else 0.0)
        for y in _tile_starts(rect.top(), rect.height(), phase, row_step):
            painter.drawPolygon(QPolygonF([
                QPointF(
                    x + radius * math.cos(math.radians(step * 60)),
                    y + radius * math.sin(math.radians(step * 60)),
                )
                for step in range(6)
            ]))


def _paint_triangles(painter, rect, texture, base, tile, gap):
    """三角瓷砖：上下交替的等腰三角逐格平铺。"""
    painter.setPen(_pen(base, texture.alpha))
    inset = gap / 2.0
    side = tile - gap
    for row, y in enumerate(
        _tile_starts(rect.top(), rect.height(), texture.origin_y, tile)
    ):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            if (row + column) % 2:
                points = (
                    QPointF(x + inset, y + inset),
                    QPointF(x + inset + side, y + inset),
                    QPointF(x + inset + side / 2.0, y + inset + side),
                )
            else:
                points = (
                    QPointF(x + inset, y + inset + side),
                    QPointF(x + inset + side, y + inset + side),
                    QPointF(x + inset + side / 2.0, y + inset),
                )
            painter.drawPolygon(QPolygonF(list(points)))


def _paint_dots(painter, rect, texture, base, tile, gap):
    """圆点瓷砖：直径按缝隙比例缩放，再乘一层噪波让点阵有大小变化。"""
    painter.setPen(Qt.NoPen)
    base_radius = max(0.8, (tile - gap) / 6.0)
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            noise = _noise(column, row, texture.noise_salt)
            painter.setBrush(_color(base, texture.alpha * (0.7 + 0.6 * noise)))
            radius = max(0.8, base_radius * (0.6 + 0.8 * noise))
            painter.drawEllipse(
                QPointF(x + tile / 2.0, y + tile / 2.0), radius, radius
            )


def _paint_rings(painter, rect, texture, base, tile, gap):
    """同心圆环：相切的空心圆逐格平铺，半径带一点噪波。"""
    base_radius = (tile - gap) / 2.0
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            noise = _noise(column, row, texture.noise_salt)
            painter.setPen(_pen(base, texture.alpha * (0.7 + 0.6 * noise)))
            radius = base_radius * (0.72 + 0.4 * noise)
            painter.drawEllipse(
                QPointF(x + tile / 2.0, y + tile / 2.0), radius, radius
            )


# ── 艺术渐变 ─────────────────────────────────────────────────────────

def _paint_radial_dots(painter, rect, texture, base, tile, gap):
    """艺术渐变半径点阵：点半径与透明度沿到焦点的距离一起渐变。"""
    painter.setPen(Qt.NoPen)
    diameter = max(1.0, (tile - gap) / 4.0)
    focus_x = rect.left() + rect.width() * texture.focus_x
    focus_y = rect.top() + rect.height() * texture.focus_y
    spread = max(
        1.0,
        math.hypot(
            max(focus_x - rect.left(), rect.right() - focus_x),
            max(focus_y - rect.top(), rect.bottom() - focus_y),
        ),
    )
    for y in _tile_starts(rect.top(), rect.height(), texture.origin_y, tile):
        for x in _tile_starts(rect.left(), rect.width(), texture.origin_x, tile):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            distance = min(1.0, math.hypot(center_x - focus_x, center_y - focus_y) / spread)
            weight = _gradient_weight(distance, texture.bias)
            radius = max(0.6, diameter * (0.25 + 0.75 * weight))
            painter.setBrush(_color(base, _gradient_alpha(texture, weight)))
            painter.drawEllipse(QPointF(center_x, center_y), radius, radius)


def _paint_fade_stripes(painter, rect, texture, base, tile, gap):
    """艺术渐变斜线：等距斜线的透明度沿到焦点的投影距离渐变。"""
    del gap
    painter.save()
    try:
        painter.translate(rect.center())
        painter.rotate(texture.angle if texture.angle else 30)
        span = max(rect.width(), rect.height()) * 1.5 + tile
        offset = -span
        while offset <= span:
            weight = _gradient_weight(min(1.0, abs(offset) / span), texture.bias)
            painter.setPen(_pen(base, _gradient_alpha(texture, weight)))
            painter.drawLine(QPointF(-span, offset), QPointF(span, offset))
            offset += tile
    finally:
        painter.restore()


# ── 粗线几何 ─────────────────────────────────────────────────────────

def _paint_polygons(painter, rect, texture, base, tile, gap):
    """粗线多边形瓷砖：边数、倾角、线宽与半径都由种子打散。"""
    sides = max(3, int(texture.sides))
    spin = texture.angle if texture.angle else 90
    radius_base = (tile - gap) * 0.36
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            noise = _noise(column, row, texture.noise_salt)
            mix = _cell_mix(
                texture,
                _gradient_weight(
                    _focus_distance(rect, texture, center_x, center_y), texture.bias
                ),
                noise,
            )
            painter.setPen(
                _coarse_pen(
                    base,
                    _gradient_alpha(texture, mix),
                    _stroke_width(texture, tile, 0.45 + noise),
                )
            )
            painter.drawPolygon(
                _polygon_points(
                    center_x,
                    center_y,
                    max(1.0, radius_base * (0.55 + 0.7 * mix)),
                    sides,
                    spin + 18.0 * (noise - 0.5),
                )
            )


def _paint_solid_polygons(painter, rect, texture, base, tile, gap):
    """实心多边形点阵：填色多边形的大小随噪波与焦点渐变变化。"""
    painter.setPen(Qt.NoPen)
    sides = max(3, int(texture.sides))
    spin = texture.angle if texture.angle else 90
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            noise = _noise(column, row, texture.noise_salt)
            mix = _cell_mix(
                texture,
                _gradient_weight(
                    _focus_distance(rect, texture, center_x, center_y), texture.bias
                ),
                noise,
            )
            painter.setBrush(_color(base, _gradient_alpha(texture, mix)))
            painter.drawPolygon(
                _polygon_points(
                    center_x,
                    center_y,
                    max(1.0, (tile - gap) * (0.12 + 0.20 * mix)),
                    sides,
                    spin + 18.0 * (noise - 0.5),
                )
            )


def _paint_coarse_rings(painter, rect, texture, base, tile, gap):
    """粗线圆环：半径与线宽都带噪波，环与环之间允许有大小差。"""
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            noise = _noise(column, row, texture.noise_salt)
            mix = _cell_mix(
                texture,
                _gradient_weight(
                    _focus_distance(rect, texture, center_x, center_y), texture.bias
                ),
                noise,
            )
            radius = max(1.0, (tile - gap) * (0.18 + 0.26 * mix))
            painter.setPen(
                _coarse_pen(
                    base,
                    _gradient_alpha(texture, mix),
                    _stroke_width(texture, tile, 0.4 + noise),
                )
            )
            painter.drawEllipse(QPointF(center_x, center_y), radius, radius)


def _paint_coarse_crosses(painter, rect, texture, base, tile, gap):
    """粗线十字：十字按种子倾角旋转，臂长与臂宽随噪波变化。"""
    spin = texture.angle if texture.angle else 45
    for row, y in enumerate(_tile_starts(rect.top(), rect.height(), texture.origin_y, tile)):
        for column, x in enumerate(
            _tile_starts(rect.left(), rect.width(), texture.origin_x, tile)
        ):
            center_x = x + tile / 2.0
            center_y = y + tile / 2.0
            noise = _noise(column, row, texture.noise_salt)
            mix = _cell_mix(
                texture,
                _gradient_weight(
                    _focus_distance(rect, texture, center_x, center_y), texture.bias
                ),
                noise,
            )
            arm = max(1.0, (tile - gap) * 0.46 * (0.6 + 0.6 * mix))
            painter.setPen(
                _coarse_pen(
                    base,
                    _gradient_alpha(texture, mix),
                    _stroke_width(texture, tile, 0.45 + noise),
                )
            )
            painter.save()
            try:
                painter.translate(center_x, center_y)
                painter.rotate(spin + 20.0 * (noise - 0.5))
                painter.drawLine(QPointF(-arm, 0.0), QPointF(arm, 0.0))
                painter.drawLine(QPointF(0.0, -arm), QPointF(0.0, arm))
            finally:
                painter.restore()


def _paint_hatch(painter, rect, texture, base, tile, gap):
    """粗线条斜纹：种子倾角 + 范围内的随机间隔，线宽与透明度带噪波。"""
    del gap
    spacing = max(2.0, float(texture.spacing or tile))
    painter.save()
    try:
        painter.translate(rect.center())
        painter.rotate(texture.angle if texture.angle else 30)
        reach = max(rect.width(), rect.height()) * 1.6 + spacing
        offset = -reach
        index = 0
        while offset <= reach:
            noise = _noise(index, 0, texture.noise_salt)
            mix = _cell_mix(
                texture,
                _gradient_weight(min(1.0, abs(offset) / reach), texture.bias),
                noise,
            )
            painter.setPen(
                _coarse_pen(
                    base,
                    _gradient_alpha(texture, mix),
                    _stroke_width(texture, tile, 0.5 + noise),
                )
            )
            painter.drawLine(QPointF(-reach, offset), QPointF(reach, offset))
            offset += spacing * (0.55 + 0.9 * noise)
            index += 1
    finally:
        painter.restore()


_TEXTURE_PAINTERS = {
    "tiles": _paint_tiles,
    "bricks": _paint_bricks,
    "diamonds": _paint_diamonds,
    "hexes": _paint_hexes,
    "triangles": _paint_triangles,
    "crosses": _paint_crosses,
    "dots": _paint_dots,
    "rings": _paint_rings,
    "polygons": _paint_polygons,
    "solid_polygons": _paint_solid_polygons,
    "coarse_rings": _paint_coarse_rings,
    "coarse_crosses": _paint_coarse_crosses,
    "hatch": _paint_hatch,
    "radial_dots": _paint_radial_dots,
    "fade_stripes": _paint_fade_stripes,
}


__all__ = [
    "CARD_TEXTURE_ALPHA_RANGE",
    "CARD_TEXTURE_ANGLES",
    "CARD_TEXTURE_COARSE_PATTERNS",
    "CARD_TEXTURE_GAP_DIVISORS",
    "CARD_TEXTURE_PATTERNS",
    "CARD_TEXTURE_SIDES",
    "CARD_TEXTURE_SPACING_RANGE",
    "CARD_TEXTURE_STROKE_BASE",
    "CARD_TEXTURE_STROKE_DIVISORS",
    "CARD_TEXTURE_TILES",
    "CardTexture",
    "card_texture",
    "paint_card_texture",
    "texture_seed",
]
