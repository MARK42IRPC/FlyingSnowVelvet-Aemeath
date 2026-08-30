"""雪豹管理器 - 使用动态注册机制，通过事件系统通信"""
import random

from lib.core.event.center    import get_event_center, EventType, Event
from lib.core.graphics.image_loader import load_image_resource
from lib.core.graphics.types import Point, coerce_point
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import (
    create_world_object,
    get_world_object_center,
    WorldObjectInstance,
)
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.plugin_registry import manager_registry, BaseManager
from lib.script.voice.ams_enh   import AmsEnhSound
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SnowLeopardManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class SnowLeopardManager(BaseManager):
    """
    雪豹管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#雪豹 数量" 命令
    - 加载并缓存 snow_leopard.gif 的正向 / 翻转帧
    - 在屏幕底部指定高度范围内随机生成 SnowLeopard 窗口
    - 每 TICK 检测主宠物中心与各雪豹中心的距离，触发淡出
    - 响应 MANAGER_SPAWN_REQUEST 事件支持其他模块触发生成
    """

    MANAGER_ID = "snow_leopard"
    DISPLAY_NAME = "雪豹管理器"
    COMMAND_TRIGGER = "雪豹"
    COMMAND_HELP = "[数量] - 在屏幕底部生成雪豹"

    def __init__(self, entity=None):
        """
        Args:
            entity: 主宠物实体（PetWindow），用于获取位置信息
        """
        self._entity   = entity
        self._leopards: list[WorldObjectInstance] = []
        self._pending_play = False  # 碰撞已发生，等待 move 完全结束后进入 play

        # GIF 帧缓存（正向 + 翻转，在此统一预计算）
        self._resource = None

        # 读取配置
        from config.config import SNOW_LEOPARD
        self._cfg = SNOW_LEOPARD

        # 加载 GIF
        self._load_gif()

        # 与主宠物交互时播放的 ams-enh 音效
        self._ams_enh_sound = AmsEnhSound()

        # 事件订阅
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.TICK,       self._on_tick)
        # 订阅管理器生成请求事件
        self._event_center.subscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        # 订阅实体位置响应事件（用于解耦通信）
        self._event_center.subscribe(EventType.ENTITY_POSITION_RESPONSE, self._handle_entity_position_response)
        # 订阅实体状态响应事件（用于检测移动结束）
        self._event_center.subscribe(EventType.ENTITY_STATE_RESPONSE, self._handle_entity_state_response)
        # 订阅管理器查询请求事件
        self._event_center.subscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        # 订阅目标位置查询事件（用于解耦 state.py）
        self._event_center.subscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)

        # 向全局 # 命令注册中心声明本命令（供提示框显示）
        get_hash_cmd_registry().register('雪豹', '[数量]', '在屏幕底部生成雪豹')

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "SnowLeopardManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    # ==================================================================
    # GIF 加载
    # ==================================================================

    def _load_gif(self):
        """加载雪豹 GIF，生成正向帧和翻转帧缓存。"""
        gif_path = self._cfg.get('gif_file', 'resc/GIF/snow_leopard.gif')
        try:
            resource = load_image_resource(gif_path)
        except Exception as exc:
            log(f"加载 GIF 出错: {exc}")
            return

        if resource is None:
            log(f"加载 GIF 失败：{gif_path}")
            return
        self._resource = resource
        log(f"GIF 已加载：{len(resource.frames)} 帧，文件：{gif_path}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：#雪豹 数量
        event.data['text'] 已去掉开头的 '#'，值如 "雪豹 3"
        """
        text = event.data.get('text', '').strip()
        if not text.startswith('雪豹'):
            return

        # 解析数量（默认 1）
        parts = text.split()
        count = 1
        if len(parts) >= 2:
            try:
                count = max(1, int(parts[1]))
            except ValueError:
                count = 1

        log(f"收到召唤命令，数量：{count}")
        self._spawn_leopards(count)

        # 反馈气泡
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text':  f'召唤了 {count} 只雪豹！',
            'min':   20,
            'max':   100,
        }))

    def _on_spawn_request(self, event: Event):
        """
        处理 MANAGER_SPAWN_REQUEST 事件。

        支持其他模块（如雪堆）通过事件请求生成雪豹。
        事件数据格式：
        {
            'manager_id': 'snow_leopard',  # 目标管理器ID
            'spawn_type': 'natural',       # 生成类型：'natural' 或 'command'
            'position': Point(x, y),       # 生成位置（仅 natural 类型）
        }
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        spawn_type = event.data.get('spawn_type', 'natural')

        if spawn_type == 'natural':
            position = event.data.get('position')
            if position:
                self.spawn_natural(position)
        elif spawn_type == 'command':
            count = event.data.get('count', 1)
            self._spawn_leopards(count)

    def _on_tick(self, event: Event):
        """
        每 TICK 检测主宠物与各雪豹的距离。

        当雪豹中心进入主宠物中心锚点偏移后 interact_radius 像素范围内时，
        触发该雪豹淡出消失。
        """
        # 清理已消亡的雪豹对象
        self._leopards = [l for l in self._leopards if l.is_alive()]
        if not self._leopards:
            return

        # 通过事件获取主宠物位置，而非直接引用 entity
        # 发布位置请求事件
        self._event_center.publish(Event(EventType.ENTITY_POSITION_REQUEST, {
            'entity_id': 'pet_window',
            'request_id': 'snow_leopard_interaction'
        }))

    def _handle_entity_position_response(self, event: Event):
        """处理实体位置响应事件"""
        if event.data.get('request_id') != 'snow_leopard_interaction':
            return

        pet_pos = coerce_point(event.data.get('position'))
        pet_size = event.data.get('size')

        if pet_pos is None or not pet_size:
            return

        pet_cx = pet_pos.x + pet_size[0] // 2
        pet_cy = pet_pos.y + pet_size[1] // 2

        radius = self._cfg.get('interact_radius', 50)
        r2     = radius * radius  # 使用距离平方避免 sqrt

        for leopard in self._leopards:
            if leopard.get_state().fading:
                continue
            lc = get_world_object_center(leopard)
            dx = pet_cx - lc.x
            dy = pet_cy - lc.y
            if dx * dx + dy * dy <= r2:
                log("雪豹进入交互范围，触发淡出；等待移动结束后进入 play")
                leopard.start_fadeout()
                self._ams_enh_sound.play()  # 主宠物与雪豹完成交互时播放 ams-enh 音效
                self._pending_play = True  # 打标记，不立即切换状态

                # 发布交互事件
                self._event_center.publish(Event(EventType.MANAGER_INTERACTION, {
                    'manager_id': self.MANAGER_ID,
                    'entity_id': 'pet_window',
                }))

                # 请求实体状态以检测移动结束
                self._event_center.publish(Event(EventType.ENTITY_STATE_QUERY, {
                    'entity_id': 'pet_window',
                    'query_type': 'all',
                    'request_id': 'snow_leopard_play_check',
                }))

    def _handle_entity_state_response(self, event: Event):
        """处理实体状态响应事件 - 检测移动结束后触发 play 状态"""
        if event.data.get('request_id') != 'snow_leopard_play_check':
            return

        if not self._pending_play:
            return

        is_moving = event.data.get('is_moving', True)
        current_state = event.data.get('current_state', 'moving')

        # 移动完全结束（物理停止 + 状态机已回到 idle）后才触发 play
        if not is_moving and current_state == 'idle':
            self._pending_play = False
            log("移动结束，进入 play 状态")
            self._event_center.publish(Event(EventType.STATE_CHANGE_REQUEST, {
                'new_state': 'play',
                'by_event':  False,  # 从 idle 切换，无需事件打断
            }))

    def _on_query_request(self, event: Event):
        """处理管理器查询请求事件"""
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        query_type = event.data.get('query_type')

        if query_type == 'alive_count':
            count = self.get_alive_count()
            self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                'manager_id': self.MANAGER_ID,
                'query_type': query_type,
                'result': count,
                'request_id': event.data.get('request_id'),
                'callback_data': event.data.get('callback_data'),
            }))

    def _on_target_position_query(self, event: Event):
        """处理目标位置查询事件（解耦 state.py 与管理器直接依赖）"""
        target_type = event.data.get('target_type')
        if target_type != 'snow_leopard':
            return

        from_pos = event.data.get('requester_pos')
        nearest = self.get_nearest_leopard_pos(from_pos) if from_pos else None
        log(f"目标位置查询: from_pos={from_pos}, nearest={nearest}")
        self._event_center.publish(Event(EventType.TARGET_POSITION_RESPONSE, {
            'target_type': 'snow_leopard',
            'position': nearest,
        }))

    # ==================================================================
    # 供外部查询 / 雪堆触发生成
    # ==================================================================

    def get_alive_count(self) -> int:
        """返回当前活跃（未消亡）的雪豹总数。"""
        self._leopards = [l for l in self._leopards if l.is_alive()]
        return len(self._leopards)

    def spawn_natural(self, position: Point | object, power_min: float = None, power_max: float = None) -> None:
        """
        在指定位置生成一只雪豹（供雪堆调用，不检查上限，由调用方控制）。

        Args:
            position:   目标位置（通常为雪堆中心的全局屏幕坐标）
            power_min:  弹跳力度最小倍率（None 时使用 SNOW_PILE 配置）
            power_max:  弹跳力度最大倍率（None 时使用 SNOW_PILE 配置）
        """
        if self._resource is None:
            log("无可用帧，跳过生成")
            return
        point = coerce_point(position)
        if point is None:
            return

        size = self._cfg.get('size', (80, 80))
        w, h = size

        screen = get_screen_rect_for_point(point)
        # 以 position 为中心放置，确保不超出屏幕边界
        min_x = int(screen.x)
        min_y = int(screen.y)
        max_x = int(screen.x + screen.width - w)
        max_y = int(screen.y + screen.height - h)
        if max_x < min_x:
            max_x = min_x
        if max_y < min_y:
            max_y = min_y
        x = max(min_x, min(int(point.x) - w // 2, max_x))
        y = max(min_y, min(int(point.y) - h // 2, max_y))

        leopard = create_world_object(
            "snow_leopard",
            resource       = self._resource,
            position       = Point(x, y),
            size           = size,
        )
        self._leopards.append(leopard)
        
        # 使用传入的力度参数，或从 SNOW_PILE 配置中读取
        if power_min is None or power_max is None:
            from config.config import SNOW_PILE
            if power_min is None:
                power_min = SNOW_PILE.get('spawn_power_min', 0.8)
            if power_max is None:
                power_max = SNOW_PILE.get('spawn_power_max', 1.8)
        
        leopard.spawn_jump(power_min, power_max)  # 生成时触发随机弹跳，使雪豹散开，避免重叠
        log(f"雪堆触发：生成雪豹 @ ({x}, {y})")

    # ==================================================================
    # 生成逻辑（命令，不检查上限）
    # ==================================================================

    def _spawn_leopards(self, count: int):
        """在屏幕底部指定高度范围内随机生成 count 只雪豹。"""
        if self._resource is None:
            log("无可用帧，跳过生成")
            return

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
        size   = self._cfg.get('size', (80, 80))
        w, h   = size

        # 生成区域配置：
        #   spawn_y_min / spawn_y_max 为屏幕高度占比，
        #   0.0 = 屏幕顶部，1.0 = 屏幕底部。
        #   默认 0.85~0.95，即屏幕高度 85%~95% 处（接近底部）。
        y_min_pct = self._cfg.get('spawn_y_min', 0.85)
        y_max_pct = self._cfg.get('spawn_y_max', 0.95)

        # 转换为桌面像素坐标（左上角），确保雪豹不超出屏幕边界
        y_top = sy + int(sh * y_min_pct)  # 生成区域上沿
        y_bottom = max(y_top, sy + int(sh * y_max_pct) - h)  # 生成区域下沿（减去自身高度）

        # 从 SNOW_PILE 配置读取生成时力度参数
        from config.config import SNOW_PILE
        power_min = SNOW_PILE.get('spawn_power_min', 0.8)
        power_max = SNOW_PILE.get('spawn_power_max', 1.8)

        for _ in range(count):
            min_x = sx
            max_x = max(sx, sx + sw - w)
            x = random.randint(min_x, max_x)
            y = random.randint(y_top, max(y_top, y_bottom))

            leopard = create_world_object(
                "snow_leopard",
                resource       = self._resource,
                position       = Point(x, y),
                size           = size,
            )
            self._leopards.append(leopard)
            leopard.spawn_jump(power_min, power_max)
            log(f"生成雪豹 @ ({x}, {y})，翻转={leopard.get_state().flipped}")

    # ==================================================================
    # 供状态机查询
    # ==================================================================

    def get_nearest_leopard_pos(self, from_pos: Point | object) -> Point | None:
        """
        返回距离 from_pos 最近的活跃（未淡出）雪豹的中心坐标。

        若无活跃雪豹则返回 None。
        供 StateMachine._trigger_wander() 使用。
        """
        alive = [
            leopard
            for leopard in self._leopards
            if leopard.is_alive() and not leopard.get_state().fading
        ]
        if not alive:
            return None

        origin = coerce_point(from_pos)
        if origin is None:
            return None
        centers = [
            (leopard, get_world_object_center(leopard))
            for leopard in alive
        ]
        centers = [(leopard, center) for leopard, center in centers if center is not None]
        if not centers:
            return None
        return min(
            centers,
            key=lambda item: (
                (item[1].x - origin.x) ** 2
                + (item[1].y - origin.y) ** 2
            ),
        )[1]

    def clear_all_leopards(self, fadeout: bool = True) -> int:
        """批量清理所有存活雪豹，返回清理数量。"""
        self._leopards = [l for l in self._leopards if l.is_alive()]
        alive = list(self._leopards)
        count = len(alive)
        for leopard in alive:
            try:
                if fadeout and hasattr(leopard, "start_fadeout"):
                    leopard.start_fadeout()
                else:
                    leopard.close()
            except Exception:
                pass
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅，关闭所有雪豹窗口。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.TICK,       self._on_tick)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        self._event_center.unsubscribe(EventType.ENTITY_POSITION_RESPONSE, self._handle_entity_position_response)
        self._event_center.unsubscribe(EventType.ENTITY_STATE_RESPONSE, self._handle_entity_state_response)
        self._event_center.unsubscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        self._event_center.unsubscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)
        for leopard in self._leopards:
            if leopard.is_alive():
                try:
                    leopard.close()
                except Exception:
                    pass
        self._leopards.clear()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

# 自动注册到全局注册表
manager_registry.register(SnowLeopardManager.MANAGER_ID, SnowLeopardManager)
