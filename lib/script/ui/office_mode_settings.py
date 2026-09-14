"""办公模式的配置控件：设置面板与办公页面共用同一份实现。

办公后端、独立 API（密钥 / 提供商 / 地址 / 模型）与「启动时预热」原先长在 AI 设置面板里。
办公模式独立成页面后两边都要用同一套字段，于是整块搬到这里：控件树、字段说明、本机 DSH
探测与模型探测只有一份，两个入口不会再各写一份慢慢漂移。

控件直接铺进宿主分区的 `body_layout`（`SettingsSection` 或任意带 `body_layout` 的容器），
布局结构与迁移前完全一致：QFormLayout 的隐藏行仍占行距，所以折叠的独立 API 块单独放在
一个 QWidget 里，整块显示/隐藏才能真正释放行高。
"""

from __future__ import annotations

import re
from typing import Callable

import requests

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.script.ui.workbench_settings_layout import create_settings_form
from lib.core.compute_hub import get_compute_hub
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger

logger = get_logger(__name__)

#: 探测 OpenAI 兼容接口模型列表的超时（秒）。
from lib.script.chat.network_policy import API_TIMEOUT_SECS
#: 下拉弹层的工作台层级，和设置面板里其它下拉框保持一致。
_DROPDOWN_POPUP_LAYER = 601

