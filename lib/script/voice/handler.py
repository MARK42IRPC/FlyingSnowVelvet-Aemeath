"""语音模块抽象层：接收 VOICE_REQUEST 并转发到底层 SOUND_REQUEST。"""

from __future__ import annotations

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

logger = get_logger(__name__)


def _clamp_01(value) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, num))


class VoiceRequestHandler:
    """
    语音请求路由器（script 抽象层）。

    订阅：
      - EventType.VOICE_REQUEST
    发布：
      - EventType.SOUND_REQUEST
    """

    def __init__(self):
        self._ec = get_event_center()
        self._ec.subscribe(EventType.VOICE_REQUEST, self._on_voice_request)
        logger.info("[VoiceRequestHandler] 语音抽象层已初始化")

    def _on_voice_request(self, event: Event):
        data = event.data or {}
        if str(data.get("text") or "").strip():
            # 文本 TTS 请求由 lib.script.gsvmove 消费，这里直接让行。
            return

        audio_type = str(data.get("audio_type") or "").strip()
        source = str(data.get("source") or "").strip()
        if not audio_type or not source:
            logger.debug(
                "[VoiceRequestHandler] 忽略无效语音申请: audio_type=%r source=%r",
                audio_type,
                source,
            )
            return

        self._ec.publish(Event(EventType.SOUND_REQUEST, {
            "audio_type": audio_type,
            "source": source,
            "volume_gain": _clamp_01(data.get("volume_gain", 1.0)),
            "interruptible": bool(data.get("interruptible", True)),
        }))
        event.mark_handled()

    def cleanup(self):
        self._ec.unsubscribe(EventType.VOICE_REQUEST, self._on_voice_request)


_instance: VoiceRequestHandler | None = None


def get_voice_request_handler() -> VoiceRequestHandler:
    global _instance
    if _instance is None:
        _instance = VoiceRequestHandler()
    return _instance


def cleanup_voice_request_handler():
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
