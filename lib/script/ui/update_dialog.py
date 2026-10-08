"""桌宠更新/开发版同步小窗（按窗口描述装配，不再 import PyQt5）。

本模块不再是 `QWidget` 子类：窗眉、状态行、详情行、进度条与动作按钮都由
`lib/core/render/visuals/window_specs.py` 的 `update_window_spec()` 描述，
`render_bridge.create_spec_window()` 交给 Qt 宿主
（`lib/core/render/backends/qt/widgets/spec_host.py`）装配。

本模块只做三件事：收集更新/同步状态、把后台 worker 的结果投递回 UI 线程、更新描述宿主
里的控件。为兼容既有调用方与测试，保留原 `QWidget` 的属性面（`_title_label` /
`_status_label` / `_detail_label` / `_progress_bar` / `_primary_btn` / `_secondary_btn` /
`_minimize_btn` / `_visible` / `_busy` / `width()` / `_show_dialog()` /
`minimize_floating_window()` / `deleteLater()` 等）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from config.scale import scale_px
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.visuals.window_specs import (
    UPDATE_MINIMIZE,
    UPDATE_PRIMARY,
    UPDATE_SECONDARY,
    update_window_spec,
)
from lib.script.update_manager import (
    GitSyncCheckResult,
    GitSyncManager,
    GitSyncResult,
    ReleaseCheckResult,
    UpdateError,
    UpdateManager,
    UpdateResult,
)
from lib.core.render.visuals.workbench_chrome import floating_window_stylesheet
from lib.script.workbench.theme import get_workbench_colors
from lib.script.ui import render_bridge

_WIDTH = scale_px(360, min_abs=320)
_HEIGHT = scale_px(248, min_abs=220)
_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


class _UpdateSignal:
    """`pyqtSignal(...)` 的后端中立替身：只保留 `connect` / `emit` / `disconnect`。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot=None) -> None:
        if slot is None:
            self._slots.clear()
            return
        try:
            self._slots.remove(slot)
        except ValueError:
            pass

    def emit(self, *args) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class DesktopPetUpdateDialog:
    """承载分发包更新与开发版同步的独立小窗。"""

    _detail_signal = _UpdateSignal()
    _progress_signal = _UpdateSignal()
    _release_check_signal = _UpdateSignal()
    _release_done_signal = _UpdateSignal()
    _git_check_signal = _UpdateSignal()
    _git_done_signal = _UpdateSignal()
    _restart_done_signal = _UpdateSignal()
    _error_signal = _UpdateSignal()

    def __init__(self, parent=None) -> None:
        self._dispatcher = render_bridge.create_ui_dispatcher(parent)
        self._visible = False
        self._busy = False
        self._mode = ""
        self._release_check: ReleaseCheckResult | None = None
        self._git_check: GitSyncCheckResult | None = None
        self._pending_update: UpdateResult | None = None
        self._primary_handler: Callable[[], None] | None = None
        self._secondary_handler: Callable[[], None] | None = None

        self._spec = self._build_spec()
        self._window = render_bridge.create_spec_window(
            self._spec,
            on_semantic=self._on_semantic,
            parent=parent,
        )

        self._title_label = self._window.find("title")
        self._status_label = self._window.find("status")
        self._detail_label = self._window.find("detail")
        self._progress_bar = self._window.find("progress")
        self._primary_btn = self._window.find("primary")
        self._secondary_btn = self._window.find("secondary")
        self._minimize_btn = self._window.find("minimize")

        self._secondary_btn.hide()
        self._primary_btn.hide()
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)

        self._detail_signal.connect(self._set_detail_text)
        self._progress_signal.connect(self._apply_progress)
        self._release_check_signal.connect(self._on_release_checked)
        self._release_done_signal.connect(self._on_release_done)
        self._git_check_signal.connect(self._on_git_checked)
        self._git_done_signal.connect(self._on_git_done)
        self._restart_done_signal.connect(self._on_restart_done)
        self._error_signal.connect(self._on_worker_error)

    # ── 描述 ─────────────────────────────────────────────────────────

    def _build_spec(self):
        return update_window_spec(
            status="",
            detail="",
            stylesheet=self._widget_stylesheet(),
            width=_WIDTH,
            height=_HEIGHT,
            border_width=_BORDER,
            root_margin=(
                _BORDER + scale_px(14, min_abs=12),
                _BORDER + scale_px(16, min_abs=14),
                _BORDER + scale_px(14, min_abs=12),
                _BORDER + scale_px(12, min_abs=10),
            ),
            root_spacing=scale_px(12, min_abs=8),
            header_spacing=scale_px(10, min_abs=8),
            header_lead=scale_px(26, min_abs=24),
            title_size=scale_px(16, min_abs=12),
            status_size=scale_px(13, min_abs=10),
            detail_size=scale_px(11, min_abs=9),
            minimize_size=scale_px(32, min_abs=28),
            progress_min_height=scale_px(22, min_abs=18),
        )

    def begin_release_check(self) -> bool:
        if self._busy:
            return False
        self._mode = "release"
        self._release_check = None
        self._git_check = None
        self._prepare_dialog(
            title="检查新版本",
            status="正在获取更新包",
            detail=(
                "请稍候，正在并发探测更新源"
                "（Hugging Face / ModelScope），最长 16 秒。"
            ),
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
        self._window.hide_dialog()

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
        # 安装/下载中不允许收起，避免用户失去进度入口。
        self._minimize_btn.setEnabled(not busy)

    def _show_dialog(self) -> None:
        self._visible = True
        self._window.show_window()

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
        del name
        get_compute_hub().submit_interactive_io(func)

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
            result = manager.install_release(check.release_info, launch_installer=False)
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"分发包更新失败：{exc}")
            return
        self._release_done_signal.emit(result)

    def _run_release_launch(self) -> None:
        update = self._pending_update
        if update is None:
            self._error_signal.emit("缺少待安装的更新包，请重新下载。")
            return
        manager = UpdateManager()
        try:
            result = manager.launch_pending_update(update)
        except UpdateError as exc:
            self._error_signal.emit(str(exc))
            return
        except Exception as exc:
            self._error_signal.emit(f"准备重启失败：{exc}")
            return
        self._restart_done_signal.emit(result)

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
        self._pending_update = None
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
        self._pending_update = update
        notes = "\n".join(str(note) for note in update.notes if str(note).strip())
        if update.release_info.kind == "resources":
            self._status_label.setText("资源包已安装")
            detail = (
                f"已安装 {update.release_info.tag}（{self._fmt_dt(update.release_info.published_at)}）\n"
                "资源已写入当前安装目录，后续启动将使用最新资源。"
            )
            self._detail_label.setText(f"{detail}\n{notes}" if notes else detail)
            self._set_progress_done()
            self._set_actions(None, ("关闭", self.hide_dialog))
            return
        self._status_label.setText("离线安装器已准备")
        detail = (
            f"已准备 {update.release_info.tag}（{self._fmt_dt(update.release_info.published_at)}）\n"
            f"更新文件：{update.release_info.asset_name}\n"
            "启动安装器后，当前桌宠会退出；安装器将使用包内运行环境完成更新。"
        )
        self._detail_label.setText(f"{detail}\n{notes}" if notes else detail)
        self._set_progress_done()
        self._set_actions(
            ("稍后安装", self.hide_dialog),
            ("启动安装器并退出", self._start_release_launch),
        )

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
        self._status_label.setText("正在下载新的分发包")
        self._detail_label.setText("准备开始下载，请稍候。")
        self._set_progress_busy()
        self._set_busy(True)
        self._set_actions(None, None)
        self._start_worker(self._run_release_install, "release-update-install")

    def _start_release_launch(self) -> None:
        if self._busy or self._pending_update is None:
            return
        self._status_label.setText("正在启动离线安装器")
        self._detail_label.setText("请稍候，安装器启动成功后桌宠将退出。")
        self._set_busy(True)
        self._set_actions(None, None)
        self._start_worker(self._run_release_launch, "release-update-launch")

    def _on_restart_done(self, result: object) -> None:
        if not isinstance(result, UpdateResult):
            self._on_worker_error("准备重启失败：返回结果无效。")
            return
        self._set_busy(True)
        self._set_actions(None, None)
        get_event_center().publish(Event(EventType.APP_QUIT, {"exit_code": 0}))

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

    # ── 语义与生命周期 ───────────────────────────────────────────────

    def _on_semantic(self, semantic: str) -> None:
        key = str(semantic)
        if key == UPDATE_MINIMIZE:
            self.minimize_floating_window()
        elif key == UPDATE_PRIMARY:
            self._on_primary_clicked()
        elif key == UPDATE_SECONDARY:
            self._on_secondary_clicked()

    def minimize_floating_window(self) -> None:
        """窗眉最小化：忙碌时忽略，否则收起窗口。"""
        self.hide_dialog()

    def _on_widget_closed(self) -> None:
        if self._busy:
            return
        self._visible = False

    def cleanup(self) -> None:
        self._visible = False
        self._busy = False
        self._dispatcher.clear()
        self._window.cleanup()

    def deleteLater(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._dispatcher.clear()
        self._window.cleanup()

    # ── 底层窗口的属性面（调用方按原 QWidget 用法）────────────────────

    def widget(self):
        return self._window.widget

    def findChild(self, *args, **kwargs):  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.findChild(*args, **kwargs)

    def show(self) -> None:
        self._window.widget.show()

    def hide(self) -> None:
        self._window.widget.hide()

    def close(self) -> None:
        self._window.widget.close()

    def move(self, *args) -> None:
        self._window.widget.move(*args)

    def raise_(self) -> None:
        self._window.widget.raise_()

    def activateWindow(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._window.widget.activateWindow()

    def width(self) -> int:
        return self._window.widget.width()

    def height(self) -> int:
        return self._window.widget.height()

    def isVisible(self) -> bool:  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.isVisible()

    def styleSheet(self) -> str:  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.styleSheet()

    def grab(self):
        return self._window.widget.grab()

    @staticmethod
    def _widget_stylesheet() -> str:
        # 原窗口继承 `WorkbenchFloatingWindow`，样式来自共享的
        # `floating_window_stylesheet()`（含 QProgressBar / QPushButton 尺寸档），
        # 再叠加本窗的标签对象名配色。迁移后必须逐字保留同一份外壳 QSS。
        colors = get_workbench_colors()
        return floating_window_stylesheet() + f"""
            QLabel#UpdateTitle {{
                color: {colors.text};
            }}
            QLabel#UpdateStatus {{
                color: {colors.text};
            }}
            QLabel#UpdateDetail {{
                color: {colors.text_muted};
            }}
            """

    @staticmethod
    def _fmt_dt(value: datetime) -> str:
        try:
            return value.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)
