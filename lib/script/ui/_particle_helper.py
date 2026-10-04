"""UI 点击粒子特效辅助。

控件在点击位置发出对应粒子特效：
  左键 -> click（浅色 Click 粒子）
  右键 -> pink_click（浅粉色 Click 粒子）

``publish_click_particle(widget, event)`` 从 QWidget 控件取得窗口，不继承 QWidget 的
控件用 ``publish_click_particle_at(particle_id, x, y)``（已经知道全局坐标）。
两者共享同一份"按钮 -> 粒子 ID"映射（``visuals/controls.py`` 的 ``BUTTON_PARTICLES``），
按钮翻译经 ``render_bridge.pointer_button_name``，本模块因此不再 import Qt。
"""
from __future__ import annotations

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.visuals.controls import BUTTON_PARTICLES
from lib.script.ui.render_bridge import pointer_button_name


def publish_click_particle_at(particle_id: str, x: int, y: int) -> None:
    """在给定屏幕坐标发出粒子。"""
    if not particle_id:
        return
    get_event_center().publish(Event(EventType.PARTICLE_REQUEST, {
        "particle_id": particle_id,
        "area_type": "point",
        "area_data": (int(x), int(y)),
    }))


def publish_click_particle(widget, event) -> None:
    """根据鼠标按钮在点击的全局坐标处发出对应粒子特效。"""
    particle_id = BUTTON_PARTICLES.get(pointer_button_name(event))
    if not particle_id:
        return
    gpos = widget.mapToGlobal(event.pos())
    publish_click_particle_at(particle_id, gpos.x(), gpos.y())
