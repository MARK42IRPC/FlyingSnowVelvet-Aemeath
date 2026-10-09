"""AI 设置面板配置编辑器「控件取值 → 配置值」的解析（无 Qt）。

从 `ai_settings_panel.py` 拆出的两个纯函数：

- `parse_text_by_template(text, template)`：按模板类型把编辑框文本转成目标类型
  （文本 / 布尔 / 整数 / 小数，其余走 `ast.literal_eval`，因此元组与列表模板天然可用）。
- `parse_editor_value(field, widget=...)`：按 `kind` 分派——范围对、数组、音量滑块、
  小数滑块、单值（复选 / 下拉 / 行编辑）——产出 `{配置键: 解析后的值}`。

控件类型判定（`is_text_editor` / `is_slider` / `is_decimal_field` / `is_check_box` /
`is_combo_box`）与音量滑块百分比换算（`volume_value_from_percent`）由调用方以 `widget`
注入，本模块因此既不 import `PyQt5`、也不认识任何控件类。

解析失败一律抛 `ValueError`，消息与拆分前逐字符一致。
"""

from __future__ import annotations

import ast
from typing import Protocol

#: 外部维护的配置字段（自动启动、公告抑制）不由本模块取值。
EXTERNAL_CONFIG_FIELD_KINDS = ("external_autostart", "external_announcement_suppression")


class EditorWidgets(Protocol):
    """面板注入的控件能力：只做类型判定与一次百分比换算。"""

    def is_text_editor(self, editor) -> bool: ...
    def is_slider(self, editor) -> bool: ...
    def is_decimal_field(self, editor) -> bool: ...
    def is_check_box(self, editor) -> bool: ...
    def is_combo_box(self, editor) -> bool: ...
    def volume_value_from_percent(self, percent: int): ...


__all__ = [
    "EditorWidgets",
    "EXTERNAL_CONFIG_FIELD_KINDS",
    "parse_editor_value",
    "parse_text_by_template",
]

def parse_text_by_template(text: str, template):
    if isinstance(template, str):
        return text
    if isinstance(template, bool):
        return bool(text.lower() in ("1", "true", "yes", "on"))
    if isinstance(template, int):
        return int(text)
    if isinstance(template, float):
        return float(text)
    return ast.literal_eval(text)


def parse_editor_value(field: dict, *, widget: EditorWidgets) -> dict[str, object]:
    kind = str(field.get("kind") or "single")
    if kind in EXTERNAL_CONFIG_FIELD_KINDS:
        return {}
    if kind == "range_pair":
        keys = field.get("keys") or []
        editors = field.get("editors") or []
        templates = field.get("templates") or []
        if len(keys) != 2 or len(editors) != 2 or len(templates) != 2:
            raise ValueError("范围配置结构无效")
        result = {}
        for idx in range(2):
            editor = editors[idx]
            if not widget.is_text_editor(editor):
                raise ValueError("范围配置编辑控件无效")
            text = editor.text().strip()
            result[str(keys[idx])] = parse_text_by_template(text, templates[idx])
        return result

    if kind == "sequence":
        key = str(field.get("key") or "")
        editors = field.get("editors") or []
        template = field.get("template")
        if not isinstance(template, (tuple, list)):
            raise ValueError("数组配置模板无效")
        if len(editors) != len(template):
            raise ValueError("数组配置长度不一致")
        parsed_items = []
        for idx, editor in enumerate(editors):
            if not widget.is_text_editor(editor):
                raise ValueError("数组配置编辑控件无效")
            text = editor.text().strip()
            parsed_items.append(parse_text_by_template(text, template[idx]))
        if isinstance(template, tuple):
            return {key: tuple(parsed_items)}
        return {key: list(parsed_items)}

    if kind == "volume_slider":
        key = str(field.get("key") or "")
        editor = field.get("editor")
        if not widget.is_slider(editor):
            raise ValueError("音量滑块控件无效")
        return {key: widget.volume_value_from_percent(editor.value())}

    if kind == "decimal_slider":
        key = str(field.get("key") or "")
        editor = field.get("editor")
        template = field.get("template")
        if not widget.is_decimal_field(editor):
            raise ValueError("小数滑块控件无效")
        return {key: parse_text_by_template(editor.text().strip(), template)}

    key = str(field.get("key") or "")
    editor = field.get("editor")
    template = field.get("template")
    if widget.is_check_box(editor):
        return {key: bool(editor.isChecked())}
    if widget.is_combo_box(editor):
        selected = editor.currentData()
        if selected is None:
            selected = editor.currentText().strip()
        if isinstance(template, str):
            return {key: str(selected)}
        if template is not None and isinstance(selected, type(template)):
            return {key: selected}
        return {key: parse_text_by_template(str(selected), template)}
    if not widget.is_text_editor(editor):
        raise ValueError("不支持的配置编辑控件")
    text = editor.text().strip()
    return {key: parse_text_by_template(text, template)}
