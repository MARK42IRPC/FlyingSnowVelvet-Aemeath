"""PyQt5 implementation of immutable sprite command batches."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QPainter, QPixmap, QTransform

from lib.core.graphics.commands import DrawBatch, SpriteCommand
from lib.core.graphics.types import Rect
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
        try:
            for command in batch.commands:
                self._discard_stale_resource_cache(command)
                base_pixmap = self._get_base_pixmap(command)
                if base_pixmap.isNull():
                    continue

                if viewport is not None:
                    draw_w = max(1, int(round(viewport.width)))
                    draw_h = max(1, int(round(viewport.height)))
                else:
                    draw_w = max(1, int(round(base_pixmap.width() * command.scale)))
                    draw_h = max(1, int(round(base_pixmap.height() * command.scale)))

                pixmap = self._get_render_pixmap(
                    command,
                    draw_w,
                    draw_h,
                    base_pixmap,
                )
                draw_rect = self._resolve_draw_rect(command, pixmap, viewport)

                if abs(command.alpha - 1.0) > 1e-4:
                    qt_painter.save()
                    qt_painter.setOpacity(command.alpha)
                    qt_painter.drawPixmap(draw_rect, pixmap)
                    qt_painter.restore()
                else:
                    qt_painter.drawPixmap(draw_rect, pixmap)
        finally:
            qt_painter.restore()

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
        viewport: Rect | None,
    ) -> QRect:
        if viewport is not None:
            draw_rect = QRect(QPoint(0, 0), pixmap.size())
            target = QRect(
                int(round(viewport.x)),
                int(round(viewport.y)),
                int(round(viewport.width)),
                int(round(viewport.height)),
            )
            draw_rect.moveCenter(target.center())
            return draw_rect

        position = command.position
        if position is not None:
            return QRect(QPoint(int(position.x), int(position.y)), pixmap.size())
        return QRect(QPoint(0, 0), pixmap.size())
