"""雪球管理器 - FIFO 数量控制"""
import math
import random
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np
from PyQt5.QtCore    import QPoint
from PyQt5.QtGui     import QPixmap
from PyQt5.QtWidgets import QApplication

from lib.core.event.center        import get_event_center, EventType, Event
from lib.core.hash_cmd_registry   import get_hash_cmd_registry
from lib.core.plugin_registry     import manager_registry, BaseManager
from lib.core.screen_utils        import get_screen_geometry_for_point
from lib.core.logger              import get_logger
from .snowball                    import Snowball

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SnowballManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 后台碰撞计算（纯数据，无 Qt 操作）
# ──────────────────────────────────────────────────────────────────────

def _compute_collision_results(snapshot: list, elasticity: float,
                               ball_friction: float) -> list:
    """
    后台线程中运行：numpy 向量化碰撞检测 + 弹性/切向摩擦冲量计算。

    snapshot 格式：每项为 dict{ball, cx, cy, radius, vx, vy}
    返回纯数据列表，调用方在主线程中应用。
    """
    n = len(snapshot)
    if n < 2:
        return []

    # ── numpy 向量化：一次性计算所有球对距离 ──────────────────────────
    cx = np.array([s['cx'] for s in snapshot], dtype=np.float64)
    cy = np.array([s['cy'] for s in snapshot], dtype=np.float64)
    r  = np.array([s['radius'] for s in snapshot], dtype=np.float64)

    # dx_mat[i,j] = cx[j] - cx[i]（从 i 指向 j 的方向）
    dx_mat       = cx[np.newaxis, :] - cx[:, np.newaxis]
    dy_mat       = cy[np.newaxis, :] - cy[:, np.newaxis]
    dist_sq_mat  = dx_mat * dx_mat + dy_mat * dy_mat
    min_dist_mat = r[:, np.newaxis] + r[np.newaxis, :]

    # 只取上三角，避免重复对 (i,j)/(j,i)
    hit_mask = np.triu(
        (dist_sq_mat < min_dist_mat * min_dist_mat) & (dist_sq_mat > 0.0),
        k=1,
    )
    pairs = np.argwhere(hit_mask)

    results = []
    for idx in range(len(pairs)):
        i, j = int(pairs[idx, 0]), int(pairs[idx, 1])

        s_a, s_b = snapshot[i], snapshot[j]

        # 两球均冻结时无相对运动，跳过（节约算力）
        if s_a['frozen'] and s_b['frozen']:
            continue

        d    = math.sqrt(float(dist_sq_mat[i, j]))
        nx   = float(dx_mat[i, j]) / d   # 单位法线：从 a 指向 b
        ny   = float(dy_mat[i, j]) / d
        half = (float(min_dist_mat[i, j]) - d) * 0.5

        s_a, s_b = snapshot[i], snapshot[j]

        # 法向相对速度（负 = 正在靠近）
        dvn = (s_b['vx'] - s_a['vx']) * nx + (s_b['vy'] - s_a['vy']) * ny

        normal_impulse   = 0.0
        friction_impulse = 0.0
        tx = ty = 0.0

        if dvn < 0:
            # ── 法向弹性冲量 ─────────────────────────────────────────
            normal_impulse = -(1.0 + elasticity) * dvn * 0.5

            # ── Coulomb 切向摩擦冲量 ─────────────────────────────────
            tx, ty = -ny, nx                         # 切向单位向量（垂直于法线）
            dvt    = ((s_b['vx'] - s_a['vx']) * tx
                      + (s_b['vy'] - s_a['vy']) * ty)
            fi     = dvt * 0.5                       # 消除切向相对速度所需冲量
            max_fi = ball_friction * abs(normal_impulse)
            friction_impulse = max(-max_fi, min(max_fi, fi))

        results.append({
            'ball_a':          s_a['ball'],
            'ball_b':          s_b['ball'],
            'nx': nx, 'ny': ny,
            'half': half,
            'normal_impulse':   normal_impulse,
            'friction_impulse': friction_impulse,
            'tx': tx, 'ty': ty,
        })

    return results


# ──────────────────────────────────────────────────────────────────────

