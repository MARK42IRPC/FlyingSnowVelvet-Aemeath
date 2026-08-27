"""ChatHandler ???????"""

import random
import time

from config.ollama_config import AUTO_COMPANION
from lib.core.event.center import Event
from lib.core.logger import get_logger

logger = get_logger(__name__)

AUTO_COMPANION_BASELINE_INTERVAL_MS = (120000, 120000)
AUTO_COMPANION_MIN_INTERVAL_MS = 60000
AUTO_COMPANION_MAX_INTERVAL_MS = 1200000
AUTO_COMPANION_PROMPT = '(仔细观察屏幕,然后简要分析漂泊者现在在做什么呢?)'


def _resolve_auto_companion_interval(interval_value) -> tuple[int, int]:
    """
    自动陪伴间隔兜底并限制在设置面板允许的 1~20 分钟范围内。
    """
    base_min, base_max = AUTO_COMPANION_BASELINE_INTERVAL_MS
    resolved_min, resolved_max = base_min, base_max

    if isinstance(interval_value, (list, tuple)) and len(interval_value) >= 2:
        try:
            cfg_min = int(interval_value[0])
            cfg_max = int(interval_value[1])
            resolved_min = max(AUTO_COMPANION_MIN_INTERVAL_MS, min(AUTO_COMPANION_MAX_INTERVAL_MS, cfg_min))
            resolved_max = max(AUTO_COMPANION_MIN_INTERVAL_MS, min(AUTO_COMPANION_MAX_INTERVAL_MS, cfg_max))
            if resolved_max < resolved_min:
                resolved_max = resolved_min
        except (TypeError, ValueError):
            pass

    if (resolved_min, resolved_max) != (base_min, base_max):
        logger.info(
            "[ChatHandler] 自动陪伴间隔已应用配置（默认 %d~%d ms -> 生效 %d~%d ms）",
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


def _get_effective_auto_companion_interval_ms() -> tuple[int, int]:
    interval = _resolve_auto_companion_interval(AUTO_COMPANION.get('interval_ms'))
    try:
        from lib.script.app.game_mode_service import get_game_mode_auto_companion_interval_override

        override = get_game_mode_auto_companion_interval_override()
    except Exception:
        override = None
    if isinstance(override, tuple) and len(override) >= 2:
        try:
            low = int(override[0])
            high = int(override[1])
            if low > 0 and high > 0:
                return (min(low, high), max(low, high))
        except (TypeError, ValueError):
            pass
    return interval



class ChatHandlerAutoCompanionMixin:
    def _on_app_main(self, event: Event):
        """应用主循环就绪后，启动自动陪伴轮询（仅外部 API 模式）。"""
        self._app_main_ready = True
        generation = self._current_companion_generation()
        if generation is None:
            if self._auto_timer is not None:
                self._auto_timer.stop()
            return
        if not self._ollama.use_api_key_mode:
            if self._auto_timer is not None:
                self._auto_timer.stop()
            logger.info("[ChatHandler] 当前非外部API模式，自动陪伴轮询未启用")
            return
        if not _is_auto_companion_enabled():
            if self._auto_timer is not None:
                self._auto_timer.stop()
            logger.info("[ChatHandler] 自动陪伴已关闭，轮询未启用")
            return

        self._schedule_next_auto_tick()
        interval_ms = _get_effective_auto_companion_interval_ms()
        min_s = interval_ms[0] // 1000
        max_s = interval_ms[1] // 1000
        logger.info("[ChatHandler] 自动陪伴轮询已启用（%d~%d秒）", min_s, max_s)

    def _on_auto_companion_config_updated(self, event: Event) -> None:
        data = event.data or {}
        if str(data.get("source", "")).strip() != "ai":
            return
        self._on_app_main(event)

    def _schedule_next_auto_tick(self):
        """按随机间隔调度下一次自动陪伴请求。"""
        if self._auto_timer is None:
            return
        if self._current_companion_generation() is None:
            self._auto_timer.stop()
            return
        if not _is_auto_companion_enabled():
            self._auto_timer.stop()
            return
        interval_ms = _get_effective_auto_companion_interval_ms()
        delay_ms = random.randint(interval_ms[0], interval_ms[1])
        backoff_until = float(getattr(self, '_auto_companion_backoff_until', 0.0) or 0.0)
        if backoff_until > 0:
            delay_ms = max(delay_ms, int(max(0.0, backoff_until - time.monotonic()) * 1000))
        self._auto_timer.start(delay_ms)

    def _on_game_mode_status_change(self, event: Event) -> None:
        if self._auto_timer is None:
            return
        self._schedule_next_auto_tick()

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

    def _handle_auto_companion_reply(
        self,
        reply_text: str,
        native_tool_call=None,
        *,
        mode_generation: int | None = None,
    ) -> None:
        if not self._accepts_companion_generation(mode_generation):
            return
        if not native_tool_call and self._is_auto_companion_failure_text(reply_text):
            self._record_auto_companion_failure(reply_text)
            return
        self._reset_auto_companion_failures()
        self._publish_auto_response(
            reply_text,
            include_history=True,
            user_text=AUTO_COMPANION_PROMPT,
            native_tool_call=native_tool_call,
            mode_generation=mode_generation,
        )

    def _on_auto_companion_tick(self):
        """定时自动向模型发起陪伴观察请求，并尽量附带截图。"""
        if getattr(self, '_cleaned', False):
            return
        self._auto_timer.stop()
        try:
            generation = self._current_companion_generation()
            if generation is None:
                return
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

            image_data = self._screen_capture.capture_primary_png()
            images = [image_data] if image_data else None
            if images:
                logger.debug("[ChatHandler] 自动陪伴请求附带截图（%d bytes）", len(images[0]))
            else:
                logger.debug("[ChatHandler] 自动陪伴请求未附带截图（截图失败）")

            if not self._accepts_companion_generation(generation):
                return

            self._ollama.stream_chat(
                message=AUTO_COMPANION_PROMPT,
                persona=self._build_runtime_persona(),
                callback=lambda reply_text, native_tool_call=None, request_generation=generation: self._handle_auto_companion_reply(
                    reply_text,
                    native_tool_call,
                    mode_generation=request_generation,
                ),
                on_chunk=None,
                images=images,
                quiet_throttled=True,
                history=self._get_recent_context_snapshot(),
            )
        except Exception as e:
            logger.error("[ChatHandler] 自动陪伴请求失败: %s", e)
        finally:
            self._schedule_next_auto_tick()

