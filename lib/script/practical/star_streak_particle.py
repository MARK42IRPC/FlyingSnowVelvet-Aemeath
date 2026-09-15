"""星空划过粒子：沿一个方向布朗漂移、尾段淡出的白色 bloom 光点。

触发场景：办公页推理强度滑条被按住时，每个逻辑 tick（20 次/秒）从这个控件的位置召唤
一批光点，松开即停；光点往右漂出、尾段淡出，读起来像星空从滑块旁划过。

请求参数（`particle_options`，全部可省略）：

- `count_range`：本次召唤的随机个数 `(最小, 最大)`，默认 `(0, 1)`。
- `color`：`Color`、`(r, g, b)`、`(r, g, b, a)` 或 `#rrggbb`，默认白色。
- `bloom_range`：bloom 半径的随机范围（像素），默认 `(2, 4)`；内核半径取 bloom 的一部分，
  其余部分由渲染层铺同心光圈（见 `lib/core/graphics/visuals.py` 的圆形 bloom）。
- `duration_ticks`：持续时长（tick），默认 20（1 秒）。
- `fade_ticks`：其中最后多少 tick 用来淡出，默认 10；0 表示不淡出。
- `direction`：漂移方向 `(x, y)`，默认 `(1.0, 0.0)` 即往右。

`speed_range`、`brownian`、`brownian_drag`、`spread` 是同一批可选调参，默认值就是上面
描述的观感，调用方一般不需要给。
"""

from __future__ import annotations

import math
import random
from typing import Tuple

from lib.core.graphics.types import Color
from config.config import PARTICLES
from lib.script.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta


STAR_STREAK_PARTICLE_ID = "star_streak"

#: 默认参数：往右、每次 0~1 颗、bloom 半径 2~4px、持续 20 tick、其中 10 tick 淡出、白色。
DEFAULT_COUNT_RANGE = (0, 1)
DEFAULT_COLOR = Color(255, 255, 255)
DEFAULT_BLOOM_RANGE = (2, 4)
DEFAULT_DURATION_TICKS = 20
DEFAULT_FADE_TICKS = 10
DEFAULT_DIRECTION = (1.0, 0.0)

#: 观感调参：方向速度（px/秒）、每 tick 的随机抖动加速度（px/秒）与抖动阻尼，
#: 以及召唤点附近的随机散布（px），避免多颗光点叠成一条直线。
DEFAULT_SPEED_RANGE = (36.0, 84.0)
DEFAULT_BROWNIAN = 20.0
DEFAULT_BROWNIAN_DRAG = 0.86
DEFAULT_SPREAD = 2.0

#: 内核半径占 bloom 半径的比例；bloom 的外圈由渲染层的同心光圈补足。
CORE_RATIO = 0.5
CORE_MIN_RADIUS = 1.2


def _coerce_color(value: object) -> Color:
    """把请求参数里的颜色规格化成 `Color`，无法识别时回落到白色。"""
    if isinstance(value, Color):
        return value
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) in (6, 8):
            try:
                channels = [int(text[index:index + 2], 16) for index in range(0, len(text), 2)]
            except ValueError:
                return DEFAULT_COLOR
            return Color(*channels)
        return DEFAULT_COLOR
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            channels = [max(0, min(255, int(channel))) for channel in value[:4]]
        except (TypeError, ValueError):
            return DEFAULT_COLOR
        return Color(*channels)
    return DEFAULT_COLOR


def _coerce_range(
    value: object, fallback: tuple[float, float], *, minimum: float
) -> tuple[float, float]:
    """把请求参数里的区间规格化成 `(低, 高)`；顺序反了也能用。"""
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return fallback
        if low > high:
            low, high = high, low
        return max(minimum, low), max(minimum, high)
    return fallback


def _coerce_direction(value: object) -> tuple[float, float]:
    """规格化漂移方向；零向量回落到默认的往右。"""
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            dir_x, dir_y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return DEFAULT_DIRECTION
        length = math.hypot(dir_x, dir_y)
        if length > 1e-6:
            return dir_x / length, dir_y / length
    return DEFAULT_DIRECTION


