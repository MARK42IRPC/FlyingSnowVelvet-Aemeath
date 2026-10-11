"""统一二维码窗口基类（产品侧再导出垫片）。

`BaseQrDialog` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/qr_dialog_base.py`。产品子类（网易云 / 元宝 / QQ 群）
在**类定义时**就要拿到基类，因此这里显式解析一次；屏幕几何与居中落位经本垫片注入。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _floating_base():
    return render_bridge.create_floating_window_base()


# 落位解算（档位 1）与屏幕几何（档位 B）都经 render_bridge 注入：宿主不 import 档位 A，
# 也不自己写任何落位算术。
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
)
render_bridge.install_tier_d_hook("qr_dialog_floating_base", _floating_base)
render_bridge.install_tier_d_presentation_host(render_bridge.presentation_host)
render_bridge.install_tier_d_hook(
    "qr_dialog_centered_placement", render_bridge.centered_placement
)


def _centered_placement_reference(self_size, screen):
    """供守卫测试登记的构造器：垫片自己也只从 render_bridge 取居中落位。"""
    return render_bridge.centered_placement(self_size, screen)

(BaseQrDialog,) = render_bridge.tier_d_reexports("qr_dialog_base", ("BaseQrDialog",))

__all__ = ["BaseQrDialog"]
