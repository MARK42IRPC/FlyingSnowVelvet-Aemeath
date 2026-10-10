"""语音包安装器的共享尺寸 / 配色助手、驱动下拉与安装横幅。

批次 3 后续轮次从 `voice_package_installer` 切出。该模块原先一个文件同时承载「共享底座」
（尺寸常量、`_color` / `_format_bytes` 助手、`_VoiceDriveComboBox`）与「安装器浮窗本体」；
本轮先把**被多处共用、且不依赖浮窗状态**的第一组搬出来：安装器浮窗与横幅、管理条都要用
同一套尺寸与配色助手，驱动下拉是同一种「原生弹层要盖住置顶浮窗」的下拉。

切分保持逐行等价：搬出的类与函数体与搬出前一致（缩进也未变）。`voice_package_installer`
按原名重新导出 `_WIDTH` / `_HEIGHT` / `_LAYER` / `_BORDER`、`_color` / `_format_bytes`、
`_VoiceDriveComboBox` 与 `VoicePackageInstallBanner`，既有导入面（含 `tests/
test_voice_package_installer_ui.py` 与 `ai_settings_page`）不变。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from config.scale import scale_px
from lib.core.render.layers import get_layer_manager, WindowLayer
from lib.script.gsvmove.package_manager import VoicePackageStatus
from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.script.workbench.theme import get_workbench_colors

_WIDTH = scale_px(470, min_abs=420)
_HEIGHT = scale_px(410, min_abs=372)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


def _color(name: str) -> str:
    """Resolve a workbench design token as a QSS colour string."""
    return getattr(get_workbench_colors(), name)


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
            WindowLayer.DIALOG,
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

    def wheelEvent(self, event) -> None:
        # 页面滚动时不改变安装档位或磁盘选择。
        event.ignore()


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
                background: {_color('surface_raised')};
                border: 2px solid {_color('pink')};
                border-radius: 4px;
            }}
            QFrame#VoicePackageBannerAccent {{
                background: {_color('cyan')};
                border: none;
                border-radius: 1px;
            }}
            QLabel#VoicePackageBannerTitle {{
                color: {_color('text')};
                background: transparent;
                border: none;
            }}
            QLabel#VoicePackageBannerDetail {{
                color: {_color('text_dim')};
                background: transparent;
                border: none;
            }}
            QPushButton#VoicePackageInstallButton {{
                min-height: {scale_px(34, min_abs=30)}px;
                padding: 0px {scale_px(10, min_abs=8)}px;
                color: {_color('text')};
                background: {_color('pink')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
                font-weight: 700;
            }}
            QPushButton#VoicePackageInstallButton:hover {{
                background: {_color('surface_hover')};
                color: {_color('text')};
            }}
            """
        )


__all__ = [
    "VoicePackageInstallBanner",
    "_BORDER",
    "_HEIGHT",
    "_LAYER",
    "_WIDTH",
    "_VoiceDriveComboBox",
    "_color",
    "_format_bytes",
]
