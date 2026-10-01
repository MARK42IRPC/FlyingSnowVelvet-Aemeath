"""PyQt5 implementation of immutable declarative draw command batches."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)

from lib.core.render.visuals.commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    EllipseCommand,
    LineCommand,
    LinearGradientFill,
    PathCommand,
    RectCommand,
    SpriteCommand,
    StrokeCap,
    StrokeJoin,
    TextCommand,
    TransformPop,
    TransformPush,
)
from lib.core.render.visuals.types import Color, Rect
from lib.core.render.backends.qt.gif_loader import qimage_from_raster_frame


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
                elif isinstance(command, PathCommand):
                    self._draw_path(qt_painter, command)
                elif isinstance(command, (ClipPush, TransformPush)):
                    qt_painter.save()
                    state_stack.append(type(command))
                    if isinstance(command, ClipPush):
                        qt_painter.setRenderHint(
                            QPainter.Antialiasing, bool(command.antialias)
                        )
                        qt_painter.setClipPath(
                            self._clip_path(command), Qt.IntersectClip
                        )
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
        pen = self._pen(command.color, command.width, command.cap, int(StrokeJoin.MITER))
        painter.setPen(pen)
        painter.drawLine(
            QPointF(command.start.x, command.start.y),
            QPointF(command.end.x, command.end.y),
        )
        painter.restore()

    @staticmethod
    def _cap(value: int) -> int:
        return {
            int(StrokeCap.ROUND): Qt.RoundCap,
            int(StrokeCap.SQUARE): Qt.SquareCap,
        }.get(int(value) & (int(StrokeCap.ROUND) | int(StrokeCap.SQUARE)), Qt.FlatCap)

    @staticmethod
    def _join(value: int) -> int:
        return {
            int(StrokeJoin.ROUND): Qt.RoundJoin,
            int(StrokeJoin.BEVEL): Qt.BevelJoin,
        }.get(int(value) & (int(StrokeJoin.ROUND) | int(StrokeJoin.BEVEL)), Qt.MiterJoin)

    def _brush(self, fill) -> object:
        """Resolve a solid color or a shared gradient description."""
        if fill is None:
            return Qt.NoBrush
        if isinstance(fill, LinearGradientFill):
            gradient = QLinearGradient(
                QPointF(fill.start.x, fill.start.y),
                QPointF(fill.end.x, fill.end.y),
            )
            for stop in fill.stops:
                gradient.setColorAt(stop.position, self._color(stop.color))
            return gradient
        return self._color(fill)

    def _pen(self, color, width: float, cap: int, join: int) -> QPen:
        pen = QPen(self._color(color))
        pen.setWidthF(width)
        pen.setCapStyle(self._cap(cap))
        pen.setJoinStyle(self._join(join))
        return pen

    def _path(self, segments) -> QPainterPath:
        path = QPainterPath()
        for index, segment in enumerate(segments):
            if index == 0 or path.elementCount() == 0:
                path.moveTo(QPointF(segment.start.x, segment.start.y))
            if segment.curve is not None:
                path.quadTo(
                    QPointF(segment.curve.x, segment.curve.y),
                    QPointF(segment.end.x, segment.end.y),
                )
            else:
                path.lineTo(QPointF(segment.end.x, segment.end.y))
        return path

    def _clip_path(self, command: ClipPush) -> QPainterPath:
        if command.path:
            return self._path(command.path)
        path = QPainterPath()
        rect = self._rectf(command.rect)
        if command.radius > 0.0:
            path.addRoundedRect(rect, command.radius, command.radius)
        else:
            path.addRect(rect)
        return path

    def _draw_shape(self, painter, command: RectCommand, *, ellipse: bool) -> None:
        painter.save()
        painter.setOpacity(command.alpha)
        painter.setRenderHint(QPainter.Antialiasing, bool(command.antialias))
        painter.setBrush(self._brush(command.fill))
        if command.stroke is None or command.stroke_width <= 0.0:
            painter.setPen(Qt.NoPen)
        else:
            painter.setPen(
                self._pen(command.stroke, command.stroke_width, command.stroke_cap, command.stroke_join)
            )
        rect = self._rectf(command.rect)
        if ellipse:
            painter.drawEllipse(rect)
        elif command.radius > 0.0:
            painter.drawRoundedRect(rect, command.radius, command.radius)
        else:
            painter.drawRect(rect)
        painter.restore()

    def _draw_path(self, painter, command: PathCommand) -> None:
        path = self._path(command.segments)
        if path.elementCount() == 0:
            return
        painter.save()
        painter.setOpacity(command.alpha)
        painter.setRenderHint(QPainter.Antialiasing, bool(command.antialias))
        painter.setBrush(self._brush(command.fill))
        if command.stroke is None or command.stroke_width <= 0.0:
            painter.setPen(Qt.NoPen)
        else:
            painter.setPen(
                self._pen(command.stroke, command.stroke_width, command.stroke_cap, command.stroke_join)
            )
        painter.drawPath(path)
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
