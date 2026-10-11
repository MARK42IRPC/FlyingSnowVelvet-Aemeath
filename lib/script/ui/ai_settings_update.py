"""AI 设置面板的「桌宠更新」页（产品侧再导出垫片）。

更新页（视图 + 控制器）已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_update.py`。这里按原名再导出，既有 `panel_module.X`
取用与对 `resolve_uninstaller` / `launch_uninstaller` 的 patch 都打到本垫片；卸载入口与三个对话框
由本垫片导入时注入。
"""

from __future__ import annotations

from config.shared_storage_paths import (  # noqa: F401 - 既有调用面
    get_shared_root_dir,
)
from lib.script.app.uninstall_entry import launch_uninstaller, resolve_uninstaller
from lib.script.ui import render_bridge
from lib.script.ui.confirm_dialog import ask_confirmation
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.update_dialog import DesktopPetUpdateDialog

(
    QUARK_UPDATE_URL,
    UPDATE_BUTTON_ROW_GAP,
    PetUpdateActions,
    build_desktop_pet_update_panel,
    ensure_qq_group_dialog,
    ensure_update_dialog,
    on_check_updates,
    on_sync_dev_build,
    open_quark_manual_update,
    open_update_dialog,
    show_qq_group_qrcode,
    uninstall_pet,
) = render_bridge.tier_d_reexports("ai_settings_update", (
    "QUARK_UPDATE_URL",
    "UPDATE_BUTTON_ROW_GAP",
    "PetUpdateActions",
    "build_desktop_pet_update_panel",
    "ensure_qq_group_dialog",
    "ensure_update_dialog",
    "on_check_updates",
    "on_sync_dev_build",
    "open_quark_manual_update",
    "open_update_dialog",
    "show_qq_group_qrcode",
    "uninstall_pet",
))


def _uninstall_resolve(root_dir):
    return resolve_uninstaller(root_dir)


def _uninstall_launch(uninstaller) -> None:
    launch_uninstaller(uninstaller)


def _confirm_dialog_ask(*args, **kwargs):
    return ask_confirmation(*args, **kwargs)


def _qq_group_dialog_class():
    return QQGroupDialog


def _update_dialog_class():
    return DesktopPetUpdateDialog


render_bridge.install_tier_d_hook("uninstall_resolve", _uninstall_resolve)
render_bridge.install_tier_d_hook("uninstall_launch", _uninstall_launch)
render_bridge.install_tier_d_hook("confirm_dialog_ask", _confirm_dialog_ask)
render_bridge.install_tier_d_hook("qq_group_dialog_class", _qq_group_dialog_class)
render_bridge.install_tier_d_hook("update_dialog_class", _update_dialog_class)

__all__ = [
    "QUARK_UPDATE_URL",
    "UPDATE_BUTTON_ROW_GAP",
    "PetUpdateActions",
    "build_desktop_pet_update_panel",
    "ensure_qq_group_dialog",
    "ensure_update_dialog",
    "on_check_updates",
    "on_sync_dev_build",
    "open_quark_manual_update",
    "open_update_dialog",
    "show_qq_group_qrcode",
    "uninstall_pet",
]
