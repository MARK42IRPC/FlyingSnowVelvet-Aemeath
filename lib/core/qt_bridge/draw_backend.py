"""PyQt5 implementation of the resource draw backend."""
from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QImage, QPainter, QPixmap, QTransform

from lib.core.graphics.commands import DrawRequest
from lib.core.graphics.scene import DrawScene
from lib.core.graphics.types import Point


class QtDrawBackend:
    """Convert backend-neutral draw requests into QPainter operations."""

    def __init__(self) -> None:
        self._frame_pixmap_cache: dict[tuple[str, int], QPixmap] = {}
        self._render_pixmap_cache: dict[tuple[str, int, int, int, bool], QPixmap] = {}

    def render(self, scene: DrawScene, painter: object, target_rect: object | None = None) -> None:
        if not scene.get_active_resource_ids():
            return

        qt_painter = painter
        qt_painter.save()
        qt_painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        try:
            for request in scene.ordered_requests():
                frame_index = self._resolve_frame_index(scene, request)
                frame = scene.get_frame(request.resource_id, frame_index)
                if frame is None:
                    continue

                base_pixmap = self._get_base_pixmap(request.resource_id, frame_index, frame)
                if base_pixmap.isNull():
                    continue

                if target_rect:
                    draw_w = max(1, target_rect.width())
                    draw_h = max(1, target_rect.height())
                else:
                    draw_w = max(1, int(round(base_pixmap.width() * request.scale)))
                    draw_h = max(1, int(round(base_pixmap.height() * request.scale)))

                pixmap = self._get_render_pixmap(
                    request.resource_id,
                    frame_index,
                    draw_w,
                    draw_h,
                    request.flipped,
                    base_pixmap,
                )
                draw_rect = self._resolve_draw_rect(request, pixmap, target_rect)

                if abs(request.alpha - 1.0) > 1e-4:
                    qt_painter.save()
                    qt_painter.setOpacity(request.alpha)
                    qt_painter.drawPixmap(draw_rect, pixmap)
                    qt_painter.restore()
                else:
                    qt_painter.drawPixmap(draw_rect, pixmap)
        finally:
            qt_painter.restore()

    def cleanup(self) -> None:
        self._frame_pixmap_cache.clear()
        self._render_pixmap_cache.clear()

    def _resolve_frame_index(self, scene: DrawScene, request: DrawRequest) -> int:
        if request.frame_index == -1:
            return scene.get_current_frame_index(request.resource_id)
        return request.frame_index

    def _get_base_pixmap(self, resource_id: str, frame_index: int, frame: object) -> QPixmap:
        key = (resource_id, frame_index)
        cached = self._frame_pixmap_cache.get(key)
        if cached is not None:
            return cached
        if not isinstance(frame, QImage):
            return QPixmap()
        pixmap = QPixmap.fromImage(frame)
        self._frame_pixmap_cache[key] = pixmap
        return pixmap

    def _get_render_pixmap(
        self,
        resource_id: str,
        frame_index: int,
        draw_w: int,
        draw_h: int,
        flipped: bool,
        base_pixmap: QPixmap,
    ) -> QPixmap:
        key = (resource_id, frame_index, draw_w, draw_h, flipped)
        cached = self._render_pixmap_cache.get(key)
        if cached is not None:
            return cached

        pixmap = base_pixmap
        if draw_w != pixmap.width() or draw_h != pixmap.height():
            pixmap = pixmap.scaled(draw_w, draw_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if flipped:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1), Qt.SmoothTransformation)

        self._render_pixmap_cache[key] = pixmap
        return pixmap

    def _resolve_draw_rect(
        self,
        request: DrawRequest,
        pixmap: QPixmap,
        target_rect: object | None,
    ) -> QRect:
        if target_rect:
            draw_rect = QRect(QPoint(0, 0), pixmap.size())
            draw_rect.moveCenter(target_rect.center())
            return draw_rect

        position = request.position
        if isinstance(position, Point):
            return QRect(QPoint(int(position.x), int(position.y)), pixmap.size())
        if isinstance(position, tuple):
            return QRect(QPoint(*position), pixmap.size())
        if position is not None:
            return QRect(QPoint(position), pixmap.size())
        return QRect(QPoint(0, 0), pixmap.size())
