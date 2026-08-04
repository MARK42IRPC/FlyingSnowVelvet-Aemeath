"""Qt font registration, metrics, elision, and mixed-text drawing."""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase, QFontMetrics
from PyQt5.QtWidgets import QApplication, QWidget

from config import font_config as core_font

_registered_families: tuple[str, str] | None = None


def _register_font_family(font_path: str, fallback_family: str) -> str:
    path = Path(font_path)
    if not path.exists() or QApplication.instance() is None:
        return fallback_family
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id == -1:
        return fallback_family
    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else fallback_family


def _ensure_font_families() -> tuple[str, str]:
    global _registered_families
    if _registered_families is not None:
        return _registered_families
    if QApplication.instance() is None:
        return core_font.get_ui_font_family(), core_font.get_digit_font_family()
    ui_family = _register_font_family(core_font._HARMONY_PATH, "Microsoft YaHei")
    digit_family = _register_font_family(core_font._LAHAI_ROI_PATH, ui_family)
    core_font._set_registered_font_families(ui_family, digit_family)
    _registered_families = (ui_family, digit_family)
    return _registered_families


def init_font_config() -> None:
    core_font.init_font_config()
    ui_family, _ = _ensure_font_families()
    app = QApplication.instance()
    if app is not None:
        app.setFont(_build_font(ui_family, core_font.FONT["ui_size"]))


def _build_font(family: str, pixel_size: int) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, int(pixel_size)))
    return font


def get_ui_font(size: int | None = None) -> QFont:
    family, _ = _ensure_font_families()
    font_size = core_font.FONT["ui_size"] if size is None else int(size)
    return _build_font(family, font_size)


def get_ui_font_family() -> str:
    return _ensure_font_families()[0]


def get_cmd_font(size: int | None = None) -> QFont:
    family, _ = _ensure_font_families()
    font_size = core_font.FONT["cmd_size"] if size is None else int(size)
    return _build_font(family, font_size)


def get_digit_font(size: int | None = None) -> QFont:
    _, family = _ensure_font_families()
    font_size = core_font.FONT["ui_size"] if size is None else int(size)
    return _build_font(family, font_size)


def get_digit_font_family() -> str:
    return _ensure_font_families()[1]


def apply_ui_font_tree(widget) -> None:
    family = get_ui_font_family()
    for target in (widget, *widget.findChildren(QWidget)):
        font = target.font()
        font.setFamily(family)
        target.setFont(font)


def measure_mixed_text(text: str, default_font, digit_font) -> int:
    if not text:
        return 0
    default_metrics = QFontMetrics(default_font)
    digit_metrics = QFontMetrics(digit_font)
    return sum(
        (digit_metrics if is_digit else default_metrics).horizontalAdvance(segment)
        for segment, is_digit in core_font._split_digit_segments(text)
    )


def elide_mixed_text(text: str, max_width: int, default_font, digit_font, mode=None) -> str:
    mode = Qt.ElideRight if mode is None else mode
    if max_width <= 0:
        return ""
    if measure_mixed_text(text, default_font, digit_font) <= max_width:
        return text
    if mode != Qt.ElideRight:
        return QFontMetrics(default_font).elidedText(text, mode, max_width)

    ellipsis = "..."
    if measure_mixed_text(ellipsis, default_font, digit_font) > max_width:
        return ""
    kept = ""
    for char in text:
        probe = kept + char
        if measure_mixed_text(probe + ellipsis, default_font, digit_font) > max_width:
            break
        kept = probe
    return kept + ellipsis


def wrap_mixed_text(text: str, max_width: int, default_font, digit_font) -> list[str]:
    if max_width <= 0:
        return [""]
    default_metrics = QFontMetrics(default_font)
    digit_metrics = QFontMetrics(digit_font)

    def char_width(char: str) -> int:
        metrics = digit_metrics if char.isdigit() else default_metrics
        return metrics.horizontalAdvance(char)

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        current_width = 0
        for char in paragraph:
            width = char_width(char)
            if current and current_width + width > max_width:
                lines.append(current)
                current = char
                current_width = width
            else:
                current += char
                current_width += width
        lines.append(current)
    return lines or [""]


def draw_mixed_text(painter, rect, text: str, default_font, digit_font, align=None):
    align = Qt.AlignLeft | Qt.AlignVCenter if align is None else align
    default_metrics = QFontMetrics(default_font)
    digit_metrics = QFontMetrics(digit_font)
    segments = core_font._split_digit_segments(text)
    total_width = sum(
        (digit_metrics if is_digit else default_metrics).horizontalAdvance(segment)
        for segment, is_digit in segments
    )

    if align & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - total_width) / 2.0
    elif align & Qt.AlignRight:
        x = rect.x() + rect.width() - total_width
    else:
        x = rect.x()

    ascent = max(default_metrics.ascent(), digit_metrics.ascent())
    descent = max(default_metrics.descent(), digit_metrics.descent())
    y = rect.y() + (rect.height() + ascent - descent) / 2.0

    painter.save()
    painter.setClipRect(rect)
    for segment, is_digit in segments:
        metrics = digit_metrics if is_digit else default_metrics
        painter.setFont(digit_font if is_digit else default_font)
        painter.drawText(int(round(x)), int(round(y)), segment)
        x += metrics.horizontalAdvance(segment)
    painter.restore()
    painter.setFont(default_font)
