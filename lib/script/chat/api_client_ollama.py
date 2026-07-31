"""Ollama API ?????"""

import json
import re
import time

import requests

from lib.core.logger import get_logger

from ._multimodal import images_to_ollama_payload
from .api_client_common import _ApiClientCommonMixin
from .api_client_error import _ApiClientErrorMixin
from .native_tools import NATIVE_TOOL_SYSTEM_NOTE, NativeToolCallAccumulator, get_native_tool_definitions

logger = get_logger(__name__)


class _ApiClientOllamaMixin(_ApiClientCommonMixin, _ApiClientErrorMixin):
    @staticmethod
    def _extract_character_name(persona: str) -> str:
        """
        从人格文本中提取角色名。

        匹配常见格式："你是雪绒，" / "你是雪绒。" / "你是雪绒（" 等。
        无法匹配时返回通用占位符 "助手"。
        """
        m = re.search(r'你是([^，,。（(）)\s]{1,10})[，,。（(）)\s]', persona)
        return m.group(1) if m else "助手"

    @staticmethod
    def _build_options() -> dict:
        """
        从 OLLAMA_OPTIONS 读取推理参数，构造 Ollama API 的 options 字段。

        过滤规则：
        - num_gpu 无论取何值均保留（0 表示"禁用 GPU"，是有效配置）
        - 其他参数若值为 0 或 None 则跳过，交由 Ollama 使用默认值
        """
        from config.ollama_config import OLLAMA_OPTIONS
        result = {}
        for k, v in OLLAMA_OPTIONS.items():
            if v is None:
                continue
            if k != "num_gpu" and v == 0:
                continue
            result[k] = v
        return result

    def _chat_api(self, message: str, persona: str, model: str,
                  on_chunk_emit=None, images: list[bytes] = None,
                  history: list[dict] | None = None,
                  allow_tools: bool = True, on_tool_call=None) -> str:
        """
        POST /api/chat（messages 格式，支持 system role）。

        不使用 `with resp:` 上下文管理器，以确保在 raise_for_status() 抛出
        HTTPError 后 e.response.text 仍能读取到完整错误正文。
        on_chunk_emit: 可选回调，每累积到新内容时从后台线程调用 on_chunk_emit(accumulated_text)
        images: 可选的图片字节数组列表（Ollama 多模态格式）
        """
        from config.ollama_config import OLLAMA
        OLLAMA_BASE_URL  = OLLAMA.get('base_url', 'http://localhost:11434')
        _STREAM_MAX_SECS = OLLAMA.get('stream_max_secs', 30)
        _REQUEST_TIMEOUT = OLLAMA.get('request_timeout', 60)

        user_message  = {"role": "user", "content": message}
        ollama_images = images_to_ollama_payload(images)
        if ollama_images:
            user_message["images"] = ollama_images

        payload = {
            "model":   model,
            "messages": [{"role": "system", "content": persona}] + self._build_openai_history_messages(history) + [user_message],
            "stream":  True,
            "options": self._build_options(),
        }
        native_tools_enabled = allow_tools and self._native_tools_available(OLLAMA_BASE_URL, model)
        if native_tools_enabled:
            payload["messages"][0]["content"] = f"{persona.rstrip()}\n\n{NATIVE_TOOL_SYSTEM_NOTE}"
            payload["tools"] = get_native_tool_definitions()
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            stream=True,
            timeout=_REQUEST_TIMEOUT,
        )
        try:
            if not resp.ok:
                resp.content
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                try:
                    error_detail = self._extract_error(exc).lower()
                except Exception:
                    error_detail = str(exc).lower()
                rejected_tools = any(
                    token in error_detail
                    for token in ("tools", "tool_choice", "function calling", "function_call")
                )
                if native_tools_enabled and status in (400, 422) and rejected_tools:
                    if self._mark_native_tools_unsupported(OLLAMA_BASE_URL, model):
                        logger.info(
                            "[OllamaManager] 当前 Ollama 模型不接受原生 tools，后续使用文本协议: %s",
                            model,
                        )
                raise
            full_text = ""
            tool_calls = NativeToolCallAccumulator()
            deadline  = time.monotonic() + _STREAM_MAX_SECS
            for line in self._iter_stream_lines(resp):
                if time.monotonic() > deadline:
                    break
                try:
                    chunk   = json.loads(line)
                    response_message = chunk.get("message", {})
                    tool_calls.consume_ollama_message(response_message)
                    content = response_message.get("content", "")
                    if content:
                        full_text += content
                        if on_chunk_emit:
                            on_chunk_emit(full_text)
                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
            native_tool_call = tool_calls.first()
            if native_tool_call is not None and on_tool_call is not None:
                try:
                    on_tool_call(native_tool_call)
                except Exception as cb_err:
                    logger.warning("[OllamaManager] on_tool_call 回调异常，已忽略: %s", cb_err)
            return full_text
        finally:
            resp.close()

    def _generate_api(self, message: str, persona: str, model: str,
                      on_chunk_emit=None, images: list[bytes] = None,
                      history: list[dict] | None = None) -> str:
        """
        POST /api/generate（prompt 格式，所有 ollama 版本均支持）。

        persona 与 message 拼合为单一 prompt，同样不使用 with 上下文管理器。
        on_chunk_emit: 可选回调，每累积到新内容时从后台线程调用 on_chunk_emit(accumulated_text)
        images: 可选的图片字节数组列表（Ollama 多模态格式）
        """
        from config.ollama_config import OLLAMA
        OLLAMA_BASE_URL  = OLLAMA.get('base_url', 'http://localhost:11434')
        _STREAM_MAX_SECS = OLLAMA.get('stream_max_secs', 30)
        _REQUEST_TIMEOUT = OLLAMA.get('request_timeout', 60)

        name    = self._extract_character_name(persona)
        history_prompt = self._build_generate_history_prompt(history, name)
        prompt_parts = [persona]
        if history_prompt:
            prompt_parts.append(f"[最近对话]\n{history_prompt}")
        prompt_parts.append(f"用户：{message}\n{name}：")
        prompt = "\n\n".join(part for part in prompt_parts if part)
        payload = {
            "model":   model,
            "prompt":  prompt,
            "stream":  True,
            "options": self._build_options(),
        }

        ollama_images = images_to_ollama_payload(images)
        if ollama_images:
            payload["images"] = ollama_images

        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            stream=True,
            timeout=_REQUEST_TIMEOUT,
        )
        try:
            if not resp.ok:
                resp.content
            resp.raise_for_status()
            full_text = ""
            deadline  = time.monotonic() + _STREAM_MAX_SECS
            for line in self._iter_stream_lines(resp):
                if time.monotonic() > deadline:
                    break
                try:
                    chunk   = json.loads(line)
                    content = chunk.get("response", "")
                    if content:
                        full_text += content
                        if on_chunk_emit:
                            on_chunk_emit(full_text)
                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
            return full_text
        finally:
            resp.close()
