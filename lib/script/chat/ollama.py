"""Ollama / OpenAI ?? API ???????"""

import subprocess
import threading
from collections import deque

from PyQt5.QtCore import QTimer

from lib.core.event.center import get_event_center, EventType
from config.ollama_config import get_active_config

from ._api_client import _ApiClientMixin
from .ollama_bootstrap import OllamaBootstrapMixin
from .ollama_state import OllamaStateMixin
from .ollama_session import OllamaSessionMixin
from .ollama_support import logger, OLLAMA_BASE_URL, _OllamaSignal


class OllamaManager(_ApiClientMixin, OllamaBootstrapMixin, OllamaStateMixin, OllamaSessionMixin):
    """Ollama / OpenAI ?? API ????????????"""
    def __init__(self):
        self._event_center = get_event_center()

        self._active_config = get_active_config()
        self._api_type = self._active_config.get('api_type', 'ollama')
        self._force_mode = str(self._active_config.get('force_mode', '') or '')
        self._strict_mode = bool(self._active_config.get('strict_mode', False))
        self._mode_error = str(self._active_config.get('error', '') or '').strip()
        self._use_api_key = self._api_type == 'openai_compatible'
        self._rule_reply_mode = self._api_type == 'rule_reply'

        self._is_running:        bool      = False
        self._selected_model:    str | None = None
        self._available_models:  list       = []

        # Qt 资源（APP_MAIN 触发后才可安全创建）
        self._signal:     _OllamaSignal | None = None
        self._ping_timer: QTimer | None        = None

        # 请求 ID + 回调
        self._chat_request_id:   int = 0
        self._chat_state_lock = threading.Lock()
        self._chat_callbacks: dict[int, object] = {}
        self._chat_chunk_callbacks: dict[int, object] = {}
        self._api_rate_lock = threading.Lock()
        self._api_request_timestamps: deque[float] = deque()

        # 仅跟踪本进程主动拉起的 ollama serve
        self._started_ollama: bool = False
        self._ollama_process: subprocess.Popen | None = None
        self._ollama_proc_lock = threading.Lock()

        # 正在后台下载的模型集合（去重保护）
        self._pulling_models: set = set()
        self._cli_refresh_started: bool = False

        # 订阅生命周期事件
        self._event_center.subscribe(EventType.APP_PRE_START, self._on_app_pre_start)
        self._event_center.subscribe(EventType.APP_MAIN,      self._on_app_main)

        if self._use_api_key:
            logger.info(
                "[OllamaManager] 使用 OpenAI 兼容 API 模式(%s): %s",
                self._active_config.get('key_source', 'unknown'),
                self._active_config['base_url'],
            )
        elif self._rule_reply_mode:
            logger.warning("[OllamaManager] 已启用规则回复模式（force_mode=%s）", self._force_mode or "default")
        elif self._api_type == 'error':
            logger.error("[OllamaManager] 模式初始化失败（force_mode=%s）: %s",
                         self._force_mode or "default", self._mode_error or "unknown")
        else:
            logger.info("[OllamaManager] 使用 Ollama 本地模式: %s", OLLAMA_BASE_URL)

    def reload_config(self) -> None:
        """Reload runtime AI routing config after control-panel save."""
        self._active_config = get_active_config()
        self._api_type = self._active_config.get('api_type', 'ollama')
        self._force_mode = str(self._active_config.get('force_mode', '') or '')
        self._strict_mode = bool(self._active_config.get('strict_mode', False))
        self._mode_error = str(self._active_config.get('error', '') or '').strip()
        self._use_api_key = self._api_type == 'openai_compatible'
        self._rule_reply_mode = self._api_type == 'rule_reply'

        if self._use_api_key:
            self._is_running = True
            self._selected_model = self._active_config.get('model')
        elif self._rule_reply_mode:
            self._is_running = False
            self._selected_model = 'rule_reply'
        elif self._api_type == 'error':
            self._is_running = False
            self._selected_model = None
        else:
            self._select_model()

        logger.info(
            "[OllamaManager] AI 配置已热重载: api_type=%s model=%s force_mode=%s",
            self._api_type,
            self._selected_model,
            self._force_mode or 'default',
        )



_ollama_manager: OllamaManager | None = None


def get_ollama_manager() -> OllamaManager:
    """???? OllamaManager ???????"""
    global _ollama_manager
    if _ollama_manager is None:
        _ollama_manager = OllamaManager()
    return _ollama_manager


def cleanup_ollama_manager():
    """???? OllamaManager ???"""
    global _ollama_manager
    if _ollama_manager is not None:
        _ollama_manager.cleanup()
        _ollama_manager = None
