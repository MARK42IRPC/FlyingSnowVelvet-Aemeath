"""工作台「办公模式」页：只放办公相关配置，任务界面在独立办公页面里。

办公模式原来只有一页，任务历史、对话、推理、工具记录和办公配置全挤在一起。拆开之后：
任务界面搬进独立窗口（`lib.script.ui.office_page.open_office_window`），工作台里的这一页
只保留配置项——办公后端、办公模式独立 API、启动预热，以及技能与插件管理卡片。

配置控件复用 `OfficeModeSettings`（与 AI 设置面板里的同一份实现）；保存走
`save_office_values`，只写回办公相关字段，不会顺手覆盖用户没在这页看过的其它 AI 设置。
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from config.scale import scale_px
from lib.core.logger import get_logger
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES
from lib.script.ui.ai_settings_storage import load_ai_values, save_office_values
from lib.script.ui.office_manager_card import OfficeManagerCard
from lib.script.ui.office_mode_settings import OfficeModeSettings
from lib.script.ui.office_style import office_stylesheet
from lib.script.ui.workbench_settings_layout import SettingsPageScaffold

logger = get_logger(__name__)

#: 独立办公窗口的说明文案，配置页与托盘入口共用同一句。
OPEN_OFFICE_HINT = "在独立窗口里新建任务、查看对话、推理与工具记录。"


def open_office_page() -> None:
    """打开（或复用）独立办公页面；延迟导入，避免办公窗口反向依赖这一页。"""
    from lib.script.ui.office_page import open_office_window

    open_office_window()


def list_office_skills() -> list:
    from lib.script.office import skills

    return skills.list_skills()


def install_office_skill(source):
    from lib.script.office import skills

    return skills.install_skill(source)


def remove_office_skill(name: str):
    from lib.script.office import skills

    return skills.remove_skill(name)


def list_office_plugins() -> list:
    from lib.script.office import plugins

    return plugins.list_plugins()


def install_office_plugin(source):
    from lib.script.office import plugins

    return plugins.install_plugin(source)


def remove_office_plugin(name: str):
    from lib.script.office import plugins

    return plugins.remove_plugin(name)


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

    # ── 构建 ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._scaffold = SettingsPageScaffold(
            self,
            "办公模式",
            "办公任务在独立的办公页面里执行；这里保留办公后端、独立接口与技能、插件配置。",
        )
        self._build_config_section()
        self._build_manager_cards()
        # 与设置面板同一套排版：字号、控件字体与底部留白只在这里统一收口。
        self._scaffold.finish()

    def _build_config_section(self) -> None:
        section = self._scaffold.add_section(
            "办公配置",
            "办公后端、办公模式独立 API 与启动预热都在这里设置。",
        )
        self._config_section = section

        open_row = QHBoxLayout()
        open_row.setSpacing(scale_px(10, min_abs=8))
        self._open_button = self._create_primary_button("打开办公页面", section)
        self._open_button.setToolTip(OPEN_OFFICE_HINT)
        self._open_button.clicked.connect(open_office_page)
        open_row.addWidget(self._open_button, 0)
        open_hint = QLabel(OPEN_OFFICE_HINT, section)
        open_hint.setObjectName("OfficeConfigHint")
        open_hint.setWordWrap(True)
        open_row.addWidget(open_hint, 1)
        section.body_layout.addLayout(open_row)

        self._office_settings.build_into(section)

        save_row = QHBoxLayout()
        save_row.setSpacing(scale_px(10, min_abs=8))
        self._save_button = self._create_primary_button("保存办公配置", section)
        self._save_button.clicked.connect(self.save_office_config)
        save_row.addWidget(self._save_button, 0)
        self._status_label = QLabel("保存后写入本地配置，建议重启程序后完整生效。", section)
        self._status_label.setObjectName("OfficeConfigHint")
        self._status_label.setWordWrap(True)
        save_row.addWidget(self._status_label, 1)
        section.body_layout.addLayout(save_row)

    def _build_manager_cards(self) -> None:
        self._skill_card = OfficeManagerCard(
            "技能管理",
            "列出内置与已安装的 DSH 技能；安装的目录需要包含 SKILL.md。",
            kind="技能",
            loader=list_office_skills,
            installer=install_office_skill,
            remover=remove_office_skill,
            pick_title="选择技能目录（包含 SKILL.md）",
            parent=self._scaffold.content,
        )
        self._plugin_card = OfficeManagerCard(
            "插件管理",
            "列出办公 profile 加载的 DSH 插件包；安装的目录需要包含 package.json。",
            kind="插件",
            loader=list_office_plugins,
            installer=install_office_plugin,
            remover=remove_office_plugin,
            pick_title="选择插件目录（包含 package.json）",
            parent=self._scaffold.content,
        )
        self._scaffold.content_layout.addWidget(self._skill_card)
        self._scaffold.content_layout.addWidget(self._plugin_card)

    def _create_primary_button(self, text: str, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("SettingsPrimaryAction")
        button.setProperty("primary", True)
        return button

    # ── 配置读写 ─────────────────────────────────────────────────────

    def _load_office_values(self) -> None:
        try:
            self._office_settings.set_values(load_ai_values(AI_DEFAULT_VALUES))
        except Exception as exc:
            logger.warning("[OfficeModePage] 读取办公配置失败: %s", exc)

    def save_office_config(self) -> bool:
        """只把办公相关字段写回配置；失败时把原因留在状态行上。"""
        try:
            values = self._office_settings.values()
            save_office_values(values, AI_DEFAULT_VALUES)
        except Exception as exc:
            logger.error("[OfficeModePage] 保存办公配置失败: %s", exc)
            self._set_status(f"保存办公配置失败：{exc}", tone="error")
            return False
        self._set_status("办公配置已保存。")
        return True

    def _set_status(self, text: str, *, tone: str = "") -> None:
        self._status_label.setText(str(text))
        self._status_label.setProperty("tone", tone)
        style = self._status_label.style()
        if style is not None:
            style.unpolish(self._status_label)
            style.polish(self._status_label)

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

    def _apply_theme(self) -> None:
        self.setStyleSheet(office_stylesheet(page_name="OfficeModePage"))


__all__ = ["OfficeModePage", "OPEN_OFFICE_HINT"]
