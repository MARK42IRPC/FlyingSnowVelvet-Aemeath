"""主论坛详情屏（产品垫片）。

主论坛详情屏的实现已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/forum_detail.py`。这里按原名再导出（经 `render_bridge.tier_d_reexports()` 解析，
垫片本身不得静态 import 档位 D），既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    Qt,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    scale_px,
    FORUM_REPLY_MAX,
    SmoothScrollArea,
    ForumDetailMixin,
) = render_bridge.tier_d_reexports("forum_detail", (
    "Qt",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QLineEdit",
    "QPushButton",
    "QToolButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "FORUM_REPLY_MAX",
    "SmoothScrollArea",
    "ForumDetailMixin",
))

__all__ = [
    "Qt",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QLineEdit",
    "QPushButton",
    "QToolButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "FORUM_REPLY_MAX",
    "SmoothScrollArea",
    "ForumDetailMixin",
]
