"""ChatHandler ???????"""

import random
import time

from PyQt5.QtCore import QTimer

from config.ollama_config import AUTO_COMPANION
from lib.core.event.center import Event
from lib.core.logger import get_logger
from .vision_capture import capture_screen

logger = get_logger(__name__)

AUTO_COMPANION_BASELINE_INTERVAL_MS = (120000, 360000)
AUTO_COMPANION_PROMPT = '(仔细观察屏幕,然后简要分析漂泊者现在在做什么呢?)'


def _resolve_auto_companion_interval(interval_value) -> tuple[int, int]:
    """
    自动陪伴间隔兜底：
    - 以当前默认配置作为硬编码基线
    - 仅允许上调，不允许下调
    """
    base_min, base_max = AUTO_COMPANION_BASELINE_INTERVAL_MS
    resolved_min, resolved_max = base_min, base_max

    if isinstance(interval_value, (list, tuple)) and len(interval_value) >= 2:
        try:
            cfg_min = int(interval_value[0])
            cfg_max = int(interval_value[1])
            resolved_min = max(cfg_min, base_min)
            resolved_max = max(cfg_max, base_max)
            if resolved_max < resolved_min:
                resolved_max = resolved_min
        except (TypeError, ValueError):
            pass

    if (resolved_min, resolved_max) != (base_min, base_max):
        logger.info(
            "[ChatHandler] 自动陪伴间隔已应用上调配置（基线 %d~%d ms -> 生效 %d~%d ms）",
            base_min,
            base_max,
            resolved_min,
            resolved_max,
        )
    return resolved_min, resolved_max

AUTO_COMPANION_INTERVAL_MS = _resolve_auto_companion_interval(AUTO_COMPANION.get('interval_ms'))
AUTO_COMPANION_BACKOFF_BASE_MS = 300000
AUTO_COMPANION_BACKOFF_MAX_MS = 1800000
AUTO_COMPANION_FAILURE_PREFIXES = (
    '请求失败',
    '外部API请求过于频繁',
    'OpenAI 兼容请求失败',
    'API服务未就绪',
)

def _is_auto_companion_enabled() -> bool:
    return bool(AUTO_COMPANION.get('enabled', True))



class ChatHandlerAutoCompanionMixin:
    def _on_app_main(self, event: Event):
        """应用主循环就绪后，启动自动陪伴轮询（仅外部 API 模式）。"""
        if not self._ollama.use_api_key_mode:
            logger.info("[ChatHandler] 当前非外部API模式，自动陪伴轮询未启用")
            return
        if not _is_auto_companion_enabled():
            logger.info("[ChatHandler] 自动陪伴已关闭，轮询未启用")
            return

        if self._auto_timer is None:
            self._auto_timer = QTimer()
            self._auto_timer.setSingleShot(True)
            self._auto_timer.timeout.connect(self._on_auto_companion_tick)

        self._schedule_next_auto_tick()
        min_s = AUTO_COMPANION_INTERVAL_MS[0] // 1000
        max_s = AUTO_COMPANION_INTERVAL_MS[1] // 1000
        logger.info("[ChatHandler] 自动陪伴轮询已启用（%d~%d秒）", min_s, max_s)

    def _schedule_next_auto_tick(self):
        """按随机间隔调度下一次自动陪伴请求。"""
        if self._auto_timer is None:
            return
        if not _is_auto_companion_enabled():
            self._auto_timer.stop()
            return
        delay_ms = random.randint(AUTO_COMPANION_INTERVAL_MS[0], AUTO_COMPANION_INTERVAL_MS[1])
        backoff_until = float(getattr(self, '_auto_companion_backoff_until', 0.0) or 0.0)
        if backoff_until > 0:
            delay_ms = max(delay_ms, int(max(0.0, backoff_until - time.monotonic()) * 1000))
        self._auto_timer.start(delay_ms)

    def _is_auto_companion_failure_text(self, text: str) -> bool:
        normalized = str(text or '').strip()
        if not normalized:
            return True
        return any(normalized.startswith(prefix) for prefix in AUTO_COMPANION_FAILURE_PREFIXES)

    def _record_auto_companion_failure(self, reason: str) -> None:
        failures = int(getattr(self, '_auto_companion_failures', 0) or 0) + 1
        self._auto_companion_failures = failures
        backoff_ms = min(AUTO_COMPANION_BACKOFF_MAX_MS, AUTO_COMPANION_BACKOFF_BASE_MS * failures)
        self._auto_companion_backoff_until = time.monotonic() + backoff_ms / 1000.0
        logger.warning(
            "[ChatHandler] 自动陪伴接口不可用，静默退避 %d 秒: %s",
            backoff_ms // 1000,
            str(reason or '').strip()[:160] or 'empty_reply',
        )

    def _reset_auto_companion_failures(self) -> None:
        self._auto_companion_failures = 0
        self._auto_companion_backoff_until = 0.0

    def _handle_auto_companion_reply(self, reply_text: str) -> None:
        if self._is_auto_companion_failure_text(reply_text):
            self._record_auto_companion_failure(reply_text)
            return
        self._reset_auto_companion_failures()
        self._publish_auto_response(reply_text, include_history=True)

    def _on_auto_companion_tick(self):
        """定时自动向模型发起陪伴观察请求，并尽量附带截图。"""
        try:
            if not _is_auto_companion_enabled():
                return
            if not self._ollama.use_api_key_mode:
                return
            if not self._ollama.is_running:
                logger.debug("[ChatHandler] 自动陪伴跳过：API服务未就绪")
                return
            if self._ollama.is_chat_busy:
                logger.debug("[ChatHandler] 自动陪伴跳过：当前有聊天请求进行中")
                return
            backoff_until = float(getattr(self, '_auto_companion_backoff_until', 0.0) or 0.0)
            if backoff_until > time.monotonic():
                logger.debug("[ChatHandler] 自动陪伴跳过：接口失败退避中")
                return

            images = capture_screen()
            if images:
                logger.debug("[ChatHandler] 自动陪伴请求附带截图（%d bytes）", len(images[0]))
            else:
                logger.debug("[ChatHandler] 自动陪伴请求未附带截图（截图失败）")

            self._ollama.stream_chat(
                message=AUTO_COMPANION_PROMPT,
                persona=self._build_runtime_persona(),
                callback=self._handle_auto_companion_reply,
                on_chunk=None,
                images=images,
                quiet_throttled=True,
                history=self._get_recent_context_snapshot(),
            )
        except Exception as e:
            logger.error("[ChatHandler] 自动陪伴请求失败: %s", e)
        finally:
            self._schedule_next_auto_tick()

