"""AI 设置面板：产品侧总装（档位 D 宿主）。

面板外壳、AI 主页面、配置分类页、配置存取与配置编辑器五个 mixin 已按《render 层边界契约》
第 3 节分别下沉到 `lib/core/render/backends/qt/widgets/ai_settings_*.py`。本模块把五个 mixin
与 `QWidget` 组合成最终 `AISettingsPanel`，并保留需要读产品状态的两处装配：

- 语音包状态（`_refresh_voice_package_ui`）经 `tier_d_seams.hook('voice_package_status')` 取；
- 更新页动作装箱（`_pet_update_actions` / `_run_pet_update_action` 一族）经 seam 钩子取
  `ai_settings_update` 宿主模块。

档位 D 不得 import `lib.script`；宿主需要的产品事实一律经 `tier_d_seams.hook(name)` 取。
项目根解析器仍由本宿主覆盖，让既有 `patch.object(ai_settings_panel, "_project_root", ...)` 的
语义（外壳与页面的 `_shell_project_root` 等 hook 指向本宿主）保持不变。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QWidget

from lib.core.logger import get_logger
from lib.core.ai_settings.config_schema import (
    GENERAL_DECIMAL_SLIDER_SPECS as _GENERAL_DECIMAL_SLIDER_SPECS,
)
from lib.core.settings_schema import GENERAL_CONFIG_CATEGORIES as _GENERAL_CONFIG_CATEGORIES
from lib.core.render.backends.qt.widgets import ai_settings_about as _about_host
from lib.core.render.backends.qt.widgets import ai_settings_config_page as _config_page_host
from lib.core.render.backends.qt.widgets import ai_settings_config_store as _config_store_host
from lib.core.render.backends.qt.widgets import ai_settings_editors as _editors_host
from lib.core.render.backends.qt.widgets import ai_settings_page as _page_host
from lib.core.render.backends.qt.widgets import ai_settings_shell as _shell_host
from lib.core.render.backends.qt.widgets import tier_d_seams

_AISettingsShellMixin = _shell_host.AISettingsShellMixin
_AISettingsPageMixin = _page_host.AISettingsPageMixin
_ConfigStoreMixin = _config_store_host.ConfigStoreMixin
_ConfigPageMixin = _config_page_host.ConfigPageMixin
_ConfigEditorMixin = _editors_host.ConfigEditorMixin
_AnimationDurationSliderField = _editors_host._AnimationDurationSliderField
_ContributionCardButton = _about_host._ContributionCardButton

_logger = get_logger(__name__)

#: 外部配置字段种类；面板总装只关心这个集合，UI 取值/回填在各 mixin 宿主。
_EXTERNAL_CONFIG_FIELD_KINDS = {
    "external_autostart",
    "external_announcement_suppression",
}


def _project_root() -> Path:
    """仓库根目录：本文件位于 `lib/core/render/backends/qt/widgets/`，上溯 7 级。

    宿主侧不再依赖 `lib/script`；`path-hack` 已完成，这里保持与产品侧同源的路径解析，
    让 `patch.object(ai_settings_panel, "_project_root", ...)` 的语义继续落在本模块。
    """
    return Path(__file__).resolve().parents[6]


def _voice_package_status():
    """语音包状态（产品事实）；未注入时返回 None，`_refresh_voice_package_ui` 跳过刷新。"""
    hook = tier_d_seams.hook("voice_package_status")
    return hook() if hook is not None else None


def _update_page():
    """`ai_settings_update` 宿主模块（封装更新页动作装箱所需的入口）。"""
    hook = tier_d_seams.hook("ai_settings_update_module")
    return hook() if hook is not None else None


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
        status = _voice_package_status()
        if status is None:
            return
        self._voice_package_status = status
        self._gsv_launcher_available = not status.install_required
        self._voice_package_banner.set_package_status(status)
        self._voice_package_management.set_package_status(status)
        self._update_gsv_settings_visibility()

    def _pet_update_actions(self):
        """把更新页动作要的回调打包成显式依赖；两个对话框实例仍以面板字段为准。"""
        actions_cls = _update_page().PetUpdateActions
        return actions_cls(
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

    def _ensure_update_dialog(self):
        return self._run_pet_update_action(_update_page().ensure_update_dialog)

    def _open_update_dialog(self, mode: str) -> None:
        self._run_pet_update_action(lambda actions: _update_page().open_update_dialog(actions, mode))

    def _on_check_updates(self) -> None:
        self._run_pet_update_action(_update_page().on_check_updates)

    def _on_sync_dev_build(self) -> None:
        self._run_pet_update_action(_update_page().on_sync_dev_build)

    def _ensure_qq_group_dialog(self):
        return self._run_pet_update_action(_update_page().ensure_qq_group_dialog)

    def _open_quark_manual_update(self) -> None:
        self._run_pet_update_action(_update_page().open_quark_manual_update)

    def _show_qq_group_qrcode(self) -> None:
        self._run_pet_update_action(_update_page().show_qq_group_qrcode)

    def _on_uninstall_pet(self) -> None:
        self._run_pet_update_action(_update_page().uninstall_pet)


__all__ = [
    "AISettingsPanel",
    "_ContributionCardButton",
    "_AnimationDurationSliderField",
    "_GENERAL_CONFIG_CATEGORIES",
    "_GENERAL_DECIMAL_SLIDER_SPECS",
    "_EXTERNAL_CONFIG_FIELD_KINDS",
]
