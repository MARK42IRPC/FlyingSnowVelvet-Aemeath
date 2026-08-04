"""摩托管理器 - 使用动态注册机制，通过事件系统通信"""
import random

from lib.core.event.center      import get_event_center, EventType, Event
from lib.core.graphics.types import Point, coerce_point
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.plugin_registry   import manager_registry, BaseManager
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import (
    create_world_object,
    get_world_object_center,
    load_width_scaled_image_pair,
)
from lib.script.music import get_music_service
from lib.core.logger import get_logger

_logger = get_logger(__name__)
_MORTOR_BGM_KEYWORD = "于无羁之昼点亮真彩"
_MORTOR_BGM_SONG_ID = 3333190680
_MORTOR_BGM_DISPLAY = "03:40 于无羁之昼点亮真彩（Throttle Up!) - 鸣潮先约电台"


def log(msg: str):
    _logger.debug("[MortorManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class MortorManager(BaseManager):
    """
    摩托管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#摩托 数量" 命令
    - 加载并缓存 mortor.png 正向 / 翻转图片句柄
    - 在屏幕底部区域随机生成 Mortor 窗口
    - 响应 MANAGER_QUERY_REQUEST 事件提供最近摩托位置
    """

    MANAGER_ID = "mortor"
    DISPLAY_NAME = "摩托管理器"
    COMMAND_TRIGGER = "摩托"
    COMMAND_HELP = "[数量] - 在屏幕上放置摩托"

    def __init__(self, entity=None):
        """
        Args:
            entity: 主宠物实体（PetWindow），用于获取位置信息
        """
        self._entity = entity
        self._mortors: list[object] = []

        # 绘制后端图片缓存（正向 + 翻转）
        self._image:         object | None = None
        self._flipped_image: object | None = None
        self._actual_size:    tuple[int, int] = (120, 120)

        # 重力开关状态（True = 重力开启）
        self._gravity_enabled = True
        # 摩托 BGM 是否由本管理器启动（用于在摩托全部消失时自动暂停）
        self._bgm_started_by_mortor = False

        # 读取配置
        from config.config import MORTOR
        self._cfg = MORTOR

        # 加载图片
        self._load_png()

        # 事件订阅
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        # 订阅管理器生成请求事件（供 ToolDispatcher 等模块触发生成）
        self._event_center.subscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        # 订阅管理器查询事件
        self._event_center.subscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        # 订阅目标位置查询事件（用于解耦 state.py）
        self._event_center.subscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)
        # 订阅保护半径检测事件
        self._event_center.subscribe(EventType.PROTECTION_CHECK, self._on_protection_check)
        # 监控摩托存活状态，用于在全部消失时暂停 BGM
        self._event_center.subscribe(EventType.TICK, self._on_tick)

        # 向全局 # 命令注册中心声明本命令（供提示框显示）
        get_hash_cmd_registry().register('摩托', '[数量]', '在屏幕上放置摩托')

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "MortorManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        """加载摩托 PNG，生成正向和翻转图片缓存。"""
        png_path = self._cfg.get('png_file', 'resc/GIF/mortor.png')
        # 按目标宽度等比缩放
        target_w = int(self._cfg.get('target_width', 400))
        pair = load_width_scaled_image_pair(png_path, target_w)
        if pair is None:
            log(f"加载 PNG 失败: {png_path}")
            return

        self._actual_size = pair.size
        self._image         = pair.image
        self._flipped_image = pair.flipped_image
        log(f"PNG 已加载：{png_path}，缩放至 {pair.size[0]}x{pair.size[1]}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：
        - #摩托 数量
        event.data['text'] 已去掉开头的 '#'，值如 "摩托 2"
        """
        text = event.data.get('text', '').strip()

        if not text.startswith('摩托'):
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
        self._spawn_mortors(count)

        # 反馈气泡
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'放置了 {count} 个摩托！',
            'min':  20,
            'max':  100,
        }))

    def _on_spawn_request(self, event: Event):
        """
        处理 MANAGER_SPAWN_REQUEST 事件。

        供 ToolDispatcher 等模块通过事件触发生成摩托。
        事件数据格式：
        {
            'manager_id': 'mortor',   # 目标管理器ID
            'spawn_type': 'command', # 生成类型
            'count': 1,              # 生成数量（可选，默认1）
        }
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        count = 1
        try:
            count = max(1, int(event.data.get('count', 1)))
        except (ValueError, TypeError):
            count = 1

        log(f"收到 MANAGER_SPAWN_REQUEST，生成 {count} 个摩托")
        self._spawn_mortors(count)

    def _on_query_request(self, event: Event):
        """
        处理 MANAGER_QUERY_REQUEST 事件。

        支持其他模块通过事件查询摩托信息，如最近摩托位置、保护范围等。
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        query_type = event.data.get('query_type')

        if query_type == 'nearest_position':
            from_pos = event.data.get('from_position')
            if from_pos:
                nearest = self.get_nearest_mortor_pos(from_pos)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': nearest,
                    'request_id': event.data.get('request_id'),
                }))

        elif query_type == 'protection_check':
            pet_center = event.data.get('pet_center')
            if pet_center:
                in_protection = self.is_pet_in_mortor_protection(pet_center)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': in_protection,
                    'request_id': event.data.get('request_id'),
                }))

    def _on_target_position_query(self, event: Event):
        """处理目标位置查询事件（解耦 state.py 与管理器直接依赖）"""
        target_type = event.data.get('target_type')
        if target_type != 'mortor':
            return

        from_pos = event.data.get('requester_pos')
        if from_pos:
            nearest = self.get_nearest_mortor_pos(from_pos)
            self._event_center.publish(Event(EventType.TARGET_POSITION_RESPONSE, {
                'target_type': 'mortor',
                'position': nearest,
            }))

    def _on_protection_check(self, event: Event):
        """处理保护半径检测请求事件"""
        pet_position = event.data.get('pet_position')
        current_in_protection = event.data.get('current_in_protection', False)
        request_id = event.data.get('request_id')
        if pet_position:
            in_protection = self.is_pet_in_mortor_protection(pet_position, current_in_protection)
            # 使用专门的响应事件类型
            self._event_center.publish(Event(EventType.PROTECTION_RESPONSE, {
                'in_protection': in_protection,
                'request_id': request_id,
                'manager_id': self.MANAGER_ID,
            }))

    def _on_tick(self, event: Event):
        """Tick 更新：清理失活摩托，并在全部消失时暂停摩托 BGM。"""
        self._mortors = [m for m in self._mortors if m.is_alive()]

        bgm_enabled = bool(self._cfg.get('bgm_enabled', True))
        if not bgm_enabled and self._bgm_started_by_mortor:
            self._pause_mortor_bgm()
            return

        if self._bgm_started_by_mortor and not self._mortors:
            self._pause_mortor_bgm()

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_mortors(self, count: int):
        """在宠物当前位置生成 count 个摩托，中心锚点对齐。"""
        if self._image is None:
            log("无可用图片，跳过生成")
            return
        self._mortors = [m for m in self._mortors if m.is_alive()]
        had_alive = bool(self._mortors)

        screen = get_screen_rect_for_point()
        size   = self._actual_size
        w, h   = size

        # 获取宠物当前位置（中心锚点）
        pet_center = None
        if self._entity and hasattr(self._entity, 'get_core_geometry'):
            pet_geometry = self._entity.get_core_geometry()
            pet_center = Point(
                round(pet_geometry.x + pet_geometry.width / 2),
                round(pet_geometry.y + pet_geometry.height / 2),
            )
            screen = get_screen_rect_for_point(pet_center)

        for _ in range(count):
            if pet_center:
                # 以宠物中心为基准生成，添加随机偏移
                offset_x = random.randint(-50, 50)
                offset_y = random.randint(-50, 50)
                x = int(pet_center.x) - w // 2 + offset_x
                y = int(pet_center.y) - h // 2 + offset_y
            else:
                # 兜底：屏幕底部随机生成
                sx = int(screen.x)
                sy = int(screen.y)
                sw = int(screen.width)
                sh = int(screen.height)
                y_min_pct = self._cfg.get('spawn_y_min', 0.80)
                y_max_pct = self._cfg.get('spawn_y_max', 0.90)
                y_top = sy + int(sh * y_min_pct)
                y_bottom = max(y_top, sy + int(sh * y_max_pct) - h)
                x = random.randint(sx, max(sx, sx + sw - w))
                y = random.randint(y_top, max(y_top, y_bottom))

            # 边界检查
            min_x = int(screen.x)
            min_y = int(screen.y)
            max_x = int(screen.x + screen.width - w)
            max_y = int(screen.y + screen.height - h)
            if max_x < min_x:
                max_x = min_x
            if max_y < min_y:
                max_y = min_y
            x = max(min_x, min(x, max_x))
            y = max(min_y, min(y, max_y))

            mortor = create_world_object(
                "motor",
                image          = self._image,
                flipped_image  = self._flipped_image,
                position       = Point(x, y),
                size           = size,
            )
            # 继承管理器的重力状态
            if not self._gravity_enabled:
                mortor.set_gravity_enabled(False)
            self._mortors.append(mortor)
            log(f"生成摩托 @ ({x}, {y})")

        if (not had_alive) and self._mortors and bool(self._cfg.get('bgm_enabled', True)):
            self._play_mortor_bgm()

    def _play_mortor_bgm(self) -> None:
        """摩托出现时立即播放指定 BGM。"""
        try:
            get_music_service().initialize()
        except Exception:
            pass

        if not get_music_service().can_takeover_for_bgm():
            logger.info("[MortorManager] 当前已有音乐播放，跳过摩托 BGM")
            return

        self._event_center.publish(Event(EventType.MUSIC_PLAY_TOP, {
            'song_id': _MORTOR_BGM_SONG_ID,
            'display': _MORTOR_BGM_DISPLAY,
            'keyword': _MORTOR_BGM_KEYWORD,
        }))
        self._bgm_started_by_mortor = True

    def _pause_mortor_bgm(self) -> None:
        """摩托全部消失时暂停 BGM。"""
        try:
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {'playing': False}))
        except Exception as exc:
            logger.error("[MortorManager] 暂停摩托BGM失败: %s", exc)
        self._bgm_started_by_mortor = False

    # ==================================================================
    # 供状态机查询
    # ==================================================================

    def get_nearest_mortor_pos(self, from_pos: Point | object) -> Point | None:
        """
        返回距离 from_pos 最近的存活摩托的中心坐标。

        若无存活摩托则返回 None。
        供 StateMachine._trigger_wander() 使用。
        """
        # 清理已关闭的摩托引用
        self._mortors = [s for s in self._mortors if s.is_alive()]
        if not self._mortors:
            return None

        origin = coerce_point(from_pos)
        if origin is None:
            return None
        centers = [
            (mortor, get_world_object_center(mortor))
            for mortor in self._mortors
        ]
        centers = [(mortor, center) for mortor, center in centers if center is not None]
        if not centers:
            return None
        return min(
            centers,
            key=lambda item: (
                (item[1].x - origin.x) ** 2
                + (item[1].y - origin.y) ** 2
            ),
        )[1]

    def is_pet_in_mortor_protection(self, pet_center: Point | object, in_protection: bool = False) -> bool:
        """
        检测宠物中心是否在任意摩托的保护半径内。
        使用滞回机制防止边缘抖动。

        Args:
            pet_center: 主宠物中心坐标（全局屏幕坐标）
            in_protection: 当前是否已在保护范围内（用于滞回判断）

        Returns:
            True 如果宠物在任一摩托的保护半径内
        """
        alive = [s for s in self._mortors if s.is_alive()]
        if not alive:
            return False

        # 找到距离最近的摩托
        pet_point = coerce_point(pet_center)
        if pet_point is None:
            return False
        min_dist_sq = float('inf')
        for mortor in alive:
            sc = get_world_object_center(mortor)
            dx = pet_point.x - sc.x
            dy = pet_point.y - sc.y
            dist_sq = dx * dx + dy * dy
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq

        # 滞回机制：进入和离开使用不同的阈值
        enter_radius = self._cfg.get('protect_radius', 10)
        exit_radius = self._cfg.get('protect_radius_exit', enter_radius + 20)
        
        # 根据当前状态选择阈值
        threshold = exit_radius if in_protection else enter_radius
        threshold_sq = threshold * threshold

        return min_dist_sq <= threshold_sq

    def set_gravity_enabled(self, enabled: bool):
        """
        设置所有摩托的重力开关状态。

        Args:
            enabled: True 开启重力，False 关闭重力
        """
        self._gravity_enabled = enabled
        for mortor in self._mortors:
            if mortor.is_alive():
                mortor.set_gravity_enabled(enabled)
        status = "开启" if enabled else "关闭"
        log(f"重力已{status}")

    def clear_all_mortors(self, fadeout: bool = True) -> int:
        """批量清理所有存活摩托，返回清理数量。"""
        self._mortors = [m for m in self._mortors if m.is_alive()]
        alive = list(self._mortors)
        count = len(alive)
        for mortor in alive:
            try:
                if fadeout and hasattr(mortor, "start_fadeout"):
                    mortor.start_fadeout()
                else:
                    mortor.close()
            except Exception:
                pass
        if count > 0:
            self._pause_mortor_bgm()
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅，关闭所有摩托窗口。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        self._event_center.unsubscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        self._event_center.unsubscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)
        self._event_center.unsubscribe(EventType.PROTECTION_CHECK, self._on_protection_check)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        for mortor in self._mortors:
            if mortor.is_alive():
                try:
                    mortor.close()
                except Exception:
                    pass
        self._mortors.clear()
        self._pause_mortor_bgm()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(MortorManager.MANAGER_ID, MortorManager)

