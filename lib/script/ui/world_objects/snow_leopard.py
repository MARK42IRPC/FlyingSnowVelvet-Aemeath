"""雪豹世界对象（产品侧装配垫片）。

`SnowLeopard` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/snow_leopard.py`。本模块按原名再导出，并把宿主需要的
产品侧声音与粒子入口经共享 seam 注入（`world_object_sound` / `particle_spawn` /
`screen_geometry`）；既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _sound(name: str):
    from lib.script.ui.world_objects import _sounds

    return _sounds.make_sound(name)


def _particle(x, y, particle_id) -> None:
    from lib.core.particle_utils import spawn_particle_at_point

    spawn_particle_at_point(x, y, particle_id)


render_bridge.install_tier_d_seams(draw_backend=render_bridge.create_draw_backend)
render_bridge.install_world_object_seams(
    sound=_sound,
    particle_spawn=_particle,
    screen_geometry=render_bridge.screen_rect_for_point,
)

(SnowLeopard,) = render_bridge.tier_d_reexports("snow_leopard", ("SnowLeopard",))

__all__ = ["SnowLeopard"]
