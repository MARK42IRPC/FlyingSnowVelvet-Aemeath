"""Ollama 会话调度与回复回退实现。"""

import json
import inspect
import threading
import time

import requests

from config.config import TIMEOUTS
from lib.core.compute_hub import get_compute_hub
from ._multimodal import is_image_input_error
from .handler_stream_presenter import _is_non_ai_status_text
from .ollama_support import (
    logger,
    OLLAMA_BASE_URL,
    PULL_EMIT_INTERVAL,
    API_RATE_LIMIT_WINDOW_SECS,
    API_RATE_LIMIT_MAX_REQUESTS,
)


class OllamaSessionMixin:
    def _start_pull_if_needed(self, model_name: str):
        """检测到配置模型缺失时，触发后台静默下载（去重保护）"""
        if not self._is_running:
            return
        if model_name in self._pulling_models:
            return
        self._pulling_models.add(model_name)
        logger.info("[OllamaManager] 配置模型 %r 本地不存在，开始后台下载...", model_name)
        future = get_compute_hub().submit_io(self._pull_model, model_name)
        if future is None:
            self._pulling_models.discard(model_name)

    def _pull_model(self, model_name: str):
        """
        后台线程：流式 pull 模型，每 PULL_EMIT_INTERVAL 秒打印一次进度到控制台。
        下载完成后重新 ping 刷新模型列表，使 _select_model 自动切换到该模型。
        """
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/pull",
                json={"model": model_name, "stream": True},
                stream=True,
                timeout=(10, TIMEOUTS['ollama_pull_read']),
            )
            with self._pull_response_lock:
                self._pull_response = resp
            resp.raise_for_status()

            last_emit_time = 0.0
            last_completed = 0

            for line in resp.iter_lines():
                if not self._is_running:
                    break
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                status    = obj.get("status", "")
                total     = obj.get("total", 0)
                completed = obj.get("completed", 0)
                now       = time.monotonic()

                if total > 0:
                    elapsed = now - last_emit_time if last_emit_time > 0 else None
                    if elapsed is None or elapsed >= PULL_EMIT_INTERVAL:
                        pct       = int(completed / total * 100)
                        speed_str = self._format_speed((completed - last_completed) / elapsed) \
                                    if elapsed else "计算中..."
                        logger.info("[OllamaManager] 下载 %s: %s  %s", model_name, pct, speed_str)
                        last_emit_time = now
                        last_completed = completed
                elif status and status not in ("success", ""):
                    logger.debug("[OllamaManager] 下载 %s: %s", model_name, status)

                if status == "success":
                    break

            logger.info("[OllamaManager] 模型下载完成: %s", model_name)
            get_compute_hub().submit_latest(
                "ollama_ping",
                self._ping,
                executor="io",
            )

        except requests.HTTPError as e:
            logger.error("[OllamaManager] 下载模型 %r 失败 (%s)", model_name, e.response.status_code)
        except Exception as e:
            logger.error("[OllamaManager] 下载模型 %r 异常: %s", model_name, e)
        finally:
            with self._pull_response_lock:
                response = self._pull_response
                self._pull_response = None
            if response is not None:
                response.close()
            self._pulling_models.discard(model_name)

    @staticmethod
    def _format_speed(bps: float) -> str:
        """将字节/秒转换为人类可读的速度字符串"""
        if bps >= 1_048_576:
            return f"{bps / 1_048_576:.1f} MB/s"
        if bps >= 1_024:
            return f"{bps / 1_024:.1f} KB/s"
        return f"{bps:.0f} B/s"

    def _try_consume_api_rate_quota(self) -> tuple[bool, int]:
        """
        外部 API 模式限频：滑动窗口 60 秒内最多 10 次请求。

        Returns:
            (是否可发起, 建议重试秒数)
        """
        if not self._use_api_key:
            return True, 0

        now = time.monotonic()
        window_start = now - API_RATE_LIMIT_WINDOW_SECS
        with self._api_rate_lock:
            while self._api_request_timestamps and self._api_request_timestamps[0] <= window_start:
                self._api_request_timestamps.popleft()

            if len(self._api_request_timestamps) >= API_RATE_LIMIT_MAX_REQUESTS:
                oldest = self._api_request_timestamps[0]
                retry_after = max(1, int(API_RATE_LIMIT_WINDOW_SECS - (now - oldest)))
                return False, retry_after

            self._api_request_timestamps.append(now)
            return True, 0

    def stream_chat(self, message: str, persona: str, callback, on_chunk=None,
                    images: list[bytes] = None, quiet_throttled: bool = False,
                    history: list[dict] | None = None,
                    allow_tools: bool = True):
        """
        发起流式聊天请求（后台线程），收集完整回复后通过 callback 传回主线程。

        遍历顺序：选定模型优先 → /api/chat → /api/generate → 下一个模型 → …

        并发安全：每个请求按 request_id 独立绑定回调，避免并发下回调覆盖导致串台。

        Args:
            message:  用户输入文本
            persona:  系统人格提示词（system prompt）
            callback: 完成时调用 callback(full_text: str)
            on_chunk: 可选，每收到一个流式 chunk 时调用 on_chunk(accumulated_text: str)
            images:   可选，图片字节数组列表（多模态支持）
            quiet_throttled: 超限时是否静默丢弃（True=不回调；False=回调提示文本）
        """
        if (
            self._api_type in ('rule_reply', 'error')
            or not self._is_running
            or (self._api_type == 'ollama' and not self._available_models)
        ):
            if callback is not None:
                self._dispatch_callback(callback, "")
            return

        allowed, retry_after = self._try_consume_api_rate_quota()
        if not allowed:
            logger.warning(
                "[OllamaManager] 外部API限频触发：60秒最多%d次，约%d秒后可重试",
                API_RATE_LIMIT_MAX_REQUESTS,
                retry_after,
            )
            if callback is not None and not quiet_throttled:
                self._dispatch_callback(
                    callback,
                    f"外部API请求过于频繁：每分钟最多{API_RATE_LIMIT_MAX_REQUESTS}次，请约{retry_after}秒后再试。",
                )
            return

        with self._chat_state_lock:
            self._chat_request_id += 1
            request_id = self._chat_request_id
            self._chat_callbacks[request_id] = callback
            if on_chunk is not None:
                self._chat_chunk_callbacks[request_id] = on_chunk

        try:
            future = get_compute_hub().submit_io(
                self._run_stream_chat,
                message,
                persona,
                request_id,
                on_chunk is not None,
                images,
                history,
                allow_tools,
            )
        except Exception as exc:
            logger.error("[OllamaManager] 聊天后台任务提交失败: %s", exc)
            future = None

        if future is None:
            self._dispatch_callback(
                self._on_chat_ready,
                request_id,
                "当前回复模式请求失败: 后台任务调度不可用",
                None,
            )

    def _run_stream_chat(self, message: str, persona: str, request_id: int,
                         streaming: bool, images: list[bytes] = None,
                         history: list[dict] | None = None,
                         allow_tools: bool = True):
        """
        后台线程：根据 API 类型选择不同的流式调用方式。
        """
        full_text  = ""
        last_error = ""
        sent_len = 0
        last_emit_ts = 0.0
        native_tool_call = None

        def capture_tool_call(tool_call: dict) -> None:
            nonlocal native_tool_call
            if native_tool_call is None:
                native_tool_call = tool_call

        def emit_chunk(text: str) -> None:
            nonlocal sent_len, last_emit_ts
            if not text:
                return
            now = time.monotonic()
            cur_len = len(text)
            delta = cur_len - sent_len
            if sent_len > 0 and delta < 24 and (now - last_emit_ts) < 0.04:
                return
            sent_len = cur_len
            last_emit_ts = now
            self._dispatch_callback(self._on_chunk_ready, request_id, text)

        chunk_fn = emit_chunk if streaming else None

        if self._use_api_key:
            try:
                full_text = self._openai_chat_api(
                    message,
                    persona,
                    on_chunk_emit=chunk_fn,
                    images=images,
                    history=history,
                    request_id=request_id,
                    allow_tools=allow_tools,
                    on_tool_call=capture_tool_call,
                )
            except requests.HTTPError as e:
                err    = self._extract_error(e)
                status = e.response.status_code if e.response is not None else "?"
                if images and is_image_input_error(err):
                    logger.warning("[OllamaManager] OpenAI 多模态失败，回退纯文本重试: %s", err)
                    try:
                        full_text = self._openai_chat_api(
                            message,
                            persona,
                            on_chunk_emit=chunk_fn,
                            images=None,
                            history=history,
                            request_id=request_id,
                            allow_tools=allow_tools,
                            on_tool_call=capture_tool_call,
                        )
                    except requests.HTTPError as e2:
                        err2    = self._extract_error(e2)
                        status2 = e2.response.status_code if e2.response is not None else "?"
                        logger.error("[OllamaManager] OpenAI API 错误 (%s): %s", status2, err2)
                        last_error = err2
                    except Exception as e2:
                        logger.error("[OllamaManager] OpenAI API 异常: %s", e2)
                        last_error = str(e2)
                else:
                    logger.error("[OllamaManager] OpenAI API 错误 (%s): %s", status, err)
                    last_error = err
            except Exception as e:
                logger.error("[OllamaManager] OpenAI API 异常: %s", e)
                last_error = str(e)
        if not self._use_api_key and not full_text:
            models_to_try  = self._get_models_to_try()
            image_attempts = [images]
            if images:
                image_attempts.append(None)

            for idx, current_images in enumerate(image_attempts):
                if idx == 1:
                    logger.warning("[OllamaManager] 多模态请求失败，开始纯文本兜底重试")

                for model in models_to_try:
                    # ① 尝试 /api/chat
                    try:
                        full_text = self._chat_api(
                            message, persona, model,
                            on_chunk_emit=chunk_fn, images=current_images, history=history,
                            allow_tools=allow_tools, on_tool_call=capture_tool_call,
                        )
                        if model != self._selected_model:
                            logger.info("[OllamaManager] 使用备用模型成功: %s (/api/chat)", model)
                        break
                    except requests.HTTPError as e:
                        err    = self._extract_error(e)
                        status = e.response.status_code if e.response is not None else "?"
                        if current_images and is_image_input_error(err):
                            logger.warning("[OllamaManager] /api/chat (%s) 多模态不兼容 (%s): %s",
                                           model, status, err)
                        else:
                            logger.error("[OllamaManager] /api/chat (%s) %s: %s", model, status, err)
                        last_error = err
                    except Exception as e:
                        logger.error("[OllamaManager] /api/chat (%s) 异常: %s", model, e)
                        last_error = str(e)

                    # ② 同一模型降级到 /api/generate
                    try:
                        full_text = self._generate_api(
                            message, persona, model,
                            on_chunk_emit=chunk_fn, images=current_images, history=history,
                        )
                        if model != self._selected_model:
                            logger.info("[OllamaManager] 使用备用模型成功: %s (/api/generate)", model)
                        break
                    except requests.HTTPError as e:
                        err    = self._extract_error(e)
                        status = e.response.status_code if e.response is not None else "?"
                        if current_images and is_image_input_error(err):
                            logger.warning("[OllamaManager] /api/generate (%s) 多模态不兼容 (%s): %s",
                                           model, status, err)
                        else:
                            logger.error("[OllamaManager] /api/generate (%s) %s: %s", model, status, err)
                        last_error = err
                    except Exception as e:
                        logger.error("[OllamaManager] /api/generate (%s) 异常: %s", model, e)
                        last_error = str(e)

                if full_text or native_tool_call is not None:
                    break

        if not full_text and native_tool_call is None and self._strict_mode:
            err_text = (last_error or "").strip()
            if err_text:
                full_text = f"当前回复模式请求失败: {err_text[:300]}"
            else:
                full_text = "当前回复模式请求失败: 当前模式不可用"

        if streaming and full_text and len(full_text) != sent_len and not _is_non_ai_status_text(full_text):
            self._dispatch_callback(self._on_chunk_ready, request_id, full_text)

        self._dispatch_callback(
            self._on_chat_ready,
            request_id,
            full_text,
            native_tool_call,
        )

    def _dispatch_callback(self, callback, *args) -> None:
        dispatcher = getattr(self, "_callback_dispatcher", None)
        if dispatcher is None:
            return
        dispatcher.dispatch(callback, *args)

    def _get_models_to_try(self) -> list[str]:
        """
        生成模型尝试顺序：
        1. 当前选定模型（如有）
        2. 其余模型按 size 降序排列
        """
        order: list[str] = []
        if self._selected_model:
            order.append(self._selected_model)
        for m in sorted(self._available_models, key=lambda x: x.get("size", 0), reverse=True):
            name = m.get("name", "")
            if name and name not in order:
                order.append(name)
        return order

    def _on_chunk_ready(self, request_id: int, text: str):
        """Qt 主线程：流式块就绪；按 request_id 路由到对应回调。"""
        with self._chat_state_lock:
            chunk_cb = self._chat_chunk_callbacks.get(request_id)
        if chunk_cb:
            chunk_cb(text)

    def _on_chat_ready(self, request_id: int, text: str, native_tool_call=None):
        """Qt 主线程：回复就绪；按 request_id 路由并清理该请求回调。"""
        with self._chat_state_lock:
            self._chat_chunk_callbacks.pop(request_id, None)
            callback = self._chat_callbacks.pop(request_id, None)
        if callback:
            try:
                signature = inspect.signature(callback)
                signature.bind(text, native_tool_call)
            except (TypeError, ValueError):
                callback(text)
            else:
                callback(text, native_tool_call)

    @property
    def is_chat_busy(self) -> bool:
        """当前是否存在进行中的聊天请求。"""
        with self._chat_state_lock:
            return bool(self._chat_callbacks)

