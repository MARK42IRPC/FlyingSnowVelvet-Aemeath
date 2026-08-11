"""PyQt5 implementation of immutable declarative draw command batches."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QTransform

from lib.core.graphics.commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    EllipseCommand,
    LineCommand,
    RectCommand,
    SpriteCommand,
    TextCommand,
    TransformPop,
    TransformPush,
)
from lib.core.graphics.types import Color, Rect
from lib.core.qt_bridge.gif_loader import qimage_from_raster_frame


class QtDrawBackend:
    """Convert backend-neutral draw requests into QPainter operations."""

    def __init__(self) -> None:
        self._frame_pixmap_cache: dict[tuple[str, int, int], QPixmap] = {}
        self._render_pixmap_cache: dict[tuple[str, int, int, int, int, bool], QPixmap] = {}
        self._resource_revisions: dict[str, int] = {}

    def render(
        self,
        batch: DrawBatch,
        target: object,
        viewport: Rect | None = None,
    ) -> None:
        self._sync_resource_cache(batch)
        if not batch.commands:
            return

        qt_painter = target
        qt_painter.save()
        qt_painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        state_stack: list[type] = []
        try:
            for command in batch.commands:
                if isinstance(command, SpriteCommand):
                    self._draw_sprite(qt_painter, command, viewport)
                elif isinstance(command, TextCommand):
                    self._draw_text(qt_painter, command)
                elif isinstance(command, LineCommand):
                    self._draw_line(qt_painter, command)
                elif isinstance(command, EllipseCommand):
                    self._draw_shape(qt_painter, command, ellipse=True)
                elif isinstance(command, RectCommand):
                    self._draw_shape(qt_painter, command, ellipse=False)
                elif isinstance(command, (ClipPush, TransformPush)):
                    qt_painter.save()
                    state_stack.append(type(command))
                    if isinstance(command, ClipPush):
                        qt_painter.setClipRect(self._rectf(command.rect), Qt.IntersectClip)
                    else:
                        qt_painter.setTransform(QTransform(*command.matrix), True)
                elif isinstance(command, (ClipPop, TransformPop)):
                    expected_push = ClipPush if isinstance(command, ClipPop) else TransformPush
                    if not state_stack or state_stack[-1] is not expected_push:
                        raise ValueError("draw batch pop command has no matching push")
                    qt_painter.restore()
                    state_stack.pop()
        finally:
            while state_stack:
                qt_painter.restore()
                state_stack.pop()
            qt_painter.restore()

    def _draw_sprite(self, painter, command: SpriteCommand, viewport: Rect | None) -> None:
        self._discard_stale_resource_cache(command)
        base_pixmap = self._get_base_pixmap(command)
        if base_pixmap.isNull():
            return

        if command.target_size is not None:
            draw_w = max(1, int(round(command.target_size.width)))
            draw_h = max(1, int(round(command.target_size.height)))
        else:
            draw_w = max(1, int(round(base_pixmap.width() * command.scale)))
            draw_h = max(1, int(round(base_pixmap.height() * command.scale)))

        pixmap = self._get_render_pixmap(command, draw_w, draw_h, base_pixmap)
        draw_rect = self._resolve_draw_rect(command, pixmap)
        painter.save()
        painter.setOpacity(command.alpha)
        painter.drawPixmap(draw_rect, pixmap)
        painter.restore()

    @staticmethod
    def _color(value: Color, alpha: float = 1.0) -> QColor:
        return QColor(value.red, value.green, value.blue, round(value.alpha * alpha))

    @staticmethod
    def _rectf(rect: Rect) -> QRectF:
        return QRectF(float(rect.x), float(rect.y), float(rect.width), float(rect.height))

    def _draw_text(self, painter, command: TextCommand) -> None:
        font = QFont(command.font.family)
        font.setPixelSize(command.font.pixel_size)
        font.setBold(command.font.bold)
        painter.save()
        painter.setOpacity(command.alpha)
        painter.setFont(font)
        painter.setPen(self._color(command.color))
        painter.drawText(self._rectf(command.rect), int(command.alignment), command.text)
        painter.restore()

    def _draw_line(self, painter, command: LineCommand) -> None:
        painter.save()
        painter.setOpacity(command.alpha)
        pen = QPen(self._color(command.color))
        pen.setWidthF(command.width)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(command.start.x, command.start.y),
            QPointF(command.end.x, command.end.y),
        )
        painter.restore()

    def _draw_shape(self, painter, command: RectCommand, *, ellipse: bool) -> None:
        painter.save()
        painter.setOpacity(command.alpha)
        if command.fill is None:
            painter.setBrush(Qt.NoBrush)
        else:
            painter.setBrush(self._color(command.fill))
        if command.stroke is None or command.stroke_width <= 0.0:
            painter.setPen(Qt.NoPen)
        else:
            pen = QPen(self._color(command.stroke))
            pen.setWidthF(command.stroke_width)
            painter.setPen(pen)
        rect = self._rectf(command.rect)
        if ellipse:
            painter.drawEllipse(rect)
        else:
            painter.drawRect(rect)
        painter.restore()

    def cleanup(self) -> None:
        self._frame_pixmap_cache.clear()
        self._render_pixmap_cache.clear()
        self._resource_revisions.clear()

    def _sync_resource_cache(self, batch: DrawBatch) -> None:
        active_revisions = {
            resource.resource_id: resource.revision
            for resource in batch.resource_revisions
        }
        for resource_id, current_revision in tuple(self._resource_revisions.items()):
            if active_revisions.get(resource_id) == current_revision:
                continue
            self._discard_resource_cache(resource_id)
            self._resource_revisions.pop(resource_id, None)

    def _discard_resource_cache(self, resource_id: str) -> None:
        self._frame_pixmap_cache = {
            key: value
            for key, value in self._frame_pixmap_cache.items()
            if key[0] != resource_id
        }
        self._render_pixmap_cache = {
            key: value
            for key, value in self._render_pixmap_cache.items()
            if key[0] != resource_id
        }

    def _discard_stale_resource_cache(self, command: SpriteCommand) -> None:
        current = self._resource_revisions.get(command.resource_id)
        if current == command.resource_revision:
            return
        self._discard_resource_cache(command.resource_id)
        self._resource_revisions[command.resource_id] = command.resource_revision

    def _get_base_pixmap(self, command: SpriteCommand) -> QPixmap:
        key = (command.resource_id, command.resource_revision, command.frame_index)
        cached = self._frame_pixmap_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap.fromImage(qimage_from_raster_frame(command.frame))
        self._frame_pixmap_cache[key] = pixmap
        return pixmap

    def _get_render_pixmap(
        self,
        command: SpriteCommand,
        draw_w: int,
        draw_h: int,
        base_pixmap: QPixmap,
    ) -> QPixmap:
        key = (
            command.resource_id,
            command.resource_revision,
            command.frame_index,
            draw_w,
            draw_h,
            command.flipped,
        )
        cached = self._render_pixmap_cache.get(key)
        if cached is not None:
            return cached

        pixmap = base_pixmap
        if draw_w != pixmap.width() or draw_h != pixmap.height():
            pixmap = pixmap.scaled(draw_w, draw_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if command.flipped:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1), Qt.SmoothTransformation)

        self._render_pixmap_cache[key] = pixmap
        return pixmap

    def _resolve_draw_rect(
        self,
        command: SpriteCommand,
        pixmap: QPixmap,
    ) -> QRect:
        position = command.position
        if position is not None:
            return QRect(QPoint(int(position.x), int(position.y)), pixmap.size())
        return QRect(QPoint(0, 0), pixmap.size())
