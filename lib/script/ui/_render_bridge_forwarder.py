"""零 Qt 的具名转发层（`lib/script/ui/render_bridge.py` 的实现心脏）。

`render_bridge.py` 是**零 PyQt5** 薄垫片：99 个产品文件的 `from ...render_bridge import
X` 调用面由本模块在垫片导入时铺成**具名转发函数**，函数对象因此有正确的
`__name__` / `__doc__`，`inspect.getsource()`、`patch.object(桥, "ui_font")` 一类反射
与打桩照旧可用。

转发清单的唯一事实源是核心层 `lib/core/render/qt_bridge.py` 的 `__all__`：核心加一个
转发函数，垫片导入后自动多一个。本文件里没有任何后端路径或函数名字面量，因此
`lib/script/ui` 整体零档位 A/B/D 引用。

`render_bridge.py` 另装一个 PEP 562 `__getattr__`，覆盖"模块属性访问 + 动态名字"的形态；
两条路径共用 `_default_bridge()`，实现只有核心 `qt_bridge` 一份。
"""

from __future__ import annotations

from importlib import import_module

CORE_MODULE = "lib.core.render.qt_bridge"


def _default_bridge():
    """取核心转发层模块；惰性 import，避免垫片导入期就拉起 render 层。"""
    return import_module(CORE_MODULE)


def bind(namespace, module_name):
    """把核心 `qt_bridge` 的公开名字铺成具名转发函数，返回被接管的名单。

    转发函数体是 `return _bridge.<name>(*args, **kwargs)`；单层转发对关键字调用透明，
    参数形态与关键字名因此与核心实现逐字一致。
    """
    _bridge = namespace.pop("_bridge", None) or _default_bridge()
    exported = []
    for name in _bridge.__all__:
        target = getattr(_bridge, name)
        def _forward(*args, __target=target, **kwargs):
            return __target(*args, **kwargs)
        _forward.__name__ = name
        _forward.__qualname__ = name
        _forward.__doc__ = getattr(target, "__doc__", None)
        _forward.__module__ = module_name
        namespace[name] = _forward
        exported.append(name)
    return exported
