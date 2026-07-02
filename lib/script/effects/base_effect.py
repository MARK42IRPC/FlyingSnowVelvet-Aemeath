"""特效系统基类与常用时间函数。"""

from __future__ import annotations

from typing import Any, Dict, Tuple


_EFFECT_TICK_FPS = 20.0
_EFFECT_TICK_SECONDS = 1.0 / _EFFECT_TICK_FPS


def tick_seconds() -> float:
    """单个 tick 的秒数。"""
    return _EFFECT_TICK_SECONDS


def clamp01(value: float) -> float:
    """限制到 0..1 区间。"""
    return max(0.0, min(1.0, float(value)))


def ease_out_cubic(t: float) -> float:
    """快速起步、逐渐减速。"""
    t = clamp01(t)
    return 1.0 - ((1.0 - t) ** 3)


def ease_in_cubic(t: float) -> float:
    """缓慢起步、逐渐加速。"""
    t = clamp01(t)
    return t ** 3


class BaseEffectScript:
    """特效脚本基类。"""

    EFFECT_ID = None

    def __init__(self):
        self._config: Dict[str, Any] = {}

    def get_config(self) -> Dict[str, Any]:
        return self._config

    def set_config(self, config: Dict[str, Any]):
        self._config = dict(config or {})

    def create_effects(
        self,
        anchor_type: str,
        anchor_data: Tuple | None,
        effect_options: Dict[str, Any] | None = None,
        request_context: Dict[str, Any] | None = None,
    ) -> list:
        """
        创建运行中的特效实例列表。

        Args:
            anchor_type: 锚点类型，可扩展为 point/rect/circle/widget 等
            anchor_data: 锚点数据
            effect_options: 特效参数
            request_context: 请求上下文（如 overlay 偏移、项目根路径）
        """
        raise NotImplementedError("子类必须实现 create_effects 方法")

    def get_effect_id(self) -> str:
        return self.EFFECT_ID
