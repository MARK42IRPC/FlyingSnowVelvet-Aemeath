"""音响管理器 - 使用动态注册机制，通过事件系统通信"""
import random

from lib.core.event.center      import get_event_center, EventType, Event
from lib.core.graphics.image_loader import load_image_resource, resize_image_resource_to_height
from lib.core.graphics.types import Point
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.plugin_registry   import manager_registry, BaseManager
from lib.core.screen_utils import get_screen_rect_for_point
from lib.core.world_objects import (
    create_world_object,
    get_world_object_center,
    get_world_object_geometry,
    WorldObjectInstance,
)
from lib.script.music import get_music_service, cleanup_music_service
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SpeakerManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class SpeakerManager(BaseManager):
    """
    音响管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#音响 数量" 命令
    - 加载并缓存 music.png 正向 / 翻转图片句柄
    - 在屏幕底部区域随机生成 Speaker 窗口
    """

    MANAGER_ID = "speaker"
    DISPLAY_NAME = "音响管理器"
    COMMAND_TRIGGER = "音响"
    COMMAND_HELP = "[数量] - 在屏幕上放置音响"

    def __init__(self, entity=None):
        """
        Args:
            entity: 主宠物实体（PetWindow），用于后续功能扩展
        """
        self._entity = entity
        self._speakers: list[WorldObjectInstance] = []

        self._resource = None
        self._actual_size: tuple[int, int] = (120, 120)

        # 重力开关状态（True = 重力开启）
        self._gravity_enabled = True

        from config.config import SPEAKER
        self._cfg = SPEAKER

        self._load_png()

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.SPEAKER_WINDOW_REQUEST, self._on_window_request)
        self._event_center.subscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)

        get_hash_cmd_registry().register('音响', '[数量]', '在屏幕上放置音响')
        get_hash_cmd_registry().register('音响重力', '', '开关音响重力影响')
        get_hash_cmd_registry().register('退出音乐登录', '', '退出当前音乐平台账号并删除登录缓存')

        # 初始化音乐抽象层（当前默认接管网易云后端）
        get_music_service().initialize()

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "SpeakerManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    def _on_window_request(self, event: Event):
        """处理音响窗口范围请求事件，返回所有音响的窗口范围"""
        speakers = self._get_alive_speakers()
        rects = []
        for speaker in speakers:
            rect = get_world_object_geometry(speaker)
            rects.append((
                rect.x,
                rect.y,
                rect.x + rect.width,
                rect.y + rect.height,
            ))

        # 发布响应事件
        self._event_center.publish(Event(EventType.SPEAKER_WINDOW_RESPONSE, {
            'rects': rects,
        }))

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        """加载音响 PNG，生成正向和翻转图片缓存。"""
        png_path = self._cfg.get('png_file', 'resc/GIF/music.png')
        size = self._cfg.get('size', (120, 120))
        h    = size[1]   # 仅取配置高度，宽度由图片原始比例决定

        resource = load_image_resource(png_path)
        if resource is None:
            log(f"加载 PNG 失败: {png_path}")
            return

        self._resource = resize_image_resource_to_height(resource, h)
        self._actual_size = self._resource.size
        log(f"PNG 已加载：{png_path}，缩放至 {self._resource.size[0]}x{self._resource.size[1]}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：
        - #音响 数量
        - #音响重力（开关重力影响）
        - #退出音乐登录（退出当前音乐平台账号并删除登录缓存）
        event.data['text'] 已去掉开头的 '#'，值如 "音响 2" 或 "音响重力"
        """
        text = event.data.get('text', '').strip()

        # 处理音响重力命令
        if text == '音响重力':
            self._toggle_gravity()
            return

        if text == '退出音乐登录':
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '正在退出音乐平台登录...',
                'min':  10,
                'max':  60,
            }))
            self._event_center.publish(Event(EventType.MUSIC_LOGOUT_REQUEST, {}))
            return

        if not text.startswith('音响'):
            return

        parts = text.split()
        count = 1
        if len(parts) >= 2:
            try:
                count = max(1, int(parts[1]))
            except ValueError:
                count = 1

        log(f"收到召唤命令，数量：{count}")
        self._spawn_speakers(count)

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'放置了 {count} 个音响！',
            'min':  20,
            'max':  100,
        }))

    def _toggle_gravity(self):
        """切换重力开关状态"""
        self._gravity_enabled = not self._gravity_enabled

        # 更新所有音响的重力状态
        for speaker in self._speakers:
            if speaker.is_alive():
                speaker.set_gravity_enabled(self._gravity_enabled)

        status = "开启" if self._gravity_enabled else "关闭"
        log(f"重力已{status}")
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'音响重力已{status}',
            'min':  0,
            'max':  60,
        }))

    def _on_spawn_request(self, event: Event):
        """
        处理 MANAGER_SPAWN_REQUEST 事件。

        事件数据格式：
        {
            'manager_id': 'speaker',  # 目标管理器ID
            'count': 1,               # 生成数量（可选，默认1）
        }
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return
        count = max(1, int(event.data.get('count', 1)))
        log(f"收到 MANAGER_SPAWN_REQUEST，生成 {count} 个音响")
        self._spawn_speakers(count)

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_speakers(self, count: int):
        """在宠物当前位置生成 count 个音响，中心锚点对齐。"""
        if self._resource is None:
            log("无可用图片，跳过生成")
            return

        screen = get_screen_rect_for_point()
        size   = self._actual_size   # 使用按比例缩放后的真实尺寸
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
            max_x = max(min_x, int(screen.x + screen.width - w))
            max_y = max(min_y, int(screen.y + screen.height - h))
            x = max(min_x, min(x, max_x))
            y = max(min_y, min(y, max_y))

            speaker = create_world_object(
                "speaker",
                resource       = self._resource,
                position       = Point(x, y),
                size           = size,
            )
            # 继承管理器的重力状态
            if not self._gravity_enabled:
                speaker.set_gravity_enabled(False)
            self._speakers.append(speaker)
            log(f"生成音响 @ ({x}, {y})")

    # ==================================================================
    # 供外部查询（预留接口，供后续功能扩展）
    # ==================================================================

    def _get_alive_speakers(self) -> list[WorldObjectInstance]:
        """返回当前所有存活的音响实例列表。"""
        self._speakers = [s for s in self._speakers if s.is_alive()]
        return list(self._speakers)

    def get_speaker_centers(self) -> list[Point]:
        """返回当前存活音响的后端无关中心坐标。"""
        return [
            get_world_object_center(speaker)
            for speaker in self._get_alive_speakers()
        ]

    def set_gravity_enabled(self, enabled: bool):
        """
        设置所有音响的重力开关状态。

        Args:
            enabled: True 开启重力，False 关闭重力
        """
        self._gravity_enabled = enabled
        for speaker in self._speakers:
            if speaker.is_alive():
                speaker.set_gravity_enabled(enabled)
        status = "开启" if enabled else "关闭"
        log(f"重力已{status}")

    def clear_all_speakers(self, fadeout: bool = True) -> int:
        """批量清理所有存活音响，返回清理数量。"""
        self._speakers = [s for s in self._speakers if s.is_alive()]
        alive = list(self._speakers)
        count = len(alive)
        for speaker in alive:
            try:
                if fadeout and hasattr(speaker, "start_fadeout"):
                    speaker.start_fadeout()
                else:
                    speaker.close()
            except Exception:
                pass
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅并关闭所有音响对象。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.SPEAKER_WINDOW_REQUEST, self._on_window_request)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        for speaker in self._speakers:
            if speaker.is_alive():
                try:
                    speaker.close()
                except Exception:
                    pass
        self._speakers.clear()

        cleanup_music_service()

        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(SpeakerManager.MANAGER_ID, SpeakerManager)
