"""Shared Qt lifecycle for standalone-or-embedded workbench tool pages."""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt
from PyQt5.QtWidgets import QWidget

from config.config import UI
from lib.core.anchor_utils import apply_ui_opacity


class QtWorkbenchToolPage(QWidget):
    """Provide one host contract for workbench tools with standalone windows."""

    def __init__(self, *, embedded: bool = False) -> None:
        super().__init__()
        self._embedded = bool(embedded)
        self._external_close_callback: Callable[[], None] | None = None
        self._drag_handle: QWidget | None = None
        self._dragging = False
        self._drag_offset = QPoint()
        self._fading_out = False
        self._allow_hide_once = False
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(int(UI.get("ui_fade_duration", 180)))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)

    @property
    def is_embedded(self) -> bool:
        return self._embedded

    def set_embedded_mode(self, embedded: bool = True) -> None:
        self._embedded = bool(embedded)
        self._dragging = False
        if self._embedded:
            self.setWindowFlags(Qt.Widget)
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        self._sync_drag_cursor()
        self._sync_embedded_presentation()

    def set_external_close_callback(
        self,
        callback: Callable[[], None] | None,
    ) -> None:
        self._external_close_callback = callback

    def set_drag_handle(self, widget: QWidget | None) -> None:
        previous = self._drag_handle
        if previous is not None:
            previous.removeEventFilter(self)
        self._drag_handle = widget
        if widget is not None:
            widget.installEventFilter(self)
        self._sync_drag_cursor()

    def refresh_workbench_page(self) -> None:
        """Refresh page data when the workbench first materializes the page."""

    def fade_in(self) -> None:
        if self._embedded:
            self.setWindowOpacity(1.0)
            self.show()
            return
        self._before_standalone_show()
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(apply_ui_opacity(1.0))
        self._opacity_anim.start()

    def fade_out(self) -> None:
        if self._external_close_callback is not None:
            self._external_close_callback()
            return
        if self._fading_out or not self.isVisible():
            return
        self._fading_out = True
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(
            max(0.0, min(1.0, float(self.windowOpacity())))
        )
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.start()

    def hide(self) -> None:
        if self._allow_hide_once or self._fading_out or not self.isVisible():
            super().hide()
            return
        self.fade_out()

    def _request_close(self) -> None:
        self.fade_out()

    def _on_opacity_anim_finished(self) -> None:
        if not self._fading_out:
            return
        self._fading_out = False
        self._after_standalone_hide()
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False
            self.setWindowOpacity(apply_ui_opacity(1.0))

    def eventFilter(self, watched, event) -> bool:
        if watched is self._drag_handle and not self._embedded:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                self._sync_drag_cursor(closed=True)
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseMove
                and self._dragging
                and (event.buttons() & Qt.LeftButton)
            ):
                self.move(event.globalPos() - self._drag_offset)
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._dragging = False
                self._sync_drag_cursor()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _sync_drag_cursor(self, *, closed: bool = False) -> None:
        if self._drag_handle is None:
            return
        if self._embedded:
            self._drag_handle.setCursor(Qt.ArrowCursor)
        else:
            self._drag_handle.setCursor(
                Qt.ClosedHandCursor if closed else Qt.OpenHandCursor
            )

    def _sync_embedded_presentation(self) -> None:
        """Apply page-specific visibility and margins for the current host mode."""

    def _before_standalone_show(self) -> None:
        """Run page-specific setup immediately before a standalone fade-in."""

    def _after_standalone_hide(self) -> None:
        """Run page-specific cleanup immediately before a standalone hide."""
