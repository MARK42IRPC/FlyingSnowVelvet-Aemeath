"""Compact ONNX voice-package banner and installer dialog（档位 D 宿主）。

本模块原住在 `lib/script/ui/voice_package_installer.py`，按《render 层边界契约》第 3 节下沉到
档位 D：`VoicePackageManagementBar`（已安装/不完整包的管理条）与
`VoicePackageInstallerDialog`（下载、解压、校验与激活的浮窗）。

宿主不得 import `lib.script`：安装器业务（profiles / 安装器 / 远端大小探测 / 状态类型）、
工作台主题色、语音包状态与 gsvmove 服务一律经 `tier_d_seams.hook(name)` 取，声明为模块级函数
以便产品侧沿用既有补丁点（`list_fixed_drive_roots` / `get_compute_hub` /
`fetch_voice_package_size` / `voice_package_installer:VoicePackageInstaller` /
`voice_package_status_class` 等）；共享尺寸与配色助手、驱动下拉与安装横幅来自同族宿主
`voice_package_widgets`（档位 D 内部）；字体/文本度量/屏幕几何/居中落位走 `tier_d_seams`。

产品侧 `lib/script/ui/voice_package_installer.py` 只做再导出，既有导入面不变。
"""

from __future__ import annotations

import shutil
from concurrent.futures import CancelledError
from pathlib import Path
from weakref import ref

from PyQt5.QtCore import QCoreApplication, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)

from config.scale import scale_px
from lib.core.event.center import Event, EventType
from lib.core.logger import get_logger
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.voice_package_widgets import (
    _VoiceDriveComboBox,
    _color,
    _format_bytes,
)
from lib.core.render.backends.qt.widgets.message_box_host import ask_confirmation, post_message
from lib.core.render.backends.qt.widgets.spec_host import QtSpecWidget, build_spec_window
from lib.core.render.visuals.announcement_chrome import announcement_panel_body, announcement_shell_spec
from lib.core.render.visuals.message_box import ICON_WARNING
from lib.core.render.visuals.window_spec import (
    LAYOUT_ROW,
    ROLE_ACCENT,
    ROLE_PRIMARY,
    ButtonSpec,
    LabelSpec,
    LayoutSpec,
    ProgressBarSpec,
    StretchSpec,
)


logger = get_logger(__name__)


def _installer_api():
    hook = tier_d_seams.hook("voice_package_installer:api")
    if hook is None:
        raise RuntimeError("语音包安装器需要经 render_bridge 注入 gsvmove 安装接口")
    return hook()


def _product():
    """产品侧垫片模块：宿主按名字取它，使既有补丁点（`patch.object(installer_ui, ...)`）继续生效。"""
    hook = tier_d_seams.hook("voice_package_installer:product_module")
    if hook is None:
        raise RuntimeError("语音包安装器需要经 render_bridge 注入产品侧装配模块")
    return hook()


def VoicePackageStatus(*args, **kwargs):
    """语音包状态记录：类型类经 seam 注入（宿主不 import 产品包）。"""
    hook = tier_d_seams.hook("voice_package_status_class")
    if hook is None:
        raise RuntimeError("语音包安装器需要经 render_bridge 注入语音包状态类型")
    return hook()(*args, **kwargs)


def list_fixed_drive_roots(*args, **kwargs):
    return _product().list_fixed_drive_roots(*args, **kwargs)


def fetch_voice_package_sizes(*args, **kwargs):
    return _product().fetch_voice_package_sizes(*args, **kwargs)


def fetch_voice_package_size(*args, **kwargs):
    return _product().fetch_voice_package_size(*args, **kwargs)


def get_voice_package_profile(*args, **kwargs):
    return _product().get_voice_package_profile(*args, **kwargs)


def VoicePackageInstaller(*args, **kwargs):
    return _product().VoicePackageInstaller(*args, **kwargs)


def get_compute_hub():
    return _product().get_compute_hub()


def _gsvmove_service():
    hook = tier_d_seams.hook("voice_package_installer:gsvmove_service")
    if hook is None:
        raise RuntimeError("语音包安装器需要经 render_bridge 注入 gsvmove 服务")
    return hook()


