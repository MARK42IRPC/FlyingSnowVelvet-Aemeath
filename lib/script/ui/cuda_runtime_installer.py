"""NVIDIA CUDA voice-runtime installer dialog."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QEasingCurve, QPoint, Qt, QPropertyAnimation, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.cuda_runtime_installer import (
    CudaRuntimeInstallCancelled,
    CudaRuntimeInstallResult,
)
from lib.core.qt_bridge.colors import UI_THEME
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.screen import clamp_rect_position, get_screen_geometry_for_point
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.voice_runtime_contract import (
    CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES,
    CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES,
    CUDA_RUNTIME_BUNDLE_STAGING_OVERHEAD_BYTES,
)
from lib.script.gsvmove.cuda_runtime import create_cuda_runtime_installer


_WIDTH = scale_px(470, min_abs=420)
_HEIGHT = scale_px(420, min_abs=382)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


def _color(name: str) -> str:
    return UI_THEME[name].name()


def _format_bytes(value: int) -> str:
    size = max(0, int(value))
    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.2f} GiB"
    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.0f} MiB"
    return f"{size / 1024:.0f} KiB"


class CudaRuntimeInstallerDialog(QWidget):
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
        get_layer_manager().register(self, Layer.DIALOG, name="CudaRuntimeInstallerDialog")
        self._visible = False
        self._busy = False
        self._completed = False
        self._cleaned_up = False
        self._installer = None
        self._install_future = None
        self._dragging = False
        self._drag_offset = QPoint()

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._animation = QPropertyAnimation(self._opacity, b"opacity", self)
        self._animation.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._animation.finished.connect(self._on_animation_finished)

        self._title = QLabel("安装N卡推理环境", self)
        self._title.installEventFilter(self)
        self._title.setCursor(Qt.OpenHandCursor)
        title_font = get_ui_font(size=scale_px(16, min_abs=13))
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._effect = QLabel(
            "启用后，ONNX 语音的语义解码与声学模型会优先使用 NVIDIA 显卡，"
            "可明显缩短合成等待；会增加显存占用。安装时将自动清理旧 CUDA 环境。",
            self,
        )
        self._effect.setWordWrap(True)
        self._effect.setFont(get_ui_font(size=scale_px(10, min_abs=9)))

        required = (
            CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES
            + CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES
            + CUDA_RUNTIME_BUNDLE_STAGING_OVERHEAD_BYTES
        )
        self._size_detail = QLabel(
            f"下载大小  {_format_bytes(CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES)}\n"
            f"安装占用  {_format_bytes(CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES)}\n"
            f"安装过程需约 {_format_bytes(required)} 可用空间",
            self,
        )
        self._size_detail.setFont(get_ui_font(size=scale_px(10, min_abs=9)))

        self._status = QLabel("准备安装精简 NVIDIA CUDA 推理环境", self)
        self._status.setWordWrap(True)
        self._status.setFont(get_ui_font(size=scale_px(11, min_abs=10)))

        download_label = QLabel("下载进度", self)
        download_label.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        self._download_bar = QProgressBar(self)
        self._download_bar.setObjectName("CudaRuntimeDownloadProgress")
        self._download_bar.setFixedHeight(scale_px(22, min_abs=20))

        install_label = QLabel("解压、校验与安装进度", self)
        install_label.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        self._install_bar = QProgressBar(self)
        self._install_bar.setObjectName("CudaRuntimeInstallProgress")
        self._install_bar.setFixedHeight(scale_px(22, min_abs=20))

        self._cancel = QPushButton("取消", self)
        self._cancel.clicked.connect(self._on_cancel)
        self._primary = QPushButton("开始安装", self)
        self._primary.setObjectName("CudaRuntimeInstallerPrimary")
        self._primary.clicked.connect(self._on_primary)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(scale_px(9, min_abs=7))
        button_row.addStretch(1)
        button_row.addWidget(self._cancel)
        button_row.addWidget(self._primary)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            scale_px(20, min_abs=17),
            scale_px(16, min_abs=14),
            scale_px(20, min_abs=17),
            scale_px(16, min_abs=14),
        )
        layout.setSpacing(scale_px(8, min_abs=6))
        layout.addWidget(self._title)
        layout.addWidget(self._effect)
        layout.addWidget(self._size_detail)
        layout.addWidget(self._status)
        layout.addWidget(download_label)
        layout.addWidget(self._download_bar)
        layout.addWidget(install_label)
        layout.addWidget(self._install_bar)
        layout.addStretch(1)
        layout.addLayout(button_row)

        queued = Qt.QueuedConnection
        self._progress_signal.connect(self._apply_progress, queued)
        self._info_signal.connect(self._status.setText, queued)
        self._success_signal.connect(self._on_install_success, queued)
        self._error_signal.connect(self._on_install_error, queued)
        self._cancelled_signal.connect(self._on_install_cancelled, queued)
        self._apply_style()
        self._reset()

    def is_busy(self) -> bool:
        return self._busy

    def show_dialog(self) -> None:
        if not self._busy:
            self._reset()
        self._center_on_screen()
        self._visible = True
        self.show()
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._animate(1.0)

    def _reset(self) -> None:
        self._completed = False
        self._status.setText("准备安装精简 NVIDIA CUDA 推理环境")
        for bar in (self._download_bar, self._install_bar):
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setFormat("0.0%")
        self._cancel.setText("取消")
        self._cancel.setEnabled(True)
        self._cancel.show()
        self._primary.setText("开始安装")
        self._primary.setEnabled(True)
        self._primary.show()

    def _on_primary(self) -> None:
        if self._busy:
            return
        if self._completed:
            self.hide_dialog()
            return
        self._busy = True
        self._status.setText("正在检查 Python、磁盘空间与旧运行环境")
        self._download_bar.setRange(0, 0)
        self._download_bar.setFormat("准备中")
        self._install_bar.setRange(0, 0)
        self._install_bar.setFormat("准备中")
        self._primary.hide()
        self._cancel.setText("取消安装")

        try:
            installer = create_cuda_runtime_installer(
                progress_callback=self._progress_signal.emit,
                info_callback=self._info_signal.emit,
            )
        except Exception as exc:
            self._on_install_error(f"安装器初始化失败：{exc}")
            return
        self._installer = installer

        def run_install() -> None:
            try:
                self._success_signal.emit(installer.install())
            except CudaRuntimeInstallCancelled:
                self._cancelled_signal.emit()
            except Exception as exc:
                self._error_signal.emit(str(exc))

        try:
            self._install_future = get_compute_hub().submit_interactive_io(run_install)
        except Exception as exc:
            self._on_install_error(f"安装任务启动失败：{exc}")

    def _on_cancel(self) -> None:
        if not self._busy:
            self.hide_dialog()
            return
        self._cancel.setEnabled(False)
        self._status.setText("正在取消并清理临时文件")
        if self._installer is not None:
            self._installer.cancel()

    def _apply_progress(self, phase: str, current: int, total: int, message: str) -> None:
        if message:
            self._status.setText(message)
        bar = self._download_bar if phase == "download" else self._install_bar
        if total <= 0:
            bar.setRange(0, 0)
            bar.setFormat("处理中")
            return
        value = max(0, min(1000, int(round((current / total) * 1000))))
        if bar.maximum() > 0:
            value = max(bar.value(), value)
        bar.setRange(0, 1000)
        bar.setValue(value)
        bar.setFormat(f"{value / 10:.1f}%")

    def _on_install_success(self, result: object) -> None:
        self._busy = False
        self._completed = True
        self._installer = None
        self._install_future = None
        self._status.setText("N卡推理环境安装完成，设置页已开放N卡加速")
        for bar in (self._download_bar, self._install_bar):
            bar.setRange(0, 1000)
            bar.setValue(1000)
            bar.setFormat("完成")
        self._cancel.hide()
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._primary.show()
        self.install_succeeded.emit(
            result if isinstance(result, CudaRuntimeInstallResult) else result
        )

    def _on_install_error(self, message: str) -> None:
        self._busy = False
        self._installer = None
        self._install_future = None
        self._status.setText(str(message or "N卡推理环境安装失败"))
        for bar in (self._download_bar, self._install_bar):
            if bar.maximum() == 0:
                bar.setRange(0, 1000)
            bar.setFormat("安装失败")
        self._cancel.setText("关闭")
        self._cancel.setEnabled(True)
        self._cancel.show()
        self._primary.setText("重新安装")
        self._primary.setEnabled(True)
        self._primary.show()

    def _on_install_cancelled(self) -> None:
        self._busy = False
        self._installer = None
        self._install_future = None
        self._status.setText("安装已取消，临时文件已清理")
        for bar in (self._download_bar, self._install_bar):
            if bar.maximum() == 0:
                bar.setRange(0, 1000)
            bar.setFormat("已取消")
        self._cancel.setText("关闭")
        self._cancel.setEnabled(True)
        self._cancel.show()
        self._primary.setText("重新安装")
        self._primary.setEnabled(True)
        self._primary.show()

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
        self._cleaned_up = True
        if self._installer is not None:
            self._installer.cancel()
            self._installer = None
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
        get_layer_manager().unregister(self)
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._title:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                self._title.setCursor(Qt.ClosedHandCursor)
                return True
            if event.type() == QEvent.MouseMove and self._dragging and event.buttons() & Qt.LeftButton:
                self.move(event.globalPos() - self._drag_offset)
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._dragging = False
                self._title.setCursor(Qt.OpenHandCursor)
                return True
        return super().eventFilter(watched, event)

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
            QProgressBar {{
                color: {_color('text')};
                background: {_color('mid')};
                border: 1px solid {_color('border')};
                border-radius: 3px;
                text-align: center;
            }}
            QProgressBar#CudaRuntimeDownloadProgress::chunk {{ background: {_color('deep_cyan')}; }}
            QProgressBar#CudaRuntimeInstallProgress::chunk {{ background: {_color('deep_pink')}; }}
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
            QPushButton#CudaRuntimeInstallerPrimary {{
                background: {_color('deep_pink')};
                font-weight: 700;
            }}
            QPushButton:disabled {{ color: {_color('deep_blue')}; }}
            """
        )


__all__ = ["CudaRuntimeInstallerDialog"]
