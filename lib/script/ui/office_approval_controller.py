"""Qt owner for office approval dialogs in main and helper processes."""

from __future__ import annotations

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QWidget

from lib.script.office.ipc import OfficeFileIpc
from lib.script.ui.office_approval_dialog import OfficeApprovalDialog


class OfficeApprovalController(QObject):
    POLL_INTERVAL_MS = 200

    def __init__(
        self,
        *,
        ipc: OfficeFileIpc | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc or OfficeFileIpc()
        self._dialog_parent = parent
        self._dialog: OfficeApprovalDialog | None = None
        self._shown_ids: set[str] = set()
        self._cleaned = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def active_dialog(self) -> OfficeApprovalDialog | None:
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
        dialog = OfficeApprovalDialog(approval, self._dialog_parent)
        dialog.decision_made.connect(self._on_decision)
        dialog.destroyed.connect(
            lambda _obj=None, owned=dialog: self._clear_dialog(owned)
        )
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

    def _clear_dialog(self, dialog: OfficeApprovalDialog) -> None:
        if self._dialog is dialog:
            self._dialog = None

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.dismiss_without_decision()
