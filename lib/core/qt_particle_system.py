"""粒子效果系统 (PyQt5版) - 事件驱动重构版"""
from concurrent.futures import Future
from copy import copy, deepcopy
from collections import deque
from math import floor
from time import perf_counter

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QRect, QRectF, QLineF, QPointF
from PyQt5.QtGui import QPainter, QColor, QPen, QRegion

from config.config import PARTICLES, UI_THEME
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.layer import Layer, normalize_layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.screen_utils import get_virtual_screen_geometry
from lib.script.practical.manager import get_particle_script_manager

_ASYNC_PARTICLE_UPDATE_THRESHOLD = 1200
_PARTICLE_TILE_SIZE = 128
_logger = get_logger(__name__)


def _particle_alive(particle) -> bool:
    """兼容 alive 属性/方法，异常时按死亡处理。"""
    alive = getattr(particle, 'alive', True)
    try:
        return bool(alive() if callable(alive) else alive)
    except Exception:
        return False


def _update_particles_batch(particles: list) -> list:
    """后台更新粒子并返回存活粒子列表。"""
    alive_particles = []
    for particle in particles:
        try:
            particle.update()
        except Exception:
            continue
        if _particle_alive(particle):
            alive_particles.append(particle)
    return alive_particles


def _clone_particle_for_update(particle):
    """复制粒子供异步 tick 使用，避免后台线程修改正在绘制的对象。"""
    try:
        clone = deepcopy(particle)
    except Exception:
        clone = copy(particle)
    if clone is particle:
        raise TypeError('Particle snapshot must be an independent object')
    clone._tick_prev_x = float(getattr(particle, 'x', 0.0))
    clone._tick_prev_y = float(getattr(particle, 'y', 0.0))
    return clone


def _snapshot_particles_for_update(particles: list) -> list:
    """构建隔离的异步更新快照。"""
    return [_clone_particle_for_update(particle) for particle in particles]


def _prepare_particles_for_inplace_update(particles: list) -> None:
    """在原地更新前保存逻辑坐标，作为本次 tick 的插值起点。"""
    for particle in particles:
        particle._tick_prev_x = float(getattr(particle, 'x', 0.0))
        particle._tick_prev_y = float(getattr(particle, 'y', 0.0))


def _can_use_async_updates() -> bool:
    return bool(PARTICLES.get('async_update_enabled', False))


def _particle_bounds(particle) -> QRectF:
    """返回粒子在当前 overlay 本地坐标中的保守绘制包围盒。

    该函数不依赖 QWidget，用于空间索引、脏区裁剪和纯单元测试。
    """
    positions = [(
        float(getattr(particle, '_render_x', getattr(particle, 'x', 0.0))),
        float(getattr(particle, '_render_y', getattr(particle, 'y', 0.0))),
    )]
    for x_name, y_name in (("_tick_prev_x", "_tick_prev_y"), ("x", "y")):
        if hasattr(particle, x_name) and hasattr(particle, y_name):
            positions.append((float(getattr(particle, x_name)), float(getattr(particle, y_name))))

    bounds: QRectF | None = None

    def include(rect: QRectF) -> None:
        nonlocal bounds
        bounds = QRectF(rect) if bounds is None else bounds.united(rect)

    if getattr(particle, 'is_text', False):
        half_width = max(1.0, float(getattr(particle, '_text_w', 0.0)) / 2.0)
        line_height = max(12.0, float(getattr(particle, '_text_h', 0.0) or 12.0))
        baseline = float(getattr(particle, '_baseline_offset', 0.0))
        bloom = max(0.0, float(getattr(particle, 'bloom', 0.0) or 0.0))
        for x, y in positions:
            include(QRectF(
                x - half_width - bloom,
                y + baseline - line_height - bloom,
                half_width * 2.0 + bloom * 2.0,
                line_height + bloom * 2.0,
            ))
    elif getattr(particle, 'is_line', False):
        length = max(0.0, float(getattr(particle, 'length', 0.0)))
        dx = float(getattr(particle, 'line_dx', 0.0)) * length
        dy = float(getattr(particle, 'line_dy', 0.0)) * length
        margin = max(1.0, float(getattr(particle, 'pen_width', 1.0)))
        for x, y in positions:
            include(QRectF(
                min(x, x + dx) - margin,
                min(y, y + dy) - margin,
                abs(dx) + margin * 2.0,
                abs(dy) + margin * 2.0,
            ))
    elif hasattr(particle, 'width') and hasattr(particle, 'height'):
        width = max(0.0, float(getattr(particle, 'width', 0.0)))
        height = max(0.0, float(getattr(particle, 'height', 0.0)))
        for x, y in positions:
            include(QRectF(x, y - height / 2.0, width, height))
    else:
        size = max(0.0, float(getattr(particle, 'size', 0.0)))
        radius = size if getattr(particle, 'is_circle', False) else size / 2.0
        for x, y in positions:
            include(QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0))

    return bounds or QRectF()


