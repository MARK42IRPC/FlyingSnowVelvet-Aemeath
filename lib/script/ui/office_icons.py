"""办公模式线性图标的产品门面。

图形规格（SVG 文本与尺寸）已是后端中立事实，落在
``lib/core/render/visuals/office_icons.py``；Qt 渲染在
``lib/core/render/backends/qt/widgets/office_icons.py``，控件统一经 ``render_bridge`` 取用。

本模块只保留既有函数名，供 ``office_page`` / ``office_approval_dialog`` 继续按原路径调用；
它不再直接 import ``PyQt5``。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def office_new_icon(color: str):
    return render_bridge.render_office_icon("new", color)


def office_delete_icon(color: str):
    return render_bridge.render_office_icon("delete", color)


def office_browse_icon(color: str):
    return render_bridge.render_office_icon("browse", color)


def office_cancel_icon(color: str):
    return render_bridge.render_office_icon("cancel", color)


def office_submit_icon(color: str):
    return render_bridge.render_office_icon("submit", color)


def office_reject_icon(color: str):
    return render_bridge.render_office_icon("reject", color)


def office_warning_icon(color: str):
    return render_bridge.render_office_icon("warning", color)


def office_allow_icon(color: str):
    return render_bridge.render_office_icon("allow", color)


def office_allow_task_icon(color: str):
    return render_bridge.render_office_icon("allow_task", color)


__all__ = [
    "office_new_icon",
    "office_delete_icon",
    "office_browse_icon",
    "office_cancel_icon",
    "office_submit_icon",
    "office_reject_icon",
    "office_allow_icon",
    "office_allow_task_icon",
    "office_warning_icon",
]
