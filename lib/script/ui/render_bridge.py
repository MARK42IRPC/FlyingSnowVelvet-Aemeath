"""`lib/core/render/` 解析 / 转发层的产品侧薄垫片（零 Qt）。

本模块原先自己就是"UI 侧唯一 Qt 落点"：函数体内惰性 import
`lib/core/render/backends/qt/{drawing,runtime,widgets}` 并把结果转给产品控件。按《render 层
边界契约》的最终目标「只有 `lib/core/render/` 内存在 Qt 导入」，那份实现已整体搬进 render 层
（`lib/core/render/qt_bridge.py`），本模块只保留历史导入面。

**转发形态：同一函数对象，不生成包装器。** 核心 `qt_bridge.__all__` 里的公开名字在导入时
原样搬进本模块（`globals()[name] = getattr(core, name)`），因此：

- `inspect.signature` / `inspect.getsource` / `__doc__` / `__module__` 与下沉前一致——
  包装器会吞掉真实签名，这里是刻意避开的坑；
- `patch.object(render_bridge, "ui_font", ...)` 照旧只改本模块属性，不影响核心层；
- 产品垫片捕获的名字（`from ...render_bridge import ui_font`）与核心是同一个对象，
  既有的 `assertIs(host._font_factory, render_bridge.ui_font)` 一类断言继续成立。

`__getattr__`（PEP 562）只作为兜底：`__all__` 之外的名字仍转发给核心层。
本文件因此**零 PyQt5、零档位 A/B/D 路径字面量**，不再出现在 `frozen_ui_qt_importers` 里。

两个手工成员留在垫片：`apply_settings_page_fonts()` 原实现经产品侧
`lib.script.ui.workbench_settings_layout` 取宿主实现，与"本模块不得反向 import 产品包"冲突，
且只被 `office_style.apply_office_fonts()` 使用；`configure_forum_window_violation_sound()`
要 import 产品侧 `lib.script.voice.forum_violation`。两者放这里既保持调用面不变，
也让核心层保持"不反向 import 产品"。
"""

from __future__ import annotations

from importlib import import_module

CORE_MODULE = "lib.core.render.qt_bridge"


def _core_bridge():
    """取核心转发层模块；惰性 import，避免垫片导入期就拉起 render 层。"""
    return import_module(CORE_MODULE)


#: 核心层的公开转发面（`__all__` 是唯一事实源）。
_CORE = _core_bridge()
for _name in _CORE.__all__:
    globals()[_name] = getattr(_CORE, _name)
del _name


def apply_settings_page_fonts(page) -> None:
    """把工作台设置页的字号档铺到一页控件树上（产品面调用面不变）。"""
    from lib.script.ui.workbench_settings_layout import apply_settings_page_fonts as apply

    apply(page)


def configure_forum_window_violation_sound() -> None:
    """给论坛窗口宿主接上违规提示语音工厂（延迟 import，宿主本身不 import 产品包）。"""
    forum_window = _core_bridge().tier_d_reexports_module("forum_window")

    def _make():
        from lib.script.voice.forum_violation import ForumViolationSound

        return ForumViolationSound()

    forum_window.configure_violation_sound_factory(_make)


__all__ = [
    *_CORE.__all__,
    "apply_settings_page_fonts",
    "configure_forum_window_violation_sound",
]


def __getattr__(name):
    """PEP 562 兜底：`__all__` 之外的名字转发给核心转发层。"""
    return getattr(_core_bridge(), name)
