"""雪堆管理器 - 使用动态注册机制，通过事件系统通信"""
import random

from config.config              import SNOW_LEOPARD
from lib.core.event.center      import get_event_center, EventType, Event
from lib.core.graphics.image_loader import load_image_resource, resize_image_resource
from lib.core.graphics.types    import Point, coerce_point
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.plugin_registry   import manager_registry, BaseManager
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import (
    create_world_object,
    WorldObjectInstance,
)
from lib.script.voice.snow        import SnowSound
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SnowPileManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class SnowPileManager(BaseManager):
    """
    雪堆管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#雪堆 数量" 命令
    - 加载基础尺寸的 snow.png 图片，生成时对每个雪堆随机缩放 120%~150%
    - 通过事件系统请求雪豹管理器生成雪豹（解耦通信）
    """

    MANAGER_ID = "snow_pile"
    DISPLAY_NAME = "雪堆管理器"
    COMMAND_TRIGGER = "雪堆"
    COMMAND_HELP = "[数量] - 在屏幕底部放置雪堆"

    def __init__(self, entity=None):
        self._entity = entity
        self._piles: list[WorldObjectInstance] = []
        self._resource = None

        from config.config import SNOW_PILE
        self._cfg = SNOW_PILE

        self._load_png()

        # 生成雪豹时的出现音效
        self._snow_sound = SnowSound()

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.MANAGER_INTERACTION, self._on_manager_interaction)
        # 订阅管理器查询响应事件（用于雪豹数量检查）
        self._event_center.subscribe(EventType.MANAGER_QUERY_RESPONSE, self.handle_query_response)

        get_hash_cmd_registry().register('雪堆', '[数量]', '在屏幕底部放置雪堆')

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "SnowPileManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        """加载雪堆 PNG，缓存基础尺寸图片（供后续随机缩放使用）。"""
        png_path = self._cfg.get('png_file', 'resc/GIF/snow.png')
        resource = load_image_resource(png_path)
        if resource is None:
            log(f"加载 PNG 失败: {png_path}")
            return

        base_w, base_h = self._cfg.get('size', (80, 80))
        self._resource = resize_image_resource(
            resource,
            (base_w, base_h),
            keep_aspect=True,
        )
        actual_w, actual_h = self._resource.size
        log(f"PNG 已加载：{png_path}，基础尺寸 {actual_w}x{actual_h}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：#雪堆 数量
        event.data['text'] 已去掉开头的 '#'
        """
        text = event.data.get('text', '').strip()
        if not text.startswith('雪堆'):
            return

        parts = text.split()
        count = 1
        if len(parts) >= 2:
            try:
                count = max(1, int(parts[1]))
            except ValueError:
                count = 1

        log(f"收到召唤命令，数量：{count}")
        self._spawn_piles(count)

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'放置了 {count} 个雪堆！',
            'min':  20,
            'max':  100,
        }))

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_piles(self, count: int):
        """在屏幕底部生成 count 个随机缩放（120%~150%）的雪堆。"""
        if self._resource is None:
            log("无可用图片，跳过生成")
            return

        # 清理已关闭的旧引用
        self._piles = [p for p in self._piles if p.is_alive()]

        anchor = None
        if self._entity and hasattr(self._entity, 'get_core_position'):
            try:
                anchor = self._entity.get_core_position()
            except Exception:
                anchor = None
        screen = get_screen_rect_for_point(anchor)
        sx = int(screen.x)
        sy = int(screen.y)
        sw = int(screen.width)
        sh = int(screen.height)

        base_w, base_h = self._resource.size
        y_min_pct = self._cfg.get('spawn_y_min', 0.82)
        y_max_pct = self._cfg.get('spawn_y_max', 0.93)
        scale_min = self._cfg.get('scale_min',   1.2)
        scale_max = self._cfg.get('scale_max',   1.5)

        for _ in range(count):
            # 每个雪堆独立随机缩放（120%~150%）
            scale      = random.uniform(scale_min, scale_max)
            scaled_w   = int(base_w * scale)
            scaled_h   = int(base_h * scale)
            pile_resource = resize_image_resource(
                self._resource,
                (scaled_w, scaled_h),
                keep_aspect=True,
            )
            actual_w, actual_h = pile_resource.size

            y_top = sy + int(sh * y_min_pct)
            y_bottom = max(y_top, sy + int(sh * y_max_pct) - actual_h)

            x = random.randint(sx, max(sx, sx + sw - actual_w))
            y = random.randint(y_top, max(y_top, y_bottom))

            pile = create_world_object(
                "snow_pile",
                resource       = pile_resource,
                position       = Point(x, y),
                size           = (actual_w, actual_h),
                batch_interval = tuple(self._cfg.get('batch_interval', (10000, 20000))),
                batch_size = tuple(self._cfg.get('batch_size', (1, 2))),
                batch_item_interval = tuple(
                    self._cfg.get('batch_item_interval', (3000, 5000))
                ),
            )
            self._piles.append(pile)
            log(f"生成雪堆 @ ({x}, {y})，缩放比例 {scale:.2f}x，尺寸 {actual_w}x{actual_h}")

    # ==================================================================
    # 雪豹生成（通过事件系统，解耦通信）
    # ==================================================================

    def _on_manager_interaction(self, event: Event) -> None:
        """
        雪堆请求生成一只雪豹（批次自动生成 / 右键触发）。

        通过事件系统请求雪豹管理器生成，实现解耦通信。
        上限来自 SNOW_LEOPARD['natural_spawn_limit']，默认 12 只。
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return
        if event.data.get('action') != 'spawn_leopard':
            return
        pile_center = coerce_point(event.data.get('position'))
        if pile_center is None:
            return

        # 先查询当前雪豹数量
        self._event_center.publish(Event(EventType.MANAGER_QUERY_REQUEST, {
            'manager_id': 'snow_leopard',
            'query_type': 'alive_count',
            'request_id': 'snow_pile_spawn_check',
            'callback_data': {'pile_center': pile_center},
        }))

    def handle_query_response(self, event: Event):
        """处理管理器查询响应事件"""
        if event.data.get('request_id') != 'snow_pile_spawn_check':
            return

        alive_count = event.data.get('result', 0)
        pile_center = event.data.get('callback_data', {}).get('pile_center')

        if pile_center is None:
            return

        limit = SNOW_LEOPARD.get('natural_spawn_limit', 12)
        if alive_count >= limit:
            log(f"自然生成上限({limit})已达到（当前 {alive_count} 只），跳过")
            return

        # 通过事件请求生成雪豹
        self._event_center.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
            'manager_id': 'snow_leopard',
            'spawn_type': 'natural',
            'position': pile_center,
        }))

        # snow 音效 + snow 粒子（与雪豹淡出同款，作为出现特效）
        self._snow_sound.play()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snow',
            'area_type':   'point',
            'area_data':   (pile_center.x, pile_center.y),
        }))
        log(f"雪堆触发：生成雪豹 @ ({pile_center.x}, {pile_center.y})，"
            f"当前雪豹数 {alive_count + 1}/{limit}")

    def clear_all_piles(self) -> int:
        """批量清理所有存活雪堆，返回清理数量。"""
        self._piles = [p for p in self._piles if p.is_alive()]
        alive = list(self._piles)
        count = len(alive)
        for pile in alive:
            try:
                pile.close()
            except Exception:
                pass
        self._piles.clear()
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅，关闭所有雪堆窗口。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.MANAGER_INTERACTION, self._on_manager_interaction)
        self._event_center.unsubscribe(EventType.MANAGER_QUERY_RESPONSE, self.handle_query_response)
        for pile in self._piles:
            if pile.is_alive():
                try:
                    pile.close()
                except Exception:
                    pass
        self._piles.clear()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(SnowPileManager.MANAGER_ID, SnowPileManager)
