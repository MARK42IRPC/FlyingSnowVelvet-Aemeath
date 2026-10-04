"""Qt window-control button factory for workbench shells.

The product pixels of an action button are backend-neutral
（`visuals/controls.py` + `speaker_menu_style`），but the minimise/close affordance of
a *window chrome* is a native toolkit standard icon. This factory is the single Qt
place that resolves it, shared by the main workbench window and the floating dialogs.
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import QStyle, QToolButton, QWidget

from config.scale import scale_px


def create_window_button(
    parent: QWidget,
    standard_icon: QStyle.StandardPixmap,
    tooltip: str,
    callback: Callable[[], None],
    *,
    danger: bool = False,
) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("WorkbenchWindowButton")
    button.setProperty("danger", danger)
    button.setAutoRaise(True)
    button.setIcon(parent.style().standardIcon(standard_icon))
    button.setIconSize(QSize(scale_px(15, min_abs=13), scale_px(15, min_abs=13)))
    button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


__all__ = ["create_window_button"]
