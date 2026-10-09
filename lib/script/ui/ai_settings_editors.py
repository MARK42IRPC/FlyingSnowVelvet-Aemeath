"""AI 设置面板的「配置编辑器控件族」：表单标签、行编辑、选择器与滑块字段。

这些能力原先全部长在 `ai_settings_panel.AISettingsPanel` 里，六个编辑器工厂 + 一组
字段谓词 + 音量百分比换算 + 两个「浏览 / 打开」对话框助手共 ~330 行，占了 `QWidget`
子类的一大块，却与面板自身的状态（`self._config_tab_meta`、`_force_mode` 等）无关：
它们只读像素档、工作台控件与 schema 表。

批次 3 第四轮把它们切成两类：

- **模块级控件类**：`_NoWheelSlider` / `_DecimalSliderField` /
  `_AnimationDurationSliderField`——纯 `QWidget` 子树，原样搬出，逐行不变；
- **`ConfigEditorMixin`**：编辑器工厂与谓词。方法体逐行未动（缩进也未变），
  只拆掉三处对面板类的硬引用：
  `AISettingsPanel._folder_options` → 模块级 `_folder_options()`，
  `AISettingsPanel._volume_percent_from_value` → `self._volume_percent_from_value`，
  `_set_config_editor_value` 由 `@staticmethod` 恢复成读 `self` 的普通方法
  （它原本就在读 `AISettingsPanel`）。

`AISettingsPanel` 现在继承本 mixin，`ui/ai_settings_config_parse.parse_editor_value(field,`
`widget=type(self))` 约定的五个谓词（`is_text_editor` / `is_slider` / `is_decimal_field` /
`is_check_box` / `is_combo_box`）与 `volume_value_from_percent` 都在这里，调用面零改动。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，因此按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from config.config import ANIMATION
from config.scale import scale_px
from lib.script.SEanima.clip import (
    list_animation_folder_choices,
    resolve_animation_folder_path,
)
from lib.script.SEanima.decoder import playback_duration_seconds, scan_animation_frame_files
from lib.script.ui import ai_settings_validation as _validation
from lib.script.ui.ai_settings_config_schema import (
    GENERAL_DECIMAL_SLIDER_SPECS as _GENERAL_DECIMAL_SLIDER_SPECS,
    VOLUME_SLIDER_FIELDS as _VOLUME_SLIDER_FIELDS,
    format_config_editor_value as _format_config_editor_value,
)
from lib.script.ui.ai_settings_labels import (
    animation_folder_display_name as _animation_folder_display_name,
)
from lib.script.ui.office_mode_settings import (
    WatermarkComboBox as _WatermarkComboBox,
    create_field_row_group as _create_field_row_group_helper,
)
from lib.script.ui.render_bridge import digit_font as get_digit_font

#: 配置表单的小数字号档，与 `ai_settings_panel._CONFIG_FONT_SIZE` 同源同值。
_CONFIG_FONT_SIZE = scale_px(17, min_abs=12)


def _folder_options() -> list[tuple[str, str]]:
    """动画目录的 `(显示名, 目录名)` 选项，供校验模块按需取用。"""
    return [
        (_animation_folder_display_name(name), name)
        for name in list_animation_folder_choices()
    ]


class _NoWheelSlider(QSlider):
    """屏蔽滚轮事件的水平滑条，避免滚动页面时误操作。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _DecimalSliderField(QWidget):
    """带数值显示的小数滑块字段。"""

    def __init__(
        self,
        minimum: float,
        maximum: float,
        step: float,
        *,
        value: float,
        decimals: int = 2,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._step = max(float(step), 0.0001)
        self._decimals = max(0, int(decimals))
        self._suffix = str(suffix or "")

        total_steps = max(1, int(round((self._maximum - self._minimum) / self._step)))

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(10))

        self._slider = _NoWheelSlider(Qt.Horizontal, self)
        self._slider.setRange(0, total_steps)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(max(1, total_steps // 10))
        self._slider.setTickInterval(max(1, total_steps // 10))
        self._slider.setTickPosition(QSlider.NoTicks)
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self._slider, 1)

        self._value_label = QLabel(self)
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_label.setFixedWidth(scale_px(56, min_abs=48))
        value_font = get_digit_font(size=max(scale_px(13, min_abs=10), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
        value_font.setBold(True)
        self._value_label.setFont(value_font)
        row.addWidget(self._value_label, 0)

        self._slider.valueChanged.connect(self._sync_value_label)
        self.setFocusProxy(self._slider)
        self.setText(str(value))

    def _clamp(self, raw_value: float) -> float:
        return max(self._minimum, min(self._maximum, raw_value))

    def _value_from_slider(self, slider_value: int) -> float:
        return self._minimum + float(slider_value) * self._step

    def _slider_from_value(self, raw_value: float) -> int:
        value = self._clamp(raw_value)
        slider_value = int(round((value - self._minimum) / self._step))
        return max(self._slider.minimum(), min(self._slider.maximum(), slider_value))

    def _format_value(self, raw_value: float) -> str:
        text = f"{self._clamp(raw_value):.{self._decimals}f}"
        formatted = text.rstrip("0").rstrip(".") if "." in text else text
        return f"{formatted}{self._suffix}"

    def _sync_value_label(self, _slider_value: int) -> None:
        self._value_label.setText(self.text())

    def value(self) -> float:
        return self._clamp(self._value_from_slider(self._slider.value()))

    def set_value(self, raw_value) -> None:
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            numeric = self._minimum
        slider_value = self._slider_from_value(numeric)
        self._slider.setValue(slider_value)
        if self._slider.value() == slider_value:
            self._sync_value_label(slider_value)

    def text(self) -> str:
        return self._format_value(self.value())

    def setText(self, text) -> None:
        self.set_value(text)


class _AnimationDurationSliderField(_DecimalSliderField):
    """动画目标时长滑块，同时显示按帧数计算出的实际播放时长。"""

    def __init__(self, *args, frame_count: int, fps: int, **kwargs):
        self._frame_count = max(0, int(frame_count))
        self._fps = max(1, int(fps))
        super().__init__(*args, **kwargs)
        self._value_label.setFixedWidth(scale_px(92, min_abs=84))
        self._sync_value_label(self._slider.value())

    def _sync_value_label(self, _slider_value: int) -> None:
        target = self.value()
        if self._frame_count <= 0:
            self._value_label.setText("暂无帧数据")
            return
        actual = playback_duration_seconds(
            self._frame_count,
            speed_multiplier=target,
            fps=self._fps,
        )
        self._value_label.setText(f"{target:.1f}x/{actual:.1f}s")

    def set_frame_count(self, frame_count: int) -> None:
        self._frame_count = max(0, int(frame_count))
        self._sync_value_label(self._slider.value())

class ConfigEditorMixin:
    """配置编辑器工厂与字段谓词；由 `AISettingsPanel` 混入。"""

    #: 项目根目录解析器；宿主（`AISettingsPanel`）覆盖它以便测试替换 `_project_root`。
    _editor_project_root: "Callable[[], Path]" = staticmethod(
        lambda: Path(__file__).resolve().parents[3]
    )

    @staticmethod
    def _create_field_row_group(spacing: int = 0):
        return _create_field_row_group_helper(spacing)

    def _create_config_line_edit(
        self,
        value=...,
        *,
        placeholder_text: str = "",
        expanding: bool = False,
    ) -> QLineEdit:
        editor = QLineEdit()
        if placeholder_text:
            editor.setPlaceholderText(placeholder_text)
        if value is not ...:
            self._set_config_editor_value(editor, value)
        if expanding:
            editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return editor

    @staticmethod
    def _create_config_choice_editor(options: list[tuple[str, str]]) -> QComboBox:
        editor = _WatermarkComboBox()
        editor.setView(QListView(editor))
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for label, value in options:
            editor.addItem(str(label), value)
        return editor

    def _create_compact_pair_editor(
        self,
        left_value,
        right_value,
        *,
        left_hint: str = "",
        right_hint: str = "",
    ):
        group, row = self._create_field_row_group(spacing=scale_px(10))

        left = self._create_config_line_edit(
            left_value,
            placeholder_text=left_hint,
            expanding=True,
        )
        row.addWidget(left, 1)

        right = self._create_config_line_edit(
            right_value,
            placeholder_text=right_hint,
            expanding=True,
        )
        row.addWidget(right, 1)
        return left, right, group

    @staticmethod
    def _create_form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('ConfigFormLabel')
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    @staticmethod
    def _is_local_music_path_field(dict_name: str, key: str) -> bool:
        pair = (str(dict_name), str(key))
        return pair in {
            ("CLOUD_MUSIC", "local_music_dir"),
            ("CLOUD_MUSIC", "launch_wuwa_path"),
        }

    @staticmethod
    def _is_launch_wuwa_path_field(dict_name: str, key: str) -> bool:
        return str(dict_name) == "CLOUD_MUSIC" and str(key) == "launch_wuwa_path"

    @staticmethod
    def _is_volume_slider_field(dict_name: str, key: str, value) -> bool:
        pair = (str(dict_name), str(key))
        if pair not in _VOLUME_SLIDER_FIELDS:
            return False
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float))

    @staticmethod
    def _is_decimal_slider_field(dict_name: str, key: str, value) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        pair = (str(dict_name), str(key))
        return pair in _GENERAL_DECIMAL_SLIDER_SPECS

    @staticmethod
    def _get_decimal_slider_spec(dict_name: str, key: str, value) -> tuple[float, float, float, int] | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return _GENERAL_DECIMAL_SLIDER_SPECS.get((str(dict_name), str(key)))

    @staticmethod
    def _get_choice_field_options(dict_name: str, key: str) -> list[tuple[str, str]] | None:
        """某个字段的可选值；动画目录一类需要实时枚举，其余取静态表。"""
        return _validation.get_choice_field_options(
            dict_name, key, folder_options=_folder_options
        )


    @staticmethod
    def _volume_percent_from_value(value) -> int:
        try:
            v = float(value)
        except Exception:
            v = 0.0
        v = max(0.0, min(1.0, v))
        return int(round(v * 100))

    @staticmethod
    def _volume_value_from_percent(percent: int) -> float:
        p = max(0, min(100, int(percent)))
        # 步进按 1% 固定，避免浮点误差导致显示与落盘不一致。
        return round(p / 100.0, 2)

    #: `ai_settings_config_parse.parse_editor_value(field, widget=...)` 协议要求的不带
    #: 下划线名字；`_volume_value_from_percent` 是面板历史名，保留为同一实现。
    volume_value_from_percent = _volume_value_from_percent


    # ── 解析模块注入的控件能力（无 Qt 模块只认这几个谓词）───────────────

    @staticmethod
    def is_text_editor(editor) -> bool:
        return isinstance(editor, QLineEdit)

    @staticmethod
    def is_slider(editor) -> bool:
        return isinstance(editor, QSlider)

    @staticmethod
    def is_decimal_field(editor) -> bool:
        return isinstance(editor, _DecimalSliderField)

    @staticmethod
    def is_check_box(editor) -> bool:
        return isinstance(editor, QCheckBox)

    @staticmethod
    def is_combo_box(editor) -> bool:
        return isinstance(editor, QComboBox)

    def _create_volume_slider_editor(self, value):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(8, min_abs=6))

        slider = _NoWheelSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(1)
        slider.setTickInterval(10)
        slider.setTickPosition(QSlider.NoTicks)
        slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        label = QLabel()
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setFixedWidth(scale_px(44, min_abs=38))

        percent = self._volume_percent_from_value(value)
        slider.setValue(percent)
        label.setText(f"{percent}%")
        slider.valueChanged.connect(lambda v, lbl=label: lbl.setText(f"{int(v)}%"))

        row.addWidget(slider, 1)
        row.addWidget(label, 0)
        return slider, label, group

    def _create_animation_folder_duration_editor(
        self,
        animation_type: str,
        folder_value: str,
        duration_value: float,
    ):
        """Keep each animation's folder and timing controls in one editor group."""
        folder_key = f"{animation_type}_animation_folder"
        duration_key = f"{animation_type}_animation_duration"
        options = self._get_choice_field_options("ANIMATION", folder_key) or []
        folder_editor = self._create_config_choice_editor(options)
        duration_spec = self._get_decimal_slider_spec("ANIMATION", duration_key, duration_value)
        if duration_spec is None:
            raise ValueError(f"缺少动画时长滑块规格: {duration_key}")
        minimum, maximum, step, decimals = duration_spec
        frame_count = len(scan_animation_frame_files(resolve_animation_folder_path(str(folder_value))))
        duration_editor = _AnimationDurationSliderField(
            minimum,
            maximum,
            step,
            value=float(duration_value),
            decimals=decimals,
            frame_count=frame_count,
            fps=int(ANIMATION.get("frame_fps", 60) or 60),
        )
        folder_editor.currentIndexChanged.connect(
            lambda _index, combo=folder_editor, timing=duration_editor: timing.set_frame_count(
                len(scan_animation_frame_files(resolve_animation_folder_path(str(combo.currentData() or ""))))
            )
        )
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(5, min_abs=3))
        layout.addWidget(folder_editor)
        layout.addWidget(duration_editor)
        return folder_editor, duration_editor, group

    def _create_path_editor_with_open_button(
        self,
        dict_name: str,
        key: str,
        value,
    ):
        group, row = self._create_field_row_group(spacing=scale_px(8, min_abs=6))

        editor = self._create_config_line_edit(value, expanding=True)
        row.addWidget(editor, 1)

        open_btn = QPushButton("浏览")
        open_btn.setFixedWidth(scale_px(52, min_abs=46))
        if self._is_launch_wuwa_path_field(dict_name, key):
            open_btn.clicked.connect(lambda _=False, line=editor: self._browse_launch_wuwa_file(line))
        elif self._is_local_music_path_field(dict_name, key):
            open_btn.clicked.connect(lambda _=False, line=editor: self._browse_local_music_dir(line))
        row.addWidget(open_btn, 0)
        return editor, open_btn, group

    def _browse_local_music_dir(self, editor: QLineEdit) -> None:
        start_dir = self._editor_project_root()
        current_text = str(editor.text() or "").strip()
        if current_text:
            expanded = os.path.expandvars(os.path.expanduser(current_text))
            candidate = Path(expanded)
            if not candidate.is_absolute():
                candidate = self._editor_project_root() / candidate
            if candidate.is_file():
                candidate = candidate.parent
            if candidate.exists() and candidate.is_dir():
                start_dir = candidate
            elif candidate.parent.exists() and candidate.parent.is_dir():
                start_dir = candidate.parent

        selected = QFileDialog.getExistingDirectory(
            self,
            "选择本地音乐文件夹",
            str(start_dir),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if selected:
            editor.setText(os.path.normpath(selected))

    def _browse_launch_wuwa_file(self, editor: QLineEdit) -> None:
        start_dir = self._editor_project_root()
        current_text = str(editor.text() or "").strip()
        if current_text:
            expanded = os.path.expandvars(os.path.expanduser(current_text))
            candidate = Path(expanded)
            if not candidate.is_absolute():
                candidate = self._editor_project_root() / candidate
            if candidate.exists():
                start_dir = candidate.parent if candidate.is_file() else candidate
            elif candidate.parent.exists() and candidate.parent.is_dir():
                start_dir = candidate.parent

        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择鸣潮启动文件",
            str(start_dir),
            "启动文件 (*.exe *.bat *.lnk);;可执行文件 (*.exe);;批处理 (*.bat);;快捷方式 (*.lnk);;所有文件 (*.*)",
        )
        if selected:
            editor.setText(os.path.normpath(selected))

    @staticmethod
    @staticmethod
    @staticmethod
    def _wrap_field_widget(widget: QWidget) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(widget, 1, Qt.AlignVCenter)
        return wrap

    def _create_sequence_editor(self, value):
        group, row = self._create_field_row_group(spacing=scale_px(10))

        items = list(value) if isinstance(value, (tuple, list)) else [value]
        editors: list[QLineEdit] = []
        for item in items:
            editor = self._create_config_line_edit(item, expanding=True)
            editors.append(editor)
            row.addWidget(editor, 1)
        return editors, group

    @staticmethod
    def _set_sequence_editor_values(editors, value) -> None:
        if not isinstance(value, (tuple, list)):
            return
        for idx, editor in enumerate(editors):
            if idx >= len(value):
                break
            if isinstance(editor, QLineEdit):
                editor.setText(_format_config_editor_value(value[idx]))

    def _set_config_editor_value(self, editor, value) -> None:
        if isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
            return
        if isinstance(editor, QSlider):
            editor.setValue(self._volume_percent_from_value(value))
            return
        if isinstance(editor, _DecimalSliderField):
            editor.set_value(value)
            return
        if isinstance(editor, QComboBox):
            index = editor.findData(value)
            if index < 0:
                index = editor.findText(str(value))
            if index >= 0:
                editor.setCurrentIndex(index)
            elif editor.count() > 0:
                editor.setCurrentIndex(0)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(_format_config_editor_value(value))

__all__ = [
    "ConfigEditorMixin",
]
