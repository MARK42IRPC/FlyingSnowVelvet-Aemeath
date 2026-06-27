"""粒子工具函数 - 提供便捷的粒子事件发布接口"""
from lib.core.event.center import get_event_center, EventType, Event


def spawn_particle(
    particle_id: str = 'scatter_fall',
    area_type: str = 'point',
    area_data=None,
    particle_options: dict | None = None,
):
    """
    发布粒子申请事件的便捷函数

    Args:
        particle_id: 粒子ID（如 'scatter_fall', 'heart'）
        area_type: 区域类型（'point', 'rect', 'circle'）
        area_data: 区域数据
            - 如果是 'rect': (x1, y1, x2, y2) 矩形范围
            - 如果是 'circle': (x, y, radius) 圆形范围
            - 如果是 'point': (x, y) 单点坐标
    """
    event = Event(EventType.PARTICLE_REQUEST, {
        'particle_id': particle_id,
        'area_type': area_type,
        'area_data': area_data,
        'particle_options': dict(particle_options or {}),
    })
    get_event_center().publish(event)


def spawn_particle_at_point(
    x: int,
    y: int,
    particle_id: str = 'scatter_fall',
    particle_options: dict | None = None,
):
    """
    在指定点生成粒子（便捷函数）

    Args:
        x: X 坐标
        y: Y 坐标
        particle_id: 粒子ID
    """
    spawn_particle(particle_id, 'point', (x, y), particle_options)


def spawn_particle_in_rect(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    particle_id: str = 'scatter_fall',
    particle_options: dict | None = None,
):
    """
    在矩形范围内生成粒子（便捷函数）

    Args:
        x1: 左上角 X 坐标
        y1: 左上角 Y 坐标
        x2: 右下角 X 坐标
        y2: 右下角 Y 坐标
        particle_id: 粒子ID
    """
    spawn_particle(particle_id, 'rect', (x1, y1, x2, y2), particle_options)


def spawn_particle_in_circle(
    x: int,
    y: int,
    radius: int,
    particle_id: str = 'scatter_fall',
    particle_options: dict | None = None,
):
    """
    在圆形范围内生成粒子（便捷函数）

    Args:
        x: 圆心 X 坐标
        y: 圆心 Y 坐标
        radius: 半径
        particle_id: 粒子ID
    """
    spawn_particle(particle_id, 'circle', (x, y, radius), particle_options)
