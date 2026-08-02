"""粒子基类脚本"""
from typing import Tuple, Dict, Any


_LEGACY_PARTICLE_FPS = 60.0
_PARTICLE_TICK_FPS = 20.0
_PARTICLE_STEP_SCALE = _LEGACY_PARTICLE_FPS / _PARTICLE_TICK_FPS
_PARTICLE_TICK_SECONDS = 1.0 / _PARTICLE_TICK_FPS


def frame_delta(value: float) -> float:
    """将旧的 60fps 每帧线性增量换算为每 tick 增量。"""
    return float(value) * _PARTICLE_STEP_SCALE


def frame_ratio(value: float) -> float:
    """将旧的 60fps 每帧乘法比例换算为每 tick 比例。"""
    return float(value) ** _PARTICLE_STEP_SCALE


def frames_to_ticks(value: float) -> float:
    """将旧的 60fps 帧数寿命换算为 tick 数。"""
    return float(value) / _PARTICLE_STEP_SCALE


def per_second_delta(value: float) -> float:
    """将按秒速度/位移换算为每 tick 增量。"""
    return float(value) / _PARTICLE_TICK_FPS


def tick_seconds() -> float:
    """单个 tick 的秒数。"""
    return _PARTICLE_TICK_SECONDS


class BaseParticleScript:
    """粒子效果基类 - 所有粒子脚本都继承此类"""

    # 粒子ID（唯一标识符）
    PARTICLE_ID = None

    def __init__(self):
        """初始化粒子脚本"""
        self._config = {}

    def get_config(self) -> Dict[str, Any]:
        """获取粒子配置"""
        return self._config

    def set_config(self, config: Dict[str, Any]):
        """设置粒子配置"""
        self._config = config

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """
        创建粒子实例

        Args:
            area_type: 区域类型 'rect' 或 'circle'
            area_data: 区域数据
                - 如果是 'rect': (x1, y1, x2, y2) 矩形范围
                - 如果是 'circle': (x, y, radius) 圆形范围

        Returns:
            粒子实例列表
        """
        raise NotImplementedError("子类必须实现 create_particles 方法")

    def get_particle_id(self) -> str:
        """获取粒子ID"""
        return self.PARTICLE_ID
