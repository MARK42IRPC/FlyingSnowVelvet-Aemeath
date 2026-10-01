"""UI 点击粒子特效辅助函数

控件在点击位置发射对应粒子特效：
  左键 -> click（浅青色 Click 文字）
  右键 -> pink_click（浅粉色 Click 文字）

``publish_click_particle(widget, event)`` 是 QWidget 控件的取用口；不继承 QWidget 的
控件走 ``publish_click_particle_at(particle_id, x, y)``，自己已经知道全局坐标。
两者共用同一份"按钮 -> 粒子 ID"映射，避免两边各写一套。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt

from lib.core.event.center import Event, EventType, get_event_center

_BUTTON_PARTICLES = {
    Qt.LeftButton: "click",
    Qt.RightButton: "pink_click",
}


def publish_click_particle_at(particle_id: str, x: int, y: int) -> None:
    """在给定屏幕坐标发射点击粒子。"""
    if not particle_id:
        return
    get_event_center().publish(Event(EventType.PARTICLE_REQUEST, {
        "particle_id": particle_id,
        "area_type": "point",
        "area_data": (int(x), int(y)),
    }))


def publish_click_particle(widget, event) -> None:
    """根据鼠标按钮在点击全局坐标处发射对应粒子特效。"""
    particle_id = _BUTTON_PARTICLES.get(event.button())
    if not particle_id:
        return
    gpos = widget.mapToGlobal(event.pos())
    publish_click_particle_at(particle_id, gpos.x(), gpos.y())
