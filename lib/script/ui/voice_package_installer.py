"""Compact ONNX voice-package banner and installer dialog."""

from __future__ import annotations

import shutil
from pathlib import Path

from PyQt5.QtCore import QEasingCurve, Qt, QPropertyAnimation, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.config import UI, UI_THEME
from config.font_config import get_ui_font
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.gsvmove.package_manager import (
    VoiceInstallResult,
    VoicePackageCancelled,
    VoicePackageInstaller,
    VOICE_PACKAGE_PROFILES,
    VoicePackageStatus,
    get_voice_package_profile,
    get_voice_package_status,
    list_fixed_drive_roots,
)


_WIDTH = scale_px(470, min_abs=420)
_HEIGHT = scale_px(410, min_abs=372)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


def _color(name: str) -> str:
    return UI_THEME[name].name()


def _format_bytes(value: int) -> str:
    size = max(0, int(value))
    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.1f} GiB"
    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.0f} MiB"
    return f"{size / 1024:.0f} KiB"


class _VoiceDriveComboBox(QComboBox):
    """Drive selector whose native popup stays above the topmost installer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._popup_window_instance = None

    def _popup_window(self):
        if self._popup_window_instance is not None:
            return self._popup_window_instance
        view = self.view()
        return view.window() if view is not None else None

    def showPopup(self) -> None:
        if self.count() <= 0:
            return
        super().showPopup()
        popup = self._popup_window()
        if popup is None:
            return
        self._popup_window_instance = popup
        popup.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        popup.show()
        layer_manager = get_layer_manager()
        layer_manager.register(
            popup,
            Layer.DIALOG,
            z=1,
            name="VoicePackageDriveDropdown",
        )
        layer_manager.enforce_burst()
        popup.raise_()
        popup.activateWindow()

    def hidePopup(self) -> None:
        popup = self._popup_window()
        if popup is not None:
            get_layer_manager().unregister(popup)
        super().hidePopup()


class VoicePackageInstallBanner(QFrame):
    install_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VoicePackageInstallBanner")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        accent = QFrame(self)
        accent.setObjectName("VoicePackageBannerAccent")
        accent.setFixedWidth(scale_px(5, min_abs=4))

        self._title = QLabel("安装最新语音包", self)
        self._title.setObjectName("VoicePackageBannerTitle")
        title_font = get_ui_font(size=scale_px(13, min_abs=11))
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._detail = QLabel("尚未安装爱弥斯 ONNX 语音包", self)
        self._detail.setObjectName("VoicePackageBannerDetail")
        self._detail.setWordWrap(True)
        self._detail.setFont(get_ui_font(size=scale_px(10, min_abs=9)))

        copy_layout = QVBoxLayout()
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(scale_px(3, min_abs=2))
        copy_layout.addWidget(self._title)
        copy_layout.addWidget(self._detail)

        self.install_button = QPushButton("安装最新语音包", self)
        self.install_button.setObjectName("VoicePackageInstallButton")
        self.install_button.setFixedWidth(scale_px(142, min_abs=126))
        self.install_button.clicked.connect(self.install_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            scale_px(10, min_abs=8),
            scale_px(11, min_abs=9),
            scale_px(12, min_abs=10),
            scale_px(11, min_abs=9),
        )
        layout.setSpacing(scale_px(11, min_abs=9))
        layout.addWidget(accent)
        layout.addLayout(copy_layout, 1)
        layout.addWidget(self.install_button, 0, Qt.AlignVCenter)
        self._apply_style()

    def set_package_status(self, status: VoicePackageStatus) -> None:
        detail = {
            "legacy": "检测到旧版 GSVmove，更新后将自动清理旧运行时",
            "invalid": "本地语音包不完整，需要重新安装",
            "missing": "尚未安装爱弥斯 ONNX 语音包",
        }.get(status.kind, status.reason)
        self._detail.setText(detail)
        self.setVisible(status.install_required)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#VoicePackageInstallBanner {{
                background: {_color('mid')};
                border: 2px solid {_color('deep_pink')};
                border-radius: 4px;
            }}
            QFrame#VoicePackageBannerAccent {{
                background: {_color('deep_cyan')};
                border: none;
                border-radius: 1px;
            }}
            QLabel#VoicePackageBannerTitle {{
                color: {_color('text')};
                background: transparent;
                border: none;
            }}
            QLabel#VoicePackageBannerDetail {{
                color: {_color('deep_blue')};
                background: transparent;
                border: none;
            }}
            QPushButton#VoicePackageInstallButton {{
                min-height: {scale_px(34, min_abs=30)}px;
                padding: 0px {scale_px(10, min_abs=8)}px;
                color: {_color('text')};
                background: {_color('deep_pink')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
                font-weight: 700;
            }}
            QPushButton#VoicePackageInstallButton:hover {{
                background: {_color('highlight')};
                color: {_color('text')};
            }}
            """
        )


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
        box = QMessageBox(self)
        box.setWindowTitle("删除语音包")
        box.setIcon(QMessageBox.Warning)
        box.setText("确认删除爱弥斯 ONNX 语音包？")
        box.setInformativeText(
            "删除后语音合成将暂停，可随时重新安装。\n"
            f"位置：{package_root}"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        yes_button = box.button(QMessageBox.Yes)
        cancel_button = box.button(QMessageBox.Cancel)
        if yes_button is not None:
            yes_button.setText("确认删除")
            yes_button.setObjectName("VoicePackageConfirmRemove")
        if cancel_button is not None:
            cancel_button.setText("取消")
            box.setEscapeButton(cancel_button)
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        box.setStyleSheet(
            f"""
            QMessageBox {{ background: {_color('bg')}; }}
            QMessageBox QLabel {{
                min-width: {scale_px(330, min_abs=300)}px;
                color: {_color('text')};
                background: transparent;
            }}
            QMessageBox QPushButton {{
                min-width: {scale_px(92, min_abs=80)}px;
                min-height: {scale_px(30, min_abs=27)}px;
                color: {_color('text')};
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
            }}
            QMessageBox QPushButton:hover {{ background: {_color('highlight')}; }}
            QMessageBox QPushButton#VoicePackageConfirmRemove {{
                color: {_color('text')};
                background: {_color('deep_pink')};
                font-weight: 700;
            }}
            """
        )
        layer_manager = get_layer_manager()
        layer_manager.register(box, Layer.DIALOG, z=1, name="VoicePackageRemovalConfirmation")
        try:
            return box.exec_() == QMessageBox.Yes
        finally:
            layer_manager.unregister(box)
            box.deleteLater()

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
                from lib.script.gsvmove import remove_voice_package

                removed = remove_voice_package(package_root)
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
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
            }}
            QLabel#VoicePackageManagementDetail {{
                color: {_color('deep_blue')};
                background: transparent;
                border: none;
            }}
            QPushButton#VoicePackageRemoveButton {{
                min-height: {scale_px(30, min_abs=27)}px;
                padding: 0px {scale_px(9, min_abs=7)}px;
                color: {_color('text')};
                background: {_color('bg')};
                border: 1px solid {_color('deep_pink')};
                border-radius: 3px;
                font-weight: 600;
            }}
            QPushButton#VoicePackageRemoveButton:hover {{
                color: {_color('text')};
                background: {_color('highlight')};
            }}
            QPushButton#VoicePackageRemoveButton:disabled {{
                color: {_color('deep_blue')};
                border-color: {_color('border')};
            }}
            """
        )


class VoicePackageInstallerDialog(QWidget):
    install_succeeded = pyqtSignal(object)
    _progress_signal = pyqtSignal(str, int, int, str)
    _info_signal = pyqtSignal(str)
    _success_signal = pyqtSignal(object)
    _error_signal = pyqtSignal(str)
    _cancelled_signal = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_WIDTH, _HEIGHT)
        get_layer_manager().register(self, Layer.DIALOG, name="VoicePackageInstallerDialog")
        self._visible = False
        self._busy = False
        self._completed = False
        self._backgrounded = False
        self._background_notified_stages: set[str] = set()
        self._installer: VoicePackageInstaller | None = None
        self._install_future = None

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._animation = QPropertyAnimation(self._opacity, b"opacity", self)
        self._animation.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._animation.finished.connect(self._on_animation_finished)

        self._title = QLabel("安装最新语音包", self)
        title_font = get_ui_font(size=scale_px(16, min_abs=13))
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._status = QLabel("选择安装磁盘", self)
        self._status.setFont(get_ui_font(size=scale_px(11, min_abs=10)))
        self._status.setWordWrap(True)

        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.setSpacing(scale_px(9, min_abs=7))
        profile_label = QLabel("语音包档位", self)
        self._profile_combo = _VoiceDriveComboBox(self)
        self._profile_combo.setObjectName("VoicePackageProfileCombo")
        self._profile_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._profile_combo.setMaxVisibleItems(3)
        self._profile_combo.currentIndexChanged.connect(self._update_drive_detail)
        profile_row.addWidget(profile_label)
        profile_row.addWidget(self._profile_combo, 1)

        drive_row = QHBoxLayout()
        drive_row.setContentsMargins(0, 0, 0, 0)
        drive_row.setSpacing(scale_px(9, min_abs=7))
        drive_label = QLabel("安装磁盘", self)
        self._drive_combo = _VoiceDriveComboBox(self)
        self._drive_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._drive_combo.setMaxVisibleItems(8)
        self._drive_combo.currentIndexChanged.connect(self._update_drive_detail)
        drive_row.addWidget(drive_label)
        drive_row.addWidget(self._drive_combo, 1)
        self._drive_detail = QLabel(self)
        self._drive_detail.setWordWrap(True)

        self._download_label = QLabel("下载进度", self)
        self._download_bar = QProgressBar(self)
        self._download_bar.setObjectName("VoiceDownloadProgress")
        self._extract_label = QLabel("解压、校验与安装进度", self)
        self._extract_bar = QProgressBar(self)
        self._extract_bar.setObjectName("VoiceExtractProgress")
        for bar in (self._download_bar, self._extract_bar):
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setFormat("等待开始")
            bar.setTextVisible(True)
            bar.setFixedHeight(scale_px(24, min_abs=21))

        self._secondary = QPushButton("取消", self)
        self._secondary.clicked.connect(self._on_secondary)
        self._background = QPushButton("后台安装", self)
        self._background.setObjectName("VoiceInstallerBackground")
        self._background.clicked.connect(self._move_install_to_background)
        self._background.hide()
        self._primary = QPushButton("开始安装", self)
        self._primary.setObjectName("VoiceInstallerPrimary")
        self._primary.clicked.connect(self._on_primary)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(scale_px(8, min_abs=6))
        action_row.addWidget(self._background)
        action_row.addStretch(1)
        action_row.addWidget(self._secondary)
        action_row.addWidget(self._primary)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _BORDER + scale_px(18, min_abs=15),
            _BORDER + scale_px(16, min_abs=13),
            _BORDER + scale_px(18, min_abs=15),
            _BORDER + scale_px(16, min_abs=13),
        )
        layout.setSpacing(scale_px(8, min_abs=6))
        layout.addWidget(self._title)
        layout.addWidget(self._status)
        layout.addSpacing(scale_px(3, min_abs=2))
        layout.addLayout(profile_row)
        layout.addLayout(drive_row)
        layout.addWidget(self._drive_detail)
        layout.addSpacing(scale_px(4, min_abs=3))
        layout.addWidget(self._download_label)
        layout.addWidget(self._download_bar)
        layout.addWidget(self._extract_label)
        layout.addWidget(self._extract_bar)
        layout.addStretch(1)
        layout.addLayout(action_row)

        # Installation runs on ComputeHub's worker; never mutate Qt widgets from
        # that thread, even if the signal sender remains affiliated with this dialog.
        queued = Qt.QueuedConnection
        self._progress_signal.connect(self._apply_progress, queued)
        self._info_signal.connect(self._status.setText, queued)
        self._success_signal.connect(self._on_install_success, queued)
        self._error_signal.connect(self._on_install_error, queued)
        self._cancelled_signal.connect(self._on_install_cancelled, queued)
        self._apply_style()

    def show_dialog(self) -> None:
        if not self._busy:
            self._reset()
        else:
            self._backgrounded = False
            self._background.setEnabled(True)
        self._center_on_screen()
        self._visible = True
        self.show()
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._animate(1.0)

    def is_busy(self) -> bool:
        return self._busy

    def _reset(self) -> None:
        self._completed = False
        self._backgrounded = False
        self._background_notified_stages.clear()
        self._status.setText("选择安装磁盘")
        self._download_bar.setValue(0)
        self._download_bar.setFormat("等待开始")
        self._extract_bar.setValue(0)
        self._extract_bar.setFormat("等待开始")
        self._secondary.setText("取消")
        self._secondary.setEnabled(True)
        self._secondary.show()
        self._background.setEnabled(True)
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
        for profile in VOICE_PACKAGE_PROFILES.values():
            recommendation = " · 强烈推荐" if profile.key == "int8" else ""
            self._profile_combo.addItem(
                f"{profile.title}{recommendation} · {_format_bytes(profile.archive_bytes)}",
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

    def _update_drive_detail(self, *_args) -> None:
        path_text = str(self._drive_combo.currentData() or "")
        if not path_text:
            self._drive_detail.setText("未检测到可写入的固定磁盘")
            return
        target = Path(path_text) / "AemeathDeskPet" / "voice"
        profile = get_voice_package_profile(self._profile_combo.currentData())
        self._drive_detail.setText(
            f"{profile.title}：安装过程需要 {_format_bytes(profile.required_free_bytes)}，"
            f"下载包大小为 {_format_bytes(profile.archive_bytes)}，"
            f"安装后占用硬盘空间 {_format_bytes(profile.extracted_bytes)}\n"
            f"安装到 {target}"
        )

    def _on_primary(self) -> None:
        if self._busy:
            return
        if self._completed:
            self.hide_dialog()
            return
        drive_text = str(self._drive_combo.currentData() or "")
        profile_key = str(self._profile_combo.currentData() or "")
        if not drive_text:
            self._status.setText("没有可用的安装磁盘")
            return

        self._busy = True
        self._status.setText("正在准备下载")
        self._download_bar.setRange(0, 0)
        self._download_bar.setFormat("准备中")
        self._primary.setEnabled(False)
        self._primary.hide()
        self._secondary.setText("取消安装")
        self._background.show()
        self._profile_combo.setEnabled(False)
        self._drive_combo.setEnabled(False)
        installer = VoicePackageInstaller(
            progress_callback=self._progress_signal.emit,
            info_callback=self._info_signal.emit,
        )
        self._installer = installer

        def run_install() -> None:
            try:
                self._info_signal.emit("安装任务已启动，正在检查磁盘空间")
                from lib.script.gsvmove import get_gsvmove_service

                result = installer.install(
                    Path(drive_text),
                    profile=profile_key,
                    before_activate=get_gsvmove_service().prepare_voice_package_install,
                )
                self._success_signal.emit(result)
            except VoicePackageCancelled:
                self._cancelled_signal.emit()
            except Exception as exc:
                self._error_signal.emit(str(exc))

        try:
            self._install_future = get_compute_hub().submit_interactive_io(run_install)
        except Exception as exc:
            self._on_install_error(f"安装任务启动失败：{exc}")

    def _on_secondary(self) -> None:
        if self._busy:
            self._secondary.setEnabled(False)
            self._status.setText("正在取消安装并清理临时文件")
            if self._installer is not None:
                self._installer.cancel()
            return
        self.hide_dialog()

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
        self._visible = False
        self._animation.stop()
        self._opacity.setOpacity(0.0)
        self.hide()

    def _notify_background_stage(self, stage: str, text: str) -> None:
        if not self._backgrounded or stage in self._background_notified_stages:
            return
        self._background_notified_stages.add(stage)
        get_event_center().publish(Event(EventType.INFORMATION, {
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
        self._installer = None
        self._install_future = None
        install_result = result if isinstance(result, VoiceInstallResult) else None
        warnings = install_result.warnings if install_result is not None else ()
        self._status.setText(
            "语音包安装完成" if not warnings else "语音包安装完成；" + "；".join(warnings)
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
        try:
            from lib.script.gsvmove import get_gsvmove_service

            get_gsvmove_service().reload_voice_package()
        except Exception:
            pass
        self._notify_background_stage("completed", "ONNX 语音包安装完成，已启用。")
        self.install_succeeded.emit(result)

    def _on_install_error(self, message: str) -> None:
        self._busy = False
        self._installer = None
        self._install_future = None
        self._status.setText(str(message or "语音包安装失败"))
        for bar in (self._download_bar, self._extract_bar):
            if bar.minimum() == 0 and bar.maximum() == 0:
                bar.setRange(0, 1000)
                bar.setValue(0)
            bar.setFormat("安装失败")
        self._secondary.setText("关闭")
        self._secondary.setEnabled(True)
        self._background.hide()
        self._primary.setText("重新安装")
        self._primary.setEnabled(True)
        self._primary.show()
        self._profile_combo.setEnabled(True)
        self._drive_combo.setEnabled(True)
        self._notify_background_stage(
            "failed",
            f"ONNX 语音包安装失败：{self._status.text()}",
        )

    def _on_install_cancelled(self) -> None:
        self._busy = False
        self._installer = None
        self._install_future = None
        self._status.setText("安装已取消，临时文件已清理")
        for bar in (self._download_bar, self._extract_bar):
            if bar.minimum() == 0 and bar.maximum() == 0:
                bar.setRange(0, 1000)
                bar.setValue(0)
            bar.setFormat("已取消")
        self._secondary.setText("关闭")
        self._secondary.setEnabled(True)
        self._background.hide()
        self._primary.setText("重新安装")
        self._primary.setEnabled(True)
        self._primary.show()
        self._profile_combo.setEnabled(True)
        self._drive_combo.setEnabled(True)
        self._notify_background_stage("cancelled", "ONNX 语音包安装已取消。")

    def hide_dialog(self) -> None:
        if self._busy:
            return
        self._visible = False
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._opacity.opacity())
        self._animation.setEndValue(apply_ui_opacity(target))
        self._animation.start()

    def _on_animation_finished(self) -> None:
        if not self._visible:
            self.hide()

    def _center_on_screen(self) -> None:
        cursor = QCursor.pos()
        screen = get_screen_geometry_for_point(point=cursor, fallback_widget=self)
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + (screen.height() - self.height()) // 2
        x, y, _ = clamp_rect_position(
            x, y, self.width(), self.height(), point=cursor, fallback_widget=self
        )
        self.move(x, y)

    def cleanup(self) -> None:
        if self._installer is not None:
            self._installer.cancel()
            self._installer = None
        self._drive_combo.hidePopup()
        self._profile_combo.hidePopup()
        try:
            get_layer_manager().unregister(self)
        except Exception:
            pass
        self.hide()
        self.deleteLater()

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            return
        self._visible = False
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), UI_THEME["border"])
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), UI_THEME["mid"])
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), UI_THEME["bg"])

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QLabel {{ color: {_color('text')}; background: transparent; }}
            QComboBox {{
                min-height: {scale_px(32, min_abs=28)}px;
                padding: 0px {scale_px(42, min_abs=36)}px 0px {scale_px(9, min_abs=7)}px;
                color: {_color('text')};
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
            }}
            QComboBox:focus {{ border: 2px solid {_color('deep_blue')}; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {scale_px(34, min_abs=30)}px;
                background: {_color('deep_cyan')};
                border: none;
                border-left: 1px solid {_color('border')};
            }}
            QComboBox::drop-down:hover {{ background: {_color('highlight')}; }}
            QComboBox::down-arrow {{
                image: url(resc/ui/combo_down_arrow.svg);
                width: {scale_px(12, min_abs=10)}px;
                height: {scale_px(8, min_abs=6)}px;
            }}
            QProgressBar {{
                color: {_color('text')};
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
                text-align: center;
            }}
            QProgressBar#VoiceDownloadProgress::chunk {{ background: {_color('deep_cyan')}; }}
            QProgressBar#VoiceExtractProgress::chunk {{ background: {_color('deep_pink')}; }}
            QPushButton {{
                min-width: {scale_px(94, min_abs=82)}px;
                min-height: {scale_px(32, min_abs=28)}px;
                color: {_color('text')};
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
                padding: 0px {scale_px(10, min_abs=8)}px;
            }}
            QPushButton:hover {{ background: {_color('highlight')}; }}
            QPushButton#VoiceInstallerBackground {{
                color: {_color('text')};
                background: {_color('deep_cyan')};
                font-weight: 700;
            }}
            QPushButton#VoiceInstallerBackground:hover {{
                color: {_color('text')};
                background: {_color('highlight')};
            }}
            QPushButton#VoiceInstallerPrimary {{
                color: {_color('text')};
                background: {_color('deep_pink')};
                font-weight: 700;
            }}
            QPushButton#VoiceInstallerPrimary:hover {{
                color: {_color('text')};
                background: {_color('highlight')};
            }}
            QPushButton:disabled {{ color: {_color('deep_blue')}; }}
            """
        )
        dropdown_style = f"""
            QAbstractItemView {{
                color: {_color('text')};
                background: {_color('bg')};
                border: 2px solid {_color('border')};
                border-radius: 0px;
                outline: none;
                selection-color: {_color('text')};
                selection-background-color: {_color('mid')};
            }}
            QAbstractItemView::item {{
                min-height: {scale_px(32, min_abs=28)}px;
                padding: 0px {scale_px(9, min_abs=7)}px;
                color: {_color('text')};
                background: {_color('bg')};
                border: none;
            }}
            QAbstractItemView::item:hover,
            QAbstractItemView::item:selected {{
                color: {_color('text')};
                background: {_color('mid')};
            }}
            QScrollBar:vertical {{
                width: {scale_px(10, min_abs=8)}px;
                background: {_color('mid')};
                border: none;
            }}
            QScrollBar::handle:vertical {{
                min-height: {scale_px(22, min_abs=18)}px;
                background: {_color('deep_pink')};
                border: none;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
            """
        self._drive_combo.view().setStyleSheet(dropdown_style)
        self._profile_combo.view().setStyleSheet(dropdown_style)
