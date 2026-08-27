"""Theme-aware SVG line icons for office-mode Qt surfaces."""

from __future__ import annotations

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

from config.scale import scale_px


def _render_svg(svg_text: str, size: int) -> QIcon:
    size = max(12, int(size))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(2.0)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


def _line_icon(paths: str, color: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none">'
        f'<g stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</g></svg>'
    )


def office_new_icon(color: str) -> QIcon:
    return _render_svg(_line_icon('<path d="M12 5v14M5 12h14"/>', color), scale_px(15, min_abs=13))


def office_delete_icon(color: str) -> QIcon:
    paths = (
        '<path d="M4 7h16"/>'
        '<path d="M9 7V4h6v3"/>'
        '<path d="M6 7l1 13h10l1-13"/>'
        '<path d="M10 11v5M14 11v5"/>'
    )
    return _render_svg(_line_icon(paths, color), scale_px(15, min_abs=13))


def office_browse_icon(color: str) -> QIcon:
    paths = (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>'
        '<path d="M3 10h18"/>'
    )
    return _render_svg(_line_icon(paths, color), scale_px(15, min_abs=13))


def office_cancel_icon(color: str) -> QIcon:
    return _render_svg(_line_icon('<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>', color), scale_px(15, min_abs=13))


def office_submit_icon(color: str) -> QIcon:
    return _render_svg(_line_icon('<path d="M5 12h14M13 6l6 6-6 6"/>', color), scale_px(15, min_abs=13))


def office_reject_icon(color: str) -> QIcon:
    return _render_svg(_line_icon('<path d="M6 6l12 12M18 6L6 18"/>', color), scale_px(14, min_abs=12))


def office_warning_icon(color: str) -> QIcon:
    paths = (
        '<path d="M12 4l9 16H3z"/>'
        '<path d="M12 9.5V14"/>'
        '<path d="M12 17v.2"/>'
    )
    return _render_svg(_line_icon(paths, color), scale_px(24, min_abs=21))


def office_allow_icon(color: str) -> QIcon:
    return _render_svg(_line_icon('<path d="M5 12.5l4.5 4.5L19 7.5"/>', color), scale_px(14, min_abs=12))


def office_allow_task_icon(color: str) -> QIcon:
    paths = (
        '<path d="M4.5 13l3.8 3.8L14.5 10.5"/>'
        '<path d="M9.5 13l3.8 3.8L19.5 10.5"/>'
    )
    return _render_svg(_line_icon(paths, color), scale_px(14, min_abs=12))


__all__ = [
    "office_new_icon",
    "office_delete_icon",
    "office_browse_icon",
    "office_cancel_icon",
    "office_submit_icon",
    "office_reject_icon",
    "office_allow_icon",
    "office_allow_task_icon",
    "office_warning_icon",
]