def _coerce_int(value: object, fallback: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return fallback


def _fade_threshold_ratio() -> float:
    """渲染层开始淡出的生命比例（`PARTICLES.fade_threshold`），`life` 的下降要对齐它。"""
    try:
        value = float(PARTICLES.get("fade_threshold", 0.75) or 0.75)
    except (TypeError, ValueError):
        value = 0.75
    return max(1e-6, min(1.0, value))


def _sample_point(area_type: str, area_data: Tuple) -> tuple[float, float]:
    """在请求区域里取一个召唤点：点区域即原坐标，矩形与圆形区域内随机。"""
    if area_type == "rect":
        x1, y1, x2, y2 = area_data[:4]
        return random.uniform(min(x1, x2), max(x1, x2)), random.uniform(min(y1, y2), max(y1, y2))
    if area_type == "circle":
        cx, cy, radius = area_data[:3]
        distance = max(0.0, float(radius)) * math.sqrt(random.random())
        angle = random.uniform(0.0, math.tau)
        return cx + math.cos(angle) * distance, cy + math.sin(angle) * distance
    return float(area_data[0]), float(area_data[1])


@register_particle(STAR_STREAK_PARTICLE_ID)
class StarStreakParticleScript(BaseParticleScript):
    """按住推理滑条时召唤的星空光点。"""

    PARTICLE_ID = STAR_STREAK_PARTICLE_ID

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "count_range": DEFAULT_COUNT_RANGE,
            "color": DEFAULT_COLOR,
            "bloom_range": DEFAULT_BLOOM_RANGE,
            "duration_ticks": DEFAULT_DURATION_TICKS,
            "fade_ticks": DEFAULT_FADE_TICKS,
            "direction": DEFAULT_DIRECTION,
            "speed_range": DEFAULT_SPEED_RANGE,
            "brownian": DEFAULT_BROWNIAN,
            "brownian_drag": DEFAULT_BROWNIAN_DRAG,
            "spread": DEFAULT_SPREAD,
        }
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def request_config(self) -> dict:
        """把默认参数与本次请求参数合并成一份实际生效的配置。"""
        config = dict(self._config)
        options = dict(self._request_options)
        if "count_range" in options:
            config["count_range"] = _coerce_range(
                options["count_range"], DEFAULT_COUNT_RANGE, minimum=0.0
            )
        if "color" in options or "rgb" in options:
            config["color"] = _coerce_color(options.get("color", options.get("rgb")))
        if "bloom_range" in options:
            config["bloom_range"] = _coerce_range(
                options["bloom_range"], DEFAULT_BLOOM_RANGE, minimum=0.5
            )
        if "direction" in options:
            config["direction"] = _coerce_direction(options["direction"])
        if "speed_range" in options:
            config["speed_range"] = _coerce_range(
                options["speed_range"], DEFAULT_SPEED_RANGE, minimum=0.0
            )
        if "duration_ticks" in options:
            config["duration_ticks"] = _coerce_int(
                options["duration_ticks"], DEFAULT_DURATION_TICKS, minimum=1
            )
        if "fade_ticks" in options:
            config["fade_ticks"] = _coerce_int(options["fade_ticks"], DEFAULT_FADE_TICKS, minimum=0)
        for key in ("brownian", "brownian_drag", "spread"):
            if key in options:
                try:
                    config[key] = float(options[key])
                except (TypeError, ValueError):
                    continue
        return config

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        config = self.request_config()
        spread = max(0.0, float(config["spread"]))
        low, high = config["count_range"]
        count = int(low) if high <= low else random.randint(int(low), int(high))
        particles = []
        for _ in range(max(0, count)):
            x, y = _sample_point(area_type, area_data)
            particles.append(
                StarStreakParticle(
                    x + random.uniform(-spread, spread),
                    y + random.uniform(-spread, spread),
                    config,
                )
            )
        return particles


class StarStreakParticle:
    """单颗星空光点：沿方向匀速漂移、叠加布朗抖动，最后 `fade_ticks` 内淡出。

    渲染层按 `bloom`（bloom 半径）铺同心光圈、按 `size`（内核半径）画实心圆，两者都是
    像素半径。透明度由 `life` 表达：渲染层在 `life` 低于 `max_life * fade_threshold` 时
    按 `life` 线性淡出，所以常亮段把 `life` 钉在 1.0，淡出段再从 fade_threshold 线性降到 0，
    渲染层算出来的 alpha 就正好是「前 `duration - fade` tick 全亮、最后 `fade` tick 淡出」。
    """

    is_circle = True

    def __init__(self, x: float, y: float, config: dict) -> None:
        self.x = float(x)
        self.y = float(y)

        dir_x, dir_y = config["direction"]
        speed = per_second_delta(random.uniform(*config["speed_range"]))
        self._drift_x = dir_x * speed
        self._drift_y = dir_y * speed
        self._jitter_x = 0.0
        self._jitter_y = 0.0
        self._brownian = per_second_delta(max(0.0, float(config["brownian"])))
        self._drag = max(0.0, min(1.0, float(config["brownian_drag"])))

        self.bloom = max(0.0, float(random.uniform(*config["bloom_range"])))
        self.size = max(CORE_MIN_RADIUS, self.bloom * CORE_RATIO)
        self.color = config["color"]

        duration = max(1, int(config["duration_ticks"]))
        self._fade_ticks = max(0, min(duration, int(config["fade_ticks"])))
        self._fade_ratio = _fade_threshold_ratio()
        self._ticks_left = duration
        self.max_life = 1.0
        self.life = 1.0

    def update(self) -> None:
        if self._brownian > 0.0:
            self._jitter_x = (
                self._jitter_x + random.uniform(-self._brownian, self._brownian)
            ) * self._drag
            self._jitter_y = (
                self._jitter_y + random.uniform(-self._brownian, self._brownian)
            ) * self._drag
        self.x += self._drift_x + self._jitter_x
        self.y += self._drift_y + self._jitter_y
        self._ticks_left = max(0, self._ticks_left - 1)
        self.life = self._life_for(self._ticks_left)

    def _life_for(self, ticks_left: int) -> float:
        """常亮段 `life` 保持 1.0；淡出段的 `life` 让渲染层的 alpha 线性落到 0。"""
        if self._fade_ticks <= 0 or ticks_left >= self._fade_ticks:
            return 1.0
        return self._fade_ratio * (ticks_left / float(self._fade_ticks))

    @property
    def alive(self) -> bool:
        return self._ticks_left > 0
