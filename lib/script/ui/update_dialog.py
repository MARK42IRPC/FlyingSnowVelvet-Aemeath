"""桌宠更新/开发版同步小窗。"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from config.config import UI, UI_THEME
from config.font_config import get_ui_font
from config.scale import scale_px, scale_style_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.topmost_manager import get_topmost_manager
from lib.script.update_manager import (
    GitSyncCheckResult,
    GitSyncManager,
    GitSyncResult,
    ReleaseCheckResult,
    UpdateError,
    UpdateManager,
    UpdateResult,
)

_WIDTH = scale_px(360, min_abs=320)
_HEIGHT = scale_px(248, min_abs=220)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2

_C_BORDER = UI_THEME["border"]
_C_MID = UI_THEME["mid"]
_C_BG = UI_THEME["bg"]
_C_TEXT = UI_THEME["text"]


class DesktopPetUpdateDialog(QWidget):
    """承载分发包更新与开发版同步的独立小窗。"""

    _detail_signal = pyqtSignal(str)
    _progress_signal = pyqtSignal(int, int, str)
    _release_check_signal = pyqtSignal(object)
    _release_done_signal = pyqtSignal(object)
    _git_check_signal = pyqtSignal(object)
    _git_done_signal = pyqtSignal(object)
    _error_signal = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_WIDTH, _HEIGHT)
        get_topmost_manager().register(self)

        self._visible = False
        self._busy = False
        self._mode = ""
        self._release_check: ReleaseCheckResult | None = None
        self._git_check: GitSyncCheckResult | None = None
        self._primary_handler: Callable[[], None] | None = None
        self._secondary_handler: Callable[[], None] | None = None

        self._title_label = QLabel(self)
        self._title_label.setFont(self._build_title_font())
        self._title_label.setAlignment(Qt.AlignCenter)

        self._status_label = QLabel(self)
        self._status_label.setFont(get_ui_font(size=scale_px(13, min_abs=10)))
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setWordWrap(True)

        self._detail_label = QLabel(self)
        self._detail_label.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        self._detail_label.setAlignment(Qt.AlignCenter)
        self._detail_label.setWordWrap(True)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet(
            scale_style_px(
                "QProgressBar {"
                f"background: rgb({_C_BG.red()}, {_C_BG.green()}, {_C_BG.blue()});"
                f"border: 2px solid rgb({_C_BORDER.red()}, {_C_BORDER.green()}, {_C_BORDER.blue()});"
                f"color: rgb({_C_TEXT.red()}, {_C_TEXT.green()}, {_C_TEXT.blue()});"
                "text-align: center;"
                "font-weight: bold;"
                "min-height: 26px;"
                "}"
                "QProgressBar::chunk {"
                f"background: rgb({_C_MID.red()}, {_C_MID.green()}, {_C_MID.blue()});"
                "}"
            )
        )

        self._secondary_btn = QPushButton(self)
        self._secondary_btn.clicked.connect(self._on_secondary_clicked)
        self._secondary_btn.hide()

        self._primary_btn = QPushButton(self)
        self._primary_btn.clicked.connect(self._on_primary_clicked)
        self._primary_btn.hide()

        btn_style = scale_style_px(
            "QPushButton {"
            f"background: rgb({_C_BG.red()}, {_C_BG.green()}, {_C_BG.blue()});"
            f"border: 2px solid rgb({_C_BORDER.red()}, {_C_BORDER.green()}, {_C_BORDER.blue()});"
            f"color: rgb({_C_TEXT.red()}, {_C_TEXT.green()}, {_C_TEXT.blue()});"
            "font-weight: bold;"
            "padding: 4px 10px;"
            "min-height: 28px;"
            "}"
            "QPushButton:hover {"
            f"background: rgb({_C_MID.red()}, {_C_MID.green()}, {_C_MID.blue()});"
            "}"
            "QPushButton:pressed {"
            "background: rgb(255, 190, 205);"
            "}"
            "QPushButton:disabled {"
            "color: rgb(160, 160, 160);"
            "}"
        )
        self._secondary_btn.setStyleSheet(btn_style)
        self._primary_btn.setStyleSheet(btn_style)
        self._secondary_btn.setFont(get_ui_font())
        self._primary_btn.setFont(get_ui_font())

        content = QVBoxLayout(self)
        content.setContentsMargins(
            _BORDER + scale_px(14, min_abs=12),
            _BORDER + scale_px(16, min_abs=14),
            _BORDER + scale_px(14, min_abs=12),
            _BORDER + scale_px(12, min_abs=10),
        )
        content.setSpacing(scale_px(12, min_abs=8))
        content.addWidget(self._title_label)
        content.addWidget(self._status_label)
        content.addWidget(self._detail_label, 1)
        content.addWidget(self._progress_bar)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(scale_px(10, min_abs=8))
        btn_row.addStretch(1)
        btn_row.addWidget(self._secondary_btn)
        btn_row.addWidget(self._primary_btn)
        content.addLayout(btn_row)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI.get("ui_fade_duration", 180))
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._detail_signal.connect(self._set_detail_text)
        self._progress_signal.connect(self._apply_progress)
        self._release_check_signal.connect(self._on_release_checked)
        self._release_done_signal.connect(self._on_release_done)
        self._git_check_signal.connect(self._on_git_checked)
        self._git_done_signal.connect(self._on_git_done)
        self._error_signal.connect(self._on_worker_error)

    def begin_release_check(self) -> bool:
        if self._busy:
            return False
        self._mode = "release"
        self._release_check = None
        self._git_check = None
        self._prepare_dialog(
            title="检查新版本",
            status="正在通过 GitHub 检查新的分发包",
            detail="请稍候，正在读取最新发布信息。",
        )
        self._set_busy(True)
        self._show_dialog()
        self._start_worker(self._run_release_check, "release-update-check")
        return True

    def begin_git_sync_check(self) -> bool:
        if self._busy:
            return False
        self._mode = "git"
        self._release_check = None
        self._git_check = None
        self._prepare_dialog(
            title="同步开发版",
            status="正在通过 Git 检查开发版最新改动",
            detail="请稍候，正在拉取远端提交信息。",
        )
        self._set_busy(True)
        self._show_dialog()
        self._start_worker(self._run_git_check, "git-dev-sync-check")
        return True

    def is_busy(self) -> bool:
        return self._busy

    def hide_dialog(self) -> None:
        if self._busy:
            return
        if not self._visible:
            return
        self._visible = False
        self._animate(0.0)

    def _prepare_dialog(self, *, title: str, status: str, detail: str) -> None:
        self._title_label.setText(title)
        self._status_label.setText(status)
        self._detail_label.setText(detail)
        self._set_progress_busy()
        self._set_actions(None, None)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._secondary_btn.setEnabled(not busy)
        self._primary_btn.setEnabled(not busy)

    def _show_dialog(self) -> None:
        self._center_on_screen()
        if not self._visible:
            self._visible = True
            self.show()
        self.raise_()
        self.activateWindow()
        self._animate(1.0)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if not self._visible:
            self.hide()

    def _center_on_screen(self) -> None:
        cursor_pos = QCursor.pos()
        screen = get_screen_geometry_for_point(point=cursor_pos, fallback_widget=self)
        target_x = screen.x() + (screen.width() - self.width()) // 2
        target_y = screen.y() + (screen.height() - self.height()) // 2
        x, y, _ = clamp_rect_position(
            target_x,
            target_y,
            self.width(),
            self.height(),
            point=cursor_pos,
            fallback_widget=self,
        )
        self.move(x, y)

    def _set_actions(
        self,
        secondary: tuple[str, Callable[[], None]] | None,
        primary: tuple[str, Callable[[], None]] | None,
    ) -> None:
        self._secondary_handler = secondary[1] if secondary else None
        self._primary_handler = primary[1] if primary else None

        if secondary:
            self._secondary_btn.setText(secondary[0])
            self._secondary_btn.show()
        else:
            self._secondary_btn.hide()

        if primary:
            self._primary_btn.setText(primary[0])
            self._primary_btn.show()
        else:
            self._primary_btn.hide()

        self._secondary_btn.setEnabled(not self._busy)
        self._primary_btn.setEnabled(not self._busy)

    def _set_progress_busy(self) -> None:
        self._progress_bar.show()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat("处理中...")

    def _set_progress_value(self, current: int, total: int) -> None:
        safe_total = max(1, int(total))
        safe_current = max(0, min(int(current), safe_total))
        self._progress_bar.show()
        self._progress_bar.setRange(0, safe_total)
        self._progress_bar.setValue(safe_current)
        if total > 0:
            percent = int(round((safe_current / safe_total) * 100))
            self._progress_bar.setFormat(f"{percent}%")
        else:
            self._progress_bar.setFormat("处理中...")

    def _set_progress_done(self) -> None:
        self._progress_bar.show()
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._progress_bar.setFormat("完成")

    def _start_worker(self, func: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=func, daemon=True, name=name)
        thread.start()

    def _run_release_check(self) -> None:
        manager = UpdateManager(
            info_callback=self._detail_signal.emit,
            progress_callback=self._progress_signal.emit,
        )
        try:
            result = manager.check_for_updates()
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"检查新版本失败：{exc}")
            return
        self._release_check_signal.emit(result)

    def _run_release_install(self) -> None:
        check = self._release_check
        if check is None:
            self._error_signal.emit("缺少待更新的分发包信息，请重新检查。")
            return
        manager = UpdateManager(
            info_callback=self._detail_signal.emit,
            progress_callback=self._progress_signal.emit,
        )
        try:
            result = manager.install_release(check.release_info)
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"分发包更新失败：{exc}")
            return
        self._release_done_signal.emit(result)

    def _run_git_check(self) -> None:
        manager = GitSyncManager(
            info_callback=self._detail_signal.emit,
            progress_callback=self._progress_signal.emit,
        )
        try:
            result = manager.check_for_updates()
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"同步开发版失败：{exc}")
            return
        self._git_check_signal.emit(result)

    def _run_git_sync(self) -> None:
        check = self._git_check
        if check is None:
            self._error_signal.emit("缺少待同步的开发版信息，请重新检查。")
            return
        manager = GitSyncManager(
            info_callback=self._detail_signal.emit,
            progress_callback=self._progress_signal.emit,
        )
        try:
            result = manager.sync_to_remote(check.snapshot)
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"同步开发版失败：{exc}")
            return
        self._git_done_signal.emit(result)

    def _on_release_checked(self, result: object) -> None:
        check = result if isinstance(result, ReleaseCheckResult) else None
        if check is None:
            self._on_worker_error("检查新版本失败：返回结果无效。")
            return
        self._release_check = check
        self._set_busy(False)
        if check.update_available:
            self._status_label.setText("检测到新的分发包")
            self._detail_label.setText(
                f"当前：{check.installed_state.version}（{self._fmt_dt(check.installed_state.installed_at)}）\n"
                f"最新：{check.release_info.tag}（{self._fmt_dt(check.release_info.published_at)}）"
            )
            self._progress_bar.hide()
            self._set_actions(
                ("稍后再说", self.hide_dialog),
                ("立即更新", self._start_release_install),
            )
            return

        self._status_label.setText("当前已是最新分发包")
        self._detail_label.setText(
            f"本地：{check.installed_state.version}（{self._fmt_dt(check.installed_state.installed_at)}）"
        )
        self._set_progress_done()
        self._set_actions(None, ("关闭", self.hide_dialog))

    def _on_release_done(self, result: object) -> None:
        update = result if isinstance(result, UpdateResult) else None
        if update is None:
            self._on_worker_error("分发包更新失败：返回结果无效。")
            return
        self._set_busy(False)
        self._status_label.setText("分发包更新完成")
        self._detail_label.setText(
            f"已更新到 {update.release_info.tag}（{self._fmt_dt(update.release_info.published_at)}）\n"
            "请重启程序以载入最新资源。"
        )
        self._set_progress_done()
        self._set_actions(None, ("关闭", self.hide_dialog))

    def _on_git_checked(self, result: object) -> None:
        check = result if isinstance(result, GitSyncCheckResult) else None
        if check is None:
            self._on_worker_error("同步开发版失败：返回结果无效。")
            return
        self._git_check = check
        self._set_busy(False)
        snapshot = check.snapshot
        if check.update_available:
            self._status_label.setText("检测到新的开发版提交")
            self._detail_label.setText(
                f"本地提交：{self._fmt_dt(snapshot.local_committed_at)}\n"
                f"远端提交：{self._fmt_dt(snapshot.remote_committed_at)}\n"
                f"差异文件：{len(snapshot.changed_files)} 个"
            )
            self._progress_bar.hide()
            self._set_actions(
                ("稍后同步", self.hide_dialog),
                ("开始同步", self._start_git_sync),
            )
            return

        self._status_label.setText("当前开发版已是最新")
        self._detail_label.setText(
            f"本地提交时间：{self._fmt_dt(snapshot.local_committed_at)}"
        )
        self._set_progress_done()
        self._set_actions(None, ("关闭", self.hide_dialog))

    def _on_git_done(self, result: object) -> None:
        sync_result = result if isinstance(result, GitSyncResult) else None
        if sync_result is None:
            self._on_worker_error("同步开发版失败：返回结果无效。")
            return
        self._set_busy(False)
        self._status_label.setText("开发版同步完成")
        self._detail_label.setText(
            f"当前分支：{sync_result.snapshot.branch}\n"
            f"最新提交时间：{self._fmt_dt(sync_result.snapshot.local_committed_at)}"
        )
        self._set_progress_done()
        self._set_actions(None, ("关闭", self.hide_dialog))

    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False)
        prefix = "同步失败" if self._mode == "git" else "更新失败"
        self._status_label.setText(prefix)
        self._detail_label.setText(str(message or "").strip() or "未知错误")
        self._progress_bar.hide()
        self._set_actions(None, ("关闭", self.hide_dialog))

    def _start_release_install(self) -> None:
        if self._busy:
            return
        self._status_label.setText("正在下载并安装新的分发包")
        self._detail_label.setText("准备开始下载，请稍候。")
        self._set_progress_busy()
        self._set_busy(True)
        self._set_actions(None, None)
        self._start_worker(self._run_release_install, "release-update-install")

    def _start_git_sync(self) -> None:
        if self._busy:
            return
        self._status_label.setText("正在同步开发版")
        self._detail_label.setText("准备覆盖本地差异文件，请稍候。")
        self._set_progress_busy()
        self._set_busy(True)
        self._set_actions(None, None)
        self._start_worker(self._run_git_sync, "git-dev-sync-apply")

    def _set_detail_text(self, text: str) -> None:
        detail = str(text or "").strip()
        if detail:
            self._detail_label.setText(detail)

    def _apply_progress(self, current: int, total: int, message: str) -> None:
        if message:
            self._detail_label.setText(str(message))
        if total <= 0:
            self._set_progress_busy()
            return
        self._set_progress_value(current, total)

    def _on_secondary_clicked(self) -> None:
        if self._busy:
            return
        if callable(self._secondary_handler):
            self._secondary_handler()

    def _on_primary_clicked(self) -> None:
        if self._busy:
            return
        if callable(self._primary_handler):
            self._primary_handler()

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            return
        self._visible = False
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), _C_BORDER)
        painter.fillRect(
            self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER),
            _C_MID,
        )
        painter.fillRect(
            self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER),
            _C_BG,
        )

    @staticmethod
    def _build_title_font():
        font = get_ui_font(size=scale_px(16, min_abs=12))
        font.setBold(True)
        return font

    @staticmethod
    def _fmt_dt(value: datetime) -> str:
        try:
            return value.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)
