"""特效系统覆盖层。

当前版本先支持图片类 effect instance：
- 事件驱动申请
- tick 推进逻辑状态
- frame 插值绘制
- 资源路径 -> QPixmap 缓存
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.core.topmost_manager import TOPMOST_PRIORITY_OVERLAY, get_topmost_manager
from lib.script.effects.manager import cleanup_effect_script_manager, get_effect_script_manager


_logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


def _effect_alive(effect) -> bool:
    alive = getattr(effect, "alive", True)
    try:
        return bool(alive() if callable(alive) else alive)
    except Exception:
        return False


def _prepare_effects_for_inplace_update(effects: list) -> None:
    for effect in effects:
        effect._tick_prev_age = float(getattr(effect, "age", 0.0))
        effect._tick_prev_x = float(getattr(effect, "_render_x", getattr(effect, "x", 0.0)))
        effect._tick_prev_y = float(getattr(effect, "_render_y", getattr(effect, "y", 0.0)))
        effect._tick_prev_opacity = float(getattr(effect, "_render_opacity", getattr(effect, "opacity", 1.0)))
        effect._tick_prev_scale = float(getattr(effect, "_render_scale", getattr(effect, "scale", 1.0)))
        effect._tick_prev_rotation = float(getattr(effect, "_render_rotation", getattr(effect, "rotation", 0.0)))


def _resolve_resource_path(resource_path: str) -> str:
    candidate = Path(str(resource_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    return str(candidate.resolve())


def _make_edge_feather_pixmap(
    source: QPixmap,
    feather_ratio: float,
    output_size: tuple[int, int] | None = None,
) -> QPixmap:
    src_w = max(1, source.width())
    src_h = max(1, source.height())
    if output_size is None:
        final_w = src_w
        final_h = src_h
    else:
        final_w = max(1, int(output_size[0]))
        final_h = max(1, int(output_size[1]))

    pixmap = source
    if pixmap.width() != final_w or pixmap.height() != final_h:
        pixmap = pixmap.scaled(
            final_w,
            final_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    result = QPixmap(pixmap.width(), pixmap.height())
    result.fill(Qt.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.drawPixmap(0, 0, pixmap)

    feather_ratio = max(0.0, min(0.45, float(feather_ratio)))
    if feather_ratio > 0.0:
        width = max(1, result.width())
        height = max(1, result.height())
        feather_px_x = max(1.0, width * feather_ratio)
        feather_px_y = max(1.0, height * feather_ratio)
        # 四边分别做一次 destination-in 线性梯度，让原图保持比例，仅边缘软化。
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)

        left_gradient = QLinearGradient(0.0, 0.0, feather_px_x, 0.0)
        left_gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        left_gradient.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(QRectF(0.0, 0.0, feather_px_x, float(height)), left_gradient)

        right_gradient = QLinearGradient(float(width) - feather_px_x, 0.0, float(width), 0.0)
        right_gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        right_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(float(width) - feather_px_x, 0.0, feather_px_x, float(height)), right_gradient)

        top_gradient = QLinearGradient(0.0, 0.0, 0.0, feather_px_y)
        top_gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        top_gradient.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(QRectF(0.0, 0.0, float(width), feather_px_y), top_gradient)

        bottom_gradient = QLinearGradient(0.0, float(height) - feather_px_y, 0.0, float(height))
        bottom_gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        bottom_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0.0, float(height) - feather_px_y, float(width), feather_px_y), bottom_gradient)

    painter.end()
    return result


def _get_cached_pixmap(resource_path: str, effect_options: dict | None = None) -> QPixmap | None:
    resolved_path = _resolve_resource_path(resource_path)
    options = dict(effect_options or {})
    edge_feather = bool(options.get("edge_feather", False))
    feather_ratio = float(options.get("feather_ratio", 0.12) or 0.12)
    output_size = options.get("masked_output_size")
    try:
        if isinstance(output_size, (list, tuple)) and len(output_size) >= 2:
            masked_output_size = (max(1, int(output_size[0])), max(1, int(output_size[1])))
        elif output_size is not None:
            size = max(1, int(output_size))
            masked_output_size = (size, size)
        else:
            masked_output_size = None
    except (TypeError, ValueError):
        masked_output_size = None

    cache_key = (resolved_path, edge_feather, round(feather_ratio, 4), masked_output_size)
    cached = _PIXMAP_CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    base_pixmap = QPixmap(resolved_path)
    if base_pixmap.isNull():
        _logger.warning("特效资源加载失败: %s", resolved_path)
        return None

    pixmap = base_pixmap
    if edge_feather:
        pixmap = _make_edge_feather_pixmap(
            base_pixmap,
            feather_ratio=feather_ratio,
            output_size=masked_output_size,
        )

    _PIXMAP_CACHE[cache_key] = pixmap
    return pixmap


class EffectOverlay(QWidget):
    """全屏透明覆盖层，仅用于绘制特效。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")
        get_topmost_manager().register(self, priority=TOPMOST_PRIORITY_OVERLAY)

        self._effects = []
        self._pending_requests = deque()
        self._needs_immediate_repaint = False
        self._event_center = get_event_center()
        self._effect_manager = get_effect_script_manager()

        self._event_center.subscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    def _on_effect_request(self, event: Event):
        data = event.data
        effect_id = data.get("effect_id")
        if not effect_id:
            return

        self._pending_requests.append({
            "effect_id": effect_id,
            "anchor_type": data.get("anchor_type", "point"),
            "anchor_data": data.get("anchor_data"),
            "effect_options": dict(data.get("effect_options") or {}),
        })
        event.mark_handled()

    def _on_tick(self, event: Event):
        self._drain_effect_requests()
        if not self._effects:
            return

        _prepare_effects_for_inplace_update(self._effects)

        alive_effects = []
        for effect in self._effects:
            try:
                effect.update()
            except Exception:
                continue
            if _effect_alive(effect):
                alive_effects.append(effect)

        self._effects = alive_effects
        if not self._effects:
            self._clear_and_hide()

    def _on_frame(self, event: Event):
        if not self._effects:
            return

        alpha = float((event.data or {}).get("tick_alpha", 1.0) or 0.0)
        alpha = max(0.0, min(1.0, alpha))
        for effect in self._effects:
            if hasattr(effect, "apply_frame_interpolation"):
                try:
                    effect.apply_frame_interpolation(alpha)
                    continue
                except Exception:
                    pass
            prev_x = float(getattr(effect, "_tick_prev_x", getattr(effect, "x", 0.0)))
            prev_y = float(getattr(effect, "_tick_prev_y", getattr(effect, "y", 0.0)))
            prev_opacity = float(getattr(effect, "_tick_prev_opacity", getattr(effect, "opacity", 1.0)))
            prev_scale = float(getattr(effect, "_tick_prev_scale", getattr(effect, "scale", 1.0)))
            prev_rotation = float(getattr(effect, "_tick_prev_rotation", getattr(effect, "rotation", 0.0)))
            cur_x = float(getattr(effect, "x", prev_x))
            cur_y = float(getattr(effect, "y", prev_y))
            cur_opacity = float(getattr(effect, "opacity", prev_opacity))
            cur_scale = float(getattr(effect, "scale", prev_scale))
            cur_rotation = float(getattr(effect, "rotation", prev_rotation))

            effect._render_x = prev_x + (cur_x - prev_x) * alpha
            effect._render_y = prev_y + (cur_y - prev_y) * alpha
            effect._render_opacity = prev_opacity + (cur_opacity - prev_opacity) * alpha
            effect._render_scale = prev_scale + (cur_scale - prev_scale) * alpha
            effect._render_rotation = prev_rotation + (cur_rotation - prev_rotation) * alpha

        get_topmost_manager().bring_to_front(self)
        self.update()

    def _drain_effect_requests(self) -> None:
        if not self._pending_requests:
            return

        if not self._effects:
            screen = self.screen().geometry() if self.screen() else self.geometry()
            self.setGeometry(screen)

        offset_x = float(self.geometry().x())
        offset_y = float(self.geometry().y())
        had_effects = bool(self._effects)
        appended = False

        while self._pending_requests:
            request = self._pending_requests.popleft()
            effect_id = request["effect_id"]
            anchor_type = request["anchor_type"]
            anchor_data = request["anchor_data"]
            effect_options = dict(request["effect_options"])

            script = self._effect_manager.get_script(effect_id)
            if script is None:
                continue

            resource_path = effect_options.get("resource_path")
            if resource_path and "pixmap" not in effect_options:
                pixmap = _get_cached_pixmap(str(resource_path), effect_options)
                if pixmap is None:
                    continue
                effect_options["pixmap"] = pixmap
                effect_options["resolved_resource_path"] = _resolve_resource_path(str(resource_path))

            if anchor_type == "rect" and isinstance(anchor_data, (list, tuple)) and len(anchor_data) >= 4:
                x1, y1, x2, y2 = anchor_data
                local_anchor_data = (x1 - offset_x, y1 - offset_y, x2 - offset_x, y2 - offset_y)
            elif anchor_type == "circle" and isinstance(anchor_data, (list, tuple)) and len(anchor_data) >= 3:
                x, y, radius = anchor_data
                local_anchor_data = (x - offset_x, y - offset_y, radius)
            elif anchor_type == "point" and isinstance(anchor_data, (list, tuple)) and len(anchor_data) >= 2:
                x, y = anchor_data
                local_anchor_data = (x - offset_x, y - offset_y)
            else:
                local_anchor_data = anchor_data

            try:
                new_effects = script.create_effects(
                    anchor_type=anchor_type,
                    anchor_data=local_anchor_data,
                    effect_options=effect_options,
                    request_context={
                        "offset_x": offset_x,
                        "offset_y": offset_y,
                        "project_root": str(_PROJECT_ROOT),
                    },
                )
            except Exception:
                continue

            if not new_effects:
                continue

            self._effects.extend(new_effects)
            for effect in new_effects:
                effect._tick_prev_age = float(getattr(effect, "age", 0.0))
                effect._tick_prev_x = float(getattr(effect, "x", 0.0))
                effect._tick_prev_y = float(getattr(effect, "y", 0.0))
                effect._tick_prev_opacity = float(getattr(effect, "opacity", 1.0))
                effect._tick_prev_scale = float(getattr(effect, "scale", 1.0))
                effect._tick_prev_rotation = float(getattr(effect, "rotation", 0.0))
                effect._render_x = float(getattr(effect, "x", 0.0))
                effect._render_y = float(getattr(effect, "y", 0.0))
                effect._render_opacity = float(getattr(effect, "opacity", 1.0))
                effect._render_scale = float(getattr(effect, "scale", 1.0))
                effect._render_rotation = float(getattr(effect, "rotation", 0.0))
            appended = True

        if not appended:
            return

        if not had_effects:
            self._needs_immediate_repaint = True
            self.show()
            self.raise_()
            get_topmost_manager().enforce_burst()
        self.update()
        if self._needs_immediate_repaint:
            self.repaint()
            self._needs_immediate_repaint = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        for effect in sorted(self._effects, key=lambda item: int(getattr(item, "z", 0))):
            if not _effect_alive(effect):
                continue

            pixmap = getattr(effect, "pixmap", None)
            if not isinstance(pixmap, QPixmap) or pixmap.isNull():
                continue

            opacity = max(0.0, min(1.0, float(getattr(effect, "_render_opacity", getattr(effect, "opacity", 1.0)))))
            if opacity <= 0.0:
                continue

            scale = max(0.001, float(getattr(effect, "_render_scale", getattr(effect, "scale", 1.0))))
            rotation = float(getattr(effect, "_render_rotation", getattr(effect, "rotation", 0.0)))
            x = float(getattr(effect, "_render_x", getattr(effect, "x", 0.0)))
            y = float(getattr(effect, "_render_y", getattr(effect, "y", 0.0)))

            painter.save()
            painter.setOpacity(opacity)
            painter.translate(x, y)
            if rotation:
                painter.rotate(rotation)
            if scale != 1.0:
                painter.scale(scale, scale)
            half_w = pixmap.width() / 2.0
            half_h = pixmap.height() / 2.0
            painter.drawPixmap(int(-half_w), int(-half_h), pixmap)
            painter.restore()

        painter.end()

    def _clear_and_hide(self) -> None:
        """隐藏前先同步清空透明缓冲，避免下次复显出现上一帧残影。"""
        self._needs_immediate_repaint = False
        if self.isVisible():
            self.update()
            self.repaint()
        self.hide()

    def cleanup(self):
        if self._event_center:
            self._event_center.unsubscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._pending_requests.clear()
        self._effects.clear()
        cleanup_effect_script_manager()
        self._clear_and_hide()
