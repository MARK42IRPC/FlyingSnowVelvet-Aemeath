"""AI 设置面板「外壳」（产品侧再导出垫片）。

`AISettingsShellMixin` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_shell.py`。这里按原名再导出（经
`render_bridge.tier_d_reexports()` 解析，垫片本身不得静态 import 档位 D），继承面与导入面不变；
硬件水印载荷 / AI 取值 / 信息框 / 点击粒子经 `tier_d_seams` 由本垫片注入。
"""

from __future__ import annotations

from lib.script.app.startup_probe import (
    load_saved_watermark_payload as _load_saved_watermark_payload,
)
from lib.script.ui import render_bridge
from lib.script.ui._particle_helper import publish_click_particle as _publish_click_particle
from lib.script.ui.ai_settings_storage import (
    apply_ai_runtime as _apply_ai_runtime,
    load_ai_values as _load_ai_values,
    save_ai_values as _save_ai_values,
)
from lib.script.ui.confirm_dialog import show_message as _show_message

(AISettingsShellMixin,) = render_bridge.tier_d_reexports(
    "ai_settings_shell", ("AISettingsShellMixin",)
)


def _info_message_hook(parent, message: str):
    return _show_message(parent, title="提示", text=message)


def _click_particle_hook(widget, event) -> None:
    return _publish_click_particle(widget, event)


def _saved_watermark_payload_hook():
    return _load_saved_watermark_payload()


render_bridge.install_tier_d_seams(
    ui_font=render_bridge.ui_font, draw_backend=render_bridge.create_draw_backend
)
render_bridge.install_tier_d_hook("info_message", _info_message_hook)
render_bridge.install_tier_d_hook("click_particle", _click_particle_hook)
render_bridge.install_tier_d_hook("saved_watermark_payload", _saved_watermark_payload_hook)
render_bridge.install_tier_d_hook(
    "ai_storage_load", lambda *a, **k: _load_ai_values(*a, **k)
)
render_bridge.install_tier_d_hook(
    "ai_storage_save", lambda *a, **k: _save_ai_values(*a, **k)
)
render_bridge.install_tier_d_hook(
    "ai_storage_apply_runtime", lambda *a, **k: _apply_ai_runtime(*a, **k)
)

__all__ = ["AISettingsShellMixin"]
