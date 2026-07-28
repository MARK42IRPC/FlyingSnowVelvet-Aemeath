"""Ollama ?????????????"""

import requests

from config.ollama_config import OLLAMA_MODEL

from .ollama_registry import update_models_from_tags
from .ollama_support import logger


class OllamaStateMixin:
    def _apply_status_direct(self, resp: requests.Response):
        """
        预启动阶段直接写入服务状态（Qt 信号尚不可用时调用）。

        Python GIL 保证对实例属性的简单赋值具有原子性，跨线程直接写入安全。
        """
        try:
            models = resp.json().get("models", [])
        except Exception:
            models = []

        self._is_running       = True
        self._available_models = models
        update_models_from_tags(models)
        self._select_model()
        logger.info("[OllamaManager] 预启动检测完成：%d 个可用模型%s",
                    len(models), f"，已选定 {self._selected_model}" if self._selected_model else "")

    def _on_status_ready(self, is_running: bool, models: list):
        """Qt 主线程：处理 ping 结果，仅在状态或模型列表变化时更新"""
        was_running = self._is_running

        old_names     = {m.get("name") for m in self._available_models}
        new_names     = {m.get("name") for m in models}
        models_changed = old_names != new_names

        self._is_running       = is_running
        self._available_models = models
        update_models_from_tags(models)

        if is_running and not was_running:
            logger.info("[OllamaManager] ollama 已连接，发现 %d 个模型", len(models))
        elif not is_running and was_running:
            logger.warning("[OllamaManager] ollama 连接断开")

        if (is_running and not was_running) or models_changed or self._selected_model is None:
            self._select_model()

    def _model_exists(self, name: str) -> bool:
        """
        检查模型名是否在可用列表中。

        支持无 tag 模糊匹配：配置写 'qwen2.5' 时，
        能匹配本地的 'qwen2.5:latest' / 'qwen2.5:7b' 等。
        """
        names = {m.get("name", "") for m in self._available_models}
        if name in names:
            return True
        if ":" not in name:
            return any(n.startswith(name + ":") for n in names)
        return False

    def _select_model(self):
        """根据配置选择模型（仅在变化时打印）"""
        if self._use_api_key:
            new_model = self._active_config.get('model')
        elif self._rule_reply_mode:
            new_model = 'rule_reply'
        elif self._api_type == 'error':
            new_model = None
        else:
            preferred = OLLAMA_MODEL.strip() if OLLAMA_MODEL else ""

            if preferred:
                if not self._model_exists(preferred):
                    self._start_pull_if_needed(preferred)
                new_model = preferred
            elif self._available_models:
                best      = max(self._available_models, key=lambda m: m.get("size", 0))
                new_model = best["name"]
            else:
                new_model = None

        if new_model != self._selected_model:
            self._selected_model = new_model
            if self._use_api_key:
                logger.info("[OllamaManager] API Key 模式，使用模型: %s", new_model)
            elif self._rule_reply_mode:
                logger.warning("[OllamaManager] 规则回复模式已启用")
            elif self._api_type == 'error':
                logger.error("[OllamaManager] 模式错误：%s", self._mode_error or "unknown")
            elif OLLAMA_MODEL and new_model and self._model_exists(OLLAMA_MODEL):
                logger.info("[OllamaManager] 使用配置指定模型: %s", new_model)
            elif OLLAMA_MODEL and new_model:
                logger.info("[OllamaManager] 配置模型下载中，暂以 %r 占位", new_model)
            elif new_model:
                logger.info("[OllamaManager] 自动选择最大模型: %s", new_model)
            else:
                logger.warning("[OllamaManager] 暂无可用模型")
        else:
            self._selected_model = new_model

    @property
    def is_running(self) -> bool:
        """当前 ollama 服务是否可连接"""
        return self._is_running

    @property
    def use_api_key_mode(self) -> bool:
        """是否处于外部 API（API Key）模式。"""
        return self._use_api_key

    @property
    def strict_mode_enabled(self) -> bool:
        """当前回复模式是否禁止切换到其他来源。"""
        return self._strict_mode

    @property
    def mode_error_message(self) -> str:
        """返回当前模式不可用时的错误提示（为空表示无错误）。"""
        if self._mode_error:
            return self._mode_error
        if self._strict_mode and self._api_type == 'ollama':
            if not self._is_running:
                return "本地 Ollama 模式不可用：服务未就绪"
            if not self._available_models:
                return "本地 Ollama 模式不可用：无可用模型"
        return ""

    @property
    def selected_model(self) -> str | None:
        """当前选用的模型名"""
        return self._selected_model

