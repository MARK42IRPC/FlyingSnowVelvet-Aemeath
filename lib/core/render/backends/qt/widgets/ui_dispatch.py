"""UI 线程调度宿主：把回调投递回 Qt 事件循环（档位 D）。

后端中立的业务控制器（公告、论坛、帮助、办公模式页）需要在**UI 线程**里执行一段回调。
它们的后台工作跑在计算线程上，回调必须回到 UI 线程执行；早先每个控制器都在自己的
`QObject` 上声明一个 `pyqtSignal(object)`，只为把回调挪回事件循环——那迫使这些
"只订阅事件、收集状态"的产品控制器继承 `QObject`、被迫 `import PyQt5`。

本宿主把这条 Qt 事实收成一处，语义是「保证回调在 UI 线程执行」：

- 从**其它线程**调用：经 `QueuedConnection` 投递，回到 UI 线程执行；
- 在**自己的线程**调用：就地执行（等价于一次直接连接），避免在无 `exec_()` 的
  单元测试里把回调排进一个永远不派发的队列。

产品控制器只持有 `render_bridge.create_ui_dispatcher()` 的返回值，不再自己声明信号。

本模块不 import `lib.script`，也不静态引用档位 A（`drawing/`）。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal


class UiDispatcher(QObject):
    """把回调投递到创建它的线程（UI 线程）执行的宿主。"""

    _posted = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread = QThread.currentThread()
        self._pending: list[Callable[[], None]] = []
        self._posted.connect(self._run, Qt.QueuedConnection)

    def post(self, callback: Callable[[], None]) -> None:
        """保证 `callback` 在 UI 线程执行；已在 UI 线程时就地执行。"""
        if QThread.currentThread() is self._thread:
            callback()
            return
        self._pending.append(callback)
        self._posted.emit(callback)

    def _run(self, callback: Callable[[], None]) -> None:
        try:
            self._pending.remove(callback)
        except ValueError:
            pass
        callback()

    def flush(self) -> None:
        """同步执行仍在排队、尚未被事件循环取走的跨线程回调。"""
        while self._pending:
            callback = self._pending.pop(0)
            callback()

    def clear(self) -> None:
        """丢弃仍在排队的回调（清理时调用，避免回调在销毁的控件上跑）。"""
        self._pending.clear()


__all__ = ["UiDispatcher"]