def _tile_keys_for_bounds(bounds: QRectF) -> set[tuple[int, int]]:
    """返回矩形覆盖的固定网格块；坐标允许位于虚拟桌面负半轴。"""
    if bounds.isEmpty():
        return set()
    left = floor(bounds.left() / _PARTICLE_TILE_SIZE)
    top = floor(bounds.top() / _PARTICLE_TILE_SIZE)
    right = floor((bounds.right() - 1e-6) / _PARTICLE_TILE_SIZE)
    bottom = floor((bounds.bottom() - 1e-6) / _PARTICLE_TILE_SIZE)
    return {
        (tile_x, tile_y)
        for tile_y in range(top, bottom + 1)
        for tile_x in range(left, right + 1)
    }


def _tile_rect(key: tuple[int, int]) -> QRect:
    tile_x, tile_y = key
    return QRect(
        tile_x * _PARTICLE_TILE_SIZE,
        tile_y * _PARTICLE_TILE_SIZE,
        _PARTICLE_TILE_SIZE,
        _PARTICLE_TILE_SIZE,
    )


def _merged_tile_rects(keys: set[tuple[int, int]]) -> list[QRect]:
    """按行合并连续分块，减少 QRegion 的矩形节点数量。"""
    rows: dict[int, list[int]] = {}
    for tile_x, tile_y in keys:
        rows.setdefault(tile_y, []).append(tile_x)

    rects = []
    for tile_y, tile_x_values in rows.items():
        values = sorted(tile_x_values)
        run_start = run_end = values[0]
        for tile_x in values[1:]:
            if tile_x == run_end + 1:
                run_end = tile_x
                continue
            rects.append(QRect(
                run_start * _PARTICLE_TILE_SIZE,
                tile_y * _PARTICLE_TILE_SIZE,
                (run_end - run_start + 1) * _PARTICLE_TILE_SIZE,
                _PARTICLE_TILE_SIZE,
            ))
            run_start = run_end = tile_x
        rects.append(QRect(
            run_start * _PARTICLE_TILE_SIZE,
            tile_y * _PARTICLE_TILE_SIZE,
            (run_end - run_start + 1) * _PARTICLE_TILE_SIZE,
            _PARTICLE_TILE_SIZE,
        ))
    return rects


def _region_for_tiles(keys: set[tuple[int, int]]) -> QRegion:
    region = QRegion()
    for rect in _merged_tile_rects(keys):
        region = region.united(QRegion(rect))
    return region


def _tile_keys_for_region(region: QRegion) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for rect in region.rects():
        keys.update(_tile_keys_for_bounds(QRectF(rect)))
    return keys


def _render_order_key(particle) -> tuple[int, int, int]:
    return (
        int(getattr(particle, 'layer', Layer.PARTICLE)),
        int(getattr(particle, 'z', 0)),
        int(getattr(particle, '_draw_order', 0)),
    )


