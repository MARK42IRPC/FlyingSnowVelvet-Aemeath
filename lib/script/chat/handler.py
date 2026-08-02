"""聊天输入处理与模型请求调度。"""

from collections import deque

from PyQt5.QtCore import QTimer

from lib.core.event.center import get_event_center, EventType, Event
from lib.script.chat.ollama import get_ollama_manager
from .vision_capture import capture_screen
from lib.core.logger import get_logger

from .handler_auto_companion import ChatHandlerAutoCompanionMixin
from .handler_persona import ChatHandlerPersonaMixin, RECENT_CONTEXT_MESSAGES
from .handler_stream_presenter import (
    ChatHandlerStreamPresenterMixin,
    BUBBLE_MIN_TICKS,
    BUBBLE_MAX_TICKS,
    _should_capture_screen,
)

logger = get_logger(__name__)


class ChatHandler(ChatHandlerPersonaMixin, ChatHandlerAutoCompanionMixin, ChatHandlerStreamPresenterMixin):
    def __init__(self):
        self._event_center  = get_event_center()
        self._ollama        = get_ollama_manager()
        self._persona       = self._load_persona()
        self._last_message  = ""   # 保存最近一条用户消息，供降级回复时关键词匹配使用
        self._auto_timer: QTimer | None = None
        self._recent_context: deque[dict[str, str]] = deque(maxlen=RECENT_CONTEXT_MESSAGES)

        self._event_center.subscribe(EventType.INPUT_CHAT, self._on_input_chat)
        self._event_center.subscribe(EventType.APP_MAIN, self._on_app_main)
        self._event_center.subscribe(EventType.GAME_MODE_STATUS_CHANGE, self._on_game_mode_status_change)
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_auto_companion_config_updated)
        self._stream_first_chunk: bool = True   # 每次新请求重置；首个流式 chunk 触发粒子
        self._stream_pending_raw: str = ""
        self._stream_last_display: str = ""
        self._stream_flush_timer = QTimer()
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_stream_chunk)
        logger.info("[ChatHandler] 聊天处理器已初始化")

    def _on_input_chat(self, event: Event):
        """处理聊天输入：转发给 OllamaManager 获取 AI 回复"""
        text = event.data.get("text", "").strip()
        if not text:
            return

        source = str(event.data.get("source", "")).strip()
        is_screen_peek = source == 'tool_screen_peek'
        include_history = source not in ('tool_recall', 'tool_screen_peek')
        is_tool_recall = source == 'tool_recall'
        allow_tool_commands = bool(event.data.get('allow_tool_commands', True))
        context_history = self._get_recent_context_snapshot()

        logger.debug("[ChatHandler] 收到聊天消息: %s", text[:60])
        self._last_message = text   # 保存，供 _publish_response 降级时使用

        mode_error = getattr(self._ollama, "mode_error_message", "") or ""
        strict_mode = bool(getattr(self._ollama, "strict_mode_enabled", False))
        if strict_mode and mode_error:
            self._event_center.publish(Event(EventType.INFORMATION, {
                "text": mode_error,
                "min":  BUBBLE_MIN_TICKS,
                "max":  BUBBLE_MAX_TICKS,
            }))
            logger.error("[ChatHandler] 当前回复模式不可用，未切换其他来源: %s", mode_error)
            return

        if not self._ollama.is_running:
            # Ollama 未启动：传空串触发 bot_reply 兜底路径
            self._publish_response("")
            return

        # 检查是否触发视觉请求
        images = None
        should_capture_screen = bool(event.data.get('capture_screen')) or _should_capture_screen(text)
        if should_capture_screen:
            logger.info("[ChatHandler] 检测到视觉请求，正在截图...")
            images = capture_screen()
            if images:
                logger.info("[ChatHandler] 截图成功 (%d bytes)，将发送给模型", len(images[0]))
            else:
                if is_screen_peek:
                    logger.warning("[ChatHandler] 窥屏工具截图失败，已取消模型请求")
                    self._event_center.publish(Event(EventType.INFORMATION, {
                        "text": "窥屏失败：无法获取屏幕截图",
                        "min": BUBBLE_MIN_TICKS,
                        "max": BUBBLE_MAX_TICKS,
                    }))
                    return
                logger.warning("[ChatHandler] 截图失败，仅发送文本")

        # 立即发布等待气泡，填补发起请求到收到回复的空白时间
        self._stream_first_chunk = True   # 重置：下一个流式 chunk 将触发粒子
        self._stream_pending_raw = ""
        self._stream_last_display = ""
        self._stream_flush_timer.stop()
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "...",
            "min":  1,
            "max":  600,
        }))

        self._ollama.stream_chat(
            message=text,
            persona=self._build_runtime_persona(skip_memory_block=is_tool_recall),
            callback=lambda reply_text, native_tool_call=None, user_text=text, keep_history=include_history, allow_tools=allow_tool_commands: self._publish_response(
                reply_text,
                user_text=user_text,
                include_history=keep_history,
                allow_tool_commands=allow_tools,
                native_tool_call=native_tool_call,
            ),
            on_chunk=self._on_stream_chunk,
            images=images,
            history=context_history,
            allow_tools=allow_tool_commands,
        )

    def cleanup(self):
        """取消事件订阅"""
        self._event_center.unsubscribe(EventType.INPUT_CHAT, self._on_input_chat)
        self._event_center.unsubscribe(EventType.APP_MAIN, self._on_app_main)
        self._event_center.unsubscribe(EventType.GAME_MODE_STATUS_CHANGE, self._on_game_mode_status_change)
        self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_auto_companion_config_updated)
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            try:
                self._stream_flush_timer.timeout.disconnect(self._flush_stream_chunk)
            except Exception:
                pass
        if self._auto_timer is not None:
            self._auto_timer.stop()
            try:
                self._auto_timer.timeout.disconnect(self._on_auto_companion_tick)
            except Exception:
                pass
            self._auto_timer = None



_chat_handler: ChatHandler | None = None


def get_chat_handler() -> ChatHandler:
    """获取全局 ChatHandler 单例。"""
    global _chat_handler
    if _chat_handler is None:
        _chat_handler = ChatHandler()
    return _chat_handler


def cleanup_chat_handler():
    """清理全局 ChatHandler 实例。"""
    global _chat_handler
    if _chat_handler is not None:
        _chat_handler.cleanup()
        _chat_handler = None
