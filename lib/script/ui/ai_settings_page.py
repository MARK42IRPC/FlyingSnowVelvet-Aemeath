"""AI 设置面板的「AI 主页面」：`_build_ui` 与本页专属的取值/回填闭环。

`ai_settings_panel.AISettingsPanel` 原来把 AI 主页面的整段视图装配写在一个 508 行的
`_build_ui` 里（回复模式 / 手动 API / 本地 Ollama / 语音与 GSV / 记忆 / 动作栏都在同一个
方法体内），旁边还散着本页专属的取值（`_collect_values`）、回填（`_update_*_visibility`、
Ollama 与手动 API 模型刷新）、以及页面动作（恢复默认 / 保存 / 保存并重启）。批次 3
第六轮把这些整体切到这里。

切分保持逐行等价：`AISettingsPageMixin` 的方法体与搬出前一致（缩进也未变），
`AISettingsPanel` 只是多继承本 mixin，`__init__` 里的 `self._build_ui()` 与
`lib/script/ui/ai_settings_tabs.py` 的调用点零改动。面板级设施（动画 / 提示 / 保存任务
调度 / 配置分类页取值 / 面板状态回填）留在 `AISettingsPanel`——它们被本页与其他页共用。

三处对面板类的硬引用在本轮一并改写（沿用第四轮先例）：`AISettingsPanel._get_choice_field_options`
→ `self._get_choice_field_options`（同类族方法）、`AISettingsPanel._normalize_manual_api_base_url(...)`
→ `self._normalize_manual_api_base_url(...)`。`_num_gpu_from_mode` / `_GPU_MODE_*` /
`WELFARE_AUTO_COMPANION_INTERVAL_MINUTES` / `_AI_HINT_TEXT` / `_logger` 只有本页在用，
按「定义随调用点走」随本模块一起搬走。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.config import UI
from config.ollama_config import (
    AI_VOICE_MAX_CHARS_DEFAULT,
    AI_VOICE_MAX_CHARS_MAX,
    AI_VOICE_MAX_CHARS_MIN,
)
from config.scale import scale_px
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType
from lib.core.logger import get_logger
from lib.script.chat.handler_auto_companion import WELFARE_AUTO_COMPANION_INTERVAL_MS
from lib.script.chat.ollama_registry import get_available_model_names, get_model_list_error
from lib.script.chat.persona_storage import ensure_user_persona_file
from lib.script.gsvmove import get_voice_package_status
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES as _DEFAULT_VALUES
from lib.script.ui.ai_settings_editors import _DecimalSliderField as _DecimalSliderField
from lib.script.ui.ai_settings_storage import load_ai_values, save_ai_values, apply_ai_runtime
from lib.script.ui.ai_settings_tabs import attach_ai_settings_tabs
from lib.script.ui.ai_settings_validators import validate_ai_values
from lib.script.ui.office_mode_settings import (
    describe_form_row as _describe_form_row_helper,
    fetch_api_models as _fetch_api_models,
    manual_api_models_url as _manual_api_models_url,
    normalize_api_base_url as _normalize_api_base_url,
    parse_api_models as _parse_api_models,
    set_widget_description as _set_widget_description_helper,
)
from lib.script.ui.voice_package_installer import (
    VoicePackageInstallBanner,
    VoicePackageInstallerDialog,
    VoicePackageManagementBar,
)
from lib.script.ui.workbench_settings_layout import (
    SettingsPageScaffold,
    SmoothScrollArea,
)
from lib.script.ui.ai_settings_page_sections import AISettingsPageSectionsMixin
from lib.script.workbench.settings import GENERAL_CONFIG_CATEGORIES as _GENERAL_CONFIG_CATEGORIES
from lib.script.ui import ai_settings_config_parse as _config_parse
from lib.script.ui import ai_settings_validation as _validation

_logger = get_logger(__name__)

#: 运行时 num_gpu 只暴露三档（CPU / 尽量卸载到 GPU / 交给 llama.cpp 自动）。
_GPU_MODE_CPU = "cpu"
_GPU_MODE_GPU = "gpu"
_GPU_MODE_AUTO = "auto"

#: 福利 API 模式下自动陪伴固定 6 分钟。真源在
#: ``lib.script.chat.handler_auto_companion.WELFARE_AUTO_COMPANION_INTERVAL_MS``，
#: 这里只取它的分钟数用于预填滑条与校验，避免两处各写一个 6。
WELFARE_AUTO_COMPANION_INTERVAL_MINUTES = int(
    WELFARE_AUTO_COMPANION_INTERVAL_MS[0] // 60000
)

#: AI 主页面没有自带说明时的兜底提示，与面板同源同值。
_AI_HINT_TEXT = "保存后会写入本地 AI 配置文件，建议重启程序后完整生效"

#: 项目根目录解析器；宿主（`AISettingsPanel`）覆盖它，让既有
#: `patch.object(ai_settings_panel, "_project_root", ...)` 继续生效（同第 51 节的
#: `_editor_project_root`）。本页只有 `_validate_general_config_values` 读它。
_page_project_root: "Callable[[], Path]" = staticmethod(
    lambda: Path(__file__).resolve().parents[3]
)


def _gpu_mode_from_num_gpu(num_gpu_value) -> str:
    try:
        num_gpu = int(num_gpu_value)
    except (TypeError, ValueError):
        return _GPU_MODE_AUTO
    if num_gpu == 0:
        return _GPU_MODE_CPU
    if num_gpu > 0:
        return _GPU_MODE_GPU
    return _GPU_MODE_AUTO


def _num_gpu_from_mode(mode: str) -> int:
    if mode == _GPU_MODE_CPU:
        return 0
    if mode == _GPU_MODE_GPU:
        # 使用较大层数，尽量将更多层卸载到 GPU。
        return 999
    return -1


class AISettingsPageMixin(AISettingsPageSectionsMixin):
    """AI 主页面的装配与取值/回填闭环；由 `AISettingsPanel` 混入。"""

    @staticmethod
    def _set_widget_description(widget: QWidget | None, text: str) -> None:
        _set_widget_description_helper(widget, text)

    def _set_form_row_description(self, form: QFormLayout, field_widget: QWidget, text: str) -> None:
        _describe_form_row_helper(form, field_widget, text)

    def _run_on_ui_thread(self, func: Callable[[], None]) -> None:
        if threading.current_thread() is threading.main_thread():
            func()
        else:
            self._ui_thread_call.emit(func)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(
            self._border + scale_px(10, min_abs=8),
            self._border + scale_px(8, min_abs=6),
            self._border + scale_px(10, min_abs=8),
            self._border + scale_px(10, min_abs=8),
        )
        root_layout.setSpacing(0)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)
        self._center_row = center_row
        content_panel = QWidget(self)
        content_panel.setMinimumSize(scale_px(600, min_abs=560), scale_px(420, min_abs=380))
        content_panel.setMaximumSize(16777215, 16777215)
        content_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center_row.addWidget(content_panel, 1)
        root_layout.addLayout(center_row, 1)
        self._ai_panel = content_panel
        self._tab_pages = [self._ai_panel]

        scaffold = SettingsPageScaffold(
            content_panel,
            "AI设置",
            _AI_HINT_TEXT,
            scroll_factory=SmoothScrollArea,
        )
        self._ai_scaffold = scaffold
        self._title_label = scaffold.title_label
        self._hint_label = scaffold.description_label

        self._voice_package_status = get_voice_package_status()
        self._voice_package_banner = VoicePackageInstallBanner(scaffold.content)
        self._voice_package_banner.install_requested.connect(self._on_install_voice_package)
        self._voice_package_banner.set_package_status(self._voice_package_status)
        scaffold.content_layout.addWidget(self._voice_package_banner)
        self._voice_package_management = VoicePackageManagementBar(scaffold.content)
        self._voice_package_management.package_removed.connect(self._on_voice_package_removed)
        self._voice_package_management.removal_failed.connect(self._on_voice_package_removal_failed)
        self._voice_package_management.set_package_status(self._voice_package_status)
        scaffold.content_layout.addWidget(self._voice_package_management)

        self._build_reply_mode_section(scaffold)
        self._build_welfare_section(scaffold)
        self._build_manual_api_section(scaffold)
        self._build_ollama_section(scaffold)
        self._build_generation_section(scaffold)
        self._build_reply_mode_sync()
        self._build_voice_section(scaffold)
        self._build_memory_section(scaffold)

        scaffold.finish()
        self._reload_btn = scaffold.add_action("恢复本页默认", self._on_restore_ai_defaults)
        self._save_exit_btn = scaffold.add_action("保存更改", self._on_save_ai_action, primary=True)
        self._save_restart_btn = scaffold.add_action("保存并重启", self._on_save_and_restart)
        self._save_restart_btn.setObjectName("SettingsRestartAction")
        self._save_restart_btn.setProperty("restartAction", True)

        if self._lazy_workbench_pages:
            self._tab_pages = [self._ai_panel]
        else:
            attach_ai_settings_tabs(self, _GENERAL_CONFIG_CATEGORIES)
        self._ensure_config_defaults_integrity()

    @staticmethod
    def _open_path_with_system_default(path: Path) -> None:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], shell=False)
            return
        if os.name == "posix":
            subprocess.Popen(["xdg-open", str(path)], shell=False)
            return
        subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)

    def _on_open_persona_file(self) -> None:
        try:
            candidate = ensure_user_persona_file()
            self._open_path_with_system_default(candidate)
            self._emit_info(f"已打开人格文件：{candidate.name}", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开人格文件失败: %s", e)
            self._emit_info(f"打开人格文件失败: {e}", min_tick=20, max_tick=180)

    def _on_open_ollama_app(self) -> None:
        candidates: list[Path] = []
        local_app = os.getenv("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Programs" / "Ollama" / "Ollama.exe")
        program_files = os.getenv("PROGRAMFILES")
        if program_files:
            candidates.append(Path(program_files) / "Ollama" / "Ollama.exe")
        program_files_x86 = os.getenv("PROGRAMFILES(X86)")
        if program_files_x86:
            candidates.append(Path(program_files_x86) / "Ollama" / "Ollama.exe")

        for candidate in candidates:
            if candidate and candidate.exists():
                try:
                    if hasattr(os, "startfile"):
                        os.startfile(str(candidate))  # type: ignore[attr-defined]
                    else:
                        subprocess.Popen([str(candidate)], shell=False)
                    self._emit_info("已尝试打开 Ollama 应用，请在其中下载或管理模型。", min_tick=10, max_tick=90)
                    return
                except Exception as e:
                    _logger.error("打开 Ollama 应用失败: %s", e)
                    break

        try:
            webbrowser.open("https://ollama.com/download")
            self._emit_info("未找到本地 Ollama 应用，已打开 Ollama 下载页面。", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开 Ollama 下载页面失败: %s", e)
            self._emit_info(f"打开 Ollama 页面失败: {e}", min_tick=20, max_tick=180)

    def _on_open_gsv_cache_dir(self) -> None:
        try:
            from lib.script.gsvmove import get_gsvmove_service

            cache_dir = get_gsvmove_service().get_saved_audio_cache_root()
            if hasattr(os, "startfile"):
                os.startfile(str(cache_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", str(cache_dir)], shell=False)
            self._emit_info("已打开 ONNX 语音缓存文件夹。", min_tick=10, max_tick=90)
        except Exception as e:
            _logger.error("打开 ONNX 语音缓存文件夹失败: %s", e)
            self._emit_info(f"打开 ONNX 语音缓存文件夹失败: {e}", min_tick=20, max_tick=180)

    def _ensure_voice_installer_dialog(self) -> VoicePackageInstallerDialog:
        if self._voice_installer_dialog is None:
            dialog = VoicePackageInstallerDialog()
            dialog.install_succeeded.connect(self._on_voice_package_installed)
            self._voice_installer_dialog = dialog
        return self._voice_installer_dialog

    def _on_install_voice_package(self) -> None:
        dialog = self._ensure_voice_installer_dialog()
        if dialog.is_busy():
            self.fade_out()
            delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))
            QTimer.singleShot(delay_ms, dialog.show_dialog)
            return
        self.fade_out()
        delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))
        QTimer.singleShot(delay_ms, dialog.show_dialog)

    def _on_voice_package_installed(self, _result=None) -> None:
        values = load_ai_values(_DEFAULT_VALUES)
        values["gsv_auto_start"] = True
        save_ai_values(values, _DEFAULT_VALUES)
        apply_ai_runtime(values, _DEFAULT_VALUES)
        self._gsv_auto_start.setChecked(True)
        self._refresh_voice_package_ui()
        try:
            from lib.script.gsvmove import get_gsvmove_service

            get_gsvmove_service().reload_voice_package()
        except Exception as exc:
            _logger.warning("安装后预热 ONNX 语音包失败: %s", exc)

    def _on_voice_package_removed(self, _package_root=None) -> None:
        self._refresh_voice_package_ui()
        self._emit_info("ONNX 语音包已删除，可通过安装入口重新安装。", min_tick=12, max_tick=120)

    def _on_voice_package_removal_failed(self, message: str) -> None:
        self._refresh_voice_package_ui()
        if self._voice_package_status.kind == "installed":
            try:
                from lib.script.gsvmove import get_gsvmove_service

                get_gsvmove_service().reload_voice_package()
            except Exception as exc:
                _logger.warning("删除失败后恢复 ONNX 语音包失败: %s", exc)
        self._emit_info(f"删除 ONNX 语音包失败：{message}", min_tick=20, max_tick=180)

    def _parse_editor_value(self, field: dict) -> dict[str, object]:
        return _config_parse.parse_editor_value(field, widget=type(self))

    def _validate_general_config_values(self, values_by_dict: dict[str, dict]) -> None:
        _validation.validate_general_config_values(
            values_by_dict,
            choice_options=self._get_choice_field_options,
            project_root=_page_project_root(),
        )

    def _validate_ai_values(self, values: dict) -> None:
        validate_ai_values(values)

    def _collect_all_general_config_values(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id or category_id not in self._config_tab_meta:
                continue
            values = self._collect_config_category_values(category_id)
            for dict_name, items in values.items():
                target = merged.setdefault(str(dict_name), {})
                target.update(items)
        self._validate_general_config_values(merged)
        return merged

    def _apply_all_external_config_fields(self) -> None:
        for category in _GENERAL_CONFIG_CATEGORIES:
            category_id = category.page_id
            if not category_id:
                continue
            self._apply_external_category_fields(category_id)

    def _collect_values(self) -> dict:
        force_mode = str(self._force_mode.currentData() or "").strip()
        if force_mode not in ("0", "1", "2", "3", "4"):
            raise ValueError("回复模式值无效")

        gpu_mode = str(self._gpu_mode.currentData() or _GPU_MODE_AUTO)
        num_gpu = _num_gpu_from_mode(gpu_mode)

        try:
            num_thread = int(self._num_thread.text().strip() or "0")
        except ValueError as e:
            raise ValueError("CPU线程数必须是整数") from e
        if num_thread < 0:
            raise ValueError("CPU线程数不能小于 0")

        try:
            api_temperature = float(self._api_temperature.text().strip() or "0.8")
        except ValueError as e:
            raise ValueError("采样温度必须是数字") from e
        if not (0.0 <= api_temperature <= 2.0):
            raise ValueError("采样温度范围应为 0~2")

        try:
            model_vision = int(float(self._model_vision.text().strip() or "0"))
        except ValueError as e:
            raise ValueError("模型视力必须是整数") from e
        if not (0 <= model_vision <= 100):
            raise ValueError("模型视力范围应为 0~100")

        try:
            gsv_temperature = float(self._gsv_temperature.text().strip() or str(_DEFAULT_VALUES["gsv_temperature"]))
        except ValueError as e:
            raise ValueError("GSV服务温度必须是数字") from e
        if not (0.01 <= gsv_temperature <= 2.0):
            raise ValueError("GSV服务温度范围应为 0.01~2")

        gsv_top_k = int(float(self._gsv_top_k.text().strip() or str(_DEFAULT_VALUES["gsv_top_k"])))
        gsv_top_p = float(self._gsv_top_p.text().strip() or str(_DEFAULT_VALUES["gsv_top_p"]))
        gsv_repetition_penalty = float(self._gsv_repetition_penalty.text().strip() or str(_DEFAULT_VALUES["gsv_repetition_penalty"]))

        try:
            gsv_speed_factor = float(self._gsv_speed_factor.text().strip() or str(_DEFAULT_VALUES["gsv_speed_factor"]))
        except ValueError as e:
            raise ValueError("GSV语速必须是数字") from e
        if not (0.5 <= gsv_speed_factor <= 2.0):
            raise ValueError("GSV语速范围应为 0.5~2.0")

        gsv_text_split_method = str(self._gsv_text_split_method.currentData() or str(_DEFAULT_VALUES["gsv_text_split_method"]))
        gsv_fragment_interval = float(self._gsv_fragment_interval.text().strip() or str(_DEFAULT_VALUES["gsv_fragment_interval"]))
        try:
            gsv_seed = int(self._gsv_seed.text().strip() or str(_DEFAULT_VALUES["gsv_seed"]))
        except ValueError as e:
            raise ValueError("GSV随机种子必须是整数") from e

        try:
            ai_voice_max_chars = int(float(
                self._ai_voice_max_chars.text().strip()
                or str(AI_VOICE_MAX_CHARS_DEFAULT)
            ))
        except ValueError as e:
            raise ValueError("GSV语音字数限制必须是整数") from e
        if not (
            AI_VOICE_MAX_CHARS_MIN
            <= ai_voice_max_chars
            <= AI_VOICE_MAX_CHARS_MAX
        ):
            raise ValueError(
                f"GSV语音字数限制范围应为 "
                f"{AI_VOICE_MAX_CHARS_MIN}~{AI_VOICE_MAX_CHARS_MAX}"
            )

        try:
            gsv_cache_max_files = int(float(self._gsv_cache_max_files.text().strip() or str(_DEFAULT_VALUES["gsv_cache_max_files"])))
        except ValueError as e:
            raise ValueError("GSV缓存上限必须是整数") from e
        if not (1 <= gsv_cache_max_files <= 128):
            raise ValueError("GSV缓存上限范围应为 1~128")

        try:
            memory_context_limit = int(float(self._memory_context_limit.text().strip() or str(_DEFAULT_VALUES["memory_context_limit"])))
        except ValueError as e:
            raise ValueError("记忆上下文条数必须是整数") from e
        if not (0 <= memory_context_limit <= 48):
            raise ValueError("记忆上下文条数范围应为 0~48")

        try:
            memory_recall_count = int(float(self._memory_recall_count.text().strip() or str(_DEFAULT_VALUES["memory_recall_count"])))
        except ValueError as e:
            raise ValueError("回忆提取条数必须是整数") from e
        if not (5 <= memory_recall_count <= 50):
            raise ValueError("回忆提取条数范围应为 5~50")

        values = {
            "api_key": self._api_key.raw_text(),
            "force_reply_mode": force_mode,
            "welfare_intelligence_boost": bool(self._welfare_intelligence_boost.isChecked()),
            "api_base_url": self._normalize_manual_api_base_url(self._api_base_url.text()),
            "api_model": self._api_model.currentText().strip(),
            "ollama_base_url": self._ollama_base_url.text().strip(),
            "ollama_model": self._ollama_model.currentText().strip(),
            "num_gpu": num_gpu,
            "num_thread": num_thread,
            "api_temperature": api_temperature,
            "model_vision": model_vision,
            "gsv_auto_start": bool(self._gsv_auto_start.isChecked()),
            "gsv_gpu_hybrid": bool(self._gsv_gpu_hybrid.isChecked()),
            "gsv_nvidia_cuda_acceleration": bool(
                getattr(self, "_gsv_nvidia_cuda_acceleration", None)
                and self._gsv_nvidia_cuda_acceleration.isChecked()
            ),
            "gsv_temperature": gsv_temperature,
            "gsv_top_k": gsv_top_k,
            "gsv_top_p": gsv_top_p,
            "gsv_repetition_penalty": gsv_repetition_penalty,
            "gsv_speed_factor": gsv_speed_factor,
            "gsv_text_split_method": gsv_text_split_method,
            "gsv_fragment_interval": gsv_fragment_interval,
            "gsv_seed": gsv_seed,
            "ai_voice_max_chars": ai_voice_max_chars,
            "gsv_cache_max_files": gsv_cache_max_files,
            "memory_context_limit": memory_context_limit,
            "memory_recall_count": memory_recall_count,
            "api_enable_thinking": bool(self._api_enable_thinking.isChecked()),
            "auto_companion_enabled": bool(self._auto_companion_enabled.isChecked()),
            "auto_companion_interval_minutes": int(self._auto_companion_interval_minutes.value()),
        }
        self._validate_ai_values(values)
        return values

    def _update_reply_mode_sections(self, *_args) -> None:
        mode = str(self._force_mode.currentData() or "1").strip()
        self._welfare_section.setVisible(mode == "1")
        self._manual_api_section.setVisible(mode == "0")
        self._ollama_section.setVisible(mode == "2")
        self._update_auto_companion_interval_row()

    def _welfare_interval_locked(self) -> bool:
        """福利 API 模式下自动陪伴间隔固定为 6 分钟。"""
        return str(self._force_mode.currentData() or "1").strip() == "1"

    def _update_auto_companion_interval_row(self, *_args) -> None:
        """福利 API 时收起间隔滑条；其余模式按开关状态启用滑条。

        Qt5 的 ``QFormLayout`` 没有 ``setRowVisible``，所以整行收起只能把这一行的
        标签与字段一起隐藏：两个都不可见时该行高度归零。
        """
        locked = self._welfare_interval_locked()
        slider = getattr(self, "_auto_companion_interval_minutes", None)
        form = getattr(self, "_reply_mode_form", None)
        if slider is None or form is None:
            return
        if locked:
            # 固定值写回控件，保存时写盘的分钟数与运行时实际生效的 6 分钟一致。
            slider.set_value(WELFARE_AUTO_COMPANION_INTERVAL_MINUTES)
        label = form.labelForField(slider)
        if label is not None:
            label.setVisible(not locked)
        slider.setVisible(not locked)
        slider.setEnabled(not locked and self._auto_companion_enabled.isChecked())

    def _update_gsv_settings_visibility(self) -> None:
        """语音包不可用时整段语音设置收起，N 卡开关同进同出。

        历史上这里还要 `and nvidia_present`，那条异步能力探测（第 2191 行的
        `_refresh_nvidia_acceleration_capability_async`）在重构中已经没有调用点，
        `_nvidia_gpu_present` 因此恒为 `False`，开关实际上从不显示。本轮把死链清掉，
        对外表现不变：开关只由语音包可用性决定。
        """
        voice_available = bool(self._gsv_launcher_available)
        self._voice_section.setVisible(voice_available)
        cuda_checkbox = getattr(self, "_gsv_nvidia_cuda_acceleration", None)
        if cuda_checkbox is not None:
            cuda_checkbox.setVisible(voice_available)

    def _update_gsv_advanced_visibility(self, *_args) -> None:
        group = getattr(self, "_gsv_advanced_group", None)
        toggle = getattr(self, "_gsv_advanced_toggle", None)
        if group is None or toggle is None:
            return
        group.setVisible(bool(toggle.isChecked()))

    def _ollama_model_placeholder_message(self) -> str:
        error = get_model_list_error()
        return f"未检测到 Ollama 模型（{error}）" if error else "未检测到 Ollama 模型"

    def _refresh_ollama_model_choices(self, selected_model: str = "") -> None:
        if not isinstance(self._ollama_model, QComboBox):
            return
        models = get_available_model_names()
        selected_text = str(selected_model or "").strip()
        self._ollama_model.blockSignals(True)
        self._ollama_model.clear()
        if models:
            for model in models:
                self._ollama_model.addItem(model, model)
            if selected_text:
                idx = self._ollama_model.findData(selected_text)
                if idx >= 0:
                    self._ollama_model.setCurrentIndex(idx)
                else:
                    self._ollama_model.setEditText(selected_text)
            else:
                self._ollama_model.setCurrentIndex(0)
            if self._ollama_model.lineEdit():
                self._ollama_model.lineEdit().setPlaceholderText("")
            self._ollama_model.setToolTip(f"检测到 {len(models)} 个 Ollama 模型")
        else:
            placeholder = self._ollama_model_placeholder_message()
            if self._ollama_model.lineEdit():
                self._ollama_model.lineEdit().clear()
                self._ollama_model.lineEdit().setPlaceholderText(placeholder)
            self._ollama_model.setToolTip(placeholder)
            if selected_text:
                self._ollama_model.setEditText(selected_text)
        self._ollama_model.blockSignals(False)

    def _refresh_ollama_model_dropdown(self) -> None:
        if not isinstance(self._ollama_model, QComboBox):
            return
        selected_text = self._ollama_model.currentText().strip()
        self._refresh_ollama_model_choices(selected_text)

    @staticmethod
    def _normalize_manual_api_base_url(raw_url: str) -> str:
        """补全手动 OpenAI 兼容地址的协议，保留用户填写的路径。"""
        return _normalize_api_base_url(raw_url)

    def _normalize_manual_api_base_url_input(self) -> None:
        normalized = self._normalize_manual_api_base_url(self._api_base_url.text())
        if normalized != self._api_base_url.text().strip():
            self._api_base_url.setText(normalized)

    def _on_manual_api_provider_changed(self, _index: int) -> None:
        base_url = str(self._manual_api_provider.currentData() or "").strip()
        if base_url:
            self._api_base_url.setText(base_url)

    def _sync_manual_api_provider_selection(self, *_args) -> None:
        current_base_url = self._normalize_manual_api_base_url(self._api_base_url.text())
        matched_index = 0
        for index in range(1, self._manual_api_provider.count()):
            preset_url = self._normalize_manual_api_base_url(
                str(self._manual_api_provider.itemData(index) or "")
            )
            if current_base_url and current_base_url == preset_url:
                matched_index = index
                break
        if self._manual_api_provider.currentIndex() != matched_index:
            self._manual_api_provider.blockSignals(True)
            self._manual_api_provider.setCurrentIndex(matched_index)
            self._manual_api_provider.blockSignals(False)

    @classmethod
    def _manual_api_models_url(cls, base_url: str) -> str:
        return _manual_api_models_url(base_url)

    @staticmethod
    def _parse_manual_api_models(payload) -> list[str]:
        return _parse_api_models(payload)

    @classmethod
    def _probe_manual_api_models(cls, base_url: str, api_key: str) -> list[str]:
        return _fetch_api_models(base_url, api_key)

    def _refresh_manual_api_model_choices(self, selected_model: str = "", models: list[str] | None = None) -> None:
        if not isinstance(self._api_model, QComboBox):
            return
        selected_text = str(selected_model or "").strip()
        choices = list(models or [])
        self._api_model.blockSignals(True)
        self._api_model.clear()
        for model in choices:
            self._api_model.addItem(model, model)
        if selected_text:
            index = self._api_model.findData(selected_text)
            if index >= 0:
                self._api_model.setCurrentIndex(index)
            else:
                self._api_model.setEditText(selected_text)
        elif choices:
            self._api_model.setCurrentIndex(0)
        self._api_model.blockSignals(False)

    def _on_probe_manual_api_models(self) -> None:
        base_url = self._normalize_manual_api_base_url(self._api_base_url.text())
        api_key = self._api_key.raw_text()
        if not base_url or not api_key:
            self._emit_info("请先填写接口地址和接口密钥。", min_tick=10, max_tick=100)
            return
        self._api_base_url.setText(base_url)
        selected_model = self._api_model.currentText().strip()
        self._probe_manual_api_models_btn.setEnabled(False)
        self._probe_manual_api_models_btn.setText("探测中...")

        def worker() -> None:
            try:
                models = self._probe_manual_api_models(base_url, api_key)
            except Exception as exc:
                _logger.warning("手动 API 模型探测失败: %s", exc)

                def apply_failure() -> None:
                    self._probe_manual_api_models_btn.setEnabled(True)
                    self._probe_manual_api_models_btn.setText("探测模型")
                    self._emit_info("模型探测失败，请检查接口地址、密钥和服务兼容性。", min_tick=12, max_tick=140)

                self._run_on_ui_thread(apply_failure)
                return

            def apply_success() -> None:
                self._refresh_manual_api_model_choices(selected_model, models)
                self._probe_manual_api_models_btn.setEnabled(True)
                self._probe_manual_api_models_btn.setText("探测模型")
                self._emit_info(f"已探测到 {len(models)} 个模型。", min_tick=10, max_tick=100)

            self._run_on_ui_thread(apply_success)

        future = get_compute_hub().submit_latest(
            "ai_settings_manual_api_model_probe",
            worker,
            executor="io",
        )
        if future is None:
            self._probe_manual_api_models_btn.setEnabled(True)
            self._probe_manual_api_models_btn.setText("探测模型")
            self._emit_info("模型探测正在进行，请稍候。", min_tick=10, max_tick=100)

    def _on_restore_ai_defaults(self) -> None:
        self._set_values_to_form(_DEFAULT_VALUES)
        self._emit_info("AI 设置已恢复默认值，保存后生效。", min_tick=10, max_tick=90)

    def _on_save_ai_action(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return
        self._save_completion_action = None if self._workbench_attached else self.fade_out
        if not self._on_save():
            self._save_completion_action = None
        elif not self._save_task_pending and callable(self._save_completion_action):
            action = self._save_completion_action
            self._save_completion_action = None
            action()

    def _on_save_and_restart(self) -> None:
        if self._save_task_pending:
            self._emit_info("已有配置保存任务正在进行，请稍候。", min_tick=10, max_tick=60)
            return

        def restart_action():
            self._ec.publish(Event(
                EventType.APP_QUIT,
                {"exit_code": 0, "restart": True},
            ))
        self._save_completion_action = restart_action
        if not self._on_save(apply_runtime=False):
            self._save_completion_action = None
            return
        # Keep compatibility with lightweight test doubles and integrations
        # that replace _on_save with a synchronous implementation.
        if not self._save_task_pending:
            action = self._save_completion_action
            self._save_completion_action = None
            if callable(action):
                action()


__all__ = [
    "AISettingsPageMixin",
]