class _ParticleSpatialIndex:
    """缓存粒子包围盒、块归属和绘制顺序的增量二维索引。"""

    def __init__(self) -> None:
        self._buckets: dict[tuple[int, int], dict[int, object]] = {}
        self._tile_keys_by_id: dict[int, set[tuple[int, int]]] = {}
        self._bounds_by_id: dict[int, QRectF] = {}
        self._particles_by_id: dict[int, object] = {}
        self._ordered_particles: list = []
        self._order_signature: tuple = ()

    @property
    def occupied_tiles(self) -> set[tuple[int, int]]:
        return set(self._buckets)

    def sync(self, particles: list) -> set[tuple[int, int]]:
        """同步逻辑状态；只有跨块或增删粒子时修改桶成员。"""
        dirty_tiles: set[tuple[int, int]] = set()
        live_particles = []
        live_ids: set[int] = set()

        for particle in particles:
            if not _particle_alive(particle):
                continue
            particle_id = id(particle)
            live_ids.add(particle_id)
            live_particles.append(particle)
            bounds = _particle_bounds(particle).adjusted(-2.0, -2.0, 2.0, 2.0)
            new_keys = _tile_keys_for_bounds(bounds)
            old_keys = self._tile_keys_by_id.get(particle_id, set())
            dirty_tiles.update(old_keys)
            dirty_tiles.update(new_keys)

            for key in old_keys - new_keys:
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                bucket.pop(particle_id, None)
                if not bucket:
                    self._buckets.pop(key, None)
            for key in new_keys - old_keys:
                self._buckets.setdefault(key, {})[particle_id] = particle

            self._tile_keys_by_id[particle_id] = new_keys
            self._bounds_by_id[particle_id] = bounds
            self._particles_by_id[particle_id] = particle

        for particle_id in set(self._particles_by_id) - live_ids:
            old_keys = self._tile_keys_by_id.pop(particle_id, set())
            dirty_tiles.update(old_keys)
            for key in old_keys:
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                bucket.pop(particle_id, None)
                if not bucket:
                    self._buckets.pop(key, None)
            self._bounds_by_id.pop(particle_id, None)
            self._particles_by_id.pop(particle_id, None)

        signature = tuple((id(particle), *_render_order_key(particle)) for particle in live_particles)
        if signature != self._order_signature:
            self._ordered_particles = sorted(live_particles, key=_render_order_key)
            self._order_signature = signature
        return dirty_tiles

    def particles_for_tiles(self, keys: set[tuple[int, int]], region: QRegion) -> list:
        candidate_ids: set[int] = set()
        for key in keys:
            candidate_ids.update(self._buckets.get(key, ()))
        if not candidate_ids:
            return []
        return [
            particle
            for particle in self._ordered_particles
            if id(particle) in candidate_ids
            and region.intersects(self._bounds_by_id[id(particle)].toAlignedRect())
        ]

    def bounds_for(self, particle) -> QRectF:
        return self._bounds_by_id.get(id(particle), QRectF())

    def clear(self) -> None:
        self._buckets.clear()
        self._tile_keys_by_id.clear()
        self._bounds_by_id.clear()
        self._particles_by_id.clear()
        self._ordered_particles.clear()
        self._order_signature = ()


