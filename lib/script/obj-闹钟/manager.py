"""闹钟管理器 - 使用动态注册机制，通过事件系统通信"""
import random
import re

from lib.core.event.center      import get_event_center, EventType, Event
from lib.core.graphics.image_loader import load_image_resource, resize_image_resource_to_width
from lib.core.graphics.types import Point, coerce_point
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.plugin_registry   import manager_registry, BaseManager
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import (
    create_world_object,
    get_world_object_center,
    WorldObjectInstance,
)
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[ClockManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class ClockManager(BaseManager):
    """
    闹钟管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#闹钟 数量" 命令
    - 加载并缓存 clock.png 图片句柄
    - 在屏幕底部区域随机生成 Clock 窗口
    - 响应 MANAGER_QUERY_REQUEST 事件提供最近闹钟位置
    """

    MANAGER_ID = "clock"
    DISPLAY_NAME = "闹钟管理器"
    COMMAND_TRIGGER = "闹钟"
    COMMAND_HELP = "[时 分 秒|分 秒|秒] - 在宠物位置放置闹钟并开始倒计时（默认30秒）"

    def __init__(self, entity=None):
        """
        Args:
            entity: 主宠物实体（PetWindow），用于获取位置信息
        """
        self._entity = entity
        self._clocks: list[WorldObjectInstance] = []

        self._resource = None
        self._actual_size:    tuple[int, int] = (120, 120)

        # 重力开关状态（True = 重力开启）
        self._gravity_enabled = True

        # 读取配置
        from config.config import CLOCK
        self._cfg = CLOCK
        try:
            self._default_countdown_seconds = int(self._cfg.get('countdown_ss', 30))
        except Exception:
            self._default_countdown_seconds = 30
        if self._default_countdown_seconds < 1:
            self._default_countdown_seconds = 1
        self._max_countdown_seconds = 99 * 3600 + 59 * 60 + 59

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

        # 向全局 # 命令注册中心声明本命令（供提示框显示）
        get_hash_cmd_registry().register('闹钟', '[时 分 秒|分 秒|秒]', '放置倒计时闹钟（默认30秒）')
        get_hash_cmd_registry().register('闹钟重力', '', '开关闹钟重力影响')

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "ClockManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        """加载闹钟 PNG，生成图片缓存。"""
        png_path = self._cfg.get('png_file', 'resc/GIF/clock.png')
        target_w = int(self._cfg.get('target_width', 150))
        resource = load_image_resource(png_path)
        if resource is None:
            log(f"加载 PNG 失败: {png_path}")
            return

        self._resource = resize_image_resource_to_width(resource, target_w)
        self._actual_size = self._resource.size
        log(f"PNG 已加载：{png_path}，缩放至 {self._resource.size[0]}x{self._resource.size[1]}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：
        - #闹钟 时 分 秒
        - #闹钟 分 秒
        - #闹钟 秒（默认 30 秒）
        - #闹钟重力（开关重力影响）
        event.data['text'] 已去掉开头的 '#'，值如 "闹钟 45" 或 "闹钟重力"
        """
        text = event.data.get('text', '').strip()
        
        # 处理闹钟重力命令
        if text == '闹钟重力':
            self._toggle_gravity()
            return
        
        if not text.startswith('闹钟'):
            return

        # 解析秒数（默认 30 秒）
        parts = text.split(maxsplit=1)
        seconds_arg = parts[1].strip() if len(parts) >= 2 else ''
        seconds = self._parse_countdown_seconds(seconds_arg)

        log(f"收到召唤命令，倒计时：{seconds} 秒")
        self._spawn_clocks(count=1, countdown_seconds=seconds)

        # 反馈气泡
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'已放置闹钟，倒计时 {seconds} 秒',
            'min':  20,
            'max':  100,
        }))

    def _on_spawn_request(self, event: Event):
        """
        处理 MANAGER_SPAWN_REQUEST 事件。

        供 ToolDispatcher 等模块通过事件触发生成闹钟。
        事件数据格式：
        {
            'manager_id': 'clock',   # 目标管理器ID
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

        seconds = self._parse_countdown_seconds(event.data.get('seconds', ''))
        log(f"收到 MANAGER_SPAWN_REQUEST，生成 {count} 个闹钟（倒计时 {seconds} 秒）")
        self._spawn_clocks(count=count, countdown_seconds=seconds)

    def _toggle_gravity(self):
        """切换重力开关状态"""
        self._gravity_enabled = not self._gravity_enabled
        
        # 更新所有闹钟的重力状态
        for clock in self._clocks:
            if clock.is_alive():
                clock.set_gravity_enabled(self._gravity_enabled)
        
        status = "开启" if self._gravity_enabled else "关闭"
        log(f"重力已{status}")
        
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'闹钟重力已{status}',
            'min':  0,
            'max':  60,
        }))

    def _on_query_request(self, event: Event):
        """
        处理 MANAGER_QUERY_REQUEST 事件。

        支持其他模块通过事件查询闹钟信息，如最近闹钟位置、保护范围等。
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        query_type = event.data.get('query_type')

        if query_type == 'nearest_position':
            from_pos = event.data.get('from_position')
            if from_pos:
                nearest = self.get_nearest_clock_pos(from_pos)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': nearest,
                    'request_id': event.data.get('request_id'),
                }))

        elif query_type == 'protection_check':
            pet_center = event.data.get('pet_center')
            if pet_center:
                in_protection = self.is_pet_in_clock_protection(pet_center)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': in_protection,
                    'request_id': event.data.get('request_id'),
                }))

    def _on_target_position_query(self, event: Event):
        """处理目标位置查询事件（解耦 state.py 与管理器直接依赖）"""
        target_type = event.data.get('target_type')
        if target_type != 'clock':
            return

        from_pos = event.data.get('requester_pos')
        if from_pos:
            nearest = self.get_nearest_clock_pos(from_pos)
            self._event_center.publish(Event(EventType.TARGET_POSITION_RESPONSE, {
                'target_type': 'clock',
                'position': nearest,
            }))

    def _on_protection_check(self, event: Event):
        """处理保护半径检测请求事件"""
        pet_position = event.data.get('pet_position')
        current_in_protection = event.data.get('current_in_protection', False)
        request_id = event.data.get('request_id')
        if pet_position:
            in_protection = self.is_pet_in_clock_protection(pet_position, current_in_protection)
            # 使用专门的响应事件类型
            self._event_center.publish(Event(EventType.PROTECTION_RESPONSE, {
                'in_protection': in_protection,
                'request_id': request_id,
                'manager_id': self.MANAGER_ID,
            }))

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _parse_countdown_seconds(self, raw_value) -> int:
        """解析倒计时秒数；支持 时分秒/分秒/秒，且兼容旧版纯秒数。"""
        def _clamp_seconds(value: int) -> int:
            return max(1, min(self._max_countdown_seconds, int(value)))

        def _try_parse_number(text: str) -> int | None:
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return None

        if raw_value is None:
            return self._default_countdown_seconds

        if isinstance(raw_value, (int, float)):
            return _clamp_seconds(int(raw_value))

        text = str(raw_value).strip()
        if not text:
            return self._default_countdown_seconds

        normalized = text
        if normalized.endswith('秒钟'):
            normalized = normalized[:-2].strip()
        if normalized.endswith('秒'):
            normalized = normalized[:-1].strip()
        if not normalized:
            return self._default_countdown_seconds

        # 兼容旧版：#闹钟 45
        direct_seconds = _try_parse_number(normalized)
        if direct_seconds is not None:
            return _clamp_seconds(direct_seconds)

        # 新格式：#闹钟 时 分 秒 / #闹钟 分 秒 / #闹钟 秒
        parts = [p for p in re.split(r"\s+", normalized) if p]
        if not parts:
            return self._default_countdown_seconds

        values: list[int] = []
        for part in parts:
            token = str(part).strip()
            if token.endswith('秒钟'):
                token = token[:-2].strip()
            elif token.endswith('秒'):
                token = token[:-1].strip()
            if not token:
                return self._default_countdown_seconds
            parsed = _try_parse_number(token)
            if parsed is None or parsed < 0:
                return self._default_countdown_seconds
            values.append(parsed)

        if len(values) == 1:
            total_seconds = values[0]
        elif len(values) == 2:
            mm, ss = values
            total_seconds = mm * 60 + ss
        elif len(values) == 3:
            hh, mm, ss = values
            total_seconds = hh * 3600 + mm * 60 + ss
        else:
            return self._default_countdown_seconds

        return _clamp_seconds(total_seconds)

    @staticmethod
    def _seconds_to_hh_mm_ss(seconds: int) -> tuple[int, int, int]:
        """将总秒数转换为 hh/mm/ss。"""
        total = max(0, int(seconds))
        hh = total // 3600
        total %= 3600
        mm = total // 60
        ss = total % 60
        return hh, mm, ss

    def _spawn_clocks(self, count: int, countdown_seconds: int):
        """在宠物当前位置生成 count 个闹钟，中心锚点对齐。"""
        if self._resource is None:
            log("无可用图片，跳过生成")
            return

        screen = get_screen_rect_for_point()
        size   = self._actual_size
        w, h   = size
        countdown_hh, countdown_mm, countdown_ss = self._seconds_to_hh_mm_ss(countdown_seconds)
        countdown_ms = 0

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

            clock = create_world_object(
                "clock",
                resource       = self._resource,
                position       = Point(x, y),
                size           = size,
                countdown_hh   = countdown_hh,
                countdown_mm   = countdown_mm,
                countdown_ss   = countdown_ss,
                countdown_ms   = countdown_ms,
            )
            # 继承管理器的重力状态
            if not self._gravity_enabled:
                clock.set_gravity_enabled(False)
            self._clocks.append(clock)
            log(f"生成闹钟 @ ({x}, {y})")

    # ==================================================================
    # 供状态机查询
    # ==================================================================

    def get_nearest_clock_pos(self, from_pos: Point | object) -> Point | None:
        """
        返回距离 from_pos 最近的存活闹钟的中心坐标。

        若无存活闹钟则返回 None。
        供 StateMachine._trigger_wander() 使用。
        """
        # 清理已关闭的闹钟引用
        self._clocks = [s for s in self._clocks if s.is_alive()]
        if not self._clocks:
            return None

        origin = coerce_point(from_pos)
        if origin is None:
            return None
        centers = [
            (clock, get_world_object_center(clock))
            for clock in self._clocks
        ]
        centers = [(clock, center) for clock, center in centers if center is not None]
        if not centers:
            return None
        return min(
            centers,
            key=lambda item: (
                (item[1].x - origin.x) ** 2
                + (item[1].y - origin.y) ** 2
            ),
        )[1]

    def is_pet_in_clock_protection(self, pet_center: Point | object, in_protection: bool = False) -> bool:
        """
        检测宠物中心是否在任意闹钟的保护半径内。
        使用滞回机制防止边缘抖动。

        Args:
            pet_center: 主宠物中心坐标（全局屏幕坐标）
            in_protection: 当前是否已在保护范围内（用于滞回判断）

        Returns:
            True 如果宠物在任一闹钟的保护半径内
        """
        alive = [s for s in self._clocks if s.is_alive()]
        if not alive:
            return False

        # 找到距离最近的闹钟
        pet_point = coerce_point(pet_center)
        if pet_point is None:
            return False
        min_dist_sq = float('inf')
        for clock in alive:
            sc = get_world_object_center(clock)
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
        设置所有闹钟的重力开关状态。

        Args:
            enabled: True 开启重力，False 关闭重力
        """
        self._gravity_enabled = enabled
        for clock in self._clocks:
            if clock.is_alive():
                clock.set_gravity_enabled(enabled)
        status = "开启" if enabled else "关闭"
        log(f"重力已{status}")

    def clear_all_clocks(self, fadeout: bool = True) -> int:
        """批量清理所有存活闹钟，返回清理数量。"""
        self._clocks = [c for c in self._clocks if c.is_alive()]
        alive = list(self._clocks)
        count = len(alive)
        for clock in alive:
            try:
                if fadeout and hasattr(clock, "start_fadeout"):
                    clock.start_fadeout()
                else:
                    clock.close()
            except Exception:
                pass
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅，关闭所有闹钟窗口。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        self._event_center.unsubscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        self._event_center.unsubscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)
        self._event_center.unsubscribe(EventType.PROTECTION_CHECK, self._on_protection_check)
        for clock in self._clocks:
            if clock.is_alive():
                try:
                    clock.close()
                except Exception:
                    pass
        self._clocks.clear()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(ClockManager.MANAGER_ID, ClockManager)

