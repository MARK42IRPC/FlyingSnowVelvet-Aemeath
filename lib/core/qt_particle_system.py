"""粒子效果系统 (PyQt5版) - 事件驱动重构版"""
from concurrent.futures import Future
from copy import deepcopy
from collections import deque

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QRectF, QLineF, QPointF
from PyQt5.QtGui import QPainter, QColor, QPen

from config.config import PARTICLES, UI_THEME
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.script.practical.manager import get_particle_script_manager
from lib.core.topmost_manager import get_topmost_manager

_ASYNC_PARTICLE_UPDATE_THRESHOLD = 1200


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
    """
    生成用于后台计算的粒子副本，避免工作线程直接修改主线程正在绘制的对象。
    优先深拷贝；失败时退化到复制 __dict__ 的轻量副本。
    """
    try:
        clone = deepcopy(particle)
    except Exception:
        clone = particle.__class__.__new__(particle.__class__)
        if hasattr(particle, "__dict__"):
            clone.__dict__ = dict(particle.__dict__)
    # 插值起点优先取“当前屏幕上最后一次渲染到的位置”。
    # 这样即使后台更新结果晚一个 tick 回填，也不会从更旧的真实坐标重新插值，
    # 避免慢速粒子沿最近轨迹出现向后回弹的抽搐感。
    clone._tick_prev_x = float(getattr(particle, '_render_x', getattr(particle, 'x', 0.0)))
    clone._tick_prev_y = float(getattr(particle, '_render_y', getattr(particle, 'y', 0.0)))
    return clone


def _snapshot_particles_for_update(particles: list) -> list:
    """为后台更新构建隔离快照。"""
    return [_clone_particle_for_update(p) for p in particles]


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
        get_topmost_manager().register(self)

        self._particles = []
        self._pending_requests = deque()
        self._pending_future: Future | None = None
        self._pending_snapshot_ids: set[int] = set()

        # 获取事件中心和粒子脚本管理器
        self._event_center = get_event_center()
        self._particle_manager = get_particle_script_manager()

        # 订阅粒子申请事件
        self._event_center.subscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)

        # TICK 推进状态，FRAME 只负责插值与重绘
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

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

        self._pending_requests.append({
            'particle_id': particle_id,
            'area_type': area_type,
            'area_data': area_data,
            'particle_options': dict(particle_options),
        })
        event.mark_handled()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _on_tick(self, event: Event):
        """全局 tick 事件处理 - 应用后台更新结果并提交下一 tick 粒子更新。"""
        self._apply_pending_updates()
        self._drain_particle_requests()
        if not self._particles:
            return

        # 粒子是高度连续的视觉对象。
        # 当数量不大时，优先在主线程逐 tick 连续推进，避免 submit_latest 在前一帧未完成时
        # 跳过中间 tick，导致慢速粒子出现“停一拍再追一格”的局部回弹/抽搐感。
        if len(self._particles) < _ASYNC_PARTICLE_UPDATE_THRESHOLD:
            updated_particles = _update_particles_batch(_snapshot_particles_for_update(self._particles))
            self._particles = updated_particles
            for particle in self._particles:
                if not hasattr(particle, '_tick_prev_x'):
                    particle._tick_prev_x = float(getattr(particle, '_render_x', getattr(particle, 'x', 0.0)))
                if not hasattr(particle, '_tick_prev_y'):
                    particle._tick_prev_y = float(getattr(particle, '_render_y', getattr(particle, 'y', 0.0)))
            self._pending_future = None
            self._pending_snapshot_ids = set()
            if not self._particles:
                self.hide()
            return

        future = get_compute_hub().submit_latest(
            "particle_overlay_update",
            _update_particles_batch,
            _snapshot_particles_for_update(self._particles),
            executor="vector",
        )
        if future is not None:
            self._pending_future = future
            self._pending_snapshot_ids = {id(p) for p in self._particles}

    def _on_frame(self, event: Event):
        """全局帧事件处理 - 按 tick alpha 插值并请求重绘。"""
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
        get_topmost_manager().bring_to_front(self)
        self.update()

    def _apply_pending_updates(self) -> None:
        future = self._pending_future
        if future is None or not future.done():
            return
        self._pending_future = None
        try:
            updated_particles = future.result()
        except Exception:
            updated_particles = []

        # 合并后台快照提交之后新到达的粒子，避免高并发申请时被旧结果覆盖。
        snapshot_ids = self._pending_snapshot_ids
        extra_particles = [
            p for p in self._particles
            if id(p) not in snapshot_ids and _particle_alive(p)
        ]
        self._pending_snapshot_ids = set()
        self._particles = updated_particles + extra_particles
        for particle in self._particles:
            if not hasattr(particle, '_tick_prev_x'):
                particle._tick_prev_x = float(getattr(particle, 'x', 0.0))
            if not hasattr(particle, '_tick_prev_y'):
                particle._tick_prev_y = float(getattr(particle, 'y', 0.0))
            particle._render_x = float(getattr(particle, 'x', 0.0))
            particle._render_y = float(getattr(particle, 'y', 0.0))

        if not self._particles:
            self.hide()
            return

    def _drain_particle_requests(self) -> None:
        """在帧边界批量创建粒子，减少主线程事件风暴。"""
        if not self._pending_requests:
            return

        if not self._particles:
            screen = self.screen().geometry() if self.screen() else self.geometry()
            self.setGeometry(screen)

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
            for particle in new_particles:
                particle._tick_prev_x = float(getattr(particle, 'x', 0.0))
                particle._tick_prev_y = float(getattr(particle, 'y', 0.0))
                particle._render_x = float(getattr(particle, 'x', 0.0))
                particle._render_y = float(getattr(particle, 'y', 0.0))
            appended = True

        if not appended:
            return

        if not had_particles:
            self.show()
            self.raise_()
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        # 透明覆盖层每帧先清屏，避免上一帧像素残留
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if not self._particles:
            painter.end()
            return

        # 从配置中读取描边开关
        enable_stroke = PARTICLES.get('enable_stroke', True)
        fade_threshold = PARTICLES.get('fade_threshold', 0.75)

        for p in self._particles:
            if not _particle_alive(p):
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

            # ── 文字粒子（is_text=True）──────────────────────────────────
            if getattr(p, 'is_text', False):
                color = QColor(p.color)
                color.setAlpha(alpha)
                painter.setFont(p.font)
                painter.setPen(color)
                painter.setRenderHint(QPainter.Antialiasing, True)
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
                # 正方形粒子（其他粒子）
                px = float(getattr(p, '_render_x', p.x))
                py = float(getattr(p, '_render_y', p.y))
                half = p.size / 2.0
                rect = QRectF(
                    px - half,
                    py - half,
                    float(p.size),
                    float(p.size),
                )

            # 圆形粒子开启抗锯齿，其他关闭
            painter.setRenderHint(QPainter.Antialiasing, is_circle)

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

        painter.end()

    # ------------------------------------------------------------------
    def cleanup(self):
        """清理资源"""
        if self._event_center:
            self._event_center.unsubscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._pending_future = None
        self._pending_requests.clear()
        self._pending_snapshot_ids.clear()
        self._particles.clear()
        self.hide()
