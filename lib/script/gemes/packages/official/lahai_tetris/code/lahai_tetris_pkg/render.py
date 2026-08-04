"""Rendering helpers for Lahai Tetris."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, QPointF, QRect, QRectF, QVariantAnimation
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from lib.core.qt_bridge.font import get_digit_font, get_ui_font
from config.scale import scale_px

from .constants import (
    BOARD_H as _BOARD_H,
    BOARD_W as _BOARD_W,
    PREVIEW_GRID as _PREVIEW_GRID,
    SHAPES as _SHAPES,
    SPECIAL_FILL_KIND as _SPECIAL_FILL_KIND,
    SUN_KIND as _SUN_KIND,
    THEME as _THEME,
    WARNING_LINE_DEFAULT_HZ as _WARNING_LINE_DEFAULT_HZ,
    WARNING_LINE_FLASH_HZ as _WARNING_LINE_FLASH_HZ,
    WARNING_LINE_FLASH_STACK_HEIGHT as _WARNING_LINE_FLASH_STACK_HEIGHT,
    WARNING_LINE_ROW as _WARNING_LINE_ROW,
)

if TYPE_CHECKING:
    from .widget import LahaiTetrisWidget


def draw_round_panel(widget: "LahaiTetrisWidget", painter: QPainter, rect: QRectF, fill: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, widget._PANEL_RADIUS, widget._PANEL_RADIUS)
    painter.fillPath(path, fill)
    painter.setPen(QPen(widget._C_BORDER_DEEP, max(1, scale_px(3, min_abs=1))))
    painter.drawPath(path)
    painter.setPen(QPen(widget._C_PANEL_EDGE, max(1, scale_px(2, min_abs=1))))
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), widget._PANEL_RADIUS - 2, widget._PANEL_RADIUS - 2)
    painter.setPen(QPen(widget._C_PANEL_LINE, max(1, scale_px(1, min_abs=1))))
    painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), widget._PANEL_RADIUS - 4, widget._PANEL_RADIUS - 4)


def draw_block(
    widget: "LahaiTetrisWidget",
    painter: QPainter,
    x: float,
    y: float,
    kind: str,
    size: float | None = None,
    active: bool = False,
) -> None:
    block = float(widget._block_size if size is None else size)
    if active and kind == _SUN_KIND:
        _draw_block_procedural(widget, painter, x, y, kind, size=size, active=True)
        return
    if active and kind in _THEME:
        glow = _get_active_glow_pixmap(widget, kind, block)
        if glow is not None and not glow.isNull():
            margin = (float(glow.width()) - block) * 0.5
            target = QRectF(x - margin, y - margin, float(glow.width()), float(glow.height()))
            painter.drawPixmap(target, glow, QRectF(glow.rect()))
    pixmap = _get_block_pixmap(widget, kind, size)
    if pixmap is not None and not pixmap.isNull():
        target = QRectF(x, y, float(pixmap.width()), float(pixmap.height()))
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        return
    _draw_block_procedural(widget, painter, x, y, kind, size=size, active=active)


def _get_block_pixmap(widget: "LahaiTetrisWidget", kind: str, size: float | None = None) -> QPixmap | None:
    block = int(round(widget._block_size if size is None else size))
    if block <= 0:
        return None
    cache_key = (kind, block)
    pixmap = widget._block_pixmap_cache.get(cache_key)
    if pixmap is not None:
        return pixmap
    pixmap = QPixmap(block, block)
    pixmap.fill(Qt.transparent)
    block_painter = QPainter(pixmap)
    block_painter.setRenderHint(QPainter.Antialiasing, True)
    block_painter.setRenderHint(QPainter.TextAntialiasing, True)
    block_painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    _draw_block_procedural(widget, block_painter, 0.0, 0.0, kind, size=float(block), active=False)
    block_painter.end()
    widget._block_pixmap_cache[cache_key] = pixmap
    return pixmap


def _get_active_glow_pixmap(widget: "LahaiTetrisWidget", kind: str, block_size: float) -> QPixmap | None:
    block = int(round(block_size))
    if block <= 0:
        return None
    cache_key = (f"__active_glow__:{kind}", block)
    cached = widget._block_pixmap_cache.get(cache_key)
    if cached is not None:
        return cached
    extent = max(block, int(math.ceil(block * 1.84)))
    pixmap = QPixmap(extent, extent)
    pixmap.fill(Qt.transparent)
    glow_painter = QPainter(pixmap)
    glow_painter.setRenderHint(QPainter.Antialiasing, True)
    base = QColor(_THEME[kind][0])
    center = QPointF(extent * 0.5, extent * 0.5)
    gradient = QRadialGradient(center, extent * 0.48)
    glow_core = QColor(base)
    glow_mid = QColor(base)
    glow_edge = QColor(base)
    glow_core.setAlpha(138)
    glow_mid.setAlpha(72)
    glow_edge.setAlpha(0)
    gradient.setColorAt(0.0, glow_core)
    gradient.setColorAt(0.58, glow_mid)
    gradient.setColorAt(1.0, glow_edge)
    glow_painter.setPen(Qt.NoPen)
    glow_painter.setBrush(gradient)
    glow_painter.drawEllipse(QRectF(0.0, 0.0, float(extent), float(extent)))
    glow_painter.end()
    widget._block_pixmap_cache[cache_key] = pixmap
    return pixmap

def _draw_block_procedural(
    widget: "LahaiTetrisWidget",
    painter: QPainter,
    x: float,
    y: float,
    kind: str,
    size: float | None = None,
    active: bool = False,
) -> None:
    block = float(widget._block_size if size is None else size)
    outer = QRectF(x, y, block, block)
    if kind == _SPECIAL_FILL_KIND:
        _draw_special_fill_block(widget, painter, outer)
        return
    if kind == _SUN_KIND:
        _draw_sun_block(widget, painter, outer, active=active)
        return
    base, inner, letter = _THEME[kind]

    if active:
        painter.setPen(Qt.NoPen)
        glow_rect = outer.adjusted(-block * 0.42, -block * 0.42, block * 0.42, block * 0.42)
        gradient = QRadialGradient(glow_rect.center(), glow_rect.width() * 0.48)
        glow_core = QColor(base)
        glow_mid = QColor(base)
        glow_edge = QColor(base)
        glow_core.setAlpha(138)
        glow_mid.setAlpha(72)
        glow_edge.setAlpha(0)
        gradient.setColorAt(0.0, glow_core)
        gradient.setColorAt(0.58, glow_mid)
        gradient.setColorAt(1.0, glow_edge)
        painter.setBrush(gradient)
        painter.drawEllipse(glow_rect)

    path = QPainterPath()
    radius = max(4.0, block * 0.22)
    path.addRoundedRect(outer, radius, radius)
    painter.fillPath(path, base)
    painter.setPen(QPen(letter.lighter(115), max(1, int(block * 0.07))))
    painter.drawPath(path)

    inner_rect = outer.adjusted(block * 0.12, block * 0.12, -block * 0.12, -block * 0.12)
    inner_path = QPainterPath()
    inner_path.addRoundedRect(inner_rect, radius * 0.72, radius * 0.72)
    painter.fillPath(inner_path, inner)

    glow_rect = inner_rect.adjusted(block * 0.02, block * 0.01, 0, 0)
    glow_color = QColor(letter)
    glow_color.setAlpha(105)
    for offset in ((0.0, 0.0), (0.8, 0.0), (-0.8, 0.0), (0.0, 0.8), (0.0, -0.8)):
        painter.setPen(glow_color)
        painter.setFont(widget._block_font)
        painter.drawText(
            glow_rect.adjusted(offset[0], offset[1], offset[0], offset[1]),
            Qt.AlignCenter,
            kind,
        )

    painter.setPen(letter)
    painter.setFont(widget._block_font)
    painter.drawText(inner_rect, Qt.AlignCenter, kind)


def _draw_special_fill_block(widget: "LahaiTetrisWidget", painter: QPainter, outer: QRectF) -> None:
    block = outer.width()
    radius = max(4.0, block * 0.22)
    path = QPainterPath()
    path.addRoundedRect(outer, radius, radius)
    base = QColor(8, 8, 10)
    inner = QColor(72, 12, 16)
    accent = QColor(255, 30, 30)
    painter.fillPath(path, base)
    painter.setPen(QPen(accent.lighter(110), max(1, int(block * 0.07))))
    painter.drawPath(path)

    inner_rect = outer.adjusted(block * 0.12, block * 0.12, -block * 0.12, -block * 0.12)
    inner_path = QPainterPath()
    inner_path.addRoundedRect(inner_rect, radius * 0.72, radius * 0.72)
    painter.fillPath(inner_path, inner)

    glow_rect = inner_rect.adjusted(block * 0.02, block * 0.01, 0, 0)
    glow_color = QColor(accent)
    glow_color.setAlpha(105)
    for offset in ((0.0, 0.0), (0.8, 0.0), (-0.8, 0.0), (0.0, 0.8), (0.0, -0.8)):
        painter.setPen(glow_color)
        painter.setFont(widget._block_font)
        painter.drawText(
            glow_rect.adjusted(offset[0], offset[1], offset[0], offset[1]),
            Qt.AlignCenter,
            "K",
        )

    painter.setPen(accent.lighter(120))
    painter.setFont(widget._block_font)
    painter.drawText(inner_rect, Qt.AlignCenter, "K")


def _draw_sun_block(widget: "LahaiTetrisWidget", painter: QPainter, outer: QRectF, *, active: bool = False) -> None:
    block = outer.width()
    base = QColor(214, 190, 128)
    inner = QColor(88, 67, 26)
    accent = QColor(255, 244, 214)
    outer_stroke = QColor(170, 248, 232)
    always_glow = True
    if active or always_glow:
        painter.setPen(Qt.NoPen)
        glow_rect = outer.adjusted(-block * 0.42, -block * 0.42, block * 0.42, block * 0.42)
        gradient = QRadialGradient(glow_rect.center(), glow_rect.width() * 0.48)
        glow_core = QColor(base)
        glow_mid = QColor(base)
        glow_edge = QColor(base)
        glow_core.setAlpha(156)
        glow_mid.setAlpha(88)
        glow_edge.setAlpha(0)
        gradient.setColorAt(0.0, glow_core)
        gradient.setColorAt(0.58, glow_mid)
        gradient.setColorAt(1.0, glow_edge)
        painter.setBrush(gradient)
        painter.drawEllipse(glow_rect)

    radius = max(4.0, block * 0.22)
    path = QPainterPath()
    stroke_width = max(1, int(block * 0.07)) + 1
    stroke_inset = stroke_width * 0.5
    stroke_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
    stroke_radius = max(1.0, radius - stroke_inset)
    path.addRoundedRect(outer, radius, radius)
    painter.fillPath(path, base)
    stroke_path = QPainterPath()
    stroke_path.addRoundedRect(stroke_rect, stroke_radius, stroke_radius)
    painter.setPen(QPen(outer_stroke, stroke_width))
    painter.drawPath(stroke_path)

    inner_rect = outer.adjusted(block * 0.12, block * 0.12, -block * 0.12, -block * 0.12)
    inner_path = QPainterPath()
    inner_path.addRoundedRect(inner_rect, radius * 0.72, radius * 0.72)
    painter.fillPath(inner_path, inner)

    center = inner_rect.center()
    painter.setPen(Qt.NoPen)
    petal_fill = QColor(242, 220, 156)
    petal_distance = block * 0.154
    petal_radius = block * 0.056
    for index in range(8):
        angle = math.tau * index / 8.0 - math.pi * 0.5
        px = center.x() + math.cos(angle) * petal_distance
        py = center.y() + math.sin(angle) * petal_distance
        painter.setBrush(petal_fill)
        painter.drawEllipse(
            QRectF(
                px - petal_radius,
                py - petal_radius,
                petal_radius * 2.0,
                petal_radius * 2.0,
            )
        )

    painter.setBrush(QColor(250, 233, 178))
    painter.drawEllipse(QRectF(center.x() - block * 0.16, center.y() - block * 0.16, block * 0.32, block * 0.32))
    painter.setBrush(accent)
    painter.drawEllipse(QRectF(center.x() - block * 0.112, center.y() - block * 0.112, block * 0.224, block * 0.224))
    painter.setBrush(QColor(255, 238, 188))
    painter.drawEllipse(QRectF(center.x() - block * 0.056, center.y() - block * 0.056, block * 0.112, block * 0.112))


def draw_preview(widget: "LahaiTetrisWidget", painter: QPainter, rect: QRectF) -> None:
    draw_round_panel(widget, painter, rect, widget._C_PANEL_ALT)
    header_h = float(max(scale_px(26, min_abs=1), widget._block_size * 0.9))
    title_rect = QRectF(
        rect.x() + widget._PADDING,
        rect.y() + widget._PADDING * 0.45,
        rect.width() - widget._PADDING * 2,
        header_h,
    )
    painter.setPen(widget._C_NEON)
    painter.setFont(widget._digit_font)
    painter.drawText(title_rect, Qt.AlignHCenter | Qt.AlignVCenter, "NEXT")
    if widget._next_piece is None:
        return

    cells = _SHAPES[widget._next_piece.kind]
    min_x = min(px for px, _ in cells)
    max_x = max(px for px, _ in cells)
    min_y = min(py for _, py in cells)
    max_y = max(py for _, py in cells)
    preview_rect = rect.adjusted(
        widget._PADDING,
        header_h + widget._PADDING * 0.65,
        -widget._PADDING,
        -widget._PADDING,
    )
    block = min(
        preview_rect.width() / _PREVIEW_GRID,
        preview_rect.height() / _PREVIEW_GRID,
    )
    origin_x = preview_rect.x() + (preview_rect.width() - (max_x - min_x + 1) * block) / 2
    origin_y = preview_rect.y() + (preview_rect.height() - (max_y - min_y + 1) * block) / 2
    for px, py in cells:
        draw_block(
            widget,
            painter,
            origin_x + (px - min_x) * block,
            origin_y + (py - min_y) * block,
            widget._next_piece.kind,
            size=block,
        )


def draw_warning_line(widget: "LahaiTetrisWidget", painter: QPainter, inner: QRectF) -> None:
    warning_row = _WARNING_LINE_ROW
    if warning_row <= 0 or warning_row >= _BOARD_H:
        return
    frequency_hz = (
        _WARNING_LINE_FLASH_HZ
        if widget._settled_stack_height() > _WARNING_LINE_FLASH_STACK_HEIGHT
        else _WARNING_LINE_DEFAULT_HZ
    )
    should_pulse = widget._settled_stack_height() > _WARNING_LINE_FLASH_STACK_HEIGHT
    pulse = (math.sin(time.monotonic() * math.tau * frequency_hz) + 1.0) * 0.5 if should_pulse else 0.5
    alpha_scale = 0.30 + pulse * 0.70
    y = inner.y() + warning_row * widget._block_size
    glow_h = max(6.0, widget._block_size * 0.45)
    glow_rect = QRectF(inner.x(), y - glow_h * 0.5, inner.width(), glow_h)
    gradient = QLinearGradient(glow_rect.left(), glow_rect.top(), glow_rect.left(), glow_rect.bottom())
    edge = QColor(255, 102, 112, 0)
    mid = QColor(255, 118, 128, int(92 * alpha_scale))
    core = QColor(255, 186, 190, int(168 * alpha_scale))
    gradient.setColorAt(0.0, edge)
    gradient.setColorAt(0.42, mid)
    gradient.setColorAt(0.5, core)
    gradient.setColorAt(0.58, mid)
    gradient.setColorAt(1.0, edge)
    painter.setPen(Qt.NoPen)
    painter.fillRect(glow_rect, QBrush(gradient))
    painter.setPen(QPen(QColor(255, 216, 218, int(145 * alpha_scale)), max(1, scale_px(1, min_abs=1))))
    painter.drawLine(int(inner.left()), int(round(y)), int(inner.right()), int(round(y)))


def draw_skill_slots(widget: "LahaiTetrisWidget", painter: QPainter) -> None:
    if not widget._skill_slots:
        return
    for index, rect in widget._skill_slots:
        painter.save()
        draw_round_panel(widget, painter, rect, QColor(63, 72, 158, 118))
        skill = widget._skills.get(index)
        if skill is not None and skill.avatar_filename:
            draw_avatar_skill_slot(
                widget,
                painter,
                rect,
                index,
                widget._load_skill_avatar(skill.avatar_filename),
                skill.cooldown_remaining(),
                highlighted=(widget._hovered_skill_slot_index == index and skill.cooldown_remaining() <= 0.0),
            )
            painter.restore()
            continue
        slot_inner = rect.adjusted(rect.width() * 0.08, rect.height() * 0.14, -rect.width() * 0.08, -rect.height() * 0.14)
        glow = QLinearGradient(slot_inner.left(), slot_inner.top(), slot_inner.right(), slot_inner.bottom())
        glow.setColorAt(0.0, QColor(255, 255, 255, 28))
        glow.setColorAt(1.0, QColor(91, 219, 255, 34))
        painter.setPen(Qt.NoPen)
        painter.fillRect(slot_inner, QBrush(glow))
        painter.setFont(widget._digit_font)
        painter.setPen(QColor(219, 245, 255, 220))
        painter.drawText(rect, Qt.AlignCenter, str(index))
        painter.restore()


def draw_avatar_skill_slot(
    widget: "LahaiTetrisWidget",
    painter: QPainter,
    rect: QRectF,
    index: int,
    avatar: QPixmap,
    remaining: float,
    highlighted: bool = False,
) -> None:
    painter.save()
    avatar_size = rect.width() * 0.68
    avatar_rect = QRectF(
        rect.center().x() - avatar_size * 0.5,
        rect.y() + rect.height() * 0.16,
        avatar_size,
        avatar_size,
    )
    draw_skill_avatar(widget, painter, avatar_rect, avatar, disabled=remaining > 0.0, highlighted=highlighted)

    key_rect = QRectF(rect.x(), rect.bottom() - rect.height() * 0.34, rect.width(), rect.height() * 0.30)
    key_font = get_digit_font(max(9, int(widget._block_size * 0.60)))
    painter.setFont(key_font)
    painter.setPen(QColor(232, 247, 255, 232))
    painter.drawText(key_rect, Qt.AlignHCenter | Qt.AlignVCenter, str(index))

    if remaining <= 0.0:
        painter.restore()
        return
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(16, 17, 28, 150))
    painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), max(4.0, rect.width() * 0.16), max(4.0, rect.width() * 0.16))
    painter.setFont(get_digit_font(max(10, int(widget._block_size * 0.62))))
    painter.setPen(QColor(246, 246, 248, 236))
    painter.drawText(rect, Qt.AlignCenter, str(int(math.ceil(remaining))))
    painter.restore()


def draw_skill_avatar(
    widget: "LahaiTetrisWidget",
    painter: QPainter,
    rect: QRectF,
    avatar: QPixmap,
    *,
    disabled: bool = False,
    highlighted: bool = False,
) -> None:
    path = QPainterPath()
    path.addEllipse(rect)
    if highlighted:
        glow_rect = rect.adjusted(-rect.width() * 0.12, -rect.height() * 0.12, rect.width() * 0.12, rect.height() * 0.12)
        glow = QRadialGradient(glow_rect.center(), glow_rect.width() * 0.5)
        glow.setColorAt(0.0, QColor(124, 246, 255, 92))
        glow.setColorAt(0.58, QColor(124, 246, 255, 46))
        glow.setColorAt(1.0, QColor(124, 246, 255, 0))
        painter.fillRect(glow_rect, glow)
    painter.save()
    painter.setClipPath(path)
    if not avatar.isNull():
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        source = QRectF(avatar.rect())
        side = min(source.width(), source.height())
        source = QRectF(
            source.center().x() - side * 0.5,
            source.center().y() - side * 0.5,
            side,
            side,
        )
        painter.drawPixmap(rect, avatar, source)
    else:
        painter.fillPath(path, QColor(235, 216, 255, 190))
        painter.setFont(get_ui_font(max(9, int(rect.width() * 0.28))))
        painter.setPen(QColor(83, 55, 118))
        painter.drawText(rect, Qt.AlignCenter, "星")
    if disabled:
        painter.fillPath(path, QColor(72, 72, 80, 172))
    feather = QRadialGradient(rect.center(), rect.width() * 0.52)
    feather.setColorAt(0.0, QColor(255, 255, 255, 0))
    feather.setColorAt(0.72, QColor(255, 255, 255, 0))
    feather.setColorAt(1.0, QColor(18, 21, 46, 178))
    painter.fillPath(path, QBrush(feather))
    painter.restore()
    ring_color = QColor(124, 246, 255, 236) if highlighted else QColor(238, 240, 255, 190)
    painter.setPen(QPen(ring_color, max(1, scale_px(1, min_abs=1))))
    painter.drawEllipse(rect)


def _paint_static_scene(widget: "LahaiTetrisWidget", painter: QPainter) -> None:
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.fillRect(widget.rect(), widget._C_BG)

    bg_step = scale_px(40, min_abs=1)
    painter.setPen(QPen(widget._C_BG_GRID, max(1, scale_px(1, min_abs=1))))
    for gx in range(0, widget.width() + bg_step, bg_step):
        painter.drawLine(gx, 0, gx, widget.height())
    for gy in range(0, widget.height() + bg_step, bg_step):
        painter.drawLine(0, gy, widget.width(), gy)

    glow_height = float(scale_px(100, min_abs=1))
    glow_top = max(0.0, float(widget.height()) - glow_height)
    bg_glow = QLinearGradient(0.0, float(widget.height()), 0.0, glow_top)
    glow_base = QColor(widget._C_BG_GLOW)
    glow_clear = QColor(widget._C_BG_GLOW)
    glow_clear.setAlpha(0)
    bg_glow.setColorAt(0.0, glow_base)
    bg_glow.setColorAt(1.0, glow_clear)
    painter.setPen(Qt.NoPen)
    painter.fillRect(QRectF(0.0, glow_top, float(widget.width()), float(widget.height()) - glow_top), bg_glow)

    title_rect = QRectF(float(widget._PADDING), float(widget._PADDING), float(widget.width() - widget._PADDING * 2), float(widget._HEADER_H))
    painter.setPen(widget._C_TEXT)
    painter.setFont(widget._title_font)
    painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, "拉海洛方块")
    painter.setPen(widget._C_NEON)
    painter.setFont(widget._digit_font)
    painter.drawText(title_rect, Qt.AlignRight | Qt.AlignVCenter, "LAHAI ROI BLOCKS")


def paint_widget(widget: "LahaiTetrisWidget", _event=None, *, painter: QPainter | None = None) -> None:
    owns_painter = painter is None
    if painter is None:
        painter = QPainter(widget)
    cache_size = widget.size()
    static_cache = widget._static_scene_cache
    if widget._static_scene_cache_dirty or static_cache is None or static_cache.size() != cache_size:
        static_cache = QPixmap(cache_size)
        static_cache.fill(Qt.transparent)
        cache_painter = QPainter(static_cache)
        _paint_static_scene(widget, cache_painter)
        cache_painter.end()
        widget._static_scene_cache = static_cache
        widget._static_scene_cache_dirty = False
    painter.drawPixmap(0, 0, static_cache)

    board_rect = widget._board_screen_rect()
    inner = widget._board_inner_screen_rect()
    draw_round_panel(widget, painter, board_rect, widget._C_BOARD)
    inner_path = QPainterPath()
    inner_path.addRoundedRect(inner, widget._PANEL_RADIUS * 0.7, widget._PANEL_RADIUS * 0.7)
    painter.fillPath(inner_path, widget._C_BOARD_INNER)
    painter.setPen(QPen(QColor(212, 221, 255, 155), max(1, scale_px(1, min_abs=1))))
    painter.drawRoundedRect(inner, widget._PANEL_RADIUS * 0.7, widget._PANEL_RADIUS * 0.7)

    painter.save()
    painter.setClipRect(inner)
    painter.setPen(QPen(widget._C_GRID, 1))
    inner_x = int(round(inner.x()))
    inner_y = int(round(inner.y()))
    inner_w = int(round(inner.width()))
    inner_h = int(round(inner.height()))
    for col in range(_BOARD_W + 1):
        gx = inner_x + col * widget._block_size
        painter.drawLine(gx, inner_y, gx, inner_y + inner_h)
    for row in range(_BOARD_H + 1):
        gy = inner_y + row * widget._block_size
        painter.drawLine(inner_x, gy, inner_x + inner_w, gy)
    for row in range(_BOARD_H):
        sy = inner_y + row * widget._block_size + widget._block_size * 0.5
        painter.fillRect(QRectF(inner.x(), sy, inner.width(), 1), widget._C_SCANLINE)
    painter.restore()

    inner = widget._board_inner_screen_rect()
    painter.save()
    painter.setClipRect(inner)
    draw_warning_line(widget, painter, inner)
    painter.restore()

    settled_board_cache = widget._ensure_settled_board_cache()
    if settled_board_cache is not None and not settled_board_cache.isNull():
        painter.drawPixmap(
            QRectF(inner.x(), inner.y(), inner.width(), inner.height()),
            settled_board_cache,
            QRectF(settled_board_cache.rect()),
        )

    if widget._settled_anim.state() != QVariantAnimation.Stopped:
        fall_progress = float(widget._settled_anim.currentValue() or 0.0)
        for _final_pos, (cell, fx, from_y, to_y) in widget._settled_fall_anim.items():
            render_y = from_y + (to_y - from_y) * fall_progress
            draw_block(
                widget,
                painter,
                inner.x() + fx * widget._block_size,
                inner.y() + render_y * widget._block_size,
                cell,
            )

    if widget._fill_anim.state() != QVariantAnimation.Stopped:
        reveal_progress = max(0.0, min(1.0, float(widget._fill_anim_progress)))
        total_cells = max(1, len(widget._fill_anim_cells))
        scaled_progress = reveal_progress * total_cells
        for index, (fx, fy) in enumerate(widget._fill_anim_cells):
            cell_progress = max(0.0, min(1.0, scaled_progress - index))
            if cell_progress <= 0.0:
                continue
            cell_top = inner.y() + fy * widget._block_size
            reveal_h = widget._block_size * cell_progress
            if reveal_h <= 0.0:
                continue
            visible_rect = QRectF(
                inner.x() + fx * widget._block_size,
                cell_top + widget._block_size - reveal_h,
                widget._block_size,
                reveal_h,
            )
            painter.save()
            painter.setClipRect(visible_rect)
            draw_block(
                widget,
                painter,
                inner.x() + fx * widget._block_size,
                cell_top,
                _SPECIAL_FILL_KIND,
            )
            painter.restore()

    if widget._current is not None:
        for x, y in widget._current_render_cells():
            if y < 0:
                continue
            draw_block(
                widget,
                painter,
                inner.x() + x * widget._block_size,
                inner.y() + y * widget._block_size,
                widget._current.kind,
                active=True,
            )

    draw_preview(widget, painter, widget._preview_rect)

    stat_values = {
        "分数": str(widget._score).zfill(6),
        "消行": str(widget._lines).zfill(6),
        "等级": str(widget._level).zfill(6),
        "连击": str(widget._combo).zfill(6),
    }
    for label, rect in widget._stat_cards:
        draw_round_panel(widget, painter, rect, widget._C_PANEL)
        inset_x = max(10, int(widget._block_size * 0.36))
        inset_y = max(4, int(widget._block_size * 0.14))
        text_rect = rect.adjusted(inset_x, inset_y, -inset_x, -inset_y)
        painter.setPen(widget._C_TEXT)
        painter.setFont(widget._stat_label_font)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, f"{label}：")
        painter.setFont(widget._stat_digit_font)
        painter.setPen(widget._C_NEON)
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, stat_values[label])

    draw_skill_slots(widget, painter)

    for text, rect in (("暂停" if not widget._paused else "继续", widget._pause_rect), ("退出", widget._exit_rect)):
        draw_round_panel(widget, painter, rect, widget._C_PANEL)
        painter.setPen(widget._C_TEXT)
        painter.setFont(widget._label_font)
        painter.drawText(rect, Qt.AlignCenter, text)

    help_rect = widget._help_rect
    draw_round_panel(widget, painter, help_rect, widget._C_PANEL)
    content_rect = help_rect.adjusted(widget._PADDING, widget._PADDING, -widget._PADDING, -widget._PADDING)
    section_gap = max(6.0, widget._block_size * 0.26)
    line_gap = max(3.0, widget._block_size * 0.10)
    title_gap = max(5.0, widget._block_size * 0.18)
    control_title_font = get_digit_font(max(12, int(widget._block_size * 0.58)))
    control_title_font.setBold(True)
    tips_title_font = get_digit_font(max(11, int(widget._block_size * 0.50)))
    tips_title_font.setBold(True)
    control_body_font = get_ui_font(max(10, int(widget._block_size * 0.44)))
    control_body_font.setBold(True)
    tip_body_font = get_ui_font(max(11, int(widget._block_size * 0.46)))
    tip_body_font.setBold(True)
    control_lines = [
        "WASD/↑↓←→ 移动方块",
        "space/空格 速降方块",
        "Esc/P 暂停游戏",
        "1~6/鼠标点击 使用角色技能",
        "B 暂停/继续bgm",
	"F11 全屏/退出全屏",
    ]
    title_h = max(widget._ROW_H(), float(scale_px(26, min_abs=1)))
    best_score_h = widget._ROW_H()
    tip_title_h = widget._ROW_H()
    tip_body_h = max(widget._ROW_H() * 2.45, widget._block_size * 2.7)
    tip_rect = QRectF(
        content_rect.x(),
        content_rect.bottom() - tip_body_h,
        content_rect.width(),
        tip_body_h,
    )
    tip_title_rect = QRectF(
        content_rect.x(),
        tip_rect.y() - title_gap - tip_title_h,
        content_rect.width(),
        tip_title_h,
    )
    best_score_rect = QRectF(
        content_rect.x(),
        tip_title_rect.y() - section_gap - best_score_h,
        content_rect.width(),
        best_score_h,
    )
    control_title_rect = QRectF(
        content_rect.x(),
        content_rect.y(),
        content_rect.width(),
        title_h,
    )
    control_text_rect = QRectF(
        content_rect.x(),
        control_title_rect.bottom() + title_gap,
        content_rect.width(),
        max(0.0, best_score_rect.y() - section_gap - (control_title_rect.bottom() + title_gap)),
    )

    painter.setPen(widget._C_NEON)
    painter.setFont(control_title_font)
    painter.drawText(control_title_rect, Qt.AlignLeft | Qt.AlignTop, "CONTROL")

    painter.setPen(widget._C_TEXT_SUB)
    painter.setFont(control_body_font)
    painter.drawText(
        control_text_rect.adjusted(0.0, line_gap, 0.0, 0.0),
        Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
        "\n".join(control_lines),
    )

    painter.setFont(widget._label_font)
    painter.setPen(widget._C_TEXT)
    painter.drawText(
        best_score_rect,
        Qt.AlignHCenter | Qt.AlignVCenter,
        f"最高分：{str(widget._best_score).zfill(6)}",
    )

    painter.setPen(widget._C_NEON)
    painter.setFont(tips_title_font)
    painter.drawText(tip_title_rect, Qt.AlignLeft | Qt.AlignVCenter, "TIPS")
    painter.setPen(widget._C_TEXT_SUB)
    painter.setFont(tip_body_font)
    painter.drawText(tip_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, widget._current_tip)

    if widget._game_over:
        painter.fillRect(inner, widget._C_GAMEOVER)
        painter.setPen(QColor(245, 242, 255))
        painter.setFont(widget._digit_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.28, inner.width(), widget._ROW_H() * 2),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "GAME OVER",
        )
        painter.setFont(widget._title_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.44, inner.width(), widget._ROW_H() * 3),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "按 空格 或 回车 重开",
        )
    elif widget._paused:
        painter.fillRect(inner, QColor(18, 14, 40, 142))
        painter.setPen(QColor(245, 242, 255))
        painter.setFont(widget._digit_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.30, inner.width(), widget._ROW_H() * 2),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "PAUSED",
        )
        painter.setFont(widget._title_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.46, inner.width(), widget._ROW_H() * 2),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "按 P 继续",
        )
    elif widget._awaiting_start:
        painter.fillRect(inner, QColor(18, 14, 40, 152))
        painter.setPen(QColor(245, 242, 255))
        painter.setFont(widget._title_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.22, inner.width(), widget._ROW_H() * 2),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "按任意键开始",
        )
        painter.setFont(widget._digit_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.40, inner.width(), widget._ROW_H() * 2),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "PRESS ANY KEY",
        )
        painter.setFont(widget._title_font)
        painter.drawText(
            QRectF(inner.x(), inner.y() + inner.height() * 0.56, inner.width(), widget._ROW_H() * 3),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "TO START",
        )

    if owns_painter:
        painter.end()
