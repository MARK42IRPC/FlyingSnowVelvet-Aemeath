"""事件中心模块 - 统一管理所有事件"""
from typing import Callable, Dict, List
from enum import Enum
from collections import deque
import threading

from lib.core.logger import get_logger
logger = get_logger(__name__)


class EventType(Enum):
    """事件类型枚举"""
    # 鼠标事件
    MOUSE_PRESS = "mouse_press"
    MOUSE_MOVE = "mouse_move"
    MOUSE_ENTER = "mouse_enter"
    MOUSE_LEAVE = "mouse_leave"
    MOUSE_GET_POSITION = "mouse_get_position"          # 获取鼠标位置请求
    MOUSE_POSITION_RESPONSE = "mouse_position_response"  # 鼠标位置响应
    MOUSE_CLICK = "mouse_click"                        # 确认单击（双击间隔超时后发布）
    MOUSE_DOUBLE_CLICK = "mouse_double_click"          # 确认双击（间隔内二次点击立即发布）

    # 键盘事件
    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"

    # 状态事件
    STATE_CHANGE = "state_change"
    STATE_CHANGE_REQUEST = "state_change_request"  # 状态切换请求
    ACTION_START = "action_start"
    ACTION_END = "action_end"

    # 移动事件
    MOVE_START = "move_start"
    MOVE_END = "move_end"
    PET_TELEPORT = "pet_teleport"  # 主宠物瞬移请求（x/y 或 position）



    # 应用事件
    APP_PRE_START = "app_pre_start"  # 预启动事件（在启动前发布）
    APP_INIT_READY = "app_init_ready"  # 初始化就绪事件（3秒等待完成后发布）
    APP_START = "app_start"
    APP_MAIN = "app_main"
    APP_EXIT = "app_exit"
    APP_QUIT = "app_quit"

    # 时间事件
    FRAME = "frame"
    TICK = "tick"
    TIMER = "timer"
    TIMER_PAUSE = "timer_pause"      # 定时任务暂停（不暂停全局 tick/frame）
    TIMER_RESUME = "timer_resume"    # 定时任务恢复
    GIF_FRAME = "gif_frame"
    GIF_LOOP_COMPLETED = "gif_loop_completed"

    # 绘制事件
    DRAW_REQUEST = "draw_request"          # 绘制请求: [资源id, 资源帧[如有], 绘制位置]
    DRAW_FRAME_CHANGE = "draw_frame_change"  # 绘制帧变化
    DRAW_LOOP_COMPLETED = "draw_loop_completed"  # 绘制循环完成
    DRAW_RENDER = "draw_render"           # 执行绘制
    DRAW_CLEAR = "draw_clear"             # 清除绘制请求

    # UI 事件
    UI_CLOSE_BUTTON_CLICK = "ui_close_button_click"      # 关闭按钮点击
    UI_CLICKTHROUGH_TOGGLE = "ui_clickthrough_toggle"    # 鼠标穿透模式切换
    UI_COMMAND_ENTER = "ui_command_enter"                # 命令输入确认
    UI_BUBBLE_SHOW = "ui_bubble_show"                    # 显示气泡
    UI_BUBBLE_HIDE = "ui_bubble_hide"                    # 隐藏气泡
    UI_COMMAND_TOGGLE = "ui_command_toggle"              # 切换命令框
    UI_OPEN_CMD_WINDOW = "ui_open_cmd_window"            # 打开CMD窗口
    UI_OPEN_CMD_WINDOW_WITH_COMMAND = "ui_open_cmd_window_with_command"  # 打开CMD窗口并执行命令
    UI_CREATE = "ui_create"                              # UI 组件创建请求
    UI_ANCHOR_RESPONSE = "ui_anchor_response"            # 锚点坐标响应
    UI_HINT_PICK = "ui_hint_pick"                        # 命令提示框条目点击
    AUTOSTART_STATUS_CHANGE = "autostart_status_change"  # 开机启动状态变化

    # 信息气泡事件
    INFORMATION = "information"                          # 信息气泡事件：text, min, max

    # 粒子事件
    PARTICLE_REQUEST = "particle_request"                # 粒子申请事件
    PARTICLE_UPDATE = "particle_update"                  # 粒子更新事件

    # 音频事件
    SOUND_REQUEST = "sound_request"                      # 音频播放申请: [audio_class, volume, interruptible]
    VOICE_REQUEST = "voice_request"                      # 语音播放申请（script抽象层入口）
    AI_VOICE_REQUEST = "ai_voice_request"                # AI文本转语音申请（清洗后的短文本）
    MIC_STT_START = "mic_stt_start"                     # 麦克风语音识别启动请求
    MIC_STT_STOP = "mic_stt_stop"                       # 麦克风语音识别停止请求
    MIC_STT_PARTIAL = "mic_stt_partial"                 # 麦克风语音识别中间结果
    MIC_STT_FINAL = "mic_stt_final"                     # 麦克风语音识别最终结果
    MIC_STT_STATE_CHANGE = "mic_stt_state_change"       # 麦克风语音识别状态变化

    # 输入事件
    INPUT_COMMAND = "input_command"   # / 开头：shell 命令
    INPUT_HASH    = "input_hash"      # # 开头：扩展命令（由各管理器订阅处理）
    INPUT_CHAT    = "input_chat"      # 无前缀：聊天消息（由 ChatHandler 处理）

    # 音乐播放事件（由 SpeakerSearchResultBox 发布，CloudMusicManager 订阅）
    MUSIC_PLAY_TOP = "music_play_top"  # 左键：立即播放，中断当前曲目
    MUSIC_ENQUEUE  = "music_enqueue"   # 右键：加入播放队列末尾
    MUSIC_ENQUEUE_HISTORY = "music_enqueue_history"  # 将历史记录批量加入播放队列末尾
    MUSIC_ENQUEUE_LIKED = "music_enqueue_liked"  # 清空队列并加载“我喜欢的音乐”（随机最多32首）
    MUSIC_ENQUEUE_LOCAL = "music_enqueue_local"  # 清空队列并加载本地音乐文件夹
    MUSIC_PLAY_QUEUE_INDEX = "music_play_queue_index"  # 播放队列中指定索引歌曲
    MUSIC_PLAY_MODE_TOGGLE = "music_play_mode_toggle"  # 播放模式切换（单曲循环/列表循环/随机）
    MUSIC_PLAY_PAUSE = "music_play_pause"  # 播放/暂停切换
    MUSIC_NEXT_TRACK = "music_next_track"  # 播放下一首
    MUSIC_VOLUME     = "music_volume"      # 调整音量 {'delta': float}
    MUSIC_STATUS_CHANGE = "music_status_change"  # 音乐播放状态变化
    MUSIC_PROGRESS = "music_progress"  # 音乐播放进度更新
    MUSIC_PROGRESS_REQUEST = "music_progress_request"  # 音乐播放进度请求
    MUSIC_SEEK = "music_seek"  # 音乐跳转进度请求
    MUSIC_SONG_END = "music_song_end"  # 歌曲播放结束
    MUSIC_LOGIN_REQUEST = "music_login_request"  # 请求发起音乐二维码登录
    MUSIC_LOGIN_CANCEL_REQUEST = "music_login_cancel_request"  # 请求取消当前扫码登录流程
    MUSIC_LOGOUT_REQUEST = "music_logout_request"  # 请求退出音乐账号登录
    MUSIC_LOGIN_STATUS_CHANGE = "music_login_status_change"  # 音乐登录状态变化
    MUSIC_LOGIN_QR_SHOW = "music_login_qr_show"  # 显示二维码登录UI
    MUSIC_LOGIN_QR_STATUS = "music_login_qr_status"  # 更新二维码登录UI状态文字
    MUSIC_LOGIN_QR_HIDE = "music_login_qr_hide"  # 隐藏二维码登录UI

    YUANBAO_LOGIN_QR_SHOW = "yuanbao_login_qr_show"  # 显示元宝二维码登录UI
    YUANBAO_LOGIN_QR_STATUS = "yuanbao_login_qr_status"  # 更新元宝二维码登录UI状态文字
    YUANBAO_LOGIN_QR_HIDE = "yuanbao_login_qr_hide"  # 隐藏元宝二维码登录UI

    # 音响窗口范围请求和响应事件
    SPEAKER_WINDOW_REQUEST = "speaker_window_request"  # 请求音响窗口范围
    SPEAKER_WINDOW_RESPONSE = "speaker_window_response"  # 响应音响窗口范围

    # 管理器事件（解耦通信）
    MANAGER_SPAWN_REQUEST = "manager_spawn_request"  # 管理器生成请求
    MANAGER_INTERACTION = "manager_interaction"  # 管理器交互事件

    # 实体事件（解耦通信）
    ENTITY_POSITION_REQUEST = "entity_position_request"  # 实体位置请求
    ENTITY_POSITION_RESPONSE = "entity_position_response"  # 实体位置响应
    ENTITY_STATE_QUERY = "entity_state_query"  # 实体状态查询
    ENTITY_STATE_RESPONSE = "entity_state_response"  # 实体状态响应

    # 目标位置查询事件（用于解耦 state.py 与雪豹/沙发管理器的直接依赖）
    TARGET_POSITION_QUERY = "target_position_query"  # 目标位置查询请求
    TARGET_POSITION_RESPONSE = "target_position_response"  # 目标位置查询响应
    PROTECTION_CHECK = "protection_check"  # 保护半径检测请求
    PROTECTION_RESPONSE = "protection_response"  # 保护半径检测响应

    # 管理器查询事件
    MANAGER_QUERY_REQUEST = "manager_query_request"  # 管理器查询请求
    MANAGER_QUERY_RESPONSE = "manager_query_response"  # 管理器查询响应

    # 流式消息事件
    STREAM_FINAL = "stream_final"  # 流式消息最终完整文本（text: str）

    # 音响管理器初始化事件（用于解耦 UI 和云音乐初始化）
    SPEAKER_MANAGER_READY = "speaker_manager_ready"  # 音响管理器就绪
    SPEAKER_SEARCH_DIALOG_READY = "speaker_search_dialog_ready"  # 搜索 UI 就绪
    CLOUD_MUSIC_MANAGER_READY = "cloud_music_manager_ready"  # 云音乐管理器就绪


