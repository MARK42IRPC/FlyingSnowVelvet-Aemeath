"""AI 设置面板：编辑并保存 config/ollama_config.py。"""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QLabel,
    QLineEdit,
    QComboBox,
    QListView,
    QPushButton,
    QCheckBox,
    QApplication,
    QGraphicsOpacityEffect,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QMenu,
)
from PyQt5.QtGui import QPainter

from config.config import UI
from config.ollama_config import AI_VOICE_MAX_CHARS_DEFAULT
from lib.core.render.visuals.settings_panel_visuals import build_ai_settings_panel_visual
from lib.core.render.visuals.types import Size
from lib.script.ui.render_bridge import create_draw_backend, ui_font as get_ui_font
from config.general_user_settings import save_general_values
from config.scale import scale_px
from lib.core.render.visuals.ai_settings_panel_visuals import ai_settings_panel_stylesheet
from lib.script.ui import ai_settings_about as _about
from lib.script.ui import ai_settings_update as _update_page
from lib.script.ui.ai_settings_config_schema import (  # noqa: F401 - 既有导出面
    GENERAL_DECIMAL_SLIDER_SPECS as _GENERAL_DECIMAL_SLIDER_SPECS,
)
from lib.script.ui import ai_settings_descriptions as _descriptions
from lib.script.ui import ai_settings_validation as _validation
from lib.script.ui.ai_settings_about import (
    _ContributionCardButton as _ContributionCardButton,
)
from lib.script.ui.ai_settings_config_schema import (
    hardcoded_general_default as _hardcoded_general_default,
)
from lib.script.ui.ai_settings_config_page import (
    ConfigPageMixin as _ConfigPageMixin,
)
from lib.script.ui.ai_settings_page import (
    AISettingsPageMixin as _AISettingsPageMixin,
    _gpu_mode_from_num_gpu,
)
from lib.script.ui.ai_settings_editors import (
    ConfigEditorMixin as _ConfigEditorMixin,
    _AnimationDurationSliderField as _AnimationDurationSliderField,
)
from lib.script.ui.ai_settings_contributions import (
    contribution_list_path as _contribution_list_path_impl,
    load_contribution_records as _load_contribution_records_impl,
    sponsor_author_image_path as _sponsor_author_image_path_impl,
)
from lib.script.ui.ai_settings_storage import load_ai_values, save_ai_values, apply_ai_runtime
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES as _DEFAULT_VALUES
from lib.script.ui.confirm_dialog import show_message
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.ai_settings_tabs import (
    layout_ai_settings_tab_bar,
    show_ai_settings_tab_bar,
    hide_ai_settings_tab_bar,
    layout_ai_settings_tab_panels,
    set_active_ai_settings_tab,
)
from lib.core.anchor_utils import animate_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.layers import WindowLayer
from lib.core.render.layers import get_layer_manager
from lib.core.logger import get_logger
from lib.script.app.startup_probe import load_saved_watermark_payload as _load_saved_watermark_payload
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.ui.voice_package_installer import (
    VoicePackageInstallerDialog,
)
from lib.script.workbench.settings import (
    GENERAL_CONFIG_CATEGORIES,
)
from lib.script.ui.workbench_settings_layout import (
    SettingsPageScaffold,
)
from lib.script.gsvmove import get_voice_package_status

_logger = get_logger(__name__)


_DROPDOWN_POPUP_LAYER = 601

#: AI 主页面的装配与取值/回填闭环已下沉到 `lib/script/ui/ai_settings_page.py`
#: （`AISettingsPageMixin`，见 `doc/render层边界契约.md` 第 53 节）；本文件只按原名继承。
_EXTERNAL_CONFIG_FIELD_KINDS = {
    "external_autostart",
    "external_announcement_suppression",
}