#: 常用 OpenAI 兼容提供商预设；办公接口与手动接口共用同一张表。
MANUAL_API_PROVIDER_PRESETS = (
    ("自定义地址", ""),
    ("OpenAI", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Kimi", "https://api.moonshot.cn/v1"),
    ("智谱 AI", "https://open.bigmodel.cn/api/paas/v4"),
    ("阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("硅基流动", "https://api.siliconflow.cn/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
)


def normalize_api_base_url(raw_url: object) -> str:
    """补全 OpenAI 兼容地址的协议，保留用户填写的路径。"""
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return text.rstrip("/")
    if text.startswith("//"):
        return f"https:{text}".rstrip("/")

    host = text.split("/", 1)[0].lower()
    is_local = (
        host == "localhost"
        or host.startswith("localhost:")
        or host.startswith("127.")
        or host.startswith("0.0.0.0")
        or host.startswith("[::1]")
        or host == "::1"
    )
    scheme = "http" if is_local else "https"
    return f"{scheme}://{text}".rstrip("/")


def manual_api_models_url(base_url: object) -> str:
    """把用户填的基地址换算成 /models 端点；填了完整端点也能还原。"""
    root = normalize_api_base_url(base_url).rstrip("/")
    suffix = "/chat/completions"
    if root.lower().endswith(suffix):
        root = root[: -len(suffix)].rstrip("/")
    return f"{root}/models" if root else ""


def parse_api_models(payload: object) -> list[str]:
    """解析 OpenAI 兼容的 /models 响应，缺失或形状不对就报错。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("接口没有返回兼容的模型列表")
    models = {
        str(item.get("id", "")).strip()
        for item in data
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    return sorted(models, key=str.casefold)


def fetch_api_models(base_url: object, api_key: object) -> list[str]:
    """同步探测模型列表；调用方负责放到 IO 线程里执行。"""
    models_url = manual_api_models_url(base_url)
    if not models_url:
        raise ValueError("请先填写接口地址")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("请先填写接口密钥")
    response = requests.get(
        models_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=API_TIMEOUT_SECS,
    )
    response.raise_for_status()
    models = parse_api_models(response.json())
    if not models:
        raise ValueError("接口未返回可用模型")
    return models


def probe_local_dsh() -> dict:
    """读取启动期探测结果，没有缓存时按需只读探测；失败不影响界面。"""
    try:
        from lib.script.office import local_dsh

        cached = local_dsh.cached_local_dsh_status()
        if cached is not None:
            return dict(cached)
        return dict(local_dsh.local_dsh_status())
    except Exception as exc:
        return {
            "available": False,
            "reason": f"探测本机 DeepSeek Harness 失败：{exc}",
        }


def set_widget_description(widget: QWidget | None, text: str) -> None:
    """把说明文字挂到控件上，供工作台的说明/悬停逻辑统一读取。"""
    if widget is None:
        return
    desc = str(text or "").strip()
    if not desc:
        return
    setattr(widget, "_description", desc)


def describe_form_row(form: QFormLayout, field_widget: QWidget, text: str) -> None:
    """同一段说明同时挂到字段与它的标签上。"""
    set_widget_description(field_widget, text)
    set_widget_description(form.labelForField(field_widget), text)


def create_field_row_group(spacing: int = 0) -> tuple[QWidget, QHBoxLayout]:
    """一行放多个控件的容器：输入框 + 按钮这类组合。"""
    group = QWidget()
    group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    row = QHBoxLayout(group)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(int(spacing))
    return group, row


class WatermarkComboBox(QComboBox):
    """带刷新回调与工作台弹层登记的下拉框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._before_popup_callback: Callable[[], None] | None = None
        self._popup_refreshing = False
        self._popup_window_instance = None

    def set_before_popup_callback(self, callback: Callable[[], None] | None) -> None:
        self._before_popup_callback = callback

    def _popup_window(self):
        if self._popup_window_instance is not None:
            return self._popup_window_instance
        view = self.view()
        return view.window() if view is not None else None

    def _unregister_popup_layer(self) -> None:
        popup = self._popup_window()
        if popup is not None:
            get_layer_manager().unregister(popup)

    def showPopup(self) -> None:
        callback = self._before_popup_callback
        if callable(callback) and not self._popup_refreshing:
            self._popup_refreshing = True
            try:
                callback()
            finally:
                self._popup_refreshing = False

        if self.count() <= 0:
            return

        super().showPopup()
        popup = self._popup_window()
        if popup is not None:
            self._popup_window_instance = popup
            popup.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            popup.show()
            layer_manager = get_layer_manager()
            layer_manager.register(
                popup,
                _DROPDOWN_POPUP_LAYER,
                name="AISettingsDropdownPopup",
            )
            layer_manager.enforce_burst()
            popup.raise_()
            popup.activateWindow()

    def hidePopup(self) -> None:
        self._unregister_popup_layer()
        try:
            super().hidePopup()
        finally:
            self._popup_window_instance = None

    def wheelEvent(self, event) -> None:
        # 下拉框不消费滚轮，让外层设置页面独占滚动手势。
        event.ignore()


class ApiKeyLineEdit(QLineEdit):
    """接口密钥输入框：展示时脱敏（前7后4，中间 *），编辑时显示原文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_text = ""
        self._masked = True
        self._updating = False
        self.editingFinished.connect(self._on_editing_finished)

    @staticmethod
    def _mask_text(raw_text: str) -> str:
        text = str(raw_text or "")
        if len(text) <= 11:
            return text
        return f"{text[:7]}{'*' * (len(text) - 11)}{text[-4:]}"

    def set_raw_text(self, raw_text: str) -> None:
        self._raw_text = str(raw_text or "").strip()
        self._apply_masked_text()

    def raw_text(self) -> str:
        if not self._masked and not self._updating:
            self._raw_text = self.text().strip()
        return self._raw_text

    def _apply_masked_text(self) -> None:
        self._masked = True
        self._updating = True
        self.setText(self._mask_text(self._raw_text))
        self._updating = False

    def _apply_plain_text(self) -> None:
        self._masked = False
        self._updating = True
        self.setText(self._raw_text)
        self._updating = False

    def _on_editing_finished(self) -> None:
        if self._updating:
            return
        if not self._masked:
            self._raw_text = self.text().strip()
        self._apply_masked_text()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._apply_plain_text()
        self.selectAll()

    def focusOutEvent(self, event) -> None:
        if not self._masked and not self._updating:
            self._raw_text = self.text().strip()
        self._apply_masked_text()
        super().focusOutEvent(event)


class OfficeModeSettings(QObject):
    """办公模式配置块：办公后端、独立 API 与启动预热。

    实例只是控件与行为的持有者，控件本身按 `build_into()` 铺进宿主分区；这样设置面板与
    办公页面可以各有一份实例，读写同一份 `config/ollama_config` 配置。
    """

    changed = pyqtSignal()

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        probe: Callable[[], dict] | None = None,
        info: Callable[..., None] | None = None,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._host_parent = parent
        self._probe = probe or probe_local_dsh
        self._info = info
        self._dispatch = dispatch
        self._local_dsh_status = self._probe()
        self.section = None
        self._build()

    # ── 构建 ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.backend_form = create_settings_form()
        form = self.backend_form

        self.backend = WatermarkComboBox()
        self.backend.setView(QListView(self.backend))
        self.backend.addItem("DeepSeek Harness（推荐）", "dsh")
        self.ensure_local_dsh_item()
        form.addRow("办公后端", self.backend)
        describe_form_row(form, self.backend, self.backend_description())
        self.backend.currentIndexChanged.connect(self.refresh_backend_description)
        self.backend.currentIndexChanged.connect(self._emit_changed)

        self.use_independent_api = QCheckBox("办公模式独立api")
        self.use_independent_api.setChecked(False)
        form.addRow("", self.use_independent_api)
        describe_form_row(
            form,
            self.use_independent_api,
            "开启后，办公模式将使用下方配置的独立 API，而不是使用手动 API 的配置。",
        )

        # 折叠块整块放进分区 body：QFormLayout 隐藏行仍占行距，会留下一条大空白
        self.independent_api_group = QWidget(self._host_parent)
        independent_layout = QVBoxLayout(self.independent_api_group)
        independent_layout.setContentsMargins(0, 0, 0, 0)
        independent_layout.setSpacing(0)
        self.independent_api_form = create_settings_form()
        independent_layout.addLayout(self.independent_api_form)
        independent_form = self.independent_api_form

        self.api_key = ApiKeyLineEdit()
        independent_form.addRow("办公接口密钥", self.api_key)
        describe_form_row(
            independent_form,
            self.api_key,
            "办公模式独立使用的 OpenAI 兼容接口密钥，单独保存在用户密钥文件中。",
        )

        self.api_provider = WatermarkComboBox()
        self.api_provider.setView(QListView(self.api_provider))
        for label, base_url in MANUAL_API_PROVIDER_PRESETS:
            self.api_provider.addItem(label, base_url)
        self.api_provider.currentIndexChanged.connect(self._on_api_provider_changed)
        independent_form.addRow("常用提供商", self.api_provider)
        describe_form_row(
            independent_form,
            self.api_provider,
            "选择后自动填入该提供商的 OpenAI 兼容接口地址；自定义地址仍可直接填写。",
        )

        self.api_base_url = QLineEdit()
        self.api_base_url.textChanged.connect(self._sync_api_provider_selection)
        self.api_base_url.textChanged.connect(self._emit_changed)
        self.api_base_url.editingFinished.connect(self._normalize_api_base_url_input)
        independent_form.addRow("办公接口地址", self.api_base_url)
        describe_form_row(
            independent_form,
            self.api_base_url,
            "办公模式独立使用的外部接口地址，通常填写兼容 OpenAI 的基地址。",
        )

        api_model_row, api_model_layout = create_field_row_group(spacing=scale_px(8, min_abs=6))
        self.api_model = WatermarkComboBox()
        self.api_model.setView(QListView(self.api_model))
        self.api_model.setEditable(True)
        self.api_model.setInsertPolicy(QComboBox.NoInsert)
        self.api_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if self.api_model.lineEdit():
            self.api_model.lineEdit().setPlaceholderText("输入或探测办公接口模型")
        api_model_layout.addWidget(self.api_model, 1)
        self.probe_button = QPushButton("探测模型")
        self.probe_button.setFixedWidth(scale_px(100, min_abs=84))
        self.probe_button.clicked.connect(self.probe_models)
        api_model_layout.addWidget(self.probe_button, 0)
        independent_form.addRow("办公接口模型", api_model_row)
        describe_form_row(
            independent_form,
            api_model_row,
            "办公模式独立使用的外部接口模型名，例如 gpt-5.4。可探测 OpenAI 兼容接口的 /models 列表，也可直接手动输入。",
        )
        set_widget_description(self.probe_button, "使用当前填写的办公接口地址和密钥探测可用模型列表。")

        self.tail_form = create_settings_form()
        self.warmup_on_startup = QCheckBox("启动时预热")
        # 这一行单独在一张表单里，而 QFormLayout 的标签列宽度取自表内最长标签：
        # 整张表都没有标签时标签列塌成 0，「启动时预热」会比上面的复选框左移一个标签列。
        # 显式给一个同宽的空白标签，让它落在和其他设置行相同的字段列上。
        self.tail_form.addRow(QLabel("", self.tail_form.parentWidget()), self.warmup_on_startup)
        describe_form_row(
            self.tail_form,
            self.warmup_on_startup,
            "启用后，桌宠启动时自动预热办公运行时，减少首次任务的等待时间。",
        )
        self.warmup_on_startup.toggled.connect(self._emit_changed)

        self.independent_api_rows = (
            self.api_key,
            self.api_provider,
            self.api_base_url,
            api_model_row,
        )

        self.use_independent_api.toggled.connect(self.update_fields_visibility)
        self.update_fields_visibility()

    def build_into(self, host: object) -> None:
        """把三块表单铺进宿主分区（`SettingsSection` 或任意带 body_layout 的容器）。"""
        body = getattr(host, "body_layout", host)
        self.section = host
        body.addLayout(self.backend_form)
        body.addWidget(self.independent_api_group)
        body.addLayout(self.tail_form)

    # ── 取值 / 赋值 ──────────────────────────────────────────────────

    def values(self) -> dict:
        return {
            "office_backend": str(self.backend.currentData() or "dsh"),
            "office_use_independent_api": bool(self.use_independent_api.isChecked()),
            "office_api_key": str(self.api_key.raw_text()).strip(),
            "office_api_base_url": str(self.api_base_url.text()).strip(),
            "office_api_model": str(self.api_model.currentText()).strip(),
            "office_warmup_on_startup": bool(self.warmup_on_startup.isChecked()),
        }

    def set_values(self, values: dict) -> None:
        self.use_independent_api.setChecked(bool(values.get("office_use_independent_api", False)))
        saved_backend = str(values.get("office_backend", "dsh") or "dsh")
        self.ensure_local_dsh_item(saved_backend=saved_backend)
        backend_index = self.backend.findData(saved_backend)
        self.backend.setCurrentIndex(backend_index if backend_index >= 0 else 0)
        self.api_key.set_raw_text(str(values.get("office_api_key", "")))
        self.api_base_url.setText(str(values.get("office_api_base_url", "")))
        self._sync_api_provider_selection()
        self.api_model.setCurrentText(str(values.get("office_api_model", "gpt-5.4")))
        self.warmup_on_startup.setChecked(bool(values.get("office_warmup_on_startup", True)))

    # ── 可见性与说明 ─────────────────────────────────────────────────

    def update_fields_visibility(self, *_args) -> None:
        self.backend.setVisible(True)
        self.use_independent_api.setVisible(True)
        self.warmup_on_startup.setVisible(True)
        group = getattr(self, "independent_api_group", None)
        if group is not None:
            group.setVisible(bool(self.use_independent_api.isChecked()))

    def ensure_local_dsh_item(self, *, saved_backend: str | None = None) -> None:
        """按启动期探测结果决定是否列出「本机 DeepSeek Harness」。

        探测到可用安装时列在「DeepSeek Harness（推荐）」之后；没有探测到时
        默认不显示，只有在已保存该后端的情况下才保留一个置灰条目，避免把
        用户既有的选择静默改回内置 DSH。
        """
        if self.backend.findData("local_dsh") >= 0:
            return
        status = getattr(self, "_local_dsh_status", None) or {}
        if status.get("available"):
            self.backend.addItem("本机 DeepSeek Harness", "local_dsh")
            return
        if str(saved_backend or "") != "local_dsh":
            return
        self.backend.addItem("本机 DeepSeek Harness（未探测到）", "local_dsh")
        item = self.backend.model().item(self.backend.findData("local_dsh"))
        if item is not None:
            item.setEnabled(False)

    def backend_description(self) -> str:
        backend = str(self.backend.currentData() or "dsh")
        if backend != "local_dsh":
            return "办公模式使用 DeepSeek Harness 侧车，任务、会话和权限由桌宠统一管理。"
        status = getattr(self, "_local_dsh_status", None) or {}
        if status.get("available"):
            version = str(status.get("version") or "").strip()
            source = str(status.get("source") or "").strip()
            path = str(status.get("path") or "").strip()
            label = f"复用本机 DeepSeek Harness {version}".strip()
            if source:
                label = f"{label}（{source}）"
            return f"{label}：{path}" if path else label
        return str(status.get("reason") or "未探测到可用的本机 DeepSeek Harness")

    def refresh_backend_description(self, *_args) -> None:
        form = getattr(self, "backend_form", None)
        backend = getattr(self, "backend", None)
        if form is None or backend is None:
            return
        describe_form_row(form, backend, self.backend_description())

    # ── 交互 ─────────────────────────────────────────────────────────

    def _normalize_api_base_url_input(self) -> None:
        normalized = normalize_api_base_url(self.api_base_url.text())
        if normalized != self.api_base_url.text().strip():
            self.api_base_url.setText(normalized)

    def _on_api_provider_changed(self, _index: int) -> None:
        base_url = str(self.api_provider.currentData() or "").strip()
        if base_url:
            self.api_base_url.setText(base_url)

    def _sync_api_provider_selection(self, *_args) -> None:
        current_base_url = normalize_api_base_url(self.api_base_url.text())
        matched_index = 0
        for index in range(1, self.api_provider.count()):
            preset_url = normalize_api_base_url(
                str(self.api_provider.itemData(index) or "")
            )
            if current_base_url and current_base_url == preset_url:
                matched_index = index
                break
        if self.api_provider.currentIndex() != matched_index:
            self.api_provider.blockSignals(True)
            self.api_provider.setCurrentIndex(matched_index)
            self.api_provider.blockSignals(False)

    def refresh_model_choices(self, selected_model: str = "", models: list[str] | None = None) -> None:
        selected_text = str(selected_model or "").strip()
        choices = list(models or [])
        self.api_model.blockSignals(True)
        self.api_model.clear()
        for model in choices:
            self.api_model.addItem(model, model)
        if selected_text:
            index = self.api_model.findData(selected_text)
            if index >= 0:
                self.api_model.setCurrentIndex(index)
            else:
                self.api_model.setEditText(selected_text)
        elif choices:
            self.api_model.setCurrentIndex(0)
        self.api_model.blockSignals(False)

    def probe_models(self) -> None:
        """探测办公接口模型列表；有消息回调就顺带上报进度。"""
        base_url = normalize_api_base_url(self.api_base_url.text())
        api_key = self.api_key.raw_text()
        if not base_url or not api_key:
            self._emit_info("请先填写办公接口地址和办公接口密钥。", min_tick=10, max_tick=100)
            return
        self.api_base_url.setText(base_url)
        selected_model = self.api_model.currentText().strip()
        self.probe_button.setEnabled(False)
        self.probe_button.setText("探测中...")

        def worker() -> None:
            try:
                models = fetch_api_models(base_url, api_key)
            except Exception as exc:
                logger.warning("办公 API 模型探测失败: %s", exc)

                def apply_failure() -> None:
                    self.probe_button.setEnabled(True)
                    self.probe_button.setText("探测模型")
                    self._emit_info("办公模型探测失败，请检查接口地址、密钥和服务兼容性。", min_tick=12, max_tick=140)

                self._run_on_ui_thread(apply_failure)
                return

            def apply_success() -> None:
                self.refresh_model_choices(selected_model, models)
                self.probe_button.setEnabled(True)
                self.probe_button.setText("探测模型")
                self._emit_info(f"已探测到 {len(models)} 个办公模型。", min_tick=10, max_tick=100)

            self._run_on_ui_thread(apply_success)

        future = get_compute_hub().submit_latest(
            "ai_settings_office_api_model_probe",
            worker,
            executor="io",
        )
        if future is None:
            self.probe_button.setEnabled(True)
            self.probe_button.setText("探测模型")
            self._emit_info("办公模型探测正在进行，请稍候。", min_tick=10, max_tick=100)

    # ── 助手 ─────────────────────────────────────────────────────────

    def _run_on_ui_thread(self, func: Callable[[], None]) -> None:
        """探测结果回到 UI 线程：面板注入排队回调，办公页面本就在 UI 线程上跑。"""
        if self._dispatch is None:
            func()
            return
        self._dispatch(func)

    def _emit_changed(self, *_args) -> None:
        self.changed.emit()

    def _emit_info(self, text: str, min_tick: int = 12, max_tick: int = 140) -> None:
        if self._info is None:
            return
        self._info(text, min_tick, max_tick)


__all__ = [
    "API_TIMEOUT_SECS",
    "ApiKeyLineEdit",
    "MANUAL_API_PROVIDER_PRESETS",
    "OfficeModeSettings",
    "WatermarkComboBox",
    "create_field_row_group",
    "describe_form_row",
    "fetch_api_models",
    "manual_api_models_url",
    "normalize_api_base_url",
    "parse_api_models",
    "probe_local_dsh",
    "set_widget_description",
]
