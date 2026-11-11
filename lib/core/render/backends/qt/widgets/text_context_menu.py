"""设置面板统一的文本右键菜单：只保留「复制 / 粘贴 / 全选」。

以前 AI 设置面板自己拼一份「剪切 / 复制 / 粘贴」，办公页面又拼一份顺序不同的
「复制 / 粘贴 / 剪切」，其余页面则直接用 Qt 内建菜单（带着与项目其它菜单不一致的条目）。
这里收口成一份实现，所有设置页的文本框都挂在同一份菜单上。

落点选在档位 D：它只依赖 Qt 与 `tier_d_seams.ui_font`，不 import 任何产品包与档位 A。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.visuals.ai_settings_panel_visuals import CONFIG_FONT_SIZE

#: 菜单条目与顺序的唯一事实源；测试按这份元组断言。
TEXT_CONTEXT_MENU_ACTIONS = ("复制", "粘贴", "全选")

#: 需要统一右键菜单的文本控件类型；`QTextBrowser` 是 `QTextEdit` 的子类，一并覆盖。
TEXT_WIDGET_TYPES = (QLineEdit, QPlainTextEdit, QTextEdit)

_BOUND_FLAG = "_cn_text_context_menu_bound"
_COMBO_BOUND_FLAG = "_cn_text_context_menu_combo_bound"


def _has_selection(widget) -> bool:
    if isinstance(widget, QLineEdit):
        return bool(widget.hasSelectedText())
    cursor = widget.textCursor()
    return bool(cursor.hasSelection())


def _can_copy(widget) -> bool:
    return _has_selection(widget)


def _can_paste(widget) -> bool:
    """能否粘贴：控件自己知道只读与剪贴板内容，优先用它的判断。"""
    can_paste = getattr(widget, "canPaste", None)
    if callable(can_paste):
        try:
            return bool(can_paste())
        except Exception:
            pass
    if isinstance(widget, QLineEdit):
        return not bool(widget.isReadOnly())
    return not bool(widget.isReadOnly()) and bool(QApplication.clipboard().text())


def _apply_all(widget) -> None:
    widget.selectAll()


def show_text_context_menu(widget, pos: QPoint) -> None:
    """在控件 `pos` 处弹出统一菜单：复制 / 粘贴 / 全选。"""
    if not isinstance(widget, TEXT_WIDGET_TYPES):
        return

    menu = QMenu(widget)
    font = tier_d_seams.ui_font(size=CONFIG_FONT_SIZE)
    font.setBold(True)
    menu.setFont(font)

    action_copy = menu.addAction(TEXT_CONTEXT_MENU_ACTIONS[0])
    action_paste = menu.addAction(TEXT_CONTEXT_MENU_ACTIONS[1])
    action_select_all = menu.addAction(TEXT_CONTEXT_MENU_ACTIONS[2])
    action_copy.setEnabled(_can_copy(widget))
    action_paste.setEnabled(_can_paste(widget))
    action_select_all.setEnabled(True)

    chosen = menu.exec_(widget.mapToGlobal(pos))
    if chosen is action_copy:
        widget.copy()
    elif chosen is action_paste:
        widget.paste()
    elif chosen is action_select_all:
        _apply_all(widget)


def _is_combo_internal_editor(widget) -> bool:
    """`QComboBox` 内部那个 `QLineEdit`：它会被下拉框重建，不单独挂菜单。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QComboBox):
            return True
        parent = parent.parentWidget()
    return False


def show_combo_context_menu(combo: QComboBox, pos: QPoint) -> None:
    """可编辑下拉框的右键菜单：作用对象是它**当前**的编辑框。

    `QComboBox` 每次换选项 / 重置模型都可能重建内部 `QLineEdit`，所以菜单挂在
    `QComboBox` 自己身上，点击时再取一次 `lineEdit()`，不在重建后丢菜单。
    """
    if not isinstance(combo, QComboBox):
        return
    editor = combo.lineEdit()
    if editor is None:
        return
    offset = editor.mapFrom(combo, pos)
    show_text_context_menu(editor, offset)


def install_combo_context_menu(combo: QComboBox) -> bool:
    """给可编辑下拉框挂上统一菜单；不可编辑、已挂过的返回 False。"""
    if not isinstance(combo, QComboBox) or not combo.isEditable():
        return False
    if bool(getattr(combo, _COMBO_BOUND_FLAG, False)):
        return False
    combo.setContextMenuPolicy(Qt.CustomContextMenu)
    combo.customContextMenuRequested.connect(
        lambda pos, target=combo: show_combo_context_menu(target, pos)
    )
    setattr(combo, _COMBO_BOUND_FLAG, True)
    return True


def install_text_context_menu(widget) -> bool:
    """把统一菜单挂到一个文本控件上；已经挂过则跳过。返回本次是否新挂。"""
    if not isinstance(widget, TEXT_WIDGET_TYPES):
        return False
    if isinstance(widget, QLineEdit) and _is_combo_internal_editor(widget):
        return False
    if bool(getattr(widget, _BOUND_FLAG, False)):
        return False
    widget.setContextMenuPolicy(Qt.CustomContextMenu)
    widget.customContextMenuRequested.connect(
        lambda pos, target=widget: show_text_context_menu(target, pos)
    )
    setattr(widget, _BOUND_FLAG, True)
    return True


def install_text_context_menus(root: QWidget) -> int:
    """递归挂载到一棵控件树（含可编辑下拉框）；返回本次新挂的控件数量。

    优先取 `root` 自身：页面里若有「根就是文本控件」的宿主，也不能漏掉。
    """
    if root is None:
        return 0
    count = 0
    for widget_type in TEXT_WIDGET_TYPES:
        if isinstance(root, widget_type) and install_text_context_menu(root):
            count += 1
        for widget in root.findChildren(widget_type):
            if install_text_context_menu(widget):
                count += 1
    combos = []
    if isinstance(root, QComboBox):
        combos.append(root)
    combos.extend(root.findChildren(QComboBox))
    for combo in combos:
        if install_combo_context_menu(combo):
            count += 1
    return count


__all__ = [
    "TEXT_CONTEXT_MENU_ACTIONS",
    "TEXT_WIDGET_TYPES",
    "install_combo_context_menu",
    "install_text_context_menu",
    "install_text_context_menus",
    "show_combo_context_menu",
    "show_text_context_menu",
]