def get_ui_font(size: int | None = None):
    return tier_d_seams.ui_font(size)


#: `VOICE_PACKAGE_PROFILES`：产品侧从 gsvmove 取一次，宿主按 profile.key 遍历。
def _profiles():
    return _installer_api().VOICE_PACKAGE_PROFILES


class VoicePackageManagementBar(QFrame):
    package_removed = pyqtSignal(object)
    removal_failed = pyqtSignal(str)
    _remove_success_signal = pyqtSignal(object)
    _remove_error_signal = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VoicePackageManagementBar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._status = VoicePackageStatus("missing", "尚未安装 ONNX 语音包")
        self._busy = False
        self._remove_future = None

        self._detail = QLabel("爱弥斯 ONNX 语音包已安装", self)
        self._detail.setObjectName("VoicePackageManagementDetail")
        self._detail.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        self._detail.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.remove_button = QPushButton("删除语音包", self)
        self.remove_button.setObjectName("VoicePackageRemoveButton")
        self.remove_button.setFixedWidth(scale_px(116, min_abs=104))
        self.remove_button.clicked.connect(self._on_remove_clicked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            scale_px(11, min_abs=9),
            scale_px(8, min_abs=7),
            scale_px(11, min_abs=9),
            scale_px(8, min_abs=7),
        )
        layout.setSpacing(scale_px(10, min_abs=8))
        layout.addWidget(self._detail, 1)
        layout.addWidget(self.remove_button, 0, Qt.AlignVCenter)

        self._remove_success_signal.connect(self._on_remove_success)
        self._remove_error_signal.connect(self._on_remove_error)
        self._apply_style()

    def set_package_status(self, status: VoicePackageStatus) -> None:
        self._status = status
        has_package = status.package_root is not None and status.kind in {"installed", "invalid"}
        if status.kind == "invalid":
            self._detail.setText("检测到不完整的爱弥斯 ONNX 语音包")
        else:
            self._detail.setText("爱弥斯 ONNX 语音包已安装")
        self._detail.setToolTip(str(status.package_root or ""))
        if not self._busy:
            self.setVisible(has_package)

    def is_busy(self) -> bool:
        return self._busy

    def _confirm_removal(self, package_root: Path) -> bool:
        return ask_confirmation(
            self,
            title="删除语音包",
            text="确认删除爱弥斯 ONNX 语音包？",
            informative_text=(
                "删除后语音合成将暂停，可随时重新安装。\n"
                f"位置：{package_root}"
            ),
            confirm_text="确认删除",
            cancel_text="取消",
            destructive=True,
            layer_name="VoicePackageRemovalConfirmation",
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.remove_button.setEnabled(not self._busy)
        self.remove_button.setText("正在删除" if self._busy else "删除语音包")
        if self._busy:
            self._detail.setText("正在释放模型并删除本地语音包")

    def _on_remove_clicked(self) -> None:
        package_root = self._status.package_root
        if self._busy or package_root is None or not self._confirm_removal(package_root):
            return
        self._set_busy(True)

        def run_removal() -> None:
            try:
                removed = _product().remove_voice_package(package_root)
                self._remove_success_signal.emit(removed)
            except Exception as exc:
                self._remove_error_signal.emit(str(exc))

        try:
            future = get_compute_hub().submit_interactive_io(run_removal)
            if self._busy:
                self._remove_future = future
        except Exception as exc:
            self._on_remove_error(f"删除任务启动失败：{exc}")

    def _on_remove_success(self, package_root: object) -> None:
        self._remove_future = None
        self._set_busy(False)
        self.set_package_status(VoicePackageStatus("missing", "尚未安装 ONNX 语音包"))
        self.package_removed.emit(package_root)

    def _on_remove_error(self, message: str) -> None:
        self._remove_future = None
        self._set_busy(False)
        self._detail.setText("语音包删除失败，可重新尝试")
        self.removal_failed.emit(str(message or "语音包删除失败"))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#VoicePackageManagementBar {{
                background: {_color('surface_raised')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
            }}
            QLabel#VoicePackageManagementDetail {{
                color: {_color('text_dim')};
                background: transparent;
                border: none;
            }}
            QPushButton#VoicePackageRemoveButton {{
                min-height: {scale_px(30, min_abs=27)}px;
                padding: 0px {scale_px(9, min_abs=7)}px;
                color: {_color('text')};
                background: {_color('surface')};
                border: 1px solid {_color('pink')};
                border-radius: 3px;
                font-weight: 600;
            }}
            QPushButton#VoicePackageRemoveButton:hover {{
                color: {_color('text')};
                background: {_color('surface_hover')};
            }}
            QPushButton#VoicePackageRemoveButton:disabled {{
                color: {_color('text_dim')};
                border-color: {_color('border')};
            }}
            """
        )


def _installer_window_spec():
    """A two-choice, two-progress body inside the shared announcement shell."""
    gap = scale_px(9, min_abs=7)
    bar_height = scale_px(24, min_abs=21)
    return announcement_shell_spec(
        object_name="VoicePackageInstallerDialog",
        title="安装最新语音包",
        height=scale_px(460, min_abs=420),
        body=announcement_panel_body((
            LabelSpec("选择安装磁盘", id="status", plain_text=True, word_wrap=True,
                      font_size=scale_px(12, min_abs=11), bold=True),
            LayoutSpec(id="profile_row", direction=LAYOUT_ROW, spacing=gap,
                       children=(LabelSpec("语音包档位"),)),
            LayoutSpec(id="drive_row", direction=LAYOUT_ROW, spacing=gap,
                       children=(LabelSpec("安装磁盘"),)),
            LabelSpec("", id="drive_detail", plain_text=True, word_wrap=True),
            StretchSpec(),
            LabelSpec("下载进度"),
            ProgressBarSpec(id="download", tone="accent",
                            maximum=1000, fmt="等待开始", min_height=bar_height),
            LabelSpec("解压、校验与安装进度", id="extract_label"),
            ProgressBarSpec(id="extract", tone="highlight",
                            maximum=1000, fmt="等待开始", min_height=bar_height),
        )),
        minimize_action="minimize",
        close_action="close",
        non_closing_actions=("minimize", "close", "background", "secondary", "primary"),
        actions=(
            ButtonSpec("后台安装", "background", id="background", role=ROLE_ACCENT),
            ButtonSpec("取消", "secondary", id="secondary"),
            ButtonSpec("开始安装", "primary", id="primary", role=ROLE_PRIMARY),
        ),
    )


class VoicePackageInstallerDialog(QtSpecWidget):
    install_succeeded = pyqtSignal(object)
    # ``object`` counts retain precision for archives larger than a 32-bit int.
    _progress_signal = pyqtSignal(int, str, object, object, str)
    _info_signal = pyqtSignal(int, str)
    _finished_signal = pyqtSignal(int, str, object)
    _remote_sizes_signal = pyqtSignal(int, object)
    _app_exit_signal = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        self._completed = False
        self._backgrounded = False
        self._background_notified_stages: set[str] = set()
        self._installer = None
        self._install_future = None
        self._install_generation = 0
        self._cancel_requested = False
        self._remote_sizes = {}
        self._remote_size_generation = 0
        self._remote_size_future = None
        self._failure_notice = None
        self._cleaned_up = False

        self._shell = build_spec_window(
            _installer_window_spec(), widget=self, parent=parent, dialog=False,
            font_factory=get_ui_font, presentation_host=tier_d_seams.presentation_host(),
            on_semantic=self._on_action,
        )
        nodes = self._shell.find
        self._title = nodes("header")
        self._minimize_btn = nodes("minimize")
        self._close_btn = nodes("close")
        self._status = nodes("status")
        self._drive_detail = nodes("drive_detail")
        self._download_bar = nodes("download")
        self._extract_bar = nodes("extract")
        self._extract_label = nodes("extract_label")
        self._extract_bar.setProperty("windowProgressRole", "accent")
        self._secondary = nodes("secondary")
        self._background = nodes("background")
        self._primary = nodes("primary")
        self._background.hide()

        profile_row = nodes("profile_row")
        drive_row = nodes("drive_row")
        self._profile_combo = _VoiceDriveComboBox(profile_row)
        self._drive_combo = _VoiceDriveComboBox(drive_row)
        for combo, row, count in (
            (self._profile_combo, profile_row, 3), (self._drive_combo, drive_row, 8),
        ):
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            combo.setMaxVisibleItems(count)
            combo.currentIndexChanged.connect(self._update_drive_detail)
            row.layout().addWidget(combo, 1)

        queued = Qt.QueuedConnection
        self._progress_signal.connect(self._on_progress, queued)
        self._info_signal.connect(self._on_info, queued)
        self._finished_signal.connect(self._on_install_finished, queued)
        self._remote_sizes_signal.connect(self._apply_remote_sizes, queued)
        self._app_exit_signal.connect(self.cleanup)
        self._event_center = _product().get_event_center()
        self._event_center.subscribe(EventType.APP_EXIT, self._on_app_exit)
        self.destroyed.connect(lambda *_: self._disconnect_app_exit())

    def _on_action(self, semantic: str) -> None:
        if semantic in ("minimize", "close"):
            self.hide_dialog()
        elif semantic == "background":
            self._move_install_to_background()
        elif semantic == "secondary":
            self._on_secondary()
        elif semantic == "primary":
            self._on_primary()

    def show_dialog(self) -> None:
        if self._cleaned_up:
            return
        if not self._busy:
            try:
                self._reset()
            except Exception:
                # This entry is called from QTimer after the settings window fades.
                self._report_install_failure("安装窗口启动失败：无法读取安装选项")
                return
        else:
            self._backgrounded = False
            self._background.setEnabled(True)
        self._shell.center_on()
        self._shell.show_window()
        if not self._busy:
            self._start_remote_size_probe()

    def is_busy(self) -> bool:
        return self._busy

    def minimize_floating_window(self) -> None:
        # Busy dialogs are hidden only by the explicit background action.
        self.hide_dialog()

    def _sync_minimize_button(self) -> None:
        self._minimize_btn.setEnabled(not self._busy)
        self._close_btn.setEnabled(not self._busy)

    def _close_failure_notice(self) -> None:
        notice, self._failure_notice = self._failure_notice, None
        if notice is not None:
            notice.cleanup()

    def _on_app_exit(self, _event) -> None:
        self._app_exit_signal.emit()

    def _disconnect_app_exit(self) -> None:
        center, self._event_center = self._event_center, None
        if center is not None:
            center.unsubscribe(EventType.APP_EXIT, self._on_app_exit)

    def _reset(self) -> None:
        self._completed = False
        self._backgrounded = False
        self._background_notified_stages.clear()
        self._remote_sizes = {}
        self._status.setText("选择安装磁盘")
        self._download_bar.setRange(0, 1000)
        self._download_bar.setValue(0)
        self._download_bar.setFormat("等待开始")
        self._extract_bar.setRange(0, 1000)
        self._extract_bar.setValue(0)
        self._extract_bar.setFormat("等待开始")
        self._secondary.setText("取消")
        self._secondary.setEnabled(True)
        self._secondary.show()
        self._background.setEnabled(True)
        self._sync_minimize_button()
        self._background.hide()
        self._primary.setText("开始安装")
        self._primary.setEnabled(True)
        self._primary.show()
        self._profile_combo.setEnabled(True)
        self._refresh_profiles()
        self._drive_combo.setEnabled(True)
        self._refresh_drives()

    def _refresh_profiles(self) -> None:
        current = self._profile_combo.currentData()
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for profile in _profiles().values():
            recommendation = " · 强烈推荐" if profile.key == "int8" else ""
            archive_bytes = self._archive_bytes_for(profile)
            source_suffix = "" if profile.key in self._remote_sizes else " · 离线估算"
            self._profile_combo.addItem(
                f"{profile.title}{recommendation} · {_format_bytes(archive_bytes)}{source_suffix}",
                profile.key,
            )
        index = self._profile_combo.findData(current or "fp16")
        self._profile_combo.setCurrentIndex(max(0, index))
        self._profile_combo.blockSignals(False)

    def _refresh_drives(self) -> None:
        current = self._drive_combo.currentData()
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        for root in list_fixed_drive_roots():
            try:
                free = shutil.disk_usage(root).free
            except OSError:
                continue
            self._drive_combo.addItem(f"{root}  可用 {_format_bytes(free)}", str(root))
        if current:
            index = self._drive_combo.findData(current)
            if index >= 0:
                self._drive_combo.setCurrentIndex(index)
        self._drive_combo.blockSignals(False)
        self._primary.setEnabled(self._drive_combo.count() > 0)
        self._update_drive_detail()

    def _archive_bytes_for(self, profile) -> int:
        remote = self._remote_sizes.get(profile.key)
        if remote is not None and remote.archive_bytes > 0:
            return remote.archive_bytes
        return profile.archive_bytes

    def _start_remote_size_probe(self) -> None:
        future = self._remote_size_future
        if future is not None:
            try:
                if not future.done():
                    return
            except Exception:
                pass

        self._remote_size_generation += 1
        generation = self._remote_size_generation

        def worker() -> None:
            try:
                sizes = fetch_voice_package_sizes()
            except Exception:
                sizes = {}
            if not self._cleaned_up:
                self._remote_sizes_signal.emit(generation, sizes)

        try:
            self._remote_size_future = get_compute_hub().submit_interactive_io(worker)
        except Exception:
            self._remote_size_future = None

    def _apply_remote_sizes(self, generation: int, sizes: object) -> None:
        if self._cleaned_up or int(generation) != self._remote_size_generation:
            return
        if isinstance(sizes, dict):
            self._remote_sizes = {
                str(key): value
                for key, value in sizes.items()
                if isinstance(value, _installer_api().VoicePackageRemoteSize) and value.archive_bytes > 0
            }
        if not self._busy:
            self._refresh_profiles()
            self._update_drive_detail()

    def _update_drive_detail(self, *_args) -> None:
        path_text = str(self._drive_combo.currentData() or "")
        if not path_text:
            self._drive_detail.setText("未检测到可写入的固定磁盘")
            return
        target = Path(path_text) / "AemeathDeskPet" / "voice"
        profile = get_voice_package_profile(self._profile_combo.currentData())
        archive_bytes = self._archive_bytes_for(profile)
        remote = self._remote_sizes.get(profile.key)
        source_detail = f"（{remote.source_name}）" if remote is not None else "（离线估算）"
        self._drive_detail.setText(
            f"{profile.title}：安装过程需要 {_format_bytes(profile.required_free_bytes_for(archive_bytes))}，"
            f"下载包大小为 {_format_bytes(archive_bytes)}{source_detail}，"
            f"安装后占用硬盘空间 {_format_bytes(profile.extracted_bytes)}\n"
            f"安装到 {target}"
        )

    def _on_primary(self) -> None:
        if self._cleaned_up or self._busy:
            return
        if self._completed:
            self.hide_dialog()
            return
        drive_text = str(self._drive_combo.currentData() or "")
        profile_key = str(self._profile_combo.currentData() or "")
        if not drive_text:
            self._status.setText("没有可用的安装磁盘")
            return

        self._close_failure_notice()
        self._install_generation += 1
        generation = self._install_generation
        self._cancel_requested = False
        self._busy = True
        self._status.setText("正在准备下载")
        self._sync_minimize_button()
        self._download_bar.setRange(0, 0)
        self._download_bar.setFormat("准备中")
        self._extract_bar.setRange(0, 1000)
        self._extract_bar.setValue(0)
        self._extract_bar.setFormat("等待开始")
        self._primary.setEnabled(False)
        self._primary.hide()
        self._secondary.setText("取消安装")
        self._background.show()
        self._profile_combo.setEnabled(False)
        self._drive_combo.setEnabled(False)
        remote = self._remote_sizes.get(profile_key)
        archive_bytes = remote.archive_bytes if remote is not None else None
        owner = ref(self)

        def emit(signal, *args):
            dialog = owner()
            if dialog is not None and not dialog._cleaned_up:
                try:
                    getattr(dialog, signal).emit(generation, *args)
                except RuntimeError:
                    # Closing the parent can delete the QObject between check and emit.
                    pass

        api = None
        try:
            api = _installer_api()
            installer = VoicePackageInstaller(
                progress_callback=lambda *args: emit("_progress_signal", *args),
                info_callback=lambda message: emit("_info_signal", message),
            )
            self._installer = installer

            def run_install():
                resolved_archive_bytes = archive_bytes
                if resolved_archive_bytes is None:
                    emit("_info_signal", "正在读取当前语音包的远端大小")
                    try:
                        remote_size = fetch_voice_package_size(profile_key)
                    except Exception:
                        remote_size = None
                    if remote_size is not None and remote_size.archive_bytes > 0:
                        resolved_archive_bytes = remote_size.archive_bytes
                emit("_info_signal", "安装任务已启动，正在检查磁盘空间")
                return installer.install(
                    Path(drive_text),
                    profile=profile_key,
                    archive_bytes=resolved_archive_bytes,
                    before_activate=_gsvmove_service().prepare_voice_package_install,
                )

            def on_done(future) -> None:
                try:
                    result = future.result()
                except (CancelledError, api.VoicePackageCancelled):
                    emit("_finished_signal", "cancelled", None)
                except Exception as exc:
                    emit("_finished_signal", "failed", api.voice_package_error_message(exc))
                else:
                    emit("_finished_signal", "success", result)

            future = get_compute_hub().submit_interactive_io(run_install)
            self._install_future = future
            future.add_done_callback(on_done)
        except Exception as exc:
            # Defer even immediate constructor/submission failures until the
            # current button event returns; never enter a nested dialog loop.
            detail = (
                api.voice_package_error_message(exc, stage="安装任务启动") if api is not None
                else "安装任务启动失败：安装服务暂不可用"
            )
            emit("_finished_signal", "failed", detail)

    def _on_secondary(self) -> None:
        if not self._busy:
            self.hide_dialog()
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._secondary.setEnabled(False)
        self._status.setText("正在取消安装并清理临时文件")
        self._cancel_install()

    def _cancel_install(self) -> None:
        installer = self._installer
        future = self._install_future
        if installer is not None:
            # Set the worker's cooperative flag now; process/session cleanup
            # may wait on OS I/O and must not run inside a Qt event handler.
            installer.request_cancel()
        if future is not None and future.cancel():
            return
        if installer is not None and (future is None or not future.done()):
            try:
                get_compute_hub().submit_io(installer.cancel)
            except RuntimeError:
                # During shutdown only the cooperative request may be possible.
                pass

    def _accept_install_update(self, generation: int) -> bool:
        return not self._cleaned_up and self._busy and generation == self._install_generation

    def _on_progress(self, generation, phase, current, total, message) -> None:
        if self._accept_install_update(generation) and not self._cancel_requested:
            self._apply_progress(phase, current, total, message)

    def _on_info(self, generation: int, message: str) -> None:
        if self._accept_install_update(generation) and not self._cancel_requested:
            self._status.setText(message)

    def _on_install_finished(self, generation: int, outcome: str, result: object) -> None:
        if not self._accept_install_update(generation):
            return
        if outcome == "success":  # Activation can finish just before cancellation.
            self._on_install_success(result)
        elif outcome == "cancelled" or self._cancel_requested:
            self._on_install_cancelled()
        else:
            self._on_install_error(result)

    def _move_install_to_background(self) -> None:
        """Hide the progress window while preserving its active installer."""
        if not self._busy:
            return
        self._backgrounded = True
        self._background.setEnabled(False)
        self._notify_background_stage(
            "backgrounded",
            "语音包正在后台安装，关键进度会提醒你。",
        )
        self._drive_combo.hidePopup()
        self._profile_combo.hidePopup()
        self._shell.hide_dialog(immediate=True)

    def _notify_background_stage(self, stage: str, text: str) -> None:
        if not self._backgrounded or stage in self._background_notified_stages:
            return
        self._background_notified_stages.add(stage)
        _product().get_event_center().publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 10,
            "max": 140,
            "particle": False,
        }))

    def _notify_background_progress(self, phase: str, message: str) -> None:
        if phase != "extract":
            return
        if message == "正在解压角色模型与公共模型":
            self._notify_background_stage(
                "extracting",
                "语音包下载完成，正在解压与校验。",
            )
        elif message == "正在准备激活新语音包":
            self._notify_background_stage(
                "activating",
                "语音包校验完成，正在激活。",
            )

    def _apply_progress(self, phase: str, current: int, total: int, message: str) -> None:
        if message:
            self._status.setText(message)
            self._notify_background_progress(phase, message)
        bar = self._download_bar if phase == "download" else self._extract_bar
        if total <= 0:
            bar.setRange(0, 0)
            bar.setFormat("处理中")
            return
        value = max(0, min(1000, int(round((current / total) * 1000))))
        if phase != "download" and bar.maximum() > 0:
            value = max(bar.value(), value)
        percent = value / 10
        bar.setRange(0, 1000)
        bar.setValue(value)
        bar.setFormat(f"{percent:.1f}%")

    def _on_install_success(self, result: object) -> None:
        self._busy = False
        self._completed = True
        self._sync_minimize_button()
        self._installer = None
        self._install_future = None
        warnings = getattr(result, "warnings", ())
        self._status.setText(
            "语音包安装完成；旧版运行时有清理项目未完成" if warnings else "语音包安装完成"
        )
        self._download_bar.setRange(0, 1000)
        self._download_bar.setValue(1000)
        self._download_bar.setFormat("完成")
        self._extract_bar.setRange(0, 1000)
        self._extract_bar.setValue(1000)
        self._extract_bar.setFormat("完成")
        self._secondary.hide()
        self._background.hide()
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._primary.show()
        self._notify_background_stage("completed", "ONNX 语音包安装完成。")
        self.install_succeeded.emit(result)

    def _restore_after_incomplete_install(self, progress_text: str) -> None:
        self._busy = False
        self._sync_minimize_button()
        self._installer = None
        self._install_future = None
        for bar in (self._download_bar, self._extract_bar):
            if bar.maximum() == 0:
                bar.setRange(0, 1000)
                bar.setValue(0)
            bar.setFormat(progress_text)
        self._secondary.setText("关闭")
        self._secondary.setEnabled(True)
        self._background.hide()
        self._primary.setText("重新安装")
        self._primary.setEnabled(True)
        self._primary.show()
        self._profile_combo.setEnabled(True)
        self._drive_combo.setEnabled(True)

    def _on_install_error(self, message: str) -> None:
        self._restore_after_incomplete_install("安装失败")
        self._status.setText(message)
        self._report_install_failure(message)

    def _report_install_failure(self, message: str) -> None:
        # Only backend-authored stage text and safe codes reach this sink.
        # Don't attach exc_info: error-log records can also be exposed in the UI.
        logger.warning("ONNX 语音包安装失败：%s", message)
        if self._cleaned_up or QCoreApplication.closingDown():
            return
        self._close_failure_notice()
        try:
            self._failure_notice = post_message(
                self if self.isVisible() and not self._backgrounded else None,
                title="语音包安装失败",
                text=message,
                informative_text="请确认磁盘空间和网络正常后，从语音包安装入口重试。",
                icon=ICON_WARNING,
                layer_name="VoicePackageInstallFailure",
            )
        except Exception:
            # The settings window can be closing while a worker completes.
            logger.warning("无法显示语音包安装失败提示")

    def _on_install_cancelled(self) -> None:
        self._restore_after_incomplete_install("已取消")
        self._status.setText("安装已取消，临时文件已清理")

    def hide_dialog(self) -> None:
        if self._busy:
            return
        self._drive_combo.hidePopup()
        self._profile_combo.hidePopup()
        self._shell.hide_dialog()

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._disconnect_app_exit()
        self._cancel_install()
        self._busy = False
        self._installer = None
        self._install_future = None
        if self._remote_size_future is not None:
            self._remote_size_future.cancel()
            self._remote_size_future = None
        self._close_failure_notice()
        self._drive_combo.hidePopup()
        self._profile_combo.hidePopup()
        self._shell.cleanup()

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            return
        self._drive_combo.hidePopup()
        self._profile_combo.hidePopup()
        super().closeEvent(event)


__all__ = ["VoicePackageManagementBar", "VoicePackageInstallerDialog"]
