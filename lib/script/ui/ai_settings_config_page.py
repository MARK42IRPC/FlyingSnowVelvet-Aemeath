"""AI 设置面板的「配置分类页」：页骨架装配 + 外部配置字段族。

`ai_settings_panel.AISettingsPanel` 原来用一个 338 行的 `_build_config_category_panel`
把每个配置分类页装配出来（分区、表单、成对/序列/滑条/路径编辑器、字段元数据表
`_config_tab_meta`），旁边还散着两组「外部配置字段」——开机启动（走系统托盘逻辑）与
公告永久抑制（走公告偏好文件）。它们都不读面板的业务状态，只读 `_config_tab_meta`
与事件中心，因此批次 3 第五轮整体切到这里。

切分保持逐行等价：`ConfigPageMixin` 的方法体与搬出前一致（缩进也未变），
`AISettingsPanel` 只是多继承本 mixin，`ui/ai_settings_tabs.py` 的
`panel._build_config_category_panel(category)` 调用点零改动。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

import copy

from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QPushButton,
    QWidget,
)

from config.config import ANIMATION
from lib.core.event.center import Event, EventType
from lib.script.SEanima.clip import resolve_animation_folder_path
from lib.script.SEanima.decoder import scan_animation_frame_files
from lib.script.ui.ai_settings_config_schema import (
    CATEGORY_KEY_ALLOWLIST as _CATEGORY_KEY_ALLOWLIST,
    category_section_entries as _category_section_entries,
    friendly_range_name as _friendly_range_name,
    hardcoded_general_default as _hardcoded_general_default,
    range_pair_signature as _range_pair_signature,
)
from lib.script.ui.ai_settings_editors import (
    _AnimationDurationSliderField,
    _DecimalSliderField,
)
from lib.script.ui.ai_settings_labels import (
    friendly_key_name as _friendly_key_name,
    friendly_section_name as _friendly_section_name,
    section_help_text as _section_help_text,
)
from lib.script.ui.announcement_dialog import (
    load_announcement_preferences,
    set_announcement_forever_suppressed,
)
from lib.script.ui.workbench_settings_layout import (
    SettingsPageScaffold,
    SmoothScrollArea,
    create_settings_form,
)

#: 分类页没有自带说明时的兜底提示，与面板同源同值。
_GENERAL_HINT_TEXT = "保存后会写入本地配置文件，建议重启程序后完整生效"


class ConfigPageMixin:
    """配置分类页的装配与外部字段生命周期；由 `AISettingsPanel` 混入。"""

    def _build_config_category_panel(self, category) -> QWidget:
        import config.config as cc

        category_id = category.page_id
        category_title = category.title
        panel = QWidget(self)
        scaffold = SettingsPageScaffold(
            panel,
            category_title,
            category.description or _GENERAL_HINT_TEXT,
            scroll_factory=SmoothScrollArea,
        )
        title_label = scaffold.title_label
        hint_label = scaffold.description_label

        # 特殊处理：桌宠更新标签页 - 只有按钮，没有配置字段
        if category_id == "desktop_pet_update":
            return self._build_desktop_pet_update_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        if category_id == "sponsor_author":
            return self._build_sponsor_author_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        if category_id == "contribution_list":
            return self._build_contribution_list_panel(
                panel,
                scaffold,
                category_title,
                title_label,
                hint_label,
            )

        fields: list[dict] = []
        defaults: dict[str, dict] = {}
        category_allow_map = _CATEGORY_KEY_ALLOWLIST.get(category_id, {})

        for section_spec in category.sections:
            dict_name, section_title = section_spec.config_key, section_spec.title
            section_entries = _category_section_entries(category_id, str(dict_name), cc, category_allow_map)
            if not section_entries:
                continue

            section_fields_added = False
            section = scaffold.add_help_section(
                _friendly_section_name(str(dict_name), str(section_title)),
                help_text=_section_help_text(category_id, str(dict_name)),
            )
            section_label = section.title_label
            self._set_widget_description(
                section_label,
                f"{_friendly_section_name(str(dict_name), str(section_title))} 配置分组",
            )
            form = create_settings_form()

            consumed_keys: set[str] = set()
            for entry_dict_name, key, value in section_entries:
                entry_id = f"{entry_dict_name}.{key}"
                if entry_id in consumed_keys:
                    continue

                if str(entry_dict_name) == "ANIMATION" and key in {
                    "start_animation_folder",
                    "exit_animation_folder",
                }:
                    animation_type = "start" if key.startswith("start_") else "exit"
                    duration_key = f"{animation_type}_animation_duration"
                    duration_entry = next(
                        (
                            (other_key, other_value)
                            for other_dict_name, other_key, other_value in section_entries
                            if other_dict_name == entry_dict_name and other_key == duration_key
                        ),
                        None,
                    )
                    if duration_entry is not None:
                        _duration_key, duration_value = duration_entry
                        folder_editor, duration_editor, group_widget = self._create_animation_folder_duration_editor(
                            animation_type,
                            str(value),
                            float(duration_value),
                        )
                        self._set_config_editor_value(folder_editor, value)
                        self._set_config_editor_value(duration_editor, duration_value)
                        folder_description = self._build_config_single_description(
                            str(entry_dict_name), key, value, _friendly_key_name(str(entry_dict_name), key)
                        )
                        label = self._create_form_label(_friendly_key_name(str(entry_dict_name), key))
                        self._set_widget_description(label, folder_description)
                        self._set_widget_description(group_widget, folder_description)
                        self._set_widget_description(folder_editor, folder_description)
                        self._set_widget_description(duration_editor, self._build_config_single_description(
                            str(entry_dict_name), duration_key, duration_value,
                            _friendly_key_name(str(entry_dict_name), duration_key),
                        ))
                        form.addRow(label, group_widget)
                        fields.extend((
                            {
                                "kind": "single",
                                "dict_name": str(entry_dict_name),
                                "key": key,
                                "editor": folder_editor,
                                "template": copy.deepcopy(value),
                            },
                            {
                                "kind": "decimal_slider",
                                "dict_name": str(entry_dict_name),
                                "key": duration_key,
                                "editor": duration_editor,
                                "template": copy.deepcopy(duration_value),
                            },
                        ))
                        defaults.setdefault(str(entry_dict_name), {})[key] = _hardcoded_general_default(
                            str(entry_dict_name), key, value
                        )
                        defaults.setdefault(str(entry_dict_name), {})[duration_key] = _hardcoded_general_default(
                            str(entry_dict_name), duration_key, duration_value
                        )
                        consumed_keys.add(entry_id)
                        consumed_keys.add(f"{entry_dict_name}.{duration_key}")
                        section_fields_added = True
                        continue

                signature = _range_pair_signature(key)
                if signature is not None:
                    pair_key = None
                    pair_value = None
                    pair_dict_name = None
                    sign_base, sign_type = signature
                    for other_dict_name, other_key, other_value in section_entries:
                        other_entry_id = f"{other_dict_name}.{other_key}"
                        if other_key == key or other_entry_id in consumed_keys:
                            continue
                        if other_dict_name != entry_dict_name:
                            continue
                        other_sig = _range_pair_signature(other_key)
                        if other_sig is None:
                            continue
                        if other_sig[0] != sign_base:
                            continue
                        pair_dict_name = other_dict_name
                        pair_key = other_key
                        pair_value = other_value
                        break
                    if pair_key is not None and pair_value is not None and pair_dict_name is not None:
                        if sign_type in ("max", "upper"):
                            left_key, left_value = pair_key, pair_value
                            right_key, right_value = key, value
                        else:
                            left_key, left_value = key, value
                            right_key, right_value = pair_key, pair_value
                        left_editor, right_editor, pair_widget = self._create_compact_pair_editor(
                            left_value,
                            right_value,
                            left_hint="最小",
                            right_hint="最大",
                        )
                        friendly_name = _friendly_range_name(str(dict_name), left_key, right_key)
                        description = self._build_config_range_description(
                            str(entry_dict_name),
                            left_key,
                            right_key,
                            left_value,
                            right_value,
                            friendly_name,
                        )
                        label = self._create_form_label(friendly_name)
                        self._set_widget_description(label, description)
                        self._set_widget_description(pair_widget, description)
                        self._set_widget_description(left_editor, description)
                        self._set_widget_description(right_editor, description)
                        form.addRow(label, pair_widget)
                        fields.append({
                            "kind": "range_pair",
                            "dict_name": str(entry_dict_name),
                            "keys": [left_key, right_key],
                            "editors": [left_editor, right_editor],
                            "templates": [copy.deepcopy(left_value), copy.deepcopy(right_value)],
                        })
                        defaults.setdefault(str(entry_dict_name), {})[left_key] = _hardcoded_general_default(
                            str(entry_dict_name), left_key, left_value
                        )
                        defaults.setdefault(str(entry_dict_name), {})[right_key] = _hardcoded_general_default(
                            str(entry_dict_name), right_key, right_value
                        )
                        consumed_keys.add(f"{entry_dict_name}.{left_key}")
                        consumed_keys.add(f"{entry_dict_name}.{right_key}")
                        section_fields_added = True
                        continue

                if isinstance(value, (tuple, list)):
                    editors, group_widget = self._create_sequence_editor(value)
                    friendly_name = _friendly_key_name(str(entry_dict_name), key)
                    description = self._build_config_single_description(str(entry_dict_name), key, value, friendly_name)
                    label = self._create_form_label(friendly_name)
                    self._set_widget_description(label, description)
                    self._set_widget_description(group_widget, description)
                    for editor in editors:
                        self._set_widget_description(editor, description)
                    form.addRow(label, group_widget)
                    fields.append({
                        "kind": "sequence",
                        "dict_name": str(entry_dict_name),
                        "key": key,
                        "editors": editors,
                        "template": copy.deepcopy(value),
                    })
                    defaults.setdefault(str(entry_dict_name), {})[key] = _hardcoded_general_default(
                        str(entry_dict_name), key, value
                    )
                    consumed_keys.add(entry_id)
                    section_fields_added = True
                    continue

                open_dir_btn = None
                extra_widgets: list[QWidget] = []
                slider_spec = self._get_decimal_slider_spec(str(entry_dict_name), key, value)
                choice_options = self._get_choice_field_options(str(entry_dict_name), key)
                if self._is_volume_slider_field(str(entry_dict_name), key, value):
                    editor, percent_label, row_widget = self._create_volume_slider_editor(value)
                    extra_widgets.append(percent_label)
                elif slider_spec is not None:
                    minimum, maximum, step, decimals = slider_spec
                    if str(entry_dict_name) == "ANIMATION" and key in {
                        "start_animation_duration",
                        "exit_animation_duration",
                    }:
                        animation_type = "start" if key.startswith("start_") else "exit"
                        folder_key = f"{animation_type}_animation_folder"
                        folder_name = str(ANIMATION.get(folder_key, ""))
                        frame_count = len(scan_animation_frame_files(resolve_animation_folder_path(folder_name)))
                        editor = _AnimationDurationSliderField(
                            minimum,
                            maximum,
                            step,
                            value=float(value),
                            decimals=decimals,
                            frame_count=frame_count,
                            fps=int(ANIMATION.get("frame_fps", 60) or 60),
                        )
                    else:
                        editor = _DecimalSliderField(
                            minimum,
                            maximum,
                            step,
                            value=float(value),
                            decimals=decimals,
                        )
                    row_widget = editor
                elif choice_options is not None:
                    editor = self._create_config_choice_editor(choice_options)
                    row_widget = self._wrap_field_widget(editor)
                elif self._is_local_music_path_field(str(entry_dict_name), key) and isinstance(value, str):
                    editor, open_dir_btn, row_widget = self._create_path_editor_with_open_button(
                        str(entry_dict_name),
                        str(key),
                        value,
                    )
                elif isinstance(value, bool):
                    editor = QCheckBox()
                    row_widget = self._wrap_field_widget(editor)
                else:
                    editor = self._create_config_line_edit(expanding=True)
                    row_widget = editor
                self._set_config_editor_value(editor, value)
                friendly_name = _friendly_key_name(str(entry_dict_name), key)
                description = self._build_config_single_description(str(entry_dict_name), key, value, friendly_name)
                label = self._create_form_label(friendly_name)
                self._set_widget_description(label, description)
                self._set_widget_description(row_widget, description)
                self._set_widget_description(editor, description)
                if isinstance(open_dir_btn, QPushButton):
                    self._set_widget_description(open_dir_btn, description)
                for extra in extra_widgets:
                    self._set_widget_description(extra, description)
                form.addRow(label, row_widget)
                fields.append({
                    "kind": (
                        "volume_slider"
                        if self._is_volume_slider_field(str(entry_dict_name), key, value)
                        else "decimal_slider"
                        if slider_spec is not None
                        else "single"
                    ),
                    "dict_name": str(entry_dict_name),
                    "key": key,
                    "editor": editor,
                    "template": copy.deepcopy(value),
                })
                defaults.setdefault(str(entry_dict_name), {})[key] = _hardcoded_general_default(
                    str(entry_dict_name), key, value
                )
                consumed_keys.add(entry_id)
                section_fields_added = True

                if category_id == "system_dispatch" and str(entry_dict_name) == "STARTUP" and key == "ensure_desktop_shortcut":
                    self._append_autostart_field(form, fields)
                    section_fields_added = True

            if category_id == "ui_anim" and str(dict_name) == "UI":
                self._append_announcement_suppression_field(form, fields)
                section_fields_added = True

            if section_fields_added:
                section.body_layout.addLayout(form)
            else:
                section.hide()

        scaffold.finish()
        scaffold.add_action(
            "恢复本页默认",
            lambda _checked=False, target=category_id: self._on_restore_config_category(target),
        )
        scaffold.add_action(
            "保存更改",
            lambda _checked=False, target=category_id: self._on_save_config_category(target),
            primary=True,
        )

        self._config_tab_meta[category_id] = {
            "panel": panel,
            "fields": fields,
            "defaults": defaults,
            "title": category_title,
            "title_label": title_label,
            "hint_label": hint_label,
        }
        return panel

    @staticmethod
    def _get_autostart_enabled() -> bool:
        try:
            from lib.script.app.autostart import is_autostart_enabled

            return bool(is_autostart_enabled())
        except Exception:
            return False

    def _set_autostart_enabled(self, enabled: bool) -> None:
        try:
            from lib.script.app.tray_actions import set_autostart_enabled

            target = bool(enabled)
            result = set_autostart_enabled(target)
            actual = bool(result.enabled)
            self._ec.publish(Event(EventType.AUTOSTART_STATUS_CHANGE, {
                "enabled": actual,
                "source": "panel",
            }))
            self._set_autostart_checkbox_checked(actual)
            if not result.success or actual != target:
                raise ValueError(result.message)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"开机启动设置失败: {e}") from e

    def _subscribe_autostart_events(self) -> None:
        if self._autostart_status_subscribed:
            return
        self._ec.subscribe(EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change)
        self._autostart_status_subscribed = True

    def _unsubscribe_autostart_events(self) -> None:
        if not self._autostart_status_subscribed:
            return
        self._ec.unsubscribe(EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change)
        self._autostart_status_subscribed = False

    def _set_autostart_checkbox_checked(self, enabled: bool) -> None:
        if not isinstance(self._autostart_checkbox, QCheckBox):
            return
        target = bool(enabled)
        if self._autostart_checkbox.isChecked() == target:
            return
        blocked = self._autostart_checkbox.blockSignals(True)
        try:
            self._autostart_checkbox.setChecked(target)
        finally:
            self._autostart_checkbox.blockSignals(blocked)

    def _on_autostart_status_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get("enabled", self._get_autostart_enabled()))
        self._set_autostart_checkbox_checked(enabled)

    def _append_autostart_field(
        self,
        form: QFormLayout,
        fields: list[dict],
    ) -> None:
        editor = QCheckBox()
        default_enabled = self._get_autostart_enabled()
        editor.setChecked(default_enabled)
        self._autostart_checkbox = editor
        row_widget = self._wrap_field_widget(editor)
        label = self._create_form_label("开机启动")
        description = "桌宠随系统启动；保存时复用系统托盘开机启动逻辑。"
        self._set_widget_description(label, description)
        self._set_widget_description(row_widget, description)
        self._set_widget_description(editor, description)
        form.addRow(label, row_widget)
        fields.append({
            "kind": "external_autostart",
            "dict_name": "ANIMATION",
            "key": "autostart_enabled",
            "editor": editor,
            "template": True,
            "default": bool(default_enabled),
        })

    @staticmethod
    def _get_announcement_forever_suppressed() -> bool:
        return bool(load_announcement_preferences().suppress_forever)

    @staticmethod
    def _set_announcement_forever_suppressed(enabled: bool) -> None:
        set_announcement_forever_suppressed(bool(enabled))

    def _append_announcement_suppression_field(
        self,
        form: QFormLayout,
        fields: list[dict],
    ) -> None:
        editor = QCheckBox()
        editor.setObjectName("AnnouncementSuppressionCheckbox")
        editor.setChecked(self._get_announcement_forever_suppressed())
        self._announcement_suppression_checkbox = editor
        row_widget = self._wrap_field_widget(editor)
        label = self._create_form_label("不显示公告")
        description = (
            "控制启动时是否自动显示公告；托盘“桌宠公告”仍可手动打开。"
            "取消勾选只解除永久抑制，不清除当日抑制状态。"
        )
        self._set_widget_description(label, description)
        self._set_widget_description(row_widget, description)
        self._set_widget_description(editor, description)
        form.addRow(label, row_widget)
        fields.append({
            "kind": "external_announcement_suppression",
            "dict_name": "UI",
            "key": "announcement_suppress_forever",
            "editor": editor,
            "template": False,
            "default": False,
        })

    def _apply_external_category_fields(self, category_id: str) -> None:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return
        for field in meta.get("fields", []):
            kind = str(field.get("kind") or "single")
            editor = field.get("editor")
            if not isinstance(editor, QCheckBox):
                continue
            if kind == "external_autostart":
                self._set_autostart_enabled(bool(editor.isChecked()))
            elif kind == "external_announcement_suppression":
                self._set_announcement_forever_suppressed(bool(editor.isChecked()))

__all__ = [
    "ConfigPageMixin",
]
