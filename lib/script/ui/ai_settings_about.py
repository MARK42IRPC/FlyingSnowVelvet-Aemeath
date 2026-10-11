"""AI 设置面板「支持作者 / 贡献者」两页（产品侧再导出垫片）。

两页的视图 / 控制器 / 描述已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_about.py`。这里按原名再导出；面板与外壳既有的
`from ... import X` 与 `_about.X` 两种取用方式都保留（模块级别名属性与真名同源）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

_names = (
    "SPONSOR_AUTHOR_URL",
    "_CONFIG_FONT_SIZE",
    "_ContributionCardButton",
    "build_contribution_list_panel",
    "build_sponsor_author_panel",
    "contribution_list_stylesheet",
    "open_contribution_link",
    "open_sponsor_author_link",
    "set_sponsor_author_image",
    "sponsor_author_stylesheet",
)

_shim_module = __import__(__name__, fromlist=["_host"])
_host = render_bridge.tier_d_reexports_module("ai_settings_about")
for _name in _names:
    _obj = getattr(_host, _name)
    globals()[_name] = _obj
    setattr(_shim_module, _name, _obj)

__all__ = list(_names)