class SnowballManager(BaseManager):
    """
    雪球管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#雪球 数量" 命令
    - 加载并缓存 snowball.png
    - 在屏幕底部随机生成 Snowball 窗口
    - FIFO：超出 max_count 时自动淡出最早的雪球
    - 每 TICK：清理已消亡的雪球 + 异步球间碰撞计算
    """

    MANAGER_ID   = "snowball"
    DISPLAY_NAME = "雪球管理器"
    COMMAND_TRIGGER = "雪球"
    COMMAND_HELP    = "[数量] - 在屏幕底部生成雪球"

    def __init__(self, entity=None):
        self._entity = entity
        self._balls: list[Snowball] = []

        # 读取配置
        from config.config import SNOWBALL
        self._cfg = SNOWBALL

        # 加载 PNG
        self._pixmap_cache: dict[int, QPixmap] = {}  # diameter -> QPixmap
        self._source_pixmap: QPixmap | None = None
        self._load_png()

        # 事件订阅
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH,            self._on_hash_command)
        self._event_center.subscribe(EventType.TICK,                  self._on_tick)
        self._event_center.subscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)

        # 向 # 命令注册中心声明
        get_hash_cmd_registry().register('雪球', '[数量]', '在屏幕底部生成雪球')

        # ── 后台物理线程 ──────────────────────────────────────────────
        # max_workers=1：单线程队列，保证计算有序，不堆积任务
        self._physics_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='snowball_phys'
        )
        self._collision_future: Future | None = None
        # 上一帧提交的快照：apply 时用于校验球对象仍然有效
        self._pending_snapshot: list | None   = None

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "SnowballManager":
        return cls(entity)

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        import os
        png_path = self._cfg.get('png_file', 'resc/GIF/snowball.png')
        if not os.path.exists(png_path):
            log(f"警告：找不到雪球 PNG 文件: {png_path}")
            return
        pix = QPixmap(png_path)
        if pix.isNull():
            log(f"警告：QPixmap 加载失败: {png_path}")
            return
        self._source_pixmap = pix
        log(f"PNG 已加载: {png_path}")

    def _get_pixmap(self, diameter: int) -> QPixmap | None:
        """按直径获取缩放后的 QPixmap（缓存）。"""
        if self._source_pixmap is None:
            return None
        if diameter not in self._pixmap_cache:
            self._pixmap_cache[diameter] = self._source_pixmap.scaled(
                diameter, diameter,
                1,  # Qt.KeepAspectRatio
                1,  # Qt.SmoothTransformation
            )
        return self._pixmap_cache[diameter]

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        text = event.data.get('text', '').strip()
        if not text.startswith('雪球'):
            return

        parts = text.split()
        count = 1
        if len(parts) >= 2:
            try:
                count = max(1, int(parts[1]))
            except ValueError:
                count = 1

        log(f"收到召唤命令，数量：{count}")
        self._spawn_snowballs(count)

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'召唤了 {count} 个雪球！',
            'min':  20,
            'max':  100,
        }))

    def _on_spawn_request(self, event: Event):
        if event.data.get('manager_id') != self.MANAGER_ID:
            return
        spawn_type = event.data.get('spawn_type', 'command')
        if spawn_type == 'command':
            count = event.data.get('count', 1)
            self._spawn_snowballs(count)
        elif spawn_type == 'natural':
            position = event.data.get('position')
            if position:
                self._spawn_one(position)

    def _on_tick(self, event: Event):
        """每 TICK 三步：① 应用上帧碰撞结果 → ② 清理死亡对象 → ③ 提交新一帧计算。"""
        # ① 应用上一帧后台计算结果（若已完成）
        self._apply_collision_results()

        # ② 清理死亡对象
        self._balls = [b for b in self._balls if b.is_alive()]

        # ③ 提交新一帧碰撞计算（非阻塞）
        self._submit_collision_job()

    # ==================================================================
    # 后台碰撞计算：提交与应用
    # ==================================================================

    def _submit_collision_job(self) -> None:
        """为当前帧快照提交后台碰撞计算任务（若上帧未完成则跳过，防止堆积）。"""
        n = len(self._balls)
        if n < 2:
            return

        # 若上帧任务仍未完成，跳过本帧（宁可漏算一帧也不堆积）
        if self._collision_future is not None and not self._collision_future.done():
            return

        elasticity   = self._cfg.get('collision_elasticity', 0.60)
        ball_friction = self._cfg.get('ball_friction', 0.45)

        # 采集快照（纯 Python float，不含 Qt 对象引用给后台线程）
        snapshot = []
        for ball in self._balls:
            if ball._fading or ball._drag_offset is not None:
                continue
            body = ball.physics_body
            snapshot.append({
                'ball':   ball,          # 仅用于 apply 时查找，不会在后台线程中调用其方法
                'cx':     body.x + ball.radius,
                'cy':     body.y + ball.radius,
                'radius': ball.radius,
                'vx':     body.vx,
                'vy':     body.vy,
                'frozen': ball._frozen,
            })

        if len(snapshot) < 2:
            return

        self._pending_snapshot  = snapshot
        self._collision_future  = self._physics_executor.submit(
            _compute_collision_results, snapshot, elasticity, ball_friction
        )

    def _apply_collision_results(self) -> None:
        """将后台计算结果应用到主线程的物理体和 Qt 窗口（幂等，未完成则跳过）。"""
        if self._collision_future is None or not self._collision_future.done():
            return

        future                 = self._collision_future
        self._collision_future = None
        self._pending_snapshot = None

        try:
            results = future.result()
        except Exception as exc:
            log(f"后台碰撞计算异常: {exc}")
            return

        for res in results:
            ball_a: Snowball = res['ball_a']
            ball_b: Snowball = res['ball_b']

            # apply 时再次校验：对象仍然存活且未进入拖拽/淡出
            if not ball_a.is_alive() or ball_a._fading or ball_a._drag_offset is not None:
                continue
            if not ball_b.is_alive() or ball_b._fading or ball_b._drag_offset is not None:
                continue

            body_a = ball_a.physics_body
            body_b = ball_b.physics_body

            nx   = res['nx'];  ny   = res['ny']
            half = res['half']
            tx   = res['tx'];  ty   = res['ty']
            ni   = res['normal_impulse']
            fi   = res['friction_impulse']

            # ── 去穿透：沿法线各推开一半重叠量 ──────────────────────
            body_a.x -= nx * half
            body_a.y -= ny * half
            body_b.x += nx * half
            body_b.y += ny * half

            # 通知 Qt 窗口跟进位置
            if body_a.on_position_change:
                body_a.on_position_change(body_a)
            if body_b.on_position_change:
                body_b.on_position_change(body_b)

            if ni > 0:
                freeze_threshold = self._cfg.get('freeze_impulse_threshold', 2.5)

                # 判断各球是否因冻结而"抵挡"本次冲量
                # 冲量低于阈值 → 冻结球视为固定墙，本帧不解冻、不改变速度
                a_blocked = ball_a._frozen and ni < freeze_threshold
                b_blocked = ball_b._frozen and ni < freeze_threshold

                # ── 法向弹性冲量 + 切向摩擦冲量（按冻结状态分别施加）──
                if not a_blocked:
                    body_a.vx -= ni * nx + fi * tx
                    body_a.vy -= ni * ny + fi * ty
                    if ball_a._frozen:
                        ball_a.unfreeze()      # unfreeze 内部已设 active=True, bounce_count=0
                    else:
                        body_a.active = True
                        body_a.bounce_count = 0

                if not b_blocked:
                    body_b.vx += ni * nx + fi * tx
                    body_b.vy += ni * ny + fi * ty
                    if ball_b._frozen:
                        ball_b.unfreeze()
                    else:
                        body_b.active = True
                        body_b.bounce_count = 0

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_snowballs(self, count: int):
        """在屏幕底部随机生成 count 个雪球（带 FIFO 上限控制）。"""
        if self._source_pixmap is None:
            log("无可用 PNG，跳过生成")
            return

        anchor = None
        if self._entity and hasattr(self._entity, 'get_position'):
            try:
                anchor = self._entity.get_position()
            except Exception:
                anchor = None
        screen = get_screen_geometry_for_point(anchor)
        sx, sy, sw, sh = screen.x(), screen.y(), screen.width(), screen.height()

        y_min_pct = self._cfg.get('spawn_y_min', 0.85)
        y_max_pct = self._cfg.get('spawn_y_max', 0.95)
        size_min  = self._cfg.get('size_min', 24)
        size_max  = self._cfg.get('size_max', 48)

        for _ in range(count):
            diameter = random.randint(size_min, size_max)
            qt_y_top    = sy + int(sh * y_min_pct)
            qt_y_bottom = max(qt_y_top, sy + int(sh * y_max_pct) - diameter)
            x = random.randint(sx, max(sx, sx + sw - diameter))
            y = random.randint(qt_y_top, max(qt_y_top, qt_y_bottom))
            self._spawn_one(QPoint(x, y), diameter)

    def _spawn_one(self, position: QPoint, diameter: int = None):
        """在指定位置生成一个雪球，执行 FIFO 控制。"""
        if self._source_pixmap is None:
            return

        if diameter is None:
            size_min = self._cfg.get('size_min', 24)
            size_max = self._cfg.get('size_max', 48)
            diameter = random.randint(size_min, size_max)

        max_count = self._cfg.get('max_count', 16)

        # 清理死亡对象
        self._balls = [b for b in self._balls if b.is_alive()]

        # FIFO：超出上限时淡出最早的一个
        if len(self._balls) >= max_count:
            oldest = self._balls[0]
            oldest.start_fadeout()
            self._balls.pop(0)
            log(f"FIFO：淡出最早雪球（上限 {max_count}）")

        pix = self._get_pixmap(diameter)
        if pix is None:
            return
        size = (diameter, diameter)

        ball = Snowball(
            pixmap   = pix,
            position = position,
            size     = size,
        )
        self._balls.append(ball)
        log(f"生成雪球 @ ({position.x()}, {position.y()})，直径={diameter}")

    # ==================================================================
    # 公开查询
    # ==================================================================

    def get_alive_count(self) -> int:
        self._balls = [b for b in self._balls if b.is_alive()]
        return len(self._balls)

    def clear_all(self, fadeout: bool = True) -> int:
        self._balls = [b for b in self._balls if b.is_alive()]
        count = len(self._balls)
        for ball in list(self._balls):
            try:
                if fadeout and hasattr(ball, 'start_fadeout'):
                    ball.start_fadeout()
                else:
                    ball.close()
            except Exception:
                pass
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        self._event_center.unsubscribe(EventType.INPUT_HASH,            self._on_hash_command)
        self._event_center.unsubscribe(EventType.TICK,                  self._on_tick)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        for ball in self._balls:
            if ball.is_alive():
                try:
                    ball.close()
                except Exception:
                    pass
        self._balls.clear()
        self._pixmap_cache.clear()
        # 关闭后台线程（不等待，让已提交的任务自然结束）
        self._physics_executor.shutdown(wait=False)
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(SnowballManager.MANAGER_ID, SnowballManager)
