"""特效系统覆盖层。

当前版本先支持图片类 effect instance：
- 事件驱动申请
- tick 推进逻辑状态
- frame 插值绘制
- 资源路径 -> QPixmap 缓存
"""

from __future__ import annotations

from collections import deque
import math
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.graphics.resources import ImageResource
from lib.core.graphics.visuals import build_effect_batch, load_effect_resource
from lib.core.qt_bridge.draw_backend import QtDrawBackend


_logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RESOURCE_CACHE: dict[tuple, ImageResource] = {}


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


class EffectOverlay(QWidget):
    """全屏透明覆盖层，仅用于绘制特效。"""

    def __init__(self, effect_manager, *, manager_cleanup=None, parent=None):
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
        self._layer_manager = get_layer_manager()
        self._layer_manager.register(self, Layer.EFFECT, name='EffectOverlay')

        self._effects = []
        self._paused = False
        self._draw_seq = 0
        self._pending_requests = deque()
        self._needs_immediate_repaint = False
        self._cleanup_done = False
        self._draw_backend = QtDrawBackend()
        self._event_center = get_event_center()
        self._effect_manager = effect_manager
        self._manager_cleanup = manager_cleanup

        self._event_center.subscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    def _on_effect_request(self, event: Event):
        data = event.data
        effect_id = data.get("effect_id")
        if not effect_id:
            return
        if self._paused:
            event.mark_handled()
            return

        effect_options = dict(data.get("effect_options") or {})
        effect_options.pop("pixmap", None)
        self._pending_requests.append({
            "effect_id": effect_id,
            "anchor_type": data.get("anchor_type", "point"),
            "anchor_data": data.get("anchor_data"),
            "effect_options": effect_options,
        })
        event.mark_handled()

    def _on_tick(self, event: Event):
        if self._paused:
            return
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
        if self._paused:
            return
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

            visual_resource = None
            resource_path = effect_options.get("resource_path")
            if resource_path:
                resolved_path = _resolve_resource_path(str(resource_path))
                output_size = effect_options.get("masked_output_size")
                if isinstance(output_size, list):
                    output_size = tuple(output_size)
                cache_key = (
                    resolved_path,
                    output_size,
                    bool(effect_options.get("edge_feather", False)),
                    effect_options.get("feather_ratio", 0.12),
                )
                visual_resource = _RESOURCE_CACHE.get(cache_key)
                if visual_resource is None:
                    visual_resource = load_effect_resource(resolved_path, effect_options)
                    if visual_resource is not None:
                        _RESOURCE_CACHE[cache_key] = visual_resource
                if visual_resource is None:
                    continue
                effect_options["resolved_resource_path"] = resolved_path

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

            renderable_effects = []
            for effect in new_effects:
                if visual_resource is not None:
                    effect._visual_resource = visual_resource
                elif not str(getattr(effect, "text", "") or ""):
                    continue
                renderable_effects.append(effect)
            new_effects = renderable_effects
            if not new_effects:
                continue

            self._effects.extend(new_effects)
            for effect in new_effects:
                self._draw_seq += 1
                effect.layer = int(Layer.EFFECT)
                try:
                    effect.z = int(effect_options.get("z", getattr(effect, "z", 0)))
                except (TypeError, ValueError):
                    effect.z = 0
                effect._draw_order = self._draw_seq
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
            self._layer_manager.enforce_burst()
        self.update()
        if self._needs_immediate_repaint:
            self.repaint()
            self._needs_immediate_repaint = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        self._draw_backend.render(build_effect_batch(self._effects), painter)

        painter.end()

    def _clear_and_hide(self) -> None:
        """隐藏前先同步清空透明缓冲，避免下次复显出现上一帧残影。"""
        self._needs_immediate_repaint = False
        if self.isVisible():
            self.update()
            self.repaint()
        self.hide()

    def flush_immediately(self) -> None:
        """立即清空当前可见特效，但不解绑事件，供退出流程前段使用。"""
        self._pending_requests.clear()
        self._effects.clear()
        self._clear_and_hide()

    def set_paused(self, paused: bool) -> None:
        """暂停/恢复特效系统；暂停时立即清空当前可见特效。"""
        self._paused = bool(paused)
        if self._paused:
            self.flush_immediately()

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        if self._event_center:
            self._event_center.unsubscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self.flush_immediately()
        self._draw_backend.cleanup()
        if self._manager_cleanup is not None:
            self._manager_cleanup()
        self._layer_manager.unregister(self)
        try:
            self.close()
        except Exception:
            pass
        try:
            self.deleteLater()
        except Exception:
            pass


def create_effect_overlay_factory(effect_manager_provider, manager_cleanup=None):
    """Bind the script effect registry to the Qt renderer."""

    def create() -> EffectOverlay:
        return EffectOverlay(
            effect_manager_provider(),
            manager_cleanup=manager_cleanup,
        )

    return create
