"""雪球管理器 - FIFO 数量控制"""
import math
import random
from concurrent.futures import Future

import numpy as np

from lib.core.event.center        import get_event_center, EventType, Event
from lib.core.compute_hub         import get_compute_hub
from lib.core.graphics.image_loader import load_image_resource, resize_image_resource
from lib.core.graphics.resources import ImageResource
from lib.core.graphics.types      import Point, coerce_point
from lib.core.hash_cmd_registry   import get_hash_cmd_registry
from lib.core.plugin_registry     import manager_registry, BaseManager
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import create_world_object, WorldObjectInstance
from lib.core.logger              import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SnowballManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 后台碰撞计算（纯数据，不接触绘制后端对象）
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
        self._balls: list[WorldObjectInstance] = []

        # 读取配置
        from config.config import SNOWBALL
        self._cfg = SNOWBALL

        # 加载 PNG
        self._image_cache: dict[int, ImageResource] = {}
        self._source_resource: ImageResource | None = None
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
        png_path = self._cfg.get('png_file', 'resc/GIF/snowball.png')
        resource = load_image_resource(png_path)
        if resource is None:
            log(f"警告：图片加载失败: {png_path}")
            return
        self._source_resource = resource
        log(f"PNG 已加载: {png_path}")

    def _get_image(self, diameter: int) -> ImageResource | None:
        """按直径获取缩放后的资源（缓存）。"""
        if self._source_resource is None:
            return None
        if diameter not in self._image_cache:
            self._image_cache[diameter] = resize_image_resource(
                self._source_resource,
                (diameter, diameter),
            )
        return self._image_cache[diameter]

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

        # 采集快照（纯 Python float，不把后端对象传给后台线程）
        snapshot = []
        for ball in self._balls:
            state = ball.get_state()
            motion = ball.get_motion()
            if motion is None or state.fading or state.dragging:
                continue
            snapshot.append({
                'ball':   ball,          # 仅用于 apply 时查找，不会在后台线程中调用其方法
                'cx':     motion.position.x + motion.radius,
                'cy':     motion.position.y + motion.radius,
                'radius': motion.radius,
                'vx':     motion.velocity.x,
                'vy':     motion.velocity.y,
                'frozen': state.frozen,
            })

        if len(snapshot) < 2:
            return

        self._pending_snapshot  = snapshot
        future = get_compute_hub().submit_latest(
            "snowball_collision",
            _compute_collision_results,
            snapshot,
            elasticity,
            ball_friction,
            executor="vector",
        )
        if future is None:
            return
        self._collision_future = future

    def _apply_collision_results(self) -> None:
        """将后台计算结果应用到主线程的物理体和对象宿主（幂等）。"""
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
            ball_a = res['ball_a']
            ball_b = res['ball_b']

            # apply 时再次校验：对象仍然存活且未进入拖拽/淡出
            state_a = ball_a.get_state()
            state_b = ball_b.get_state()
            if not state_a.alive or state_a.fading or state_a.dragging:
                continue
            if not state_b.alive or state_b.fading or state_b.dragging:
                continue

            nx   = res['nx'];  ny   = res['ny']
            half = res['half']
            tx   = res['tx'];  ty   = res['ty']
            ni   = res['normal_impulse']
            fi   = res['friction_impulse']

            # ── 去穿透：沿法线各推开一半重叠量 ──────────────────────
            position_a = Point(-nx * half, -ny * half)
            position_b = Point(nx * half, ny * half)

            if ni > 0:
                freeze_threshold = self._cfg.get('freeze_impulse_threshold', 2.5)

                # 判断各球是否因冻结而"抵挡"本次冲量
                # 冲量低于阈值 → 冻结球视为固定墙，本帧不解冻、不改变速度
                a_blocked = state_a.frozen and ni < freeze_threshold
                b_blocked = state_b.frozen and ni < freeze_threshold

                # ── 法向弹性冲量 + 切向摩擦冲量（按冻结状态分别施加）──
                if not a_blocked:
                    ball_a.apply_motion_delta(
                        position=position_a,
                        velocity=Point(-ni * nx - fi * tx, -ni * ny - fi * ty),
                        wake=True,
                    )
                else:
                    ball_a.apply_motion_delta(position=position_a)

                if not b_blocked:
                    ball_b.apply_motion_delta(
                        position=position_b,
                        velocity=Point(ni * nx + fi * tx, ni * ny + fi * ty),
                        wake=True,
                    )
                else:
                    ball_b.apply_motion_delta(position=position_b)
            else:
                ball_a.apply_motion_delta(position=position_a)
                ball_b.apply_motion_delta(position=position_b)

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_snowballs(self, count: int):
        """在屏幕底部随机生成 count 个雪球（带 FIFO 上限控制）。"""
        if self._source_resource is None:
            log("无可用 PNG，跳过生成")
            return

        anchor = None
        if self._entity and hasattr(self._entity, 'get_core_position'):
            try:
                anchor = self._entity.get_core_position()
            except Exception:
                anchor = None
        screen = get_screen_rect_for_point(anchor)
        sx, sy, sw, sh = (
            int(screen.x),
            int(screen.y),
            int(screen.width),
            int(screen.height),
        )

        y_min_pct = self._cfg.get('spawn_y_min', 0.85)
        y_max_pct = self._cfg.get('spawn_y_max', 0.95)
        size_min  = self._cfg.get('size_min', 24)
        size_max  = self._cfg.get('size_max', 48)

        for _ in range(count):
            diameter = random.randint(size_min, size_max)
            y_top = sy + int(sh * y_min_pct)
            y_bottom = max(y_top, sy + int(sh * y_max_pct) - diameter)
            x = random.randint(sx, max(sx, sx + sw - diameter))
            y = random.randint(y_top, max(y_top, y_bottom))
            self._spawn_one(Point(x, y), diameter)

    def _spawn_one(self, position: Point | object, diameter: int = None):
        """在指定位置生成一个雪球，执行 FIFO 控制。"""
        if self._source_resource is None:
            return
        point = coerce_point(position)
        if point is None:
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

        image = self._get_image(diameter)
        if image is None:
            return
        size = (diameter, diameter)

        ball = create_world_object(
            "snowball",
            resource = image,
            position = point,
            size     = size,
        )
        self._balls.append(ball)
        log(f"生成雪球 @ ({point.x}, {point.y})，直径={diameter}")

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
        self._image_cache.clear()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(SnowballManager.MANAGER_ID, SnowballManager)
