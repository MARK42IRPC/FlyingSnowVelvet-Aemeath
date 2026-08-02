"""音乐音符粒子效果脚本 - 向上移动、轻微布朗运动、无重力的随机颜色音符形状粒子"""
import random

from lib.core.graphics.types import Color
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


@register_particle("music_note")
class MusicNoteParticleScript(BaseParticleScript):
    """音乐播放时的音符粒子"""

    PARTICLE_ID = "music_note"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (3, 5),           # 每次生成3-5个粒子
            'width_range':  (8, 12),         # 宽度8-12px
            'speed_range':  (42, 84),        # 向上移动速度 px/s（原 0.7x）
            'brownian':    12.6,             # 布朗运动强度 px/s（原 0.7x）
            'max_speed':   30.0,             # 水平最大漂移 px/s
            'life_range':  (1.1, 1.65),      # 寿命 1.1x
            'colors': [                        # 随机颜色
                Color(255, 182, 193),  # 粉色
                Color(173, 216, 230),  # 青色
                Color(255, 255, 255),  # 白色
                Color(255, 200, 100),  # 金色
                Color(200, 150, 255),  # 紫色
            ]
        }

    def create_particles(self, area_type: str, area_data: tuple) -> list:
        """在指定区域内生成音符粒子"""
        # 获取矩形区域
        if area_type == 'rect':
            x1, y1, x2, y2 = area_data
        else:
            # 如果是其他类型，使用默认范围
            x1, y1, x2, y2 = 0, 0, 100, 100

        count = random.randint(*self._config['count_range'])
        return [MusicNoteParticle(x1, y1, x2, y2, self._config) for _ in range(count)]


class MusicNoteParticle:
    """单个音乐音符粒子（简化为彩色方块）"""

    is_circle = False  # 不是圆形，使用自定义绘制

    def __init__(self, x1: float, y1: float, x2: float, y2: float, config: dict):
        # 在矩形范围内随机位置生成
        self.x = random.uniform(x1, x2)
        self.y = random.uniform(y1, y2)

        # 向上移动（负y速度）
        self.vy = -per_second_delta(random.uniform(*config['speed_range']))
        self.vx = 0

        # 尺寸：正方形
        self.size = random.randint(*config['width_range'])

        # 随机颜色
        self.color = random.choice(config['colors'])

        # 物理参数
        self.brownian = per_second_delta(config['brownian'])
        self.gravity = 0  # 无重力

        self.life = random.uniform(*config['life_range'])
        self.max_life = self.life
        self._max_vx = per_second_delta(config['max_speed'])

    def update(self):
        """物理更新：布朗运动 → 向上移动 → 生命衰减"""
        # 布朗运动（随机漂移）
        self.vx += random.uniform(-self.brownian, self.brownian)
        
        # 限制水平速度
        self.vx = max(-self._max_vx, min(self._max_vx, self.vx))
        
        # 向上移动
        self.x += self.vx
        self.y += self.vy
        
        # 生命衰减
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0

    def get_alpha(self) -> float:
        """获取透明度（0.0-1.0）"""
        return max(0, self.life / self.max_life)
