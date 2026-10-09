"""AI 设置面板的「桌宠更新」页（视图 + 控制器）。

批次 3 第二个切分单元。这一页与「支持作者 / 贡献者」同属只读页：没有配置字段、不参与
`_collect_values` / `_set_values_to_form` 的取值顺序。不同之处是它带五个**动作**——
检查更新 / 同步开发版、打开夸克网盘、显示 QQ 群二维码、卸载桌宠——动作会反馈到面板
（淡出、信息气泡、退出事件），因此按 C 类的「控制器」口径切成模块函数，面板状态经
`PetUpdateActions` 显式注入而不是读 `self`。

- **视图**：`build_desktop_pet_update_panel()` 装配四个分区（稳定版本 / 开发版本 / 手动获取 /
  卸载）与五个按钮，对象名、间距、文案与搬出前逐行等价；按钮回调改指向 `actions` 上的
  五个可调用项（面板在绑定时传入同名方法）。
- **控制器**：`ensure_update_dialog` / `open_update_dialog` / `on_check_updates` /
  `on_sync_dev_build` / `ensure_qq_group_dialog` / `open_quark_manual_update` /
  `show_qq_group_qrcode` / `uninstall_pet`。两个对话框实例缓存在 `actions` 的字段上，
  面板的 `_update_dialog` / `_qq_group_dialog` 继续指向同一份状态。
- **描述**：本页没有专属 QSS（按钮走面板共享样式表），只导出两个布局事实
  `UPDATE_BUTTON_ROW_GAP` 与 `QUARK_UPDATE_URL`。

本模块仍在 `lib/script/ui/` 下、仍 import `PyQt5`，因此 `frozen_ui_qt_importers` 需登记本文件；
`ui -> 产品包` 耦合清单不变（不 import `chat` / `office` / `music` / `gsvmove`）。
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from config.config import UI
from config.scale import scale_px
from config.shared_storage_paths import get_shared_root_dir
from lib.core.event.center import Event, EventType
from lib.script.app.uninstall_entry import launch_uninstaller, resolve_uninstaller
from lib.script.ui.confirm_dialog import ask_confirmation
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.ui.workbench_settings_layout import SettingsPageScaffold

UPDATE_BUTTON_ROW_GAP = scale_px(10, min_abs=10)
QUARK_UPDATE_URL = "https://pan.quark.cn/s/9158e62439e2"


@dataclass
class PetUpdateActions:
    """更新页动作所需的显式依赖；面板在绑定与建页时填充。"""

    check_updates: Callable[[], None]
    sync_dev_build: Callable[[], None]
    open_quark_manual: Callable[[], None]
    show_qq_group: Callable[[], None]
    uninstall_pet: Callable[[], None]
    show_info: Callable[[str], None]
    emit_info: Callable[..., None]
    fade_out: Callable[[], None]
    event_center: Any
    root_dir: Path
    dialog_parent: QWidget
    update_dialog: DesktopPetUpdateDialog | None = field(default=None)
    qq_group_dialog: QQGroupDialog | None = field(default=None)


def build_desktop_pet_update_panel(
    panel: QWidget,
    scaffold: SettingsPageScaffold,
    category_title: str,
    title_label: QLabel,
    hint_label: QLabel,
    tab_meta: dict[str, dict],
    actions: PetUpdateActions,
) -> QWidget:
    stable_section = scaffold.add_help_section(
        "稳定版本",
        "检查最新分发包。下载完成后桌宠会退出，由独立更新进程覆盖安装并重新启动。",
        help_text=(
            "稳定版是给所有用户用的正式版本，更新前会先核对发布清单里的校验值。\n\n"
            "点「检查新版本」后桌宠会去发布槽取最新安装包。下载完成不会当场替换文件，"
            "而是先退出桌宠，再由独立更新进程接管安装目录、覆盖完成之后把桌宠重新拉起来，"
            "所以更新过程中桌面会短暂少一只桌宠，属于正常现象。\n\n"
            "更新只覆盖程序文件，用户数据、配置和缓存都不受影响。"
        ),
    )
    stable_row = QHBoxLayout()
    stable_row.setContentsMargins(0, 0, 0, 0)
    stable_row.setSpacing(scale_px(8, min_abs=6))
    check_update_btn = QPushButton("检查新版本", stable_section)
    check_update_btn.setObjectName("checkUpdateButton")
    check_update_btn.setProperty("primary", True)
    check_update_btn.clicked.connect(actions.check_updates)
    stable_row.addWidget(check_update_btn, 1)
    stable_section.body_layout.addLayout(stable_row)

    dev_section = scaffold.add_help_section(
        "开发版本",
        "面向需要跟随远端代码的使用场景。同步前请先确认本地改动已妥善保存。",
        help_text=(
            "开发版跟随远端主干代码，比稳定版更新得更快，但也可能带着还没验证完的改动。\n\n"
            "同步会用远端版本覆盖本地程序文件。如果你改过源码、装过额外插件，"
            "请先确认这些改动已经妥善保存或另有备份，覆盖之后无法找回。\n\n"
            "只想要能稳定用的版本时，请走上面的稳定版。"
        ),
    )
    sync_dev_btn = QPushButton("同步开发版", dev_section)
    sync_dev_btn.setObjectName("syncDevButton")
    sync_dev_btn.clicked.connect(actions.sync_dev_build)
    dev_section.body_layout.addWidget(sync_dev_btn)

    manual_section = scaffold.add_help_section(
        "手动获取",
        "自动更新不可用时，可通过网盘或 QQ 群获取完整安装包。",
        help_text=(
            "网络不通、更新服务 unavailable 或者自动更新反复失败时走这里。\n\n"
            "网盘和 QQ 群里放的都是完整安装包，下载后直接安装即可，"
            "不需要先卸载旧版本，安装程序会自己处理覆盖。"
        ),
    )
    manual_row = QHBoxLayout()
    manual_row.setContentsMargins(0, 0, 0, 0)
    manual_row.setSpacing(UPDATE_BUTTON_ROW_GAP)
    quark_update_btn = QPushButton("打开夸克网盘", manual_section)
    quark_update_btn.setObjectName("quarkManualUpdateButton")
    quark_update_btn.clicked.connect(actions.open_quark_manual)
    manual_row.addWidget(quark_update_btn, 1)
    qq_group_btn = QPushButton("查看 QQ 群", manual_section)
    qq_group_btn.setObjectName("qqGroupUpdateButton")
    qq_group_btn.clicked.connect(actions.show_qq_group)
    manual_row.addWidget(qq_group_btn, 1)
    manual_section.body_layout.addLayout(manual_row)

    uninstall_section = scaffold.add_help_section(
        "卸载",
        "退出桌宠并把程序文件交给安装版卸载程序；源码工作区只能手动删除目录。",
        help_text=(
            "离线安装包装好的飞行雪绒自带卸载程序，就在启动器的旁边。\n\n"
            "点「卸载桌宠」会先退出桌宠，再由卸载程序删除 app 与 runtime 下的程序文件；"
            "用户数据、记忆、语音包默认保留，卸载界面上可以勾选一并删除。\n\n"
            "源码工作区没有卸载程序：想移除就把当前目录整个删掉。"
            "用户数据仍然放在共享目录里，需要时单独清理。"
        ),
    )
    uninstall_btn = QPushButton("卸载桌宠", uninstall_section)
    uninstall_btn.setObjectName("uninstallPetButton")
    uninstall_btn.setProperty("danger", True)
    uninstall_btn.clicked.connect(actions.uninstall_pet)
    uninstall_section.body_layout.addWidget(uninstall_btn)
    scaffold.finish()

    tab_meta["desktop_pet_update"] = {
        "panel": panel,
        "fields": [],
        "defaults": {},
        "title": category_title,
        "title_label": title_label,
        "hint_label": hint_label,
        "section_title_labels": [
            stable_section.title_label,
            dev_section.title_label,
            manual_section.title_label,
            uninstall_section.title_label,
        ],
        "section_hint_labels": [
            stable_section.description_label,
            dev_section.description_label,
            manual_section.description_label,
            uninstall_section.description_label,
        ],
        "buttons": [
            check_update_btn,
            sync_dev_btn,
            quark_update_btn,
            qq_group_btn,
            uninstall_btn,
        ],
    }

    return panel


def ensure_update_dialog(actions: PetUpdateActions) -> DesktopPetUpdateDialog:
    if actions.update_dialog is None:
        actions.update_dialog = DesktopPetUpdateDialog()
    return actions.update_dialog


def open_update_dialog(actions: PetUpdateActions, mode: str) -> None:
    dialog = ensure_update_dialog(actions)
    if dialog.is_busy():
        actions.emit_info("更新窗口正在处理任务，请稍候。", min_tick=12, max_tick=160)
        return

    actions.fade_out()
    delay_ms = max(80, int(UI.get("ui_fade_duration", 180)))

    def show_dialog() -> None:
        started = (
            dialog.begin_release_check()
            if mode == "release"
            else dialog.begin_git_sync_check()
        )
        if not started:
            actions.emit_info("更新窗口正在处理任务，请稍候。", min_tick=12, max_tick=160)

    QTimer.singleShot(delay_ms, show_dialog)


def on_check_updates(actions: PetUpdateActions) -> None:
    open_update_dialog(actions, "release")


def on_sync_dev_build(actions: PetUpdateActions) -> None:
    open_update_dialog(actions, "git")


def ensure_qq_group_dialog(actions: PetUpdateActions) -> QQGroupDialog:
    if actions.qq_group_dialog is None:
        image_path = actions.root_dir / "resc" / "GIF" / "QQqrc.png"
        actions.qq_group_dialog = QQGroupDialog(image_path)
    return actions.qq_group_dialog


def open_quark_manual_update(actions: PetUpdateActions) -> None:
    try:
        opened = webbrowser.open(QUARK_UPDATE_URL)
    except Exception as exc:
        actions.show_info(f"打开夸克更新链接失败：{exc}")
        return
    if not opened:
        actions.show_info(f"未能调用系统默认浏览器，请手动打开：{QUARK_UPDATE_URL}")


def show_qq_group_qrcode(actions: PetUpdateActions) -> None:
    ensure_qq_group_dialog(actions).show_dialog()


def uninstall_pet(actions: PetUpdateActions) -> None:
    uninstaller = resolve_uninstaller(actions.root_dir)
    if uninstaller is None:
        actions.show_info(
            "源码工作区没有安装版卸载程序。\n\n"
            "想移除飞行雪绒，直接删除当前工作区目录即可；"
            f"用户数据、记忆与语音包保存在 {get_shared_root_dir()} 下，需要时单独删除。"
        )
        return
    confirmed = ask_confirmation(
        actions.dialog_parent,
        title="卸载桌宠",
        text="确定卸载桌宠吗？",
        informative_text=(
            "将退出桌宠，并由卸载程序删除程序文件。\n"
            "用户数据、记忆与语音包默认保留，可在卸载界面上勾选一并删除。\n\n"
            "确定继续吗？"
        ),
        confirm_text="卸载",
        destructive=True,
        layer_name="UninstallPetConfirmation",
    )
    if not confirmed:
        return
    try:
        launch_uninstaller(uninstaller)
    except Exception as exc:
        actions.show_info(f"启动卸载程序失败：{exc}")
        return
    actions.emit_info("卸载程序已启动，桌宠即将退出。", min_tick=10, max_tick=120)

    def quit_for_uninstall() -> None:
        actions.event_center.publish(Event(EventType.APP_QUIT, {"exit_code": 0}))

    QTimer.singleShot(600, quit_for_uninstall)
