"""办公面的 Qt 控件树辅助（档位 C/D）：字号铺装与 accent bar 构造。

两件事都必须真的遍历 / 构造 `QWidget`，因此不能放进 `visuals/office_chrome.py`
（那里是后端中立数据）。`render_bridge` 提供转发入口，产品控件照旧按原路径调用；
本模块不 import `lib.script`，设置页那一半由 `render_bridge` 自己组合。

`office_stylesheet()` 的 `font-size` 只作用于选择器命中的那个控件本身，子控件不会跟着
变大；这里把办公面独有的控件与标签也铺上字号档，正文才不会比工作台设置页小一档。
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QTabBar,
    QToolButton,
    QTreeWidget,
    QWidget,
)

from config.scale import scale_px
from lib.core.render.visuals.office_chrome import (
    OFFICE_BOLD_LABELS,
    OFFICE_HINT_LABELS,
    SETTINGS_FONT_SIZE,
    SETTINGS_HINT_FONT_SIZE,
)


def apply_office_widget_fonts(
    root: QWidget,
    *,
    font_factory,
    settings_font_size: int | None = None,
    settings_hint_font_size: int | None = None,
) -> None:
    """把办公面独有的控件与标签铺上设置页字号档。

    设置页已经覆盖的控件（页头说明、分区标题、输入控件）由调用方的
    `apply_settings_page_fonts` 负责；这里只补设置页没有的部分：工具按钮、文本视图、
    标签页，以及按对象名分档的 `Office*` 标签。

    ``font_factory`` 由 ``render_bridge`` 注入（Qt 下是 ``ui_font``），档位 D 因此没有
    静态引用绘制档。字号默认取中立镜像常量，调用方可传入产品面的权威值。
    """
    body_size = SETTINGS_FONT_SIZE if settings_font_size is None else int(settings_font_size)
    hint_size = (
        SETTINGS_HINT_FONT_SIZE
        if settings_hint_font_size is None
        else int(settings_hint_font_size)
    )

    body_font = font_factory(body_size)
    bold_font = font_factory(body_size)
    bold_font.setBold(True)
    hint_font = font_factory(hint_size)

    # 设置页没有工具按钮，办公面的「新任务 / 浏览 / 取消」跟同页按钮一样走粗体。
    for widget in root.findChildren(QToolButton):
        widget.setFont(bold_font)
    # 文本视图与标签页跟随正文档；设置页不含这些控件，需要办公面自己铺。
    for widget_type in (QPlainTextEdit, QListWidget, QTreeWidget, QTabBar):
        for widget in root.findChildren(widget_type):
            widget.setFont(body_font)
    for label in root.findChildren(QLabel):
        if label.property("preserveCustomFont"):
            continue
        name = label.objectName()
        if name in OFFICE_HINT_LABELS:
            label.setFont(hint_font)
        elif name in OFFICE_BOLD_LABELS:
            label.setFont(bold_font)


def create_office_accent_bar(parent: QWidget) -> QWidget:
    """办公面顶部的双色 accent bar（青 / 粉各半）。"""
    bar = QWidget(parent)
    bar.setObjectName("OfficeAccentBar")
    bar.setFixedHeight(scale_px(5, min_abs=4))
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for object_name in ("OfficeAccentCyan", "OfficeAccentPink"):
        segment = QFrame(bar)
        segment.setObjectName(object_name)
        layout.addWidget(segment, 1)
    return bar


__all__ = [
    "apply_office_widget_fonts",
    "create_office_accent_bar",
]