_WATERMARK_TEXT = "Aemeath\nAIsetting"
_TITLE_FONT_SIZE = scale_px(23, min_abs=17)
_CONFIG_FONT_SIZE = scale_px(17, min_abs=12)
_DROPDOWN_ITEM_FONT_SIZE = max(scale_px(8, min_abs=8), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_PANEL_SCALE = 1.05
_GENERAL_HINT_TEXT = "保存后会写入本地配置文件，建议重启程序后完整生效"


_HINT_FONT_SIZE = max(scale_px(12, min_abs=9), _CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
_SPONSOR_AUTHOR_URL = "https://afdian.com/a/fxxrdeskpet"
# 贡献名单的常量（忽略标题片段 / 隐藏角色 / 手工条目）与解析逻辑已下沉到
# `lib/script/ui/ai_settings_contributions.py`；本文件只保留下面的路径与加载委托。
_GENERAL_CONFIG_CATEGORIES = GENERAL_CONFIG_CATEGORIES


def _save_general_config(values_by_dict: dict[str, dict]) -> None:
    values_to_save = copy.deepcopy(values_by_dict)
    cloud_music = values_to_save.get("CLOUD_MUSIC")
    if isinstance(cloud_music, dict) and "default_volume" in cloud_music:
        from config.music.volume_config import get_volume_config

        get_volume_config().set_volume(float(cloud_music.pop("default_volume")))
    save_general_values(values_to_save)


def _apply_general_runtime(values_by_dict: dict[str, dict]) -> None:
    import config.config as cc

    for dict_name, items in values_by_dict.items():
        target = getattr(cc, dict_name, None)
        if isinstance(target, dict):
            target.update(items)
        if dict_name == "CLOUD_MUSIC" and "provider" in items:
            try:
                from lib.script.music import get_music_service

                get_music_service().set_provider(str(items.get("provider") or ""), persist=False)
            except Exception as exc:
                _logger.warning("热重载音乐平台失败: %s", exc)

    try:
        get_event_center().publish(Event(EventType.CONFIG_UPDATED, {
            "source": "general",
            "values": values_by_dict,
        }))
    except Exception as exc:
        _logger.debug("发布通用配置热重载事件失败: %s", exc)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _contribution_list_path() -> Path:
    """委托给后端中立的贡献名单模块，root 取本文件的 `_project_root()`。

    传 root 而不是让子模块自己算，是为了让既有测试继续用
    `mock.patch.object(panel, "_project_root", ...)` 覆盖文档树位置。"""
    return _contribution_list_path_impl(_project_root())


def _sponsor_author_image_path() -> Path:
    return _sponsor_author_image_path_impl(_project_root())


def _load_contribution_records() -> list[dict[str, str]]:
    return _load_contribution_records_impl(_project_root())


class AISettingsPanel(_AISettingsPageMixin, _ConfigPageMixin, _ConfigEditorMixin, QWidget):
    """托盘入口 AI 设置面板。"""

    _ui_thread_call = pyqtSignal(object)

    def __init__(self, parent=None, *, lazy_workbench_pages: bool = False):
        super().__init__(parent)
        self._lazy_workbench_pages = bool(lazy_workbench_pages)
        self._workbench_pages: dict[str, QWidget] = {}
        self._ui_thread_call.connect(self._invoke_ui_callable)
        self._ec = get_event_center()
        self._autostart_checkbox = None
        self._announcement_suppression_checkbox = None
        self._autostart_status_subscribed = False
        self._update_dialog: DesktopPetUpdateDialog | None = None
        self._voice_installer_dialog: VoicePackageInstallerDialog | None = None
        self._qq_group_dialog: QQGroupDialog | None = None
        self._subscribe_autostart_events()
        self.setWindowTitle("控制面板")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        get_layer_manager().register(self, WindowLayer.PANEL, name='AISettingsPanel')
        self.setMinimumWidth(int(round(scale_px(520) * _PANEL_SCALE)))
        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2
        self._visible = False
        self._external_close_callback = None
        self._workbench_attached = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._gpu_watermark_text = "UnKnow GPU 0.00 GB\nRAM 0.00 GB"
        self._panel_watermark_text = _WATERMARK_TEXT
        self._draw_backend = create_draw_backend()
        self._tick_counter = 0
        self._tick_subscribed = False

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI.get("ui_fade_duration", 180))
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)
        self._tab_floating = None
        self._tab_pages: list[QWidget] = []
        self._config_tab_meta: dict[str, dict] = {}
        self._stable_window_size: tuple[int, int] | None = None
        self._save_task_pending = False
        self._save_completion_action: Callable[[], None] | None = None

        self._build_ui()
        self._apply_project_fonts()
        self._apply_style()
        self._cache_stable_window_size()
        self.load_values()
        self._refresh_hardware_watermark_async()

    #: 编辑器族的浏览助手经此解析项目根；覆盖为面板自己的 `_project_root`，
    #: 让既有 `patch.object(ai_settings_panel, "_project_root", ...)` 继续生效。
    _editor_project_root = staticmethod(lambda: _project_root())
    _page_project_root = staticmethod(lambda: _project_root())

    def _refresh_hardware_watermark_async(self) -> None:
        def worker() -> None:
            payload = _load_saved_watermark_payload()
            hardware_lines = payload.get("hardware", ("UnKnow GPU 0.00 GB", "RAM 0.00 GB"))
            panel_lines = payload.get("control_panel", ("Aemeath", "AIsetting"))

            def apply_result() -> None:
                self._gpu_watermark_text = "\n".join(hardware_lines)
                self._panel_watermark_text = "\n".join(panel_lines)
                try:
                    self.update()
                except RuntimeError:
                    pass

            self._ui_thread_call.emit(apply_result)

        future = get_compute_hub().submit_latest(
            "ai_settings_hardware_watermark",
            worker,
            executor="io",
        )
        if future is None:
            _logger.debug("硬件水印查询任务仍在运行，跳过重复提交")

    @staticmethod
    def _build_title_font():
        title_font = get_ui_font(size=_TITLE_FONT_SIZE)
        title_font.setBold(True)
        return title_font

    @staticmethod
    def _build_hint_font():
        return get_ui_font(size=_HINT_FONT_SIZE)



    def _invoke_ui_callable(self, func) -> None:
        if callable(func):
            func()





    @staticmethod
    def _description_preview_value(value, max_len: int = 72) -> str:
        return _descriptions.description_preview_value(value, max_len)

    @staticmethod
    def _description_value_type(value) -> str:
        return _descriptions.description_value_type(value)

    def _build_config_single_description(self, dict_name: str, key: str, value, friendly_name: str) -> str:
        return _descriptions.build_config_single_description(dict_name, key, value, friendly_name)

    def _build_config_range_description(
        self,
        dict_name: str,
        left_key: str,
        right_key: str,
        left_value,
        right_value,
        friendly_name: str,
    ) -> str:
        return _descriptions.build_config_range_description(
            dict_name, left_key, right_key, left_value, right_value, friendly_name
        )


    def set_external_close_callback(self, callback) -> None:
        self._external_close_callback = callback

    def get_workbench_page_specs(self) -> list[tuple[str, str]]:
        return [('ai', 'AI 设置')] + [
            (category.page_id, category.tab_title)
            for category in _GENERAL_CONFIG_CATEGORIES
        ]

    def create_workbench_page(self, page_id: str) -> QWidget:
        cached = self._workbench_pages.get(page_id)
        if cached is not None:
            return cached
        if not self._workbench_attached:
            self._workbench_attached = True
            self._visible = False
            self._anim.stop()
            self._opacity.setOpacity(1.0)
            self._hide_floating_tab()
            get_layer_manager().unregister(self)
            if self._tab_floating is not None:
                get_layer_manager().unregister(self._tab_floating)
                self._tab_floating.deleteLater()
                self._tab_floating = None

        if page_id == 'ai':
            self._center_row.removeWidget(self._ai_panel)
            page = self._ai_panel
        else:
            spec = next(
                (item for item in _GENERAL_CONFIG_CATEGORIES if item.page_id == page_id),
                None,
            )
            if spec is None:
                raise KeyError(f'unknown workbench settings page: {page_id}')
            page = self._build_config_category_panel(spec)
            self._load_config_tab_values()
            self._ensure_config_defaults_integrity()

        page.hide()
        page.setProperty('workbenchEmbedded', True)
        page.setMinimumSize(0, 0)
        page.setMaximumSize(16777215, 16777215)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        page_title = page.findChild(QLabel, 'SettingsPageTitle')
        if page_title is not None:
            page_title.hide()
        self._workbench_pages[page_id] = page
        return page

    def _build_desktop_pet_update_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        return _update_page.build_desktop_pet_update_panel(
            panel,
            scaffold,
            category_title,
            title_label,
            hint_label,
            self._config_tab_meta,
            self._pet_update_actions(),
        )

    def _build_sponsor_author_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        return _about.build_sponsor_author_panel(
            panel,
            scaffold,
            category_title,
            title_label,
            hint_label,
            self._config_tab_meta,
            _project_root(),
            self._show_info_message,
        )

    def _build_contribution_list_panel(
        self,
        panel: QWidget,
        scaffold: SettingsPageScaffold,
        category_title: str,
        title_label: QLabel,
        hint_label: QLabel,
    ) -> QWidget:
        return _about.build_contribution_list_panel(
            panel,
            scaffold,
            category_title,
            title_label,
            hint_label,
            self._config_tab_meta,
            _project_root(),
            self._show_info_message,
        )

    def _set_sponsor_author_image(self, label: QLabel) -> None:
        _about.set_sponsor_author_image(label, _project_root())

    def _open_sponsor_author_link(self) -> None:
        _about.open_sponsor_author_link(self._show_info_message)

    def _open_contribution_link(self, name: str, url: str) -> None:
        _about.open_contribution_link(self._show_info_message, name, url)

    def _show_info_message(self, message: str):
        """显示信息消息框"""
        show_message(self, title="提示", text=message)









    def _raise_config_value_error(self, dict_name: str, key: str, reason: str) -> None:
        _validation.raise_config_value_error(dict_name, key, reason)

    def _validate_general_numeric(self, dict_name: str, key: str, value, kind: str, min_val: float, max_val: float) -> None:
        _validation.validate_general_numeric(dict_name, key, value, kind, min_val, max_val)

    def _validate_general_config_value(self, dict_name: str, key: str, value) -> None:
        _validation.validate_general_config_value(
            dict_name,
            key,
            value,
            choice_options=AISettingsPanel._get_choice_field_options,
            project_root=_project_root(),
        )

    def _validate_general_config_relations(self, values_by_dict: dict[str, dict]) -> None:
        _validation.validate_general_config_relations(values_by_dict)



    def _load_config_tab_values(self) -> None:
        import config.config as cc

        for meta in self._config_tab_meta.values():
            for field in meta.get("fields", []):
                kind = str(field.get("kind") or "single")
                if kind == "external_autostart":
                    editor = field.get("editor")
                    if isinstance(editor, QCheckBox):
                        editor.setChecked(self._get_autostart_enabled())
                    continue
                if kind == "external_announcement_suppression":
                    editor = field.get("editor")
                    if isinstance(editor, QCheckBox):
                        editor.setChecked(self._get_announcement_forever_suppressed())
                    continue

                dict_name = str(field.get("dict_name") or "")
                section = getattr(cc, dict_name, None)
                if not isinstance(section, dict):
                    continue
                if kind == "range_pair":
                    keys = field.get("keys") or []
                    editors = field.get("editors") or []
                    if len(keys) == 2 and len(editors) == 2:
                        for idx in range(2):
                            key = str(keys[idx])
                            if key in section:
                                self._set_config_editor_value(editors[idx], section[key])
                    continue
                if kind == "sequence":
                    key = str(field.get("key") or "")
                    if key in section:
                        self._set_sequence_editor_values(field.get("editors") or [], section[key])
                    continue
                key = str(field.get("key") or "")
                if key in section:
                    self._set_config_editor_value(field.get("editor"), section[key])

    def _collect_config_category_values(self, category_id: str) -> dict[str, dict]:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            raise ValueError("未找到配置分类")

        values: dict[str, dict] = {}
        for field in meta.get("fields", []):
            dict_name = str(field.get("dict_name") or "")
            try:
                parsed_items = self._parse_editor_value(field)
            except Exception as e:
                key_text = str(field.get("key") or ",".join(field.get("keys") or []))
                raise ValueError(f"{dict_name}.{key_text} 格式错误: {e}") from e
            target_dict = values.setdefault(dict_name, {})
            for key, parsed in parsed_items.items():
                target_dict[str(key)] = parsed
        self._validate_general_config_values(values)
        return values



    def _ensure_config_defaults_integrity(self) -> None:
        """兜底检查默认值映射，避免“恢复默认”因缺项而失效。"""
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id:
                continue
            meta = self._config_tab_meta.get(category_id)
            if not isinstance(meta, dict):
                continue
            defaults = meta.setdefault("defaults", {})
            fields = meta.get("fields", [])
            for field in fields:
                kind = str(field.get("kind") or "single")
                dict_name = str(field.get("dict_name") or "")
                if kind == "external_autostart":
                    if "default" not in field:
                        field["default"] = bool(self._get_autostart_enabled())
                    continue
                if kind == "external_announcement_suppression":
                    field.setdefault("default", False)
                    continue
                if not dict_name:
                    continue
                bucket = defaults.setdefault(dict_name, {})
                if kind == "range_pair":
                    keys = field.get("keys") or []
                    templates = field.get("templates") or []
                    if len(keys) == 2 and len(templates) == 2:
                        for idx in range(2):
                            key = str(keys[idx] or "")
                            if key and key not in bucket:
                                bucket[key] = _hardcoded_general_default(dict_name, key, templates[idx])
                    continue
                key = str(field.get("key") or "")
                if not key:
                    continue
                if key in bucket:
                    continue
                if kind == "sequence":
                    bucket[key] = _hardcoded_general_default(dict_name, key, field.get("template", []))
                else:
                    bucket[key] = _hardcoded_general_default(dict_name, key, field.get("template"))

    def _on_restore_config_category(self, category_id: str, *, emit_message: bool = True) -> None:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return
        for field in meta.get("fields", []):
            kind = str(field.get("kind") or "single")
            if kind == "external_autostart":
                editor = field.get("editor")
                if isinstance(editor, QCheckBox):
                    editor.setChecked(bool(field.get("default", self._get_autostart_enabled())))
                continue
            if kind == "external_announcement_suppression":
                editor = field.get("editor")
                if isinstance(editor, QCheckBox):
                    editor.setChecked(bool(field.get("default", False)))
                continue

            dict_name = str(field.get("dict_name") or "")
            defaults = meta.get("defaults", {})
            if dict_name not in defaults:
                continue
            if kind == "range_pair":
                keys = field.get("keys") or []
                editors = field.get("editors") or []
                if len(keys) == 2 and len(editors) == 2:
                    for idx in range(2):
                        key = str(keys[idx])
                        if key in defaults[dict_name]:
                            self._set_config_editor_value(editors[idx], defaults[dict_name][key])
                continue
            if kind == "sequence":
                key = str(field.get("key") or "")
                if key in defaults[dict_name]:
                    self._set_sequence_editor_values(field.get("editors") or [], defaults[dict_name][key])
                continue
            key = str(field.get("key") or "")
            if key in defaults[dict_name]:
                self._set_config_editor_value(field.get("editor"), defaults[dict_name][key])
        if emit_message:
            self._emit_info(f"{meta.get('title', '配置')}已恢复默认，点击“保存并退出”后生效。", min_tick=10, max_tick=90)

    def _submit_save_task(
        self,
        worker: Callable[[], None],
        completion: Callable[[], None],
    ) -> bool:
        """把配置文件写入和缓存清理移出 Qt 主线程。"""
        if self._save_task_pending:
            return False
        self._save_task_pending = True

        def finish(error: Exception | None = None) -> None:
            self._save_task_pending = False
            if error is not None:
                _logger.error("后台保存控制面板设置失败: %s", error)
                self._emit_info(f"保存失败: {error}", min_tick=20, max_tick=180)
                self._save_completion_action = None
                return
            try:
                completion()
            except Exception as exc:
                _logger.error("应用已保存控制面板设置失败: %s", exc)
                self._emit_info(f"应用保存结果失败: {exc}", min_tick=20, max_tick=180)
                self._save_completion_action = None
                return
            action = self._save_completion_action
            self._save_completion_action = None
            if callable(action):
                action()

        def on_done(future) -> None:
            try:
                future.result()
            except Exception as exc:
                self._run_on_ui_thread(lambda error=exc: finish(error))
            else:
                self._run_on_ui_thread(finish)

        try:
            future = get_compute_hub().submit_interactive_io(worker)
            future.add_done_callback(on_done)
        except Exception as exc:
            self._save_task_pending = False
            self._save_completion_action = None
            _logger.error("提交后台保存任务失败: %s", exc)
            self._emit_info(f"保存失败: {exc}", min_tick=20, max_tick=180)
            return False
        return True

    def _on_save_config_category(self, category_id: str) -> bool:
        meta = self._config_tab_meta.get(category_id)
        if not meta:
            return False
        try:
            values = self._collect_config_category_values(category_id)
            if self._save_task_pending:
                self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
                return False

            def persist() -> None:
                _save_general_config(copy.deepcopy(values))

            def completed() -> None:
                _apply_general_runtime(values)
                self._apply_external_category_fields(category_id)
                message = f"{meta.get('title', '配置')}已保存。"
                if "render_backend" in values.get("UI", {}):
                    message += " 渲染后端将在重启后生效。"
                if "LAYER_VALUES" in values:
                    message += " 图层顺序将在重启后生效。"
                self._emit_info(message)

            return self._submit_save_task(persist, completed)
        except Exception as e:
            _logger.error("保存配置分类失败(%s): %s", category_id, e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_config_category_and_exit(self, category_id: str) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = self.fade_out
        saved = self._on_save_config_category(category_id)
        if not saved:
            self._save_completion_action = None
        elif not self._save_task_pending and callable(self._save_completion_action):
            action = self._save_completion_action
            self._save_completion_action = None
            action()

    def _install_line_edit_context_menus(self) -> None:
        for edit in self.findChildren(QLineEdit):
            if bool(getattr(edit, "_cn_context_menu_bound", False)):
                continue
            edit.setContextMenuPolicy(Qt.CustomContextMenu)
            edit.customContextMenuRequested.connect(
                lambda pos, target=edit: self._show_line_edit_context_menu(target, pos)
            )
            setattr(edit, "_cn_context_menu_bound", True)

    def _show_line_edit_context_menu(self, edit: QLineEdit, pos: QPoint) -> None:
        if not isinstance(edit, QLineEdit):
            return

        menu = QMenu(edit)
        font = get_ui_font(size=_CONFIG_FONT_SIZE)
        font.setBold(True)
        menu.setFont(font)

        can_edit = not bool(edit.isReadOnly())
        has_selection = bool(edit.hasSelectedText())
        can_paste = can_edit and bool(QApplication.clipboard().text())

        action_cut = menu.addAction("剪切")
        action_copy = menu.addAction("复制")
        action_paste = menu.addAction("粘贴")

        action_cut.setEnabled(can_edit and has_selection)
        action_copy.setEnabled(has_selection)
        action_paste.setEnabled(can_paste)

        chosen = menu.exec_(edit.mapToGlobal(pos))
        if chosen is action_cut:
            edit.cut()
        elif chosen is action_copy:
            edit.copy()
        elif chosen is action_paste:
            edit.paste()

    def _apply_project_fonts(self) -> None:
        """将面板及子控件字体统一为项目字体。"""
        base_font = get_ui_font()
        config_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        config_font.setBold(True)
        self.setFont(base_font)

        # 配置项与配置内容：统一粗体并放大 2xp。
        for widget in self.findChildren(QLabel):
            if widget is self._title_label or widget is self._hint_label:
                continue
            if widget.property("preserveCustomFont"):
                continue
            widget.setFont(config_font)
        for widget_type in (QLineEdit, QComboBox, QPushButton, QCheckBox):
            for widget in self.findChildren(widget_type):
                widget.setFont(config_font)

        # 下拉弹层是独立视图，需要显式设置字体。
        dropdown_font = get_ui_font(size=_DROPDOWN_ITEM_FONT_SIZE)
        dropdown_font.setBold(True)
        for combo in (self._force_mode, self._gpu_mode):
            view = combo.view()
            if view is not None:
                view.setFont(dropdown_font)

        # 标题与标题右侧说明保持统一样式。
        title_font = self._build_title_font()
        hint_font = self._build_hint_font()
        self._title_label.setFont(title_font)
        self._hint_label.setFont(hint_font)

        for meta in self._config_tab_meta.values():
            title_label = meta.get("title_label")
            hint_label = meta.get("hint_label")
            if isinstance(title_label, QLabel):
                title_label.setFont(title_font)
            if isinstance(hint_label, QLabel):
                hint_label.setFont(hint_font)
            section_title_labels = meta.get("section_title_labels") or []
            section_hint_labels = meta.get("section_hint_labels") or []
            section_title_font = get_ui_font(size=max(scale_px(12, min_abs=10), _CONFIG_FONT_SIZE))
            section_title_font.setBold(True)
            for widget in section_title_labels:
                if isinstance(widget, QLabel):
                    widget.setFont(section_title_font)
            for widget in section_hint_labels:
                if isinstance(widget, QLabel):
                    widget.setFont(hint_font)

        tab_font = get_ui_font(size=_CONFIG_FONT_SIZE)
        tab_font.setBold(True)
        # 设置标签按钮字体
        if hasattr(self, '_tab_buttons') and self._tab_buttons:
            for btn in self._tab_buttons:
                btn.setFont(tab_font)
        self._install_line_edit_context_menus()

    def _apply_style(self) -> None:
        """整段 QSS 已下沉到 `visuals/ai_settings_panel_visuals.py`（描述层）。"""
        self.setStyleSheet(ai_settings_panel_stylesheet())

    def refresh_workbench_theme(self) -> None:
        """重新应用工作台主题到面板自有样式和自定义水印控件。"""
        self._apply_style()
        for button in self.findChildren(_ContributionCardButton):
            button._apply_watermark(button.underMouse())
        # 垂直标签栏样式已在 ai_settings_tabs.py 中通过按钮样式设置

    def _layout_top_tab_bar(self) -> None:
        layout_ai_settings_tab_bar(self)

    def _show_floating_tab(self) -> None:
        show_ai_settings_tab_bar(self)

    def _hide_floating_tab(self) -> None:
        hide_ai_settings_tab_bar(self)

    def _layout_config_panels(self) -> None:
        layout_ai_settings_tab_panels(self)

    def _on_top_tab_changed(self, index: int) -> None:
        set_active_ai_settings_tab(self, index)

    def _cache_stable_window_size(self) -> tuple[int, int]:
        ai_panel = getattr(self, "_ai_panel", None)
        restore_visible = bool(ai_panel is not None and ai_panel.isVisible())
        if ai_panel is not None:
            ai_panel.show()

        self.adjustSize()
        target_w = max(self.minimumWidth(), int(round(self.width() * _PANEL_SCALE)))
        target_h = max(self.minimumHeight(), int(round(self.height() * _PANEL_SCALE)))
        self._stable_window_size = (target_w, target_h)

        if ai_panel is not None and not restore_visible:
            ai_panel.hide()
        return self._stable_window_size

    def load_values(self) -> None:
        self._set_values_to_form(load_ai_values(_DEFAULT_VALUES))
        try:
            import config.config as cc
            from config.music.volume_config import get_volume_config

            cc.CLOUD_MUSIC["default_volume"] = get_volume_config().get_volume()
        except Exception as exc:
            _logger.debug("加载音乐音量用户配置失败: %s", exc)
        self._load_config_tab_values()

    def show_centered(self) -> None:
        self.load_values()
        self._refresh_voice_package_ui()
        current_index = 0
        # 获取当前选中的标签索引（从按钮组或按钮列表）
        if hasattr(self, '_tab_button_group') and self._tab_button_group is not None:
            current_index = max(0, self._tab_button_group.checkedId())
        elif hasattr(self, '_tab_buttons') and self._tab_buttons:
            for i, btn in enumerate(self._tab_buttons):
                if btn.isChecked():
                    current_index = i
                    break
        target_panel = None
        if 0 <= current_index < len(self._tab_pages):
            target_panel = self._tab_pages[current_index]
        if target_panel is None:
            target_panel = self._ai_panel
        if target_panel is not None:
            # 在布局测量前确保目标面板可见，避免上次停留在其它标签页后被隐藏导致尺寸被压缩。
            target_panel.show()
        target_w, target_h = self._stable_window_size or self._cache_stable_window_size()
        self.resize(target_w, target_h)

        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        if screen is not None:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)
        self._on_top_tab_changed(current_index)

        self._visible = True
        self.show()
        self._show_floating_tab()
        self._layout_config_panels()
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._animate(1.0)

    def fade_out(self) -> None:
        if self._external_close_callback is not None:
            self._external_close_callback()
            return
        self._visible = False
        self._hide_floating_tab()
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        animate_opacity(self._anim, self._opacity, target)

    def _on_anim_finished(self) -> None:
        if not self._visible:
            self._hide_floating_tab()
            self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_top_tab_bar()
        self._layout_config_panels()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._layout_top_tab_bar()
        self._layout_config_panels()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._visible:
            self._subscribe_border_effect_events()
            self._show_floating_tab()
            get_layer_manager().enforce_burst()

    def hideEvent(self, event) -> None:
        self._unsubscribe_border_effect_events()
        self._hide_floating_tab()
        super().hideEvent(event)

    def _subscribe_border_effect_events(self) -> None:
        if not self._tick_subscribed:
            self._ec.subscribe(EventType.TICK, self._on_tick)
            self._tick_subscribed = True

    def _unsubscribe_border_effect_events(self) -> None:
        if self._tick_subscribed:
            self._ec.unsubscribe(EventType.TICK, self._on_tick)
            self._tick_subscribed = False

    def deleteLater(self) -> None:
        self._unsubscribe_border_effect_events()
        self._unsubscribe_autostart_events()
        self._hide_floating_tab()
        for attr_name in ("_voice_installer_dialog",):
            dialog = getattr(self, attr_name, None)
            if dialog is not None:
                try:
                    dialog.cleanup()
                except RuntimeError:
                    pass
                setattr(self, attr_name, None)
        if self._tab_floating is not None:
            self._tab_floating.deleteLater()
            self._tab_floating = None
        try:
            get_layer_manager().unregister(self)
        except (AttributeError, RuntimeError):
            pass
        super().deleteLater()

    def _random_border_spawn_point(self) -> tuple[int, int] | None:
        w = int(self.width())
        h = int(self.height())
        if w <= 0 or h <= 0:
            return None

        gx = int(self.x())
        gy = int(self.y())
        band = max(1, int(self._layer))
        edge = random.choice(("top", "bottom", "left", "right"))

        if edge == "top":
            x = random.randint(gx, gx + w - 1)
            y = random.randint(gy, min(gy + band - 1, gy + h - 1))
        elif edge == "bottom":
            x = random.randint(gx, gx + w - 1)
            y = random.randint(max(gy, gy + h - band), gy + h - 1)
        elif edge == "left":
            x = random.randint(gx, min(gx + band - 1, gx + w - 1))
            y = random.randint(gy, gy + h - 1)
        else:
            x = random.randint(max(gx, gx + w - band), gx + w - 1)
            y = random.randint(gy, gy + h - 1)
        return x, y

    def _request_border_flicker(self) -> None:
        pos = self._random_border_spawn_point()
        if pos is None:
            return
        self._ec.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "flicker_data",
            "area_type": "point",
            "area_data": pos,
        }))

    def _on_tick(self, event: Event) -> None:
        if not self.isVisible():
            return
        try:
            tick_count = int((event.data or {}).get("tick_count", 0))
        except Exception:
            tick_count = 0
        if tick_count <= 0:
            self._tick_counter += 1
            tick_count = self._tick_counter
        else:
            self._tick_counter = tick_count

        if tick_count % 5 == 0:
            for _ in range(random.randint(2, 4)):
                self._request_border_flicker()

    def _is_interactive_widget(self, widget) -> bool:
        interactive_types = (QLineEdit, QComboBox, QPushButton, QCheckBox, QSlider, QListView, QScrollArea)
        cur = widget
        while cur is not None and cur is not self:
            if isinstance(cur, interactive_types):
                return True
            cur = cur.parentWidget()
        return False

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)

        if event.button() == Qt.LeftButton:
            hit = self.childAt(event.pos())
            if not self._is_interactive_widget(hit):
                self._dragging = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


    def _set_values_to_form(self, values: dict) -> None:
        self._api_key.set_raw_text(str(values.get("api_key", "")))
        self._welfare_intelligence_boost.setChecked(bool(values.get("welfare_intelligence_boost", False)))
        self._api_base_url.setText(str(values.get("api_base_url", "")))
        self._sync_manual_api_provider_selection()
        self._refresh_manual_api_model_choices(str(values.get("api_model", "")))
        self._ollama_base_url.setText(str(values.get("ollama_base_url", "")))
        self._refresh_ollama_model_choices(str(values.get("ollama_model", "")))
        gpu_mode = _gpu_mode_from_num_gpu(values.get("num_gpu", -1))
        gpu_idx = self._gpu_mode.findData(gpu_mode)
        self._gpu_mode.setCurrentIndex(max(0, gpu_idx))
        self._num_thread.setText(str(values.get("num_thread", 0)))
        self._api_temperature.setText(str(values.get("api_temperature", 0.8)))
        self._model_vision.setText(str(values.get("model_vision", 0)))
        self._gsv_auto_start.setChecked(bool(values.get("gsv_auto_start", True)))
        self._gsv_gpu_hybrid.setChecked(bool(values.get("gsv_gpu_hybrid", False)))
        self._gsv_nvidia_cuda_acceleration.setChecked(bool(
            values.get("gsv_nvidia_cuda_acceleration", False)
        ))
        self._gsv_temperature.setText(str(values.get("gsv_temperature", 1.35)))
        self._gsv_top_k.setText(str(values.get("gsv_top_k", 15)))
        self._gsv_top_p.setText(str(values.get("gsv_top_p", 1.0)))
        self._gsv_repetition_penalty.setText(str(values.get("gsv_repetition_penalty", 1.6)))
        self._gsv_speed_factor.setText(str(values.get("gsv_speed_factor", 1.1)))
        split_method = str(values.get("gsv_text_split_method", "cut0"))
        split_index = self._gsv_text_split_method.findData(split_method)
        self._gsv_text_split_method.setCurrentIndex(max(0, split_index))
        self._gsv_fragment_interval.setText(str(values.get("gsv_fragment_interval", 0.3)))
        self._gsv_seed.setText(str(values.get("gsv_seed", -1)))
        self._ai_voice_max_chars.setText(str(values.get(
            "ai_voice_max_chars",
            AI_VOICE_MAX_CHARS_DEFAULT,
        )))
        self._gsv_cache_max_files.setText(str(values.get("gsv_cache_max_files", 20)))
        self._memory_context_limit.setText(str(values.get("memory_context_limit", 12)))
        self._memory_recall_count.setText(str(values.get("memory_recall_count", 5)))
        self._api_enable_thinking.setChecked(bool(values.get("api_enable_thinking", False)))
        self._auto_companion_enabled.setChecked(bool(values.get("auto_companion_enabled", True)))
        self._auto_companion_interval_minutes.set_value(values.get("auto_companion_interval_minutes", 2))

        mode_value = str(values.get("force_reply_mode", "") or "").strip()
        idx = self._force_mode.findData(mode_value)
        self._force_mode.setCurrentIndex(max(0, idx))
        # 这一步同时按模式收起/展开间隔滑条，并按开关决定是否可调。
        self._update_reply_mode_sections()







    def _refresh_voice_package_ui(self) -> None:
        status = get_voice_package_status()
        self._voice_package_status = status
        self._gsv_launcher_available = not status.install_required
        self._voice_package_banner.set_package_status(status)
        self._voice_package_management.set_package_status(status)
        self._update_gsv_settings_visibility()















    def _emit_info(self, text: str, min_tick: int = 12, max_tick: int = 140) -> None:
        self._ec.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": min_tick,
            "max": max_tick,
        }))

    def _pet_update_actions(self) -> _update_page.PetUpdateActions:
        """把更新页动作要的回调打包成显式依赖；两个对话框实例仍以面板字段为准。"""
        return _update_page.PetUpdateActions(
            check_updates=self._on_check_updates,
            sync_dev_build=self._on_sync_dev_build,
            open_quark_manual=self._open_quark_manual_update,
            show_qq_group=self._show_qq_group_qrcode,
            uninstall_pet=self._on_uninstall_pet,
            show_info=self._show_info_message,
            emit_info=self._emit_info,
            fade_out=self.fade_out,
            event_center=self._ec,
            root_dir=_project_root(),
            dialog_parent=self,
            update_dialog=self._update_dialog,
            qq_group_dialog=self._qq_group_dialog,
        )

    def _run_pet_update_action(self, action):
        """跑一个更新页动作，并把动作里新建的对话框缓存回面板字段（与搬出前同一份状态）。"""
        actions = self._pet_update_actions()
        result = action(actions)
        self._update_dialog = actions.update_dialog
        self._qq_group_dialog = actions.qq_group_dialog
        return result

    def _ensure_update_dialog(self) -> DesktopPetUpdateDialog:
        return self._run_pet_update_action(_update_page.ensure_update_dialog)

    def _open_update_dialog(self, mode: str) -> None:
        self._run_pet_update_action(lambda actions: _update_page.open_update_dialog(actions, mode))

    def _on_check_updates(self) -> None:
        self._run_pet_update_action(_update_page.on_check_updates)

    def _on_sync_dev_build(self) -> None:
        self._run_pet_update_action(_update_page.on_sync_dev_build)

    def _ensure_qq_group_dialog(self) -> QQGroupDialog:
        return self._run_pet_update_action(_update_page.ensure_qq_group_dialog)

    def _open_quark_manual_update(self) -> None:
        self._run_pet_update_action(_update_page.open_quark_manual_update)

    def _show_qq_group_qrcode(self) -> None:
        self._run_pet_update_action(_update_page.show_qq_group_qrcode)

    def _on_uninstall_pet(self) -> None:
        self._run_pet_update_action(_update_page.uninstall_pet)

    def _on_restore_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._ensure_config_defaults_integrity()
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id:
                continue
            self._on_restore_config_category(category_id, emit_message=False)
        self._emit_info("已恢复默认配置，保存后会移除对应用户覆盖。", min_tick=10, max_tick=90)




    def _on_save(self, *, apply_runtime: bool = True) -> bool:
        try:
            ai_values = self._collect_values()
            general_values = self._collect_all_general_config_values()
            if self._save_task_pending:
                self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
                return False

            def persist() -> None:
                save_ai_values(copy.deepcopy(ai_values), _DEFAULT_VALUES)
                _save_general_config(copy.deepcopy(general_values))
                if apply_runtime:
                    try:
                        from lib.script.gsvmove import get_gsvmove_service

                        get_gsvmove_service().cleanup_saved_audio_cache()
                    except Exception as trim_exc:
                        _logger.warning("应用 GSV 语音缓存上限失败: %s", trim_exc)

            def completed() -> None:
                if apply_runtime:
                    apply_ai_runtime(ai_values, _DEFAULT_VALUES)
                    _apply_general_runtime(general_values)
                self._apply_all_external_config_fields()
                self._emit_info("控制面板设置已保存，重启程序后完整生效。")

            return self._submit_save_task(persist, completed)
        except Exception as e:
            _logger.error("保存控制面板设置失败: %s", e)
            self._emit_info(f"保存失败: {e}", min_tick=20, max_tick=180)
            return False

    def _on_save_and_exit(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = self.fade_out
        if self._on_save():
            if not self._save_task_pending and callable(self._save_completion_action):
                action = self._save_completion_action
                self._save_completion_action = None
                action()
            return
        self._save_completion_action = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        visual = build_ai_settings_panel_visual(
            Size(rect.width(), rect.height()),
            top_watermark_text=self._gpu_watermark_text,
            side_watermark_text=self._panel_watermark_text,
            inset=self._layer,
        )
        self._draw_backend.render(visual.batch, painter)
