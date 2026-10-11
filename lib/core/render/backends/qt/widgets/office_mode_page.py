"""工作台「办公模式」配置页（档位 D 宿主）。

页面只放办公相关配置：办公后端、独立接口、启动预热，以及技能 / 插件管理卡片；任务界面在
独立办公窗口里（`office_page`）。配置控件由 `office_mode_settings` 提供，管理卡片由
`office_manager_card` 提供，两者都是同族宿主。

落进档位 D 后解耦掉的产品依赖（宿主不得 import `lib.script`）：

- 办公配置读写（`load_ai_values` / `save_office_values`）与默认值表 `AI_DEFAULT_VALUES`：
  经 `tier_d_seams.hook` 取产品侧注入物（键名 `ai_storage_load` / `office_storage_save` /
  `ai_settings_defaults`）；缺省时读一份空值表，页面照常可用。
- 技能 / 插件管理入口（`lib.script.office.skills` / `plugins` 与「打开办公页面」）：经
  `tier_d_seams.hook` 取产品侧注入物（键名 `office_skill_admin` / `office_plugin_admin` /
  `office_open_page`）。
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QPushButton

from config.scale import scale_px
from lib.core.logger import get_logger
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.office_manager_card import OfficeManagerCard
from lib.core.render.backends.qt.widgets.office_mode_settings import (
    OfficeModeSettings,
    create_field_row_group,
    describe_form_row,
)
from lib.core.render.backends.qt.widgets.workbench_page import QtWorkbenchToolPage
from lib.core.render.backends.qt.widgets.workbench_settings_layout import (
    SettingsPageScaffold,
    SmoothScrollArea,
    create_settings_form,
)
from lib.core.render.visuals.office_chrome import office_stylesheet

logger = get_logger(__name__)

#: 独立办公窗口的说明文案，配置页与托盘入口共用同一句。
OPEN_OFFICE_HINT = "在独立窗口里新建任务、查看对话、推理与工具记录。"

#: 底部动作条的常驻提示：保存语义与 AI 设置页一致，重启后完整生效。
SAVE_STATUS_HINT = "保存后写入本地配置，建议重启程序后完整生效。"


#: 推理强度档位数的缺省值：与产品契约 `lib.script.office.contracts.REASONING_EFFORTS` 同值。
_DEFAULT_EFFORT_STEPS = 5


def _effort_steps() -> int:
    """办公 QSS 里的推理档位数；产品侧经 seam 注入权威值（`office_effort_steps`）。"""
    hook = tier_d_seams.hook("office_effort_steps")
    if hook is None:
        return _DEFAULT_EFFORT_STEPS
    return int(hook())


def _default_values() -> dict:
    hook = tier_d_seams.hook("ai_settings_defaults")
    return dict(hook()) if hook is not None else {}


def _load_office_values() -> dict:
    hook = tier_d_seams.hook("ai_storage_load")
    defaults = _default_values()
    return hook(defaults) if hook is not None else defaults


def _save_office_values(values, defaults) -> None:
    hook = tier_d_seams.hook("office_storage_save")
    if hook is None:
        raise RuntimeError("办公配置写入入口未注入")
    hook(values, defaults)


def _open_office_page() -> None:
    hook = tier_d_seams.hook("office_open_page")
    if hook is not None:
        hook()


def _skill_admin():
    hook = tier_d_seams.hook("office_skill_admin")
    if hook is None:
        return (lambda: [], lambda source: None, lambda name: None)
    return hook()


def _plugin_admin():
    hook = tier_d_seams.hook("office_plugin_admin")
    if hook is None:
        return (lambda: [], lambda source: None, lambda name: None)
    return hook()


class OfficeModePage(QtWorkbenchToolPage):
    """办公模式的配置页：办公配置 + 技能管理 + 插件管理。"""

    #: 探测模型这类后台任务的结果要排队回 UI 线程再动控件。
    _ui_thread_call = pyqtSignal(object)

    def __init__(self, *, embedded: bool = True) -> None:
        super().__init__(embedded=embedded)
        self.setObjectName("OfficeModePage")
        self._ui_thread_call.connect(self._run_dispatched, Qt.QueuedConnection)
        self._office_settings = OfficeModeSettings(
            parent=self,
            info=self._on_settings_info,
            dispatch=self._run_on_ui_thread,
        )
        self._build_ui()
        self._apply_theme()
        self._load_office_values()
        self._sync_embedded_presentation()

    # ── 构建 ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._scaffold = SettingsPageScaffold(
            self,
            "办公模式",
            "办公任务在独立的办公页面里执行；这里保留办公后端、独立接口与技能、插件配置。",
            scroll_factory=SmoothScrollArea,
        )
        self._build_config_section()
        self._build_manager_cards()
        # 与设置面板同一套排版：字号、控件字体与底部留白只在这里统一收口。
        self._scaffold.finish()
        self._build_actions()

    def _build_config_section(self) -> None:
        section = self._scaffold.add_help_section(
            "办公配置",
            "办公后端、办公模式独立 API 与启动预热；改动只写回办公相关字段。",
        )
        self._config_section = section
        # 「打开办公页面」按设置面板里「人格配置」那类行的写法铺：标签 + 控件行 + 行说明。
        open_form = create_settings_form()
        open_row, open_layout = create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._open_button = QPushButton("打开办公页面", section)
        self._open_button.setToolTip(OPEN_OFFICE_HINT)
        self._open_button.clicked.connect(_open_office_page)
        open_layout.addWidget(self._open_button, 0)
        open_layout.addStretch(1)
        open_form.addRow("办公页面", open_row)
        describe_form_row(open_form, open_row, OPEN_OFFICE_HINT)
        section.body_layout.addLayout(open_form)

        self._office_settings.build_into(section)

    def _build_actions(self) -> None:
        """底部动作条：和 AI 设置页一样，按钮不进分区正文。"""
        self._save_button = self._scaffold.add_action(
            "保存办公配置",
            self.save_office_config,
            primary=True,
        )
        self._status_label = self._scaffold.action_bar.status_label
        self._set_status(SAVE_STATUS_HINT)

    def _build_manager_cards(self) -> None:
        list_skills, install_skill, remove_skill = _skill_admin()
        list_plugins, install_plugin, remove_plugin = _plugin_admin()
        self._skill_card = OfficeManagerCard(
            "技能管理",
            "列出内置与已安装的 DSH 技能；安装的目录需要包含 SKILL.md。",
            kind="技能",
            loader=list_skills,
            installer=install_skill,
            remover=remove_skill,
            pick_title="选择技能目录（包含 SKILL.md）",
            parent=self._scaffold.content,
        )
        self._plugin_card = OfficeManagerCard(
            "插件管理",
            "列出办公 profile 加载的 DSH 插件包；安装的目录需要包含 package.json。",
            kind="插件",
            loader=list_plugins,
            installer=install_plugin,
            remover=remove_plugin,
            pick_title="选择插件目录（包含 package.json）",
            parent=self._scaffold.content,
        )
        self._scaffold.content_layout.addWidget(self._skill_card)
        self._scaffold.content_layout.addWidget(self._plugin_card)

    # ── 配置读写 ─────────────────────────────────────────────────────

    def _load_office_values(self) -> None:
        try:
            self._office_settings.set_values(_load_office_values())
        except Exception as exc:
            logger.warning("[OfficeModePage] 读取办公配置失败: %s", exc)

    def save_office_config(self) -> bool:
        """只把办公相关字段写回配置；失败时把原因留在状态行上。"""
        try:
            values = self._office_settings.values()
            _save_office_values(values, _default_values())
        except Exception as exc:
            logger.error("[OfficeModePage] 保存办公配置失败: %s", exc)
            self._set_status(f"保存办公配置失败：{exc}", tone="error")
            return False
        self._set_status("办公配置已保存，重启程序后完整生效。")
        return True

    def _set_status(self, text: str, *, tone: str = "") -> None:
        self._scaffold.set_status(text, tone=tone)

    def _on_settings_info(self, text: str, *_args) -> None:
        self._set_status(text)

    # ── 生命周期 ─────────────────────────────────────────────────────

    def _run_on_ui_thread(self, func: Callable[[], None]) -> None:
        """探测结果回到 UI 线程：本页本就在 UI 线程上，其余情况排队回主线程。"""
        import threading

        if threading.current_thread() is threading.main_thread():
            func()
        else:
            self._ui_thread_call.emit(func)

    def _run_dispatched(self, func) -> None:
        if callable(func):
            func()

    def refresh_workbench_page(self) -> None:
        self._load_office_values()
        self._skill_card.refresh()
        self._plugin_card.refresh()

    def refresh_workbench_theme(self) -> None:
        self._apply_theme()
        for card in (self._skill_card, self._plugin_card):
            card.refresh()

    def _sync_embedded_presentation(self) -> None:
        """内嵌到工作台时不再重复页内大标题：顶栏已经显示页名，与 AI 设置页一致。"""
        scaffold = getattr(self, "_scaffold", None)
        if scaffold is not None:
            scaffold.title_label.setVisible(not self._embedded)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            office_stylesheet(page_name="OfficeModePage", effort_steps=_effort_steps())
        )


__all__ = ["OfficeModePage", "OPEN_OFFICE_HINT", "SAVE_STATUS_HINT"]
