"""AI 设置面板的「配置取值 / 回填 / 恢复 / 保存」控制器。

批次 3 第六轮把 AI 主页面（视图 + 本页取值）切到 `ui/ai_settings_page.py` 后，面板里剩下的
是**与页面视图无关的那一半**：按 `_config_tab_meta` 取值（`_collect_config_category_values`）、
回填（`_set_values_to_form` / `_load_config_tab_values`）、默认值兜底与逐分类恢复
（`_ensure_config_defaults_integrity` / `_on_restore_config_category` / `_on_restore_defaults`）、
通用配置的取值校验（`_validate_general_*`），以及异步保存任务调度与两个保存入口
（`_submit_save_task` / `_on_save` / `_on_save_config_category` 及各自的“并退出”变体）。
它们只读 `_config_tab_meta` / `_DEFAULT_VALUES` 与事件中心，不持有任何本页控件，因此整体切出。

切分保持逐行等价：`ConfigStoreMixin` 的方法体与搬出前一致（缩进也未变），
`AISettingsPanel` 只是多继承本 mixin，`ai_settings_tabs.py` / `ai_settings_config_page.py` /
`ai_settings_page.py` 的调用点零改动。面板级设施（`_emit_info`、`_run_on_ui_thread`、
`_apply_external_category_fields`、编辑器族的 `_set_*`）留在原处，经 `self` 解析——
它们分属 `AISettingsPageMixin` / `ConfigPageMixin` / `ConfigEditorMixin`，同一宿主上皆可用。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from PyQt5.QtWidgets import QCheckBox

from config.general_user_settings import save_general_values
from config.ollama_config import AI_VOICE_MAX_CHARS_DEFAULT
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.script.ui import ai_settings_validation as _validation
from lib.script.ui.ai_settings_config_schema import (
    hardcoded_general_default as _hardcoded_general_default,
)
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES as _DEFAULT_VALUES
from lib.script.ui.ai_settings_page import _gpu_mode_from_num_gpu
from lib.script.ui.ai_settings_storage import apply_ai_runtime, save_ai_values
from lib.script.workbench.settings import GENERAL_CONFIG_CATEGORIES as _GENERAL_CONFIG_CATEGORIES

_logger = get_logger(__name__)

#: 项目根目录解析器；宿主（`AISettingsPanel`）覆盖它，让既有
#: `patch.object(ai_settings_panel, "_project_root", ...)` 继续生效（同第 51 / 53 节）。
_store_project_root: "Callable[[], Path]" = staticmethod(
    lambda: Path(__file__).resolve().parents[3]
)


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


class ConfigStoreMixin:
    """配置取值 / 回填 / 恢复与异步保存；由 `AISettingsPanel` 混入。"""

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

    def _raise_config_value_error(self, dict_name: str, key: str, reason: str) -> None:
        _validation.raise_config_value_error(dict_name, key, reason)

    def _validate_general_numeric(self, dict_name: str, key: str, value, kind: str, min_val: float, max_val: float) -> None:
        _validation.validate_general_numeric(dict_name, key, value, kind, min_val, max_val)

    def _validate_general_config_value(self, dict_name: str, key: str, value) -> None:
        _validation.validate_general_config_value(
            dict_name,
            key,
            value,
            choice_options=self._get_choice_field_options,
            project_root=_store_project_root(),
        )

    def _validate_general_config_relations(self, values_by_dict: dict[str, dict]) -> None:
        _validation.validate_general_config_relations(values_by_dict)

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

    def _on_restore_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._ensure_config_defaults_integrity()
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id:
                continue
            self._on_restore_config_category(category_id, emit_message=False)
        self._emit_info("已恢复默认配置，保存后会移除对应用户覆盖。", min_tick=10, max_tick=90)

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


__all__ = [
    "ConfigStoreMixin",
]
