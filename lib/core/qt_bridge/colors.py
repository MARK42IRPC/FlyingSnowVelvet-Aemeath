"""Convert backend-neutral theme colors to Qt values."""

from PyQt5.QtGui import QColor

from config.config_ui import COLORS as CORE_COLORS
from config.config_ui import UI_THEME as CORE_UI_THEME
from lib.core.graphics.types import coerce_color


def to_qcolor(value: object) -> QColor:
    if isinstance(value, QColor):
        return QColor(value)
    color = coerce_color(value)
    if color is None:
        raise TypeError(f"cannot convert to QColor: {value!r}")
    return QColor(color.red, color.green, color.blue, color.alpha)


def qt_color_map(values: dict[str, object]) -> dict[str, QColor]:
    return {name: to_qcolor(value) for name, value in values.items()}


COLORS = qt_color_map(CORE_COLORS)
UI_THEME = qt_color_map(CORE_UI_THEME)
