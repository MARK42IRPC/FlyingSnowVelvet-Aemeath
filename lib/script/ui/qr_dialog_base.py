"""统一二维码窗口基类的产品侧导入面。"""

from lib.script.ui import render_bridge

render_bridge.install_tier_d_seams(draw_backend=render_bridge.create_draw_backend)
render_bridge.install_tier_d_presentation_host(render_bridge.presentation_host)

(BaseQrDialog,) = render_bridge.tier_d_reexports("qr_dialog_base", ("BaseQrDialog",))

__all__ = ["BaseQrDialog"]
