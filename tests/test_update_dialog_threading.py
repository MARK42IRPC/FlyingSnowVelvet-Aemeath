"""更新浮窗的线程边界：worker 的完成回调必须回到 UI 线程，且窗口关闭后不得再派发。

用户反馈「自动检查更新完成之后桌宠无报错闪退」。排查结论是更新窗把 `pyqtSignal` 换成了
纯 Python 的回调对象（`_UpdateSignal`），而 `emit()` 就地执行槽——于是
`compute_interactive_io` 线程里的 `_on_release_checked()` 直接在工作线程上改 Qt 控件
（`setText` / `setRange` / `setEnabled`）。Qt 不允许跨线程操作控件，Windows 上实测整个进程
直接结束、连 traceback 都不留，日志里也只剩正常退出前的那几行。

三条约束在这里钉死：

- worker 线程 `emit()` 时，槽必须在 UI 线程执行（与 `pyqtSignal` 的自动排队等价）；
- 信号是**实例级**的，两个对话框不能共用同一张槽表；
- 窗口 `cleanup()` / `deleteLater()` 之后到达的结果直接丢弃，不能再碰已销毁的控件。
"""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.script.ui.update_dialog import DesktopPetUpdateDialog


def _pump(app, seconds: float = 0.6) -> None:
    """跑事件循环，让跨线程排队的回调有机会被派发。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


class UpdateDialogThreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_emit_runs_the_slot_on_the_ui_thread(self):
        dialog = DesktopPetUpdateDialog()
        try:
            main_thread = threading.current_thread()
            seen: list[threading.Thread] = []

            dialog._release_check_signal.connect(
                lambda _result=None: seen.append(threading.current_thread())
            )

            worker = threading.Thread(
                target=lambda: dialog._release_check_signal.emit(object()),
                name="compute_interactive_io_0",
            )
            worker.start()
            worker.join(timeout=2)
            _pump(self.app)

            self.assertEqual(len(seen), 1, "完成回调应当被派发一次")
            self.assertIs(
                seen[0],
                main_thread,
                "完成回调必须回到 UI 线程；在工作线程改 Qt 控件会直接结束进程",
            )
        finally:
            dialog.deleteLater()
            _pump(self.app, 0.1)

    def test_signals_are_per_instance_not_shared_across_dialogs(self):
        """类级信号会让第二个窗口把自己接到第一个窗口上，第一个销毁后回调就落空。"""
        first = DesktopPetUpdateDialog()
        second = DesktopPetUpdateDialog()
        try:
            self.assertIsNot(first._detail_signal, second._detail_signal)
            self.assertIsNot(first._error_signal, second._error_signal)

            hits: list[str] = []
            second._error_signal.connect(lambda message: hits.append(message))
            # 第一个窗口上的 emit 不该惊动第二个窗口的槽。
            first._error_signal.emit("只属于第一个窗口")
            _pump(self.app, 0.1)
            self.assertEqual(hits, [])

            second._error_signal.emit("第二个窗口自己的")
            _pump(self.app, 0.1)
            self.assertEqual(hits, ["第二个窗口自己的"])
        finally:
            first.deleteLater()
            second.deleteLater()
            _pump(self.app, 0.1)

    def test_signals_are_dropped_after_the_dialog_is_cleaned_up(self):
        """worker 可能比窗口活得久：清理之后回来的结果不能再往已销毁的控件上写。"""
        dialog = DesktopPetUpdateDialog()
        hits: list[object] = []
        dialog._release_check_signal.connect(lambda result=None: hits.append(result))
        dialog._error_signal.connect(lambda message: hits.append(message))

        dialog.cleanup()
        try:
            dialog._release_check_signal.emit(object())
            dialog._error_signal.emit("迟到的错误")

            worker = threading.Thread(
                target=lambda: dialog._release_check_signal.emit(object()),
                name="compute_interactive_io_1",
            )
            worker.start()
            worker.join(timeout=2)
            _pump(self.app, 0.3)

            self.assertEqual(hits, [], "窗口清理后的回调必须被丢弃")
        finally:
            dialog.deleteLater()
            _pump(self.app, 0.1)

    def test_already_queued_callback_is_dropped_when_the_window_closes(self):
        """回调可以在排队途中被作废：跨线程投递后、UI 线程执行前窗口就关了。"""
        dialog = DesktopPetUpdateDialog()
        hits: list[object] = []
        dialog._release_check_signal.connect(lambda result=None: hits.append(result))
        try:
            # 在 worker 线程 emit，让回调进入 UI 队列；先不派发。
            worker = threading.Thread(
                target=lambda: dialog._release_check_signal.emit("迟到的结果"),
                name="compute_interactive_io_3",
            )
            worker.start()
            worker.join(timeout=2)

            dialog.cleanup()
            _pump(self.app, 0.4)

            self.assertEqual(hits, [], "已排队但未执行的回调在窗口关闭后必须作废")
        finally:
            dialog.deleteLater()
            _pump(self.app, 0.1)

    def test_worker_result_updates_the_window_on_the_ui_thread(self):
        """端到端：worker 里发一条错误，窗口文案在 UI 线程上被改写。"""
        dialog = DesktopPetUpdateDialog()
        try:
            main_thread = threading.current_thread()
            applied: list[threading.Thread] = []
            original = dialog._on_worker_error

            def traced(message: str) -> None:
                applied.append(threading.current_thread())
                original(message)

            dialog._error_signal.connect(traced)
            worker = threading.Thread(
                target=lambda: dialog._error_signal.emit("网络不可用"),
                name="compute_interactive_io_2",
            )
            worker.start()
            worker.join(timeout=2)
            _pump(self.app)

            self.assertEqual(len(applied), 1)
            self.assertIs(applied[0], main_thread)
            self.assertEqual(dialog._status_label.text(), "更新失败")
            self.assertEqual(dialog._detail_label.text(), "网络不可用")
        finally:
            dialog.deleteLater()
            _pump(self.app, 0.1)


if __name__ == "__main__":
    unittest.main()
