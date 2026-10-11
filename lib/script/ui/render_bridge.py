"""`lib/core/render/` 解析 / 转发层的产品侧薄垫片（零 Qt）。

本模块原先自己就是"UI 侧唯一 Qt 落点"：函数体内惰性 import
`lib/core/render/backends/qt/{drawing,runtime,widgets}` 并把结果转给产品控件。按《render 层
边界契约》的最终目标「只有 `lib/core/render/` 内存在 Qt 导入」，那份实现已整体搬进 render 层
（`lib/core/render/qt_bridge.py`），本模块只保留历史导入面。

形态说明：

- **具名转发**：`from lib.script.ui.render_bridge import ui_font` 这类调用面（99 个文件）由
  `_render_bridge_forwarder.bind()` 在导入时按核心公开名字铺出，等价于直接 import 核心层。
- **模块 `__getattr__`**：`from lib.script.ui import render_bridge; render_bridge.ui_font(...)` 这类
  模块属性访问由 PEP 562 的 `__getattr__` 兜底，不缓存结果（测试常用
  `patch.object(render_bridge, "ui_font", ...)`，缓存绑定会让补丁与转发分叉）。
- 产品侧全部 seam 注入（`install_tier_d_seams` / `install_tier_d_hook` / `tier_d_reexports` …）
  同样由 `__getattr__` 转发。本文件因此**零 PyQt5、零档位 D 静态 import**，
  不再出现在 `frozen_ui_qt_importers` 里。

两个手工成员留在垫片：`apply_settings_page_fonts()` 原实现经产品侧
`lib.script.ui.workbench_settings_layout` 取宿主实现，与"本模块不得反向 import 产品包"冲突，
且只被 `office_style.apply_office_fonts()` 使用；`configure_forum_window_violation_sound()`
要 import 产品侧 `lib.script.voice.forum_violation`。两者放这里既保持调用面不变，
也让核心层保持"不反向 import 产品"。
"""

from __future__ import annotations

from lib.script.ui import _render_bridge_forwarder as _forwarder

_forwarded = _forwarder.bind(globals(), __name__)


def apply_settings_page_fonts(page) -> None:
    """把工作台设置页的字号档铺到一页控件树上（产品面调用面不变）。"""
    from lib.script.ui.workbench_settings_layout import apply_settings_page_fonts as apply

    apply(page)


def configure_forum_window_violation_sound() -> None:
    """给论坛窗口宿主接上违规提示语音工厂（延迟 import，宿主本身不 import 产品包）。"""
    forum_window = _forwarder._default_bridge().tier_d_reexports_module("forum_window")

    def _make():
        from lib.script.voice.forum_violation import ForumViolationSound

        return ForumViolationSound()

    forum_window.configure_violation_sound_factory(_make)


__all__ = list(_forwarded) + [
    "apply_settings_page_fonts",
    "configure_forum_window_violation_sound",
]


def __getattr__(name):
    """PEP 562：把模块属性访问转发给核心转发层（不缓存，补丁友好）。"""
    return getattr(_forwarder._default_bridge(), name)
