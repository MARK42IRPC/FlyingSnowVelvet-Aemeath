"""鼠标输入处理器 - 发布核心鼠标事件。"""

from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.graphics.types import Point
from lib.core.input.types import MouseInput


class ClickHandler:
    """
    处理鼠标点击和移动事件，只负责激活事件。

    双击判定逻辑：
    - 首次按下立即发布 MOUSE_PRESS，同时进入「等待确认」状态。
    - 双击间隔（double_click_ticks 个 TICK）内再次按下同一按键
      → 立即发布 MOUSE_DOUBLE_CLICK，清除等待状态。
    - 超过间隔仍未二次点击
      → 发布 MOUSE_CLICK（确认单击），清除等待状态。
    """

    def __init__(self, pet_window, cursor_position_provider=None):
        self._pet = pet_window
        self._cursor_position_provider = cursor_position_provider
        self._event_center = get_event_center()

        # 当前鼠标位置（全局坐标）
        self._current_mouse_pos = None

        # ── 双击判定状态 ─────────────────────────────────────────────
        # 待确认的单击事件数据（None 表示无待处理点击）
        self._pending_click_data = None
        # 自第一次按下已累积的 tick 数
        self._pending_click_ticks = 0
        # 双击判定间隔（tick），从配置读取，默认 3
        self._double_click_ticks = BEHAVIOR.get('double_click_ticks', 3)
        # ─────────────────────────────────────────────────────────────

        # 订阅鼠标位置请求事件
        self._event_center.subscribe(EventType.MOUSE_GET_POSITION, self._handle_get_position)
        # 订阅 tick 事件，用于双击超时判定
        self._event_center.subscribe(EventType.TICK, self._on_tick)

    def handle_press(self, event: MouseInput):
        """处理鼠标按下事件"""
        was_moving = bool(getattr(self._pet, "is_moving", lambda: False)())
        # 移动中被点击：立即停止移动并回到 idle（由 pet.stop_move 内部发布状态切换）
        if was_moving:
            try:
                self._pet.stop_move()
            except Exception:
                pass

        event_data = {
            'button': event.button,
            'global_pos': event.global_pos,
            'pos': event.pos,
            'pet': self._pet,
            'was_moving': was_moving,
        }

        # 立即发布 MOUSE_PRESS（保持拖拽/粒子等即时响应不变）
        mouse_event = Event(EventType.MOUSE_PRESS, event_data)
        self._event_center.publish(mouse_event)

        # ── 双击判定 ──────────────────────────────────────────────────
        button = event.button

        if (self._pending_click_data is not None
                and self._pending_click_data.get('button') == button):
            # 双击间隔内再次点击同一按键 → 双击确认
            self._event_center.publish(Event(EventType.MOUSE_DOUBLE_CLICK, event_data))
            self._pending_click_data = None
            self._pending_click_ticks = 0
        else:
            # 如果有不同按键的待处理单击，先将其确认发出
            if self._pending_click_data is not None:
                self._event_center.publish(Event(EventType.MOUSE_CLICK, self._pending_click_data))

            # 记录本次点击，进入等待确认状态
            self._pending_click_data = event_data
            self._pending_click_ticks = 0
        # ─────────────────────────────────────────────────────────────

    def _on_tick(self, event):
        """处理 tick 事件：双击间隔超时后将待处理点击确认为单击"""
        if self._pending_click_data is None:
            return

        self._pending_click_ticks += 1
        if self._pending_click_ticks >= self._double_click_ticks:
            # 超时，确认为单击
            self._event_center.publish(Event(EventType.MOUSE_CLICK, self._pending_click_data))
            self._pending_click_data = None
            self._pending_click_ticks = 0

    def handle_move(self, event: MouseInput):
        """处理鼠标移动事件"""
        # 更新当前鼠标位置
        self._current_mouse_pos = event.global_pos

        event_data = {
            'buttons': event.buttons,
            'global_pos': event.global_pos,
            'pos': event.pos,
            'pet': self._pet
        }

        mouse_event = Event(EventType.MOUSE_MOVE, event_data)
        self._event_center.publish(mouse_event)

    def handle_enter(self, event=None):
        """处理鼠标进入事件"""
        event_data = {'pet': self._pet}
        mouse_event = Event(EventType.MOUSE_ENTER, event_data)
        self._event_center.publish(mouse_event)

    def handle_leave(self, event=None):
        """处理鼠标离开事件"""
        event_data = {'pet': self._pet}
        mouse_event = Event(EventType.MOUSE_LEAVE, event_data)
        self._event_center.publish(mouse_event)

    def _handle_get_position(self, event):
        """处理鼠标位置请求事件，返回当前鼠标位置"""
        # 获取请求者信息
        request_id = event.data.get('request_id', '')

        provider = self._cursor_position_provider
        mouse_pos = provider() if provider is not None else self._current_mouse_pos
        if mouse_pos is None:
            mouse_pos = Point()

        # 返回鼠标位置
        response_event = Event(EventType.MOUSE_POSITION_RESPONSE, {
            'global_pos': mouse_pos,
            'request_id': request_id
        })
        self._event_center.publish(response_event)
