"""Qt rendering of the office-mode line icons (SVG text -> QIcon/QPixmap)."""
from __future__ import annotations

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

from lib.core.render.visuals.office_icons import office_icon_size, office_icon_svg


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


def render_office_icon(name: str, color: str) -> QIcon:
    """按中立规格渲染一枚办公图标。"""
    return _render_svg(office_icon_svg(name, color), office_icon_size(name))


def render_office_icon_pixmap(name: str, color: str, pixel_size: int) -> QPixmap:
    """渲染并取指定像素尺寸的位图（用于 `QLabel.setPixmap`）。"""
    size = int(pixel_size)
    return render_office_icon(name, color).pixmap(size, size)


__all__ = ["render_office_icon", "render_office_icon_pixmap"]