class Event:
    """事件对象"""

    def __init__(self, event_type: EventType, data: dict = None):
        self.type = event_type
        self.data = data or {}
        self.handled = False

    def mark_handled(self):
        """标记事件已处理"""
        self.handled = True


try:
    from PyQt5.QtCore import QObject, pyqtSignal
except ImportError:
    QObject = None
    pyqtSignal = None


if QObject is not None and pyqtSignal is not None:
    class _QtEventPump(QObject):
        """跨线程调度器：在拥有该对象线程的事件循环中处理队列。"""

        trigger = pyqtSignal()
else:
    _QtEventPump = None


# ======================================================================
# 便捷事件发布函数
# ======================================================================

def publish_event(event_type: EventType, data: dict = None):
    """便捷发布事件函数"""
    get_event_center().publish(Event(event_type, data))


def request_entity_position(entity_id: str = 'pet_window', request_id: str = None):
    """请求实体位置"""
    get_event_center().publish(Event(EventType.ENTITY_POSITION_REQUEST, {
        'entity_id': entity_id,
        'request_id': request_id or entity_id,
    }))


def request_entity_state(entity_id: str = 'pet_window', query_type: str = 'all', request_id: str = None):
    """请求实体状态"""
    get_event_center().publish(Event(EventType.ENTITY_STATE_QUERY, {
        'entity_id': entity_id,
        'query_type': query_type,
        'request_id': request_id or f"{entity_id}_{query_type}",
    }))


