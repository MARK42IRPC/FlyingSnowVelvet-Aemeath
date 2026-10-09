"""AI 设置面板：编辑并保存 config/ollama_config.py。"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QWidget

from lib.script.ui import ai_settings_update as _update_page
from lib.script.ui.ai_settings_config_schema import (  # noqa: F401 - 既有导出面
    GENERAL_DECIMAL_SLIDER_SPECS as _GENERAL_DECIMAL_SLIDER_SPECS,
)
from lib.script.ui.ai_settings_about import (
    _ContributionCardButton as _ContributionCardButton,
)
from lib.script.ui.ai_settings_config_page import (
    ConfigPageMixin as _ConfigPageMixin,
)
from lib.script.ui.ai_settings_config_store import (
    ConfigStoreMixin as _ConfigStoreMixin,
)
from lib.script.ui.ai_settings_shell import (
    AISettingsShellMixin as _AISettingsShellMixin,
)
from lib.script.ui.ai_settings_page import (
    AISettingsPageMixin as _AISettingsPageMixin,
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
from lib.core.logger import get_logger
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.workbench.settings import GENERAL_CONFIG_CATEGORIES
from lib.script.gsvmove import get_voice_package_status


_logger = get_logger(__name__)

_DROPDOWN_POPUP_LAYER = 601

#: AI 主页面的装配与取值/回填闭环已下沉到 `lib/script/ui/ai_settings_page.py`
#: （`AISettingsPageMixin`，第 53 节）、配置存取到 `ai_settings_config_store.py`（第 54 节）、
#: 面板外壳到 `ai_settings_shell.py`（第 55 节）；本文件只做组合与更新页转发。
_EXTERNAL_CONFIG_FIELD_KINDS = {
    "external_autostart",
    "external_announcement_suppression",
}
# 贡献名单的常量（忽略标题片段 / 隐藏角色 / 手工条目）与解析逻辑已下沉到
# `lib/script/ui/ai_settings_contributions.py`；本文件只保留下面的路径与加载委托。
_GENERAL_CONFIG_CATEGORIES = GENERAL_CONFIG_CATEGORIES


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


class AISettingsPanel(
    _AISettingsShellMixin,
    _AISettingsPageMixin,
    _ConfigStoreMixin,
    _ConfigPageMixin,
    _ConfigEditorMixin,
    QWidget,
):
    """托盘入口 AI 设置面板。"""

    #: 各拆分模块的项目根解析钩子；统一覆盖为面板自己的 `_project_root`，
    #: 让既有 `patch.object(ai_settings_panel, "_project_root", ...)` 继续生效。
    _shell_project_root = staticmethod(lambda: _project_root())
    _editor_project_root = staticmethod(lambda: _project_root())
    _page_project_root = staticmethod(lambda: _project_root())
    _store_project_root = staticmethod(lambda: _project_root())

    def _refresh_voice_package_ui(self) -> None:
        status = get_voice_package_status()
        self._voice_package_status = status
        self._gsv_launcher_available = not status.install_required
        self._voice_package_banner.set_package_status(status)
        self._voice_package_management.set_package_status(status)
        self._update_gsv_settings_visibility()

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
