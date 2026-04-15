"""Global clickthrough state shared by runtime windows."""

from PyQt5.QtWidgets import QApplication

_APP_CLICKTHROUGH_PROPERTY = "aemeath_clickthrough_enabled"


def set_clickthrough_enabled(enabled: bool) -> None:
    """Persist clickthrough state on QApplication for late-created windows."""
    app = QApplication.instance()
    if app is None:
        return
    app.setProperty(_APP_CLICKTHROUGH_PROPERTY, bool(enabled))


def is_clickthrough_enabled(default: bool = False) -> bool:
    """Read current clickthrough state from QApplication."""
    app = QApplication.instance()
    if app is None:
        return bool(default)
    value = app.property(_APP_CLICKTHROUGH_PROPERTY)
    if value is None:
        return bool(default)
    return bool(value)

