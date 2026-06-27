"""粒子效果系统 (PyQt5版) - 事件驱动重构版"""
import math

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QPainter, QBrush, QColor, QPen

from config.config import PARTICLES, UI_THEME
from lib.core.event.center import get_event_center, EventType, Event
from lib.script.practical.manager import get_particle_script_manager
from lib.core.topmost_manager import get_topmost_manager


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

        # 获取事件中心和粒子脚本管理器
        self._event_center = get_event_center()
        self._particle_manager = get_particle_script_manager()

        # 订阅粒子申请事件
        self._event_center.subscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)

        # 订阅全局帧事件，驱动粒子更新
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    # ------------------------------------------------------------------
    @staticmethod
    def _particle_alive(particle) -> bool:
        """兼容 alive 属性/方法，异常时按死亡处理。"""
        alive = getattr(particle, 'alive', True)
        try:
            return bool(alive() if callable(alive) else alive)
        except Exception:
            return False

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

        # 获取粒子脚本
        script = self._particle_manager.get_script(particle_id)
        if not script:
            return
        if hasattr(script, 'set_request_options'):
            try:
                script.set_request_options(dict(particle_options))
            except Exception:
                pass

        # 首批粒子到来前，先同步覆盖层几何，保证本地坐标准确
        if not self._particles:
            screen = self.screen().geometry() if self.screen() else self.geometry()
            self.setGeometry(screen)

        # 转换全局坐标为本地坐标
        offset_x = self.geometry().x()
        offset_y = self.geometry().y()

        # 调整区域数据为本地坐标
        if area_type == 'rect':
            x1, y1, x2, y2 = area_data
            local_area_data = (x1 - offset_x, y1 - offset_y, x2 - offset_x, y2 - offset_y)
        elif area_type == 'circle':
            x, y, radius = area_data
            local_area_data = (x - offset_x, y - offset_y, radius)
        else:  # point
            x, y = area_data
            local_area_data = (x - offset_x, y - offset_y)

        # 创建粒子
        new_particles = script.create_particles(area_type, local_area_data)
        if not new_particles:
            return

        # 首批有效粒子才显示窗口
        if not self._particles:
            self.show()
            self.raise_()
            self.repaint()  # 首次显示立即同步重绘，避免显示缓存帧

        self._particles.extend(new_particles)
        self.update()  # 立即请求重绘，避免显示缓存帧

        # 标记事件已处理
        event.mark_handled()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _on_frame(self, event: Event):
        """全局帧事件处理 - 更新粒子并重绘"""
        if not self._particles:
            return

        # 使用双指针原地过滤，避免创建新列表
        write_idx = 0
        for read_idx, p in enumerate(self._particles):
            p.update()
            if self._particle_alive(p):
                if write_idx != read_idx:
                    self._particles[write_idx] = p
                write_idx += 1

        # 截断列表
        del self._particles[write_idx:]

        if not self._particles:
            self.hide()
        else:
            self.update()   # 触发 paintEvent

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
            if not self._particle_alive(p):
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
                    int(p.x - p._text_w / 2),
                    int(p.y + p._baseline_offset),
                    p.text,
                )
                continue

            # ── 线条粒子（is_line=True）──────────────────────────────────
            if getattr(p, 'is_line', False):
                ln = p.length
                if ln > 0.5:   # 极短时跳过，避免绘制噪点
                    x2 = int(p.x + p.line_dx * ln)
                    y2 = int(p.y + p.line_dy * ln)
                    color = QColor(p.color)
                    color.setAlpha(alpha)
                    pen = QPen(color, p.pen_width, Qt.SolidLine, Qt.RoundCap)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.drawLine(int(p.x), int(p.y), x2, y2)
                continue

            # ── 检测粒子形状并计算绘制矩形 ──────────────────────────
            is_circle = getattr(p, 'is_circle', False)

            if hasattr(p, 'width') and hasattr(p, 'height'):
                # 矩形粒子（right_fade）
                rect_x = int(p.x)
                rect_y = int(p.y - p.height // 2)
                rect_w = p.width
                rect_h = p.height
            elif is_circle:
                # 圆形粒子（snow）：p.size 为半径
                r = p.size
                rect_x = int(p.x) - r
                rect_y = int(p.y) - r
                rect_w = r * 2
                rect_h = r * 2
            else:
                # 正方形粒子（其他粒子）
                rect_x = int(p.x - p.size // 2)
                rect_y = int(p.y - p.size // 2)
                rect_w = p.size
                rect_h = p.size

            # 圆形粒子开启抗锯齿，其他关闭
            painter.setRenderHint(QPainter.Antialiasing, is_circle)

            # ── 描边（可选）──────────────────────────────────────────
            if enable_stroke:
                pen_color = QColor(UI_THEME['border'])
                pen_color.setAlpha(alpha)
                painter.setPen(pen_color)
                painter.setBrush(Qt.NoBrush)
                if is_circle:
                    painter.drawEllipse(rect_x, rect_y, rect_w, rect_h)
                else:
                    painter.drawRect(rect_x, rect_y, rect_w, rect_h)

            # ── 粒子本体 ──────────────────────────────────────────────
            color = QColor(p.color)
            color.setAlpha(alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            if is_circle:
                painter.drawEllipse(rect_x, rect_y, rect_w, rect_h)
            else:
                painter.drawRect(rect_x, rect_y, rect_w, rect_h)

        painter.end()

    # ------------------------------------------------------------------
    def cleanup(self):
        """清理资源"""
        if self._event_center:
            self._event_center.unsubscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._particles.clear()
        self.hide()