class EventCenter:
    """
    事件中心 - 使用观察者模式管理所有事件
    """

    def __init__(self):
        self._listeners: Dict[EventType, List[Callable]] = {}
        self._event_queue = deque()  # 事件队列
        self._processing = False  # 是否正在处理事件
        self._queue_lock = threading.Lock()
        self._drain_scheduled = False
        self._qt_pump = None
        self._ensure_qt_pump()

    def subscribe(self, event_type: EventType, callback: Callable):
        """
        订阅事件

        Args:
            event_type: 事件类型
            callback: 回调函数，接收 Event 对象
        """
        with self._queue_lock:
            if event_type not in self._listeners:
                self._listeners[event_type] = []
            self._listeners[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable):
        """
        取消订阅事件

        Args:
            event_type: 事件类型
            callback: 回调函数
        """
        with self._queue_lock:
            if event_type in self._listeners:
                if callback in self._listeners[event_type]:
                    self._listeners[event_type].remove(callback)

    def publish(self, event: Event):
        """
        发布事件（线程安全）。

        主线程发布：若当前未在处理队列，立即处理。
        后台线程发布：通过 Qt 信号调度到创建 EventCenter 的线程处理。
        """
        is_main_thread = threading.current_thread() is threading.main_thread()

        with self._queue_lock:
            self._event_queue.append(event)
            should_process_now = is_main_thread and not self._processing
            should_schedule = (not should_process_now) and (not self._drain_scheduled)
            if should_schedule:
                self._drain_scheduled = True

        if should_process_now:
            self._process_events()
        elif should_schedule:
            self._schedule_process_events()

    def _ensure_qt_pump(self):
        """按需创建 Qt 调度器。"""
        if _QtEventPump is None or self._qt_pump is not None:
            return
        try:
            self._qt_pump = _QtEventPump()
            self._qt_pump.trigger.connect(self._process_events)
        except Exception as e:
            logger.debug("Qt event pump 初始化失败: %s", e)
            self._qt_pump = None

    def _schedule_process_events(self):
        """请求在 Qt 事件循环中处理事件队列。"""
        self._ensure_qt_pump()
        if self._qt_pump is not None:
            try:
                self._qt_pump.trigger.emit()
                return
            except Exception as e:
                logger.debug("Qt event pump 触发失败: %s", e)

        # 兜底：无 Qt 时直接处理，避免队列积压
        with self._queue_lock:
            self._drain_scheduled = False
        self._process_events()

    def _process_events(self):
        """处理事件队列
        
        注意：为防止事件处理器发布新事件导致的潜在长循环，
        设置最大处理事件数限制。超过限制的事件将在下次调用时处理。
        """
        with self._queue_lock:
            if self._processing:
                return
            if not self._event_queue:
                self._drain_scheduled = False
                return
            self._processing = True
            self._drain_scheduled = False

        # 最大每次处理的事件数，防止无限循环
        max_events_per_call = 100
        processed = 0

        while processed < max_events_per_call:
            with self._queue_lock:
                if not self._event_queue:
                    break
                event = self._event_queue.popleft()
                callbacks = list(self._listeners.get(event.type, []))

            processed += 1

            for callback in callbacks:
                try:
                    callback(event)
                    if event.handled:
                        break
                except Exception as e:
                    logger.error("Event handler error: %s", e)

        with self._queue_lock:
            self._processing = False
            should_schedule = bool(self._event_queue) and not self._drain_scheduled
            if should_schedule:
                self._drain_scheduled = True

        if should_schedule:
            self._schedule_process_events()

    def clear_all(self):
        """清空所有监听器"""
        with self._queue_lock:
            self._listeners.clear()

    def cleanup(self):
        """清理事件中心，释放所有资源"""
        with self._queue_lock:
            self._listeners.clear()
            self._event_queue.clear()
            self._processing = False
            self._drain_scheduled = False
        if self._qt_pump is not None:
            try:
                self._qt_pump.trigger.disconnect(self._process_events)
            except Exception:
                pass
            self._qt_pump = None


# 全局事件中心实例
_event_center = None


def get_event_center() -> EventCenter:
    """获取全局事件中心实例（单例模式）"""
    global _event_center
    if _event_center is None:
        _event_center = EventCenter()
    return _event_center


def cleanup_event_center():
    """清理全局事件中心实例"""
    global _event_center
    if _event_center is not None:
        _event_center.cleanup()
        _event_center = None
