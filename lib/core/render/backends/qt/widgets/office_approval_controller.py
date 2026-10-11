"""办公审批弹窗的 Qt 控制器（档位 D 宿主）。

产品侧原来同时 import `OfficeFileIpc`（读写审批状态）与 `OfficeApprovalDialog`（弹窗控件），
这两者一个属于办公产品包、一个属于产品面控件，档位 D 都不能反向依赖。控制器只做三件事：
按时轮询状态、按待审批项开弹窗、把决定回写。因此两处入口都改为经共享 seam 注入：

- `office_file_ipc_factory`：返回一个 IPC 实例（缺省即 `None`，控制器构造期需要它）。
- `office_approval_dialog_class`：弹窗类。

两者未注入时给出明确的运行时错误，避免静默退化。
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QWidget

from lib.core.render.backends.qt.widgets import tier_d_seams


def _make_ipc():
    factory = tier_d_seams.hook("office_file_ipc_factory")
    if factory is None:
        raise RuntimeError("办公审批控制器需要经 render_bridge 注入 IPC 工厂")
    return factory()


def _approval_dialog_class():
    resolver = tier_d_seams.hook("office_approval_dialog_class")
    if resolver is None:
        raise RuntimeError("办公审批控制器需要经 render_bridge 注入审批弹窗类")
    return resolver()


class OfficeApprovalController(QObject):
    POLL_INTERVAL_MS = 200

    def __init__(
        self,
        *,
        ipc=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc or _make_ipc()
        self._dialog_parent = parent
        self._dialog = None
        self._shown_ids: set[str] = set()
        self._cleaned = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def active_dialog(self):
        return self._dialog

    def start(self) -> None:
        if self._cleaned:
            return
        self._poll()
        self._timer.start()

    def _poll(self) -> None:
        if self._cleaned:
            return
        state = self._ipc.read_state()
        pending = state.get("pending_approval")
        approval = pending if isinstance(pending, dict) else None
        approval_id = str((approval or {}).get("approval_id") or "")

        dialog = self._dialog
        if dialog is not None and dialog.approval_id != approval_id:
            dialog.dismiss_without_decision()
            self._dialog = None
        if not approval_id or approval_id in self._shown_ids:
            return

        self._shown_ids.add(approval_id)
        if len(self._shown_ids) > 200:
            self._shown_ids = {approval_id}
        dialog_class = _approval_dialog_class()
        dialog = dialog_class(approval, parent=self._dialog_parent)
        dialog.decision_made.connect(self._on_decision)
        dialog.destroyed.connect(self._on_dialog_destroyed)
        self._dialog = dialog
        dialog.open()
        dialog.raise_()
        dialog.activateWindow()

    def _on_decision(self, approval_id: str, decision: str) -> None:
        self._ipc.submit(
            "approval",
            approval_id=approval_id,
            decision=decision,
        )
        dialog = self._dialog
        if dialog is not None and dialog.approval_id == approval_id:
            self._dialog = None

    def _on_dialog_destroyed(self, _obj=None) -> None:
        """弹窗被 Qt 销毁后清掉引用。

        这里连**绑定方法**而不是闭包：Qt 对 QObject 接收者只持弱引用，控件销毁即自动
        断开；闭包则会一直攥住 `self`，一旦 `_poll` 的帧先被回收，回调里的 `self`
        就成了空单元格。
        """

        self._dialog = None

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.dismiss_without_decision()


__all__ = ["OfficeApprovalController"]
