"""Qt QWidget host for the backend-neutral entity contract."""
from __future__ import annotations

from abc import ABCMeta

from PyQt5.QtWidgets import QWidget

from lib.core.entity.base import BaseEntity


class QWidgetABCMeta(type(QWidget), ABCMeta):
    """Resolve the SIP QWidget and ABC metaclass combination."""


class QtEntityWidget(QWidget, BaseEntity, metaclass=QWidgetABCMeta):
    """Qt host base; concrete entities implement the core entity methods."""

    def __init__(self):
        QWidget.__init__(self)