class ParticleOverlay(QWidget):
    """
    全屏透明覆盖层，仅用于绘制粒子。
    设置为 Tool + FramelessWindowHint + WA_TransparentForMouseEvents，
    不会拦截鼠标事件。
    现在支持事件驱动的粒子创建。
    """

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
        self._layer_manager = get_layer_manager()
        self._layer_manager.register(self, Layer.PARTICLE, name='ParticleOverlay')

        self._particles = []
        self._spatial_index = _ParticleSpatialIndex()
        self._paused = False
        self._draw_seq = 0
        self._pending_requests = deque()
        self._pending_future: Future | None = None
        self._pending_snapshot_ids: set[int] = set()
        self._perf_log_enabled = bool(PARTICLES.get('perf_log_enabled', False))
        self._perf_log_interval_ticks = max(1, int(PARTICLES.get('perf_log_interval_ticks', 60) or 60))
        self._perf_tick_count = 0
        self._perf_frame_count = 0
        self._perf_request_count = 0
        self._perf_spawned_count = 0
        self._perf_update_ms_total = 0.0
        self._perf_drain_ms_total = 0.0
        self._perf_paint_ms_total = 0.0
        self._perf_max_particles = 0

        # 获取事件中心和粒子脚本管理器
        self._event_center = get_event_center()
        self._particle_manager = get_particle_script_manager()

        # 订阅粒子申请事件
        self._event_center.subscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)

        # TICK 推进状态，FRAME 只负责插值与重绘
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    def _refresh_spatial_grid(self, *, reindex: bool = True) -> None:
        """同步索引或复用缓存，并仅刷新粒子实际占用的矩形块。"""
        if reindex:
            dirty_tiles = self._spatial_index.sync(self._particles)
        else:
            dirty_tiles = self._spatial_index.occupied_tiles
        if dirty_tiles:
            dirty_region = _region_for_tiles(dirty_tiles).intersected(QRegion(self.rect()))
            if not dirty_region.isEmpty():
                self.update(dirty_region)

    # ------------------------------------------------------------------
    def _on_particle_request(self, event: Event):
        """
        处理粒子申请事件

        事件数据格式:
        - 矩形范围: {'particle_id': str, 'area_type': 'rect', 'area_data': (x1, y1, x2, y2)}
        - 圆形范围: {'particle_id': str, 'area_type': 'circle', 'area_data': (x, y, radius)}
        - 单点: {'particle_id': str, 'area_type': 'point', 'area_data': (x, y)}
        """
        data = event.data
        particle_id = data.get('particle_id')
        area_type = data.get('area_type', 'point')
        area_data = data.get('area_data')
        particle_options = data.get('particle_options') or {}

        if not particle_id or not area_data:
            return
        if self._paused:
            event.mark_handled()
            return

        self._pending_requests.append({
            'particle_id': particle_id,
            'area_type': area_type,
            'area_data': area_data,
            'particle_options': dict(particle_options),
        })
        if self._perf_log_enabled:
            self._perf_request_count += 1
        event.mark_handled()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _on_tick(self, event: Event):
        """全局 tick 事件处理 - 应用后台更新结果并提交下一 tick 粒子更新。"""
        if self._paused:
            return
        tick_start = perf_counter() if self._perf_log_enabled else 0.0
        drain_before = perf_counter() if self._perf_log_enabled else 0.0
        self._apply_pending_updates()
        self._drain_particle_requests()
        if self._perf_log_enabled:
            self._perf_drain_ms_total += (perf_counter() - drain_before) * 1000.0
        if not self._particles:
            if self._perf_log_enabled:
                self._perf_tick_count += 1
                self._maybe_log_perf()
            return

        use_async = _can_use_async_updates() and len(self._particles) >= _ASYNC_PARTICLE_UPDATE_THRESHOLD
        if not use_async:
            _prepare_particles_for_inplace_update(self._particles)
            update_before = perf_counter() if self._perf_log_enabled else 0.0
            self._particles = _update_particles_batch(self._particles)
            if self._perf_log_enabled:
                self._perf_update_ms_total += (perf_counter() - update_before) * 1000.0
            self._pending_future = None
            if not self._particles:
                self._refresh_spatial_grid()
                self.hide()
            else:
                self._refresh_spatial_grid()
            if self._perf_log_enabled:
                self._perf_tick_count += 1
                self._perf_max_particles = max(self._perf_max_particles, len(self._particles))
                self._maybe_log_perf()
            return

        if self._pending_future is not None:
            return

        try:
            snapshot = _snapshot_particles_for_update(self._particles)
        except Exception:
            _prepare_particles_for_inplace_update(self._particles)
            self._particles = _update_particles_batch(self._particles)
            if not self._particles:
                self._refresh_spatial_grid()
                self.hide()
            else:
                self._refresh_spatial_grid()
            return

        future = get_compute_hub().submit_latest(
            "particle_overlay_update",
            _update_particles_batch,
            snapshot,
            executor="vector",
        )
        if future is not None:
            self._pending_future = future
            self._pending_snapshot_ids = {id(particle) for particle in self._particles}
        if self._perf_log_enabled:
            self._perf_tick_count += 1
            self._perf_update_ms_total += (perf_counter() - tick_start) * 1000.0
            self._perf_max_particles = max(self._perf_max_particles, len(self._particles))
            self._maybe_log_perf()

    def _on_frame(self, event: Event):
        """全局帧事件处理 - 按 tick alpha 插值并请求重绘。"""
        if self._paused:
            return
        if not self._particles:
            return
        alpha = float((event.data or {}).get('tick_alpha', 1.0) or 0.0)
        alpha = max(0.0, min(1.0, alpha))
        for particle in self._particles:
            prev_x = float(getattr(particle, '_tick_prev_x', getattr(particle, 'x', 0.0)))
            prev_y = float(getattr(particle, '_tick_prev_y', getattr(particle, 'y', 0.0)))
            cur_x = float(getattr(particle, 'x', prev_x))
            cur_y = float(getattr(particle, 'y', prev_y))
            particle._render_x = prev_x + (cur_x - prev_x) * alpha
            particle._render_y = prev_y + (cur_y - prev_y) * alpha
        self._refresh_spatial_grid(reindex=False)

    def _apply_pending_updates(self) -> None:
        future = self._pending_future
        if future is None or not future.done():
            return
        self._pending_future = None
        snapshot_ids = self._pending_snapshot_ids
        try:
            updated_particles = future.result()
        except Exception:
            updated_particles = [
                particle
                for particle in self._particles
                if id(particle) in snapshot_ids and _particle_alive(particle)
            ]

        extra_particles = [
            particle
            for particle in self._particles
            if id(particle) not in snapshot_ids and _particle_alive(particle)
        ]
        self._pending_snapshot_ids = set()
        self._particles = updated_particles + extra_particles
        for particle in self._particles:
            if not hasattr(particle, '_tick_prev_x'):
                particle._tick_prev_x = float(getattr(particle, 'x', 0.0))
            if not hasattr(particle, '_tick_prev_y'):
                particle._tick_prev_y = float(getattr(particle, 'y', 0.0))
            particle._render_x = float(getattr(particle, '_tick_prev_x', getattr(particle, 'x', 0.0)))
            particle._render_y = float(getattr(particle, '_tick_prev_y', getattr(particle, 'y', 0.0)))

        if not self._particles:
            self._refresh_spatial_grid()
            self.hide()
            return
        self._refresh_spatial_grid()

    def _drain_particle_requests(self) -> None:
        """在帧边界批量创建粒子，减少主线程事件风暴。"""
        if not self._pending_requests:
            return

        if not self._particles:
            virtual_geometry = get_virtual_screen_geometry()
            if self.geometry() != virtual_geometry:
                self.setGeometry(virtual_geometry)

        offset_x = self.geometry().x()
        offset_y = self.geometry().y()
        had_particles = bool(self._particles)
        appended = False

        while self._pending_requests:
            request = self._pending_requests.popleft()
            particle_id = request['particle_id']
            area_type = request['area_type']
            area_data = request['area_data']
            particle_options = request['particle_options']

            script = self._particle_manager.get_script(particle_id)
            if not script:
                continue
            if hasattr(script, 'set_request_options'):
                try:
                    script.set_request_options(dict(particle_options))
                except Exception:
                    pass

            if area_type == 'rect':
                x1, y1, x2, y2 = area_data
                local_area_data = (x1 - offset_x, y1 - offset_y, x2 - offset_x, y2 - offset_y)
            elif area_type == 'circle':
                x, y, radius = area_data
                local_area_data = (x - offset_x, y - offset_y, radius)
            else:
                x, y = area_data
                local_area_data = (x - offset_x, y - offset_y)

            new_particles = script.create_particles(area_type, local_area_data)
            if not new_particles:
                continue

            self._particles.extend(new_particles)
            if self._perf_log_enabled:
                self._perf_spawned_count += len(new_particles)
            for particle in new_particles:
                self._draw_seq += 1
                particle.layer = normalize_layer(
                    particle_options.get('layer', getattr(particle, 'layer', Layer.PARTICLE)),
                    Layer.PARTICLE,
                )
                try:
                    particle.z = int(particle_options.get('z', getattr(particle, 'z', 0)))
                except (TypeError, ValueError):
                    particle.z = 0
                particle._draw_order = self._draw_seq
                particle._tick_prev_x = float(getattr(particle, 'x', 0.0))
                particle._tick_prev_y = float(getattr(particle, 'y', 0.0))
                particle._render_x = float(getattr(particle, 'x', 0.0))
                particle._render_y = float(getattr(particle, 'y', 0.0))
            appended = True

        if not appended:
            return

        if not had_particles:
            self.show()
            self._layer_manager.enforce_burst()
        self._refresh_spatial_grid()

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        paint_start = perf_counter() if self._perf_log_enabled else 0.0
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        # 透明覆盖层每帧先清屏，避免上一帧像素残留
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(event.region().boundingRect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if not self._particles:
            painter.end()
            return

        # 从配置中读取描边开关
        enable_stroke = PARTICLES.get('enable_stroke', True)
        fade_threshold = PARTICLES.get('fade_threshold', 0.75)
        square_stroke_pen = None
        if enable_stroke:
            square_stroke_pen = QPen(QColor(UI_THEME['border']))
        painter.setRenderHint(QPainter.Antialiasing, False)

        particles = self._spatial_index.particles_for_tiles(
            _tile_keys_for_region(event.region()),
            event.region(),
        )
        clip_rect = event.region().boundingRect()
        for p in particles:
            if not _particle_alive(p):
                continue
            if not self._spatial_index.bounds_for(p).intersects(QRectF(clip_rect)):
                continue

            life = max(0.0, float(getattr(p, 'life', 0.0)))
            max_life = max(1e-6, float(getattr(p, 'max_life', 1.0)))

            # ── alpha（no_fade 粒子跳过淡出逻辑）────────────────────────
            if getattr(p, 'no_fade', False):
                alpha = 255
            else:
                # 剩余生命低于 fade_threshold 比例时才开始淡出
                fade_start = max_life * fade_threshold
                if life >= fade_start:
                    alpha = 255
                else:
                    alpha = max(0, int(life / fade_start * 255))
            if alpha <= 0:
                continue
            painter.setRenderHint(QPainter.Antialiasing, False)

            # ── 文字粒子（is_text=True）──────────────────────────────────
            if getattr(p, 'is_text', False):
                color = QColor(p.color)
                alpha_value = int(getattr(p, 'alpha_override', alpha))
                color.setAlpha(max(0, min(255, alpha_value)))
                bloom = getattr(p, 'bloom', None)
                painter.setFont(p.font)
                painter.setRenderHint(QPainter.Antialiasing, True)
                if isinstance(bloom, (int, float)) and bloom > 0:
                    glow_color = _make_text_bloom_color(color)
                    bloom_radius = float(bloom)
                    center_x = float(getattr(p, '_render_x', p.x) - p._text_w / 2)
                    center_y = float(getattr(p, '_render_y', p.y) + p._baseline_offset)
                    bloom_steps = (
                        (0.45, 0.10),
                        (0.75, 0.07),
                        (1.0, 0.04),
                    )
                    bloom_alpha_budget = alpha_value * 0.30
                    for distance_scale, alpha_scale in bloom_steps:
                        glow_alpha = max(0, min(255, int(bloom_alpha_budget * alpha_scale)))
                        if glow_alpha <= 0:
                            continue
                        glow_color.setAlpha(glow_alpha)
                        painter.setPen(glow_color)
                        radius = bloom_radius * distance_scale
                        for dx, dy in (
                            (radius, 0.0),
                            (-radius, 0.0),
                            (0.0, radius),
                            (0.0, -radius),
                            (radius * 0.7, radius * 0.7),
                            (-radius * 0.7, radius * 0.7),
                            (radius * 0.7, -radius * 0.7),
                            (-radius * 0.7, -radius * 0.7),
                        ):
                            painter.drawText(QPointF(center_x + dx, center_y + dy), p.text)
                painter.setPen(color)
                painter.drawText(
                    QPointF(
                        float(getattr(p, '_render_x', p.x) - p._text_w / 2),
                        float(getattr(p, '_render_y', p.y) + p._baseline_offset),
                    ),
                    p.text,
                )
                continue

            # ── 线条粒子（is_line=True）──────────────────────────────────
            if getattr(p, 'is_line', False):
                ln = p.length
                if ln > 0.5:   # 极短时跳过，避免绘制噪点
                    px = float(getattr(p, '_render_x', p.x))
                    py = float(getattr(p, '_render_y', p.y))
                    x2 = px + p.line_dx * ln
                    y2 = py + p.line_dy * ln
                    color = QColor(p.color)
                    color.setAlpha(alpha)
                    pen = QPen(color, p.pen_width, Qt.SolidLine, Qt.RoundCap)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.drawLine(QLineF(px, py, x2, y2))
                continue

            # ── 检测粒子形状并计算绘制矩形 ──────────────────────────
            is_circle = getattr(p, 'is_circle', False)

            if hasattr(p, 'width') and hasattr(p, 'height'):
                # 矩形粒子（right_fade）
                px = float(getattr(p, '_render_x', p.x))
                py = float(getattr(p, '_render_y', p.y))
                rect = QRectF(
                    px,
                    py - (p.height / 2.0),
                    float(p.width),
                    float(p.height),
                )
                if enable_stroke:
                    pen_color = QColor(UI_THEME['border'])
                    pen_color.setAlpha(alpha)
                    painter.setPen(QPen(pen_color))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(rect)
                color = QColor(p.color)
                color.setAlpha(alpha)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRect(rect)
                continue
            elif is_circle:
                # 圆形粒子（snow）：p.size 为半径
                r = p.size
                px = float(getattr(p, '_render_x', p.x))
                py = float(getattr(p, '_render_y', p.y))
                rect = QRectF(
                    px - float(r),
                    py - float(r),
                    float(r * 2),
                    float(r * 2),
                )
            else:
                # 正方形粒子（热路径）
                px = float(getattr(p, '_render_x', p.x))
                py = float(getattr(p, '_render_y', p.y))
                half = p.size / 2.0
                rect = QRectF(
                    px - half,
                    py - half,
                    float(p.size),
                    float(p.size),
                )
                if enable_stroke and square_stroke_pen is not None:
                    pen_color = square_stroke_pen.color()
                    pen_color.setAlpha(alpha)
                    square_stroke_pen.setColor(pen_color)
                    painter.setPen(square_stroke_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(rect)
                color = QColor(p.color)
                color.setAlpha(alpha)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRect(rect)
                continue

            painter.setRenderHint(QPainter.Antialiasing, True)

            # ── 描边（可选）──────────────────────────────────────────
            if enable_stroke:
                pen_color = QColor(UI_THEME['border'])
                pen_color.setAlpha(alpha)
                painter.setPen(pen_color)
                painter.setBrush(Qt.NoBrush)
                if is_circle:
                    painter.drawEllipse(rect)
                else:
                    painter.drawRect(rect)

            # ── 粒子本体 ──────────────────────────────────────────────
            color = QColor(p.color)
            color.setAlpha(alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            if is_circle:
                painter.drawEllipse(rect)
            else:
                painter.drawRect(rect)
            painter.setRenderHint(QPainter.Antialiasing, False)

        painter.end()
        if self._perf_log_enabled:
            self._perf_frame_count += 1
            self._perf_paint_ms_total += (perf_counter() - paint_start) * 1000.0

    def _maybe_log_perf(self) -> None:
        if not self._perf_log_enabled or self._perf_tick_count < self._perf_log_interval_ticks:
            return
        avg_update_ms = self._perf_update_ms_total / max(1, self._perf_tick_count)
        avg_drain_ms = self._perf_drain_ms_total / max(1, self._perf_tick_count)
        avg_paint_ms = self._perf_paint_ms_total / max(1, self._perf_frame_count)
        _logger.debug(
            "[ParticlePerf] ticks=%d frames=%d live=%d peak=%d req=%d spawned=%d avg_update=%.3fms avg_drain=%.3fms avg_paint=%.3fms async=%s",
            self._perf_tick_count,
            self._perf_frame_count,
            len(self._particles),
            self._perf_max_particles,
            self._perf_request_count,
            self._perf_spawned_count,
            avg_update_ms,
            avg_drain_ms,
            avg_paint_ms,
            _can_use_async_updates(),
        )
        self._perf_tick_count = 0
        self._perf_frame_count = 0
        self._perf_request_count = 0
        self._perf_spawned_count = 0
        self._perf_update_ms_total = 0.0
        self._perf_drain_ms_total = 0.0
        self._perf_paint_ms_total = 0.0
        self._perf_max_particles = 0

    # ------------------------------------------------------------------
    def _clear_and_hide(self) -> None:
        """隐藏前先同步清空透明缓冲，避免退出时残留上一帧粒子。"""
        if self.isVisible():
            self.update()
            self.repaint()
        self.hide()

    def flush_immediately(self) -> None:
        """立即清空当前可见粒子，但不解绑事件，供退出流程前段使用。"""
        self._pending_future = None
        self._pending_requests.clear()
        self._pending_snapshot_ids.clear()
        self._particles.clear()
        self._spatial_index.clear()
        self._clear_and_hide()

    def set_paused(self, paused: bool) -> None:
        """暂停/恢复粒子系统；暂停时立即清空现有可见粒子。"""
        self._paused = bool(paused)
        if self._paused:
            self.flush_immediately()

    # ------------------------------------------------------------------
    def cleanup(self):
        """清理资源"""
        if self._event_center:
            self._event_center.unsubscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self.flush_immediately()
        self._layer_manager.unregister(self)


def _make_text_bloom_color(base: QColor) -> QColor:
    white_mix = 0.62
    r = int(base.red() * (1.0 - white_mix) + 255 * white_mix)
    g = int(base.green() * (1.0 - white_mix) + 255 * white_mix)
    b = int(base.blue() * (1.0 - white_mix) + 255 * white_mix)
    return QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
