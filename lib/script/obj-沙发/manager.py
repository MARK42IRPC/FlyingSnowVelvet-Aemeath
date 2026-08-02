"""沙发管理器 - 使用动态注册机制，通过事件系统通信"""
import os
import random

from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPixmap, QTransform

from lib.core.event.center      import get_event_center, EventType, Event
from lib.core.graphics.types import Point, coerce_point
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.plugin_registry   import manager_registry, BaseManager
from lib.core.screen_utils      import get_screen_geometry_for_point
from .sofa                      import Sofa
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[SofaManager] %s", msg)


# ──────────────────────────────────────────────────────────────────────
# 管理器类定义
# ──────────────────────────────────────────────────────────────────────

class SofaManager(BaseManager):
    """
    沙发管理器。

    职责：
    - 订阅 INPUT_HASH 事件，解析 "#沙发 数量" 命令
    - 加载并缓存 sofa.png 正向 / 翻转 QPixmap
    - 在屏幕底部区域随机生成 Sofa 窗口
    - 响应 MANAGER_QUERY_REQUEST 事件提供最近沙发位置
    """

    MANAGER_ID = "sofa"
    DISPLAY_NAME = "沙发管理器"
    COMMAND_TRIGGER = "沙发"
    COMMAND_HELP = "[数量] - 在屏幕上放置沙发"

    def __init__(self, entity=None):
        """
        Args:
            entity: 主宠物实体（PetWindow），用于获取位置信息
        """
        self._entity = entity
        self._sofas: list[Sofa] = []

        # QPixmap 缓存（正向 + 翻转）
        self._pixmap:         QPixmap | None = None
        self._flipped_pixmap: QPixmap | None = None

        # 重力开关状态（True = 重力开启）
        self._gravity_enabled = True

        # 读取配置
        from config.config import SOFA
        self._cfg = SOFA

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
        get_hash_cmd_registry().register('沙发', '[数量]', '在屏幕上放置沙发')
        get_hash_cmd_registry().register('沙发重力', '', '开关沙发重力影响')

        log("已初始化")

    @classmethod
    def create(cls, entity=None, **kwargs) -> "SofaManager":
        """工厂方法：创建管理器实例"""
        return cls(entity)

    # ==================================================================
    # PNG 加载
    # ==================================================================

    def _load_png(self):
        """加载沙发 PNG，生成正向和翻转 QPixmap 缓存。"""
        png_path = self._cfg.get('png_file', 'resc/GIF/sofa.png')
        if not os.path.exists(png_path):
            log(f"警告：找不到沙发 PNG 文件: {png_path}")
            return

        size = self._cfg.get('size', (120, 120))
        w, h  = size

        pixmap = QPixmap(png_path)
        if pixmap.isNull():
            log(f"加载 PNG 失败: {png_path}")
            return

        # 缩放到目标尺寸（忽略原始宽高比，因为沙发图本身是方形的）
        pixmap = pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        # 水平翻转版本
        transform        = QTransform().scale(-1, 1)
        flipped_pixmap   = pixmap.transformed(transform, Qt.SmoothTransformation)

        self._pixmap         = pixmap
        self._flipped_pixmap = flipped_pixmap
        log(f"PNG 已加载：{png_path}，缩放至 {w}x{h}")

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：
        - #沙发 数量
        - #沙发重力（开关重力影响）
        event.data['text'] 已去掉开头的 '#'，值如 "沙发 2" 或 "沙发重力"
        """
        text = event.data.get('text', '').strip()
        
        # 处理沙发重力命令
        if text == '沙发重力':
            self._toggle_gravity()
            return
        
        if not text.startswith('沙发'):
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
        self._spawn_sofas(count)

        # 反馈气泡
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'放置了 {count} 个沙发！',
            'min':  20,
            'max':  100,
        }))

    def _on_spawn_request(self, event: Event):
        """
        处理 MANAGER_SPAWN_REQUEST 事件。

        供 ToolDispatcher 等模块通过事件触发生成沙发。
        事件数据格式：
        {
            'manager_id': 'sofa',   # 目标管理器ID
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

        log(f"收到 MANAGER_SPAWN_REQUEST，生成 {count} 个沙发")
        self._spawn_sofas(count)

    def _toggle_gravity(self):
        """切换重力开关状态"""
        self._gravity_enabled = not self._gravity_enabled
        
        # 更新所有沙发的重力状态
        for sofa in self._sofas:
            if sofa.is_alive():
                sofa.set_gravity_enabled(self._gravity_enabled)
        
        status = "开启" if self._gravity_enabled else "关闭"
        log(f"重力已{status}")
        
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'沙发重力已{status}',
            'min':  0,
            'max':  60,
        }))

    def _on_query_request(self, event: Event):
        """
        处理 MANAGER_QUERY_REQUEST 事件。

        支持其他模块通过事件查询沙发信息，如最近沙发位置、保护范围等。
        """
        if event.data.get('manager_id') != self.MANAGER_ID:
            return

        query_type = event.data.get('query_type')

        if query_type == 'nearest_position':
            from_pos = event.data.get('from_position')
            if from_pos:
                nearest = self.get_nearest_sofa_pos(from_pos)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': nearest,
                    'request_id': event.data.get('request_id'),
                }))

        elif query_type == 'protection_check':
            pet_center = event.data.get('pet_center')
            if pet_center:
                in_protection = self.is_pet_in_sofa_protection(pet_center)
                self._event_center.publish(Event(EventType.MANAGER_QUERY_RESPONSE, {
                    'manager_id': self.MANAGER_ID,
                    'query_type': query_type,
                    'result': in_protection,
                    'request_id': event.data.get('request_id'),
                }))

    def _on_target_position_query(self, event: Event):
        """处理目标位置查询事件（解耦 state.py 与管理器直接依赖）"""
        target_type = event.data.get('target_type')
        if target_type != 'sofa':
            return

        from_pos = event.data.get('requester_pos')
        if from_pos:
            nearest = self.get_nearest_sofa_pos(from_pos)
            self._event_center.publish(Event(EventType.TARGET_POSITION_RESPONSE, {
                'target_type': 'sofa',
                'position': nearest,
            }))

    def _on_protection_check(self, event: Event):
        """处理保护半径检测请求事件"""
        pet_position = event.data.get('pet_position')
        current_in_protection = event.data.get('current_in_protection', False)
        request_id = event.data.get('request_id')
        if pet_position:
            in_protection = self.is_pet_in_sofa_protection(pet_position, current_in_protection)
            # 使用专门的响应事件类型
            self._event_center.publish(Event(EventType.PROTECTION_RESPONSE, {
                'in_protection': in_protection,
                'request_id': request_id,
                'manager_id': self.MANAGER_ID,
            }))

    # ==================================================================
    # 生成逻辑
    # ==================================================================

    def _spawn_sofas(self, count: int):
        """在宠物当前位置生成 count 个沙发，中心锚点对齐。"""
        if self._pixmap is None:
            log("无可用图片，跳过生成")
            return

        screen = get_screen_geometry_for_point()
        size   = self._cfg.get('size', (120, 120))
        w, h   = size

        # 获取宠物当前位置（中心锚点）
        pet_center = None
        if self._entity and hasattr(self._entity, 'get_core_geometry'):
            pet_geometry = self._entity.get_core_geometry()
            pet_center = QPoint(
                int(round(pet_geometry.x + pet_geometry.width / 2)),
                int(round(pet_geometry.y + pet_geometry.height / 2)),
            )
            screen = get_screen_geometry_for_point(pet_center)

        for _ in range(count):
            if pet_center:
                # 以宠物中心为基准生成，添加随机偏移
                offset_x = random.randint(-50, 50)
                offset_y = random.randint(-50, 50)
                x = pet_center.x() - w // 2 + offset_x
                y = pet_center.y() - h // 2 + offset_y
            else:
                # 兜底：屏幕底部随机生成
                sx = screen.x()
                sy = screen.y()
                sw = screen.width()
                sh = screen.height()
                y_min_pct = self._cfg.get('spawn_y_min', 0.80)
                y_max_pct = self._cfg.get('spawn_y_max', 0.90)
                qt_y_top = sy + int(sh * y_min_pct)
                qt_y_bottom = max(qt_y_top, sy + int(sh * y_max_pct) - h)
                x = random.randint(sx, max(sx, sx + sw - w))
                y = random.randint(qt_y_top, max(qt_y_top, qt_y_bottom))

            # 边界检查
            min_x = screen.x()
            min_y = screen.y()
            max_x = screen.x() + screen.width() - w
            max_y = screen.y() + screen.height() - h
            if max_x < min_x:
                max_x = min_x
            if max_y < min_y:
                max_y = min_y
            x = max(min_x, min(x, max_x))
            y = max(min_y, min(y, max_y))

            sofa = Sofa(
                pixmap         = self._pixmap,
                flipped_pixmap = self._flipped_pixmap,
                position       = QPoint(x, y),
                size           = size,
            )
            # 继承管理器的重力状态
            if not self._gravity_enabled:
                sofa.set_gravity_enabled(False)
            self._sofas.append(sofa)
            log(f"生成沙发 @ ({x}, {y})")

    # ==================================================================
    # 供状态机查询
    # ==================================================================

    def get_nearest_sofa_pos(self, from_pos: Point | object) -> Point | None:
        """
        返回距离 from_pos 最近的存活沙发的中心坐标。

        若无存活沙发则返回 None。
        供 StateMachine._trigger_wander() 使用。
        """
        # 清理已关闭的沙发引用
        self._sofas = [s for s in self._sofas if s.is_alive()]
        if not self._sofas:
            return None

        origin = coerce_point(from_pos)
        if origin is None:
            return None
        centers = [(sofa, coerce_point(sofa.get_center())) for sofa in self._sofas]
        centers = [(sofa, center) for sofa, center in centers if center is not None]
        if not centers:
            return None
        return min(
            centers,
            key=lambda item: (
                (item[1].x - origin.x) ** 2
                + (item[1].y - origin.y) ** 2
            ),
        )[1]

    def is_pet_in_sofa_protection(self, pet_center: Point | object, in_protection: bool = False) -> bool:
        """
        检测宠物中心是否在任意沙发的保护半径内。
        使用滞回机制防止边缘抖动。

        Args:
            pet_center: 主宠物中心坐标（全局屏幕坐标）
            in_protection: 当前是否已在保护范围内（用于滞回判断）

        Returns:
            True 如果宠物在任一沙发的保护半径内
        """
        alive = [s for s in self._sofas if s.is_alive()]
        if not alive:
            return False

        # 找到距离最近的沙发
        pet_point = coerce_point(pet_center)
        if pet_point is None:
            return False
        min_dist_sq = float('inf')
        for sofa in alive:
            sc = coerce_point(sofa.get_center())
            if sc is None:
                continue
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
        设置所有沙发的重力开关状态。

        Args:
            enabled: True 开启重力，False 关闭重力
        """
        self._gravity_enabled = enabled
        for sofa in self._sofas:
            if sofa.is_alive():
                sofa.set_gravity_enabled(enabled)
        status = "开启" if enabled else "关闭"
        log(f"重力已{status}")

    def clear_all_sofas(self, fadeout: bool = True) -> int:
        """批量清理所有存活沙发，返回清理数量。"""
        self._sofas = [s for s in self._sofas if s.is_alive()]
        alive = list(self._sofas)
        count = len(alive)
        for sofa in alive:
            try:
                if fadeout and hasattr(sofa, "start_fadeout"):
                    sofa.start_fadeout()
                else:
                    sofa.close()
            except Exception:
                pass
        return count

    # ==================================================================
    # 清理
    # ==================================================================

    def cleanup(self):
        """取消事件订阅，关闭所有沙发窗口。"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.MANAGER_SPAWN_REQUEST, self._on_spawn_request)
        self._event_center.unsubscribe(EventType.MANAGER_QUERY_REQUEST, self._on_query_request)
        self._event_center.unsubscribe(EventType.TARGET_POSITION_QUERY, self._on_target_position_query)
        self._event_center.unsubscribe(EventType.PROTECTION_CHECK, self._on_protection_check)
        for sofa in self._sofas:
            if sofa.is_alive():
                try:
                    sofa.close()
                except Exception:
                    pass
        self._sofas.clear()
        log("已清理")


# ──────────────────────────────────────────────────────────────────────
# 注册管理器
# ──────────────────────────────────────────────────────────────────────

manager_registry.register(SofaManager.MANAGER_ID, SofaManager)
