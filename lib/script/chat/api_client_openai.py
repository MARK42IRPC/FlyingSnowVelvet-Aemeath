"""OpenAI 兼容 API 客户端实现。"""

import json
import threading
import time

import requests

from lib.core.logger import get_logger

from ._multimodal import images_to_openai_content
from .api_client_common import _ApiClientCommonMixin, multimodal_cooldown
from .api_client_error import _ApiClientErrorMixin
from .native_tools import (
    NativeToolCallAccumulator,
    add_native_tool_instruction,
    get_native_tool_definitions,
)

logger = get_logger(__name__)


class _ApiClientOpenAIMixin(_ApiClientCommonMixin, _ApiClientErrorMixin):
    @staticmethod
    def _strip_openai_endpoint_suffix(url: str) -> str:
        text = (url or "").rstrip("/")
        for suffix in ("/v1/chat/completions", "/chat/completions"):
            if text.lower().endswith(suffix):
                return text[:-len(suffix)].rstrip("/")
        return text

    @staticmethod
    def _merge_stream_piece(full_text: str, piece: str) -> tuple[str, str, str]:
        """合并流式文本分片，兼容增量、累计和重复片段。"""
        if not piece:
            return full_text, "", "empty"
        if not full_text:
            return piece, piece, "init"
        if piece == full_text:
            return full_text, "", "duplicate_full"
        if piece.startswith(full_text):
            delta_piece = piece[len(full_text):]
            return piece, delta_piece, "cumulative"
        if full_text.endswith(piece):
            return full_text, "", "duplicate_suffix"
        if len(piece) >= 4 and full_text.startswith(piece):
            return full_text, "", "stale_prefix"

        max_overlap = min(len(full_text), len(piece))
        for overlap in range(max_overlap, 0, -1):
            if full_text.endswith(piece[:overlap]):
                delta_piece = piece[overlap:]
                if not delta_piece:
                    return full_text, "", "duplicate_overlap"
                return full_text + delta_piece, delta_piece, "overlap"

        return full_text + piece, piece, "append"

    @staticmethod
    def _openai_endpoint_candidates(base_url: str) -> list[str]:
        """
        生成 OpenAI 兼容端点候选。

        兼容两类 base_url：
        - 已包含 /v1：.../v1
        - 未包含 /v1：.../
        """
        raw_base = (base_url or "").rstrip("/")
        base = _ApiClientOpenAIMixin._strip_openai_endpoint_suffix(raw_base)
        if not base:
            return ["/chat/completions"]

        candidates: list[str] = []
        if raw_base.lower().endswith("/v1/chat/completions") or raw_base.lower().endswith("/chat/completions"):
            candidates.append(raw_base)
        candidates.append(f"{base}/chat/completions")
        tail = base.rsplit("/", 1)[-1].lower()
        if tail != "v1":
            candidates.append(f"{base}/v1/chat/completions")

        # 去重保序
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    @staticmethod
    def _is_gemini_compatible_target(base_url: str, model: str) -> bool:
        """
        判断当前目标是否为 Gemini 系（原生或三方 OpenAI 兼容网关）。
        """
        text = f"{base_url or ''} {model or ''}".lower()
        if "gemini" in text:
            return True
        # Google 官方 OpenAI 兼容入口常见域名/路径
        return (
            "generativelanguage.googleapis.com" in text
            or "ai.google.dev" in text
            or "/openai" in text and "googleapis.com" in text
        )

    @staticmethod
    def _supports_reasoning_extensions(base_url: str, model: str) -> bool:
        """
        判断是否应携带 enable_thinking / thinking_budget 这类厂商扩展字段。
        这类字段并非 OpenAI 标准，默认仅对 Qwen 生态放行。
        """
        text = f"{base_url or ''} {model or ''}".lower()
        return ("qwen" in text) or ("qwq" in text)

    @staticmethod
    def _dedupe_payload_variants(payloads: list[dict]) -> list[dict]:
        """按 JSON 文本去重 payload 变体，保持原顺序。"""
        unique: list[dict] = []
        seen: set[str] = set()
        for payload in payloads:
            try:
                key = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except Exception:
                key = str(payload)
            if key in seen:
                continue
            seen.add(key)
            unique.append(payload)
        return unique

    @staticmethod
    def _prepend_native_tool_payloads(payloads: list[dict]) -> list[dict]:
        """Try native tools first while retaining plain payloads for incompatible gateways."""
        tools = get_native_tool_definitions()
        native_payloads: list[dict] = []
        for payload in payloads:
            cloned = dict(payload)
            cloned["messages"] = add_native_tool_instruction(payload.get("messages") or [])
            cloned["tools"] = tools
            cloned["tool_choice"] = "auto"
            native_payloads.append(cloned)
        return _ApiClientOpenAIMixin._dedupe_payload_variants(native_payloads + payloads)

    @staticmethod
    def _build_openai_payload_variants(model: str, persona: str, message: str,
                                       history: list[dict] | None,
                                       images: list[bytes] | None,
                                       image_blocks: list[dict] | None = None,
                                       temperature: float | None = None,
                                       enable_thinking: bool | None = None,
                                       thinking_budget: int | None = None,
                                       request_user: str | None = None,
                                       include_reasoning_extensions: bool = True,
                                       include_user_field: bool = True,
                                       include_systemless_fallback: bool = False,
                                       include_relaxed_multimodal_variants: bool = True,
                                       include_input_multimodal_variants: bool = True) -> list[dict]:
        """
        生成 OpenAI 兼容请求体候选（多模态自动回退）。

        变体顺序：
        1. 标准 OpenAI：image_url={"url": "..."}
        2. 宽松兼容：image_url="..."
        3. input_* 兼容：input_text/input_image（部分网关要求）
        4. systemless 回退（可选）：将 system 文本并入 user，兼容不支持 system role 的网关
        """
        merged_text = message if not persona else f"{persona}\n\n用户：{message}"

        def as_systemless_user_content(content: str | list[dict]) -> str | list[dict]:
            """
            将 system 内容并入 user 内容，保留图片块顺序。
            """
            if isinstance(content, str):
                return merged_text
            cloned: list[dict] = []
            injected = False
            for block in content:
                if isinstance(block, dict):
                    b = dict(block)
                    btype = str(b.get("type", "")).strip().lower()
                    if not injected and btype in ("text", "input_text"):
                        b["text"] = merged_text
                        injected = True
                    cloned.append(b)
                else:
                    cloned.append(block)
            if not injected:
                cloned.insert(0, {"type": "text", "text": merged_text})
            return cloned

        history_messages = _ApiClientOpenAIMixin._build_openai_history_messages(history)

        def build_messages(user_content: str | list[dict], *, systemless: bool) -> list[dict]:
            if systemless:
                return history_messages + [{"role": "user", "content": as_systemless_user_content(user_content)}]
            messages = [{"role": "system", "content": persona}]
            messages.extend(history_messages)
            messages.append({"role": "user", "content": user_content})
            return messages

        def with_extra_options(payload: dict) -> dict:
            # 部分 OpenAI 兼容网关支持的扩展参数：
            # - temperature: 采样温度，适度调高可提升表达多样性
            # - enable_thinking: 控制是否输出 reasoning_content
            # - thinking_budget: 限制思考 token 上限（可选）
            if temperature is not None:
                payload["temperature"] = float(temperature)
            if include_reasoning_extensions:
                if enable_thinking is not None:
                    payload["enable_thinking"] = bool(enable_thinking)
                if thinking_budget is not None and thinking_budget > 0:
                    payload["thinking_budget"] = int(thinking_budget)
            # OpenAI 兼容字段：用户标识。使用请求级唯一值可降低网关串会话风险。
            if include_user_field and request_user:
                payload["user"] = str(request_user)
            return payload

        payloads: list[dict] = []

        if not images:
            payloads.append(with_extra_options({
                "model": model,
                "messages": build_messages(message, systemless=False),
                "stream": True,
            }))
            if include_systemless_fallback:
                payloads.append(with_extra_options({
                    "model": model,
                    "messages": build_messages(message, systemless=True),
                    "stream": True,
                }))
            return _ApiClientOpenAIMixin._dedupe_payload_variants(payloads)

        if image_blocks is None:
            image_blocks = images_to_openai_content(images)
        standard_user_content: list[dict] = [{"type": "text", "text": message}]
        standard_user_content.extend(image_blocks)

        payloads.append(with_extra_options({
            "model": model,
            "messages": build_messages(standard_user_content, systemless=False),
            "stream": True,
        }))
        if include_systemless_fallback:
            payloads.append(with_extra_options({
                "model": model,
                "messages": build_messages(standard_user_content, systemless=True),
                "stream": True,
            }))

        # 变体2：image_url 字段降级为字符串（部分兼容服务使用此格式）
        relaxed_blocks: list[dict] = []
        for block in image_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "image_url":
                relaxed_blocks.append(block)
                continue
            image_url = block.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
            else:
                url = str(image_url or "")
            relaxed_blocks.append({
                "type": "image_url",
                "image_url": url,
            })
        if include_relaxed_multimodal_variants and relaxed_blocks:
            relaxed_content = [{"type": "text", "text": message}] + relaxed_blocks
            payloads.append(with_extra_options({
                "model": model,
                "messages": build_messages(relaxed_content, systemless=False),
                "stream": True,
            }))
            if include_systemless_fallback:
                payloads.append(with_extra_options({
                    "model": model,
                    "messages": build_messages(relaxed_content, systemless=True),
                    "stream": True,
                }))

        # 变体3：input_* 格式（常见于部分三方转发网关）
        input_style_content: list[dict] = [{"type": "input_text", "text": message}]
        for block in image_blocks:
            if not isinstance(block, dict) or block.get("type") != "image_url":
                continue
            image_url = block.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
            else:
                url = str(image_url or "")
            input_style_content.append({
                "type": "input_image",
                "image_url": url,
            })
        if include_input_multimodal_variants and len(input_style_content) > 1:
            payloads.append(with_extra_options({
                "model": model,
                "messages": build_messages(input_style_content, systemless=False),
                "stream": True,
            }))
            if include_systemless_fallback:
                payloads.append(with_extra_options({
                    "model": model,
                    "messages": build_messages(input_style_content, systemless=True),
                    "stream": True,
                }))

        return _ApiClientOpenAIMixin._dedupe_payload_variants(payloads)

    def _consume_openai_stream(
        self,
        resp: requests.Response,
        on_chunk_emit,
        deadline: float,
        request_started_at: float | None = None,
        endpoint: str = "",
        on_tool_call=None,
    ) -> str:
        """消费 OpenAI 兼容流，兼容 SSE / NDJSON / 非标准 chunk 内容。"""
        full_text = ""
        line_count = 0
        piece_count = 0
        pending_data = ""
        last_data_sample = ""
        first_piece_at: float | None = None
        tool_calls = NativeToolCallAccumulator()

        def consume_chunk(chunk: dict) -> bool:
            """处理单个已解析 JSON chunk，返回是否应结束流。"""
            nonlocal full_text, piece_count

            tool_calls.consume_openai_chunk(chunk)
            piece = self._extract_openai_chunk_text(chunk)
            if piece:
                nonlocal first_piece_at
                piece_count += 1
                merged_text, delta_piece, merge_mode = self._merge_stream_piece(full_text, piece)
                if merge_mode != "append":
                    logger.debug(
                        "[APIClient] 规范化流式分片: endpoint=%s mode=%s raw_len=%d delta_len=%d total_len=%d",
                        endpoint,
                        merge_mode,
                        len(piece),
                        len(delta_piece),
                        len(merged_text),
                    )
                if not delta_piece and merged_text == full_text:
                    return False
                full_text = merged_text
                if first_piece_at is None:
                    first_piece_at = time.monotonic()
                    if request_started_at is not None:
                        logger.debug(
                            "[APIClient] 首分片到达: endpoint=%s ttfb=%.3fs",
                            endpoint,
                            first_piece_at - request_started_at,
                        )
                if on_chunk_emit:
                    try:
                        on_chunk_emit(full_text)
                    except Exception as cb_err:
                        # UI 回调异常不应中断底层流读取；记录后继续收集完整文本。
                        logger.warning("[APIClient] on_chunk_emit 回调异常，已忽略: %s", cb_err)

            # 兼容 done 标记
            if bool(chunk.get("done")):
                return True

            choices = chunk.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                if first.get("finish_reason"):
                    return True
            return False

        for line in self._iter_stream_lines(resp):
            line_count += 1
            if time.monotonic() > deadline:
                break
            if line.startswith("event:"):
                # 兼容 SSE 事件名行
                continue
            if line.startswith('data:'):
                data = line[5:].lstrip()
            else:
                # 兼容 NDJSON（无 data: 前缀）
                data = line
            if not data:
                continue
            if data == '[DONE]':
                break

            # 兼容多行 SSE 数据块：JSON 可能被拆成多行 data。
            candidate = f"{pending_data}\n{data}" if pending_data else data
            last_data_sample = candidate[:200]
            try:
                chunk = json.loads(candidate)
                pending_data = ""
            except json.JSONDecodeError:
                pending_data = candidate
                continue

            if isinstance(chunk, dict) and consume_chunk(chunk):
                break

        # 尝试解析最后残留的数据块（如流在 chunk 边界关闭）
        if pending_data and time.monotonic() <= deadline:
            try:
                chunk = json.loads(pending_data)
                if isinstance(chunk, dict):
                    consume_chunk(chunk)
            except json.JSONDecodeError:
                pass

        native_tool_call = tool_calls.first()
        if native_tool_call is not None and on_tool_call is not None:
            try:
                on_tool_call(native_tool_call)
            except Exception as cb_err:
                logger.warning("[APIClient] on_tool_call 回调异常，已忽略: %s", cb_err)

        if piece_count == 0 and native_tool_call is None:
            logger.warning("[APIClient] 流式结束但未解析到文本: lines=%d chars=%d sample=%r",
                           line_count, len(full_text), last_data_sample[:120])
        elif request_started_at is not None and first_piece_at is not None:
            logger.debug(
                "[APIClient] 流式阶段耗时: endpoint=%s stream=%.3fs total=%.3fs",
                endpoint,
                max(0.0, time.monotonic() - first_piece_at),
                max(0.0, time.monotonic() - request_started_at),
            )
        logger.debug("[APIClient] 流式解析统计: lines=%d pieces=%d chars=%d",
                     line_count, piece_count, len(full_text))
        return full_text

    @multimodal_cooldown
    def _openai_chat_api(self, message: str, persona: str,
                         on_chunk_emit=None, images: list[bytes] = None,
                         history: list[dict] | None = None,
                         request_id: int | None = None,
                         config_override: dict | None = None,
                         allow_tools: bool = True,
                         on_tool_call=None) -> str:
        """
        POST /chat/completions（OpenAI 兼容 API 格式）。

        on_chunk_emit: 可选回调，每累积到新内容时从后台线程调用 on_chunk_emit(accumulated_text)
        images: 可选的图片字节数组列表（多模态支持，内部会转换为 base64）
        """
        # 从模块级变量读取（通过 self 间接访问，在 OllamaManager 中定义）
        from config.ollama_config import OLLAMA
        base_stream_max    = float(OLLAMA.get('stream_max_secs', 30) or 30)
        api_stream_max     = float(OLLAMA.get('api_stream_max_secs', 90) or 90)
        _STREAM_MAX_SECS   = max(base_stream_max, api_stream_max)
        from .network_policy import API_TIMEOUT_SECS, API_TOTAL_ATTEMPTS

        raw_retry_backoff = OLLAMA.get('api_retry_backoff', 0.8)
        raw_disable_env_proxy = OLLAMA.get('api_disable_env_proxy', True)
        api_connect_timeout = API_TIMEOUT_SECS
        api_read_timeout = API_TIMEOUT_SECS
        api_retry_times = API_TOTAL_ATTEMPTS
        try:
            api_retry_backoff = float(raw_retry_backoff)
        except (TypeError, ValueError):
            api_retry_backoff = 0.8
        api_retry_backoff = max(0.0, min(5.0, api_retry_backoff))
        if isinstance(raw_disable_env_proxy, str):
            api_disable_env_proxy = raw_disable_env_proxy.strip().lower() in ("1", "true", "yes", "on")
        else:
            api_disable_env_proxy = bool(raw_disable_env_proxy)
        raw_api_temperature = OLLAMA.get('api_temperature', 0.8)
        try:
            api_temperature = float(raw_api_temperature)
        except (TypeError, ValueError):
            api_temperature = 0.8
        api_temperature = max(0.0, min(2.0, api_temperature))
        # Qwen3.5 系列默认会先输出 reasoning_content，若只消费 content 会表现为"无流式"。
        # 默认关闭思考模式，确保聊天与工具指令都能稳定从 content 流中实时到达。
        raw_enable_thinking = OLLAMA.get('api_enable_thinking', False)
        if isinstance(raw_enable_thinking, str):
            api_enable_thinking = raw_enable_thinking.strip().lower() in ("1", "true", "yes", "on")
        else:
            api_enable_thinking = bool(raw_enable_thinking)
        raw_thinking_budget = OLLAMA.get('api_thinking_budget', 0)
        api_thinking_budget = int(raw_thinking_budget) if str(raw_thinking_budget).strip() else 0

        active_config = config_override or self._active_config
        base_url  = active_config['base_url'].rstrip('/')
        api_key   = active_config['api_key']
        model     = active_config['model']
        is_gemini_target = self._is_gemini_compatible_target(base_url, model)
        allow_reasoning_extensions = self._supports_reasoning_extensions(base_url, model)

        headers = {
            "Content-Type":  "application/json",
            "Accept":        "text/event-stream",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if request_id is not None:
            headers["X-Request-ID"] = f"snowrol-{request_id}"

        if request_id is not None:
            request_user = f"snowrol-{request_id}-{int(time.time() * 1000)}"
        else:
            request_user = f"snowrol-{threading.get_ident()}-{int(time.time() * 1000)}"

        payload_images = images

        endpoint_candidates = self._openai_endpoint_candidates(base_url)
        prepared_image_blocks = images_to_openai_content(payload_images) if payload_images else None
        payload_candidates = self._build_openai_payload_variants(
            model=model,
            persona=persona,
            message=message,
            history=history,
            images=payload_images,
            image_blocks=prepared_image_blocks,
            temperature=api_temperature,
            enable_thinking=api_enable_thinking,
            thinking_budget=api_thinking_budget if api_thinking_budget > 0 else None,
            request_user=request_user,
            include_reasoning_extensions=allow_reasoning_extensions,
            include_user_field=True,
            include_systemless_fallback=bool(payload_images) and is_gemini_target,
            include_relaxed_multimodal_variants=True,
            include_input_multimodal_variants=True,
        )
        # 兼容兜底：仅在多模态请求下追加保守变体（禁用扩展字段+禁用 user 字段）。
        if payload_images:
            conservative_payloads = self._build_openai_payload_variants(
                model=model,
                persona=persona,
                message=message,
                history=history,
                images=payload_images,
                image_blocks=prepared_image_blocks,
                temperature=api_temperature,
                enable_thinking=api_enable_thinking,
                thinking_budget=api_thinking_budget if api_thinking_budget > 0 else None,
                request_user=request_user,
                include_reasoning_extensions=False,
                include_user_field=False,
                include_systemless_fallback=False,
                include_relaxed_multimodal_variants=True,
                include_input_multimodal_variants=True,
            )
            payload_candidates.extend(conservative_payloads)
        native_tools_enabled = allow_tools and self._native_tools_available(base_url, model)
        if native_tools_enabled:
            payload_candidates = self._prepend_native_tool_payloads(payload_candidates)
        payload_candidates = self._dedupe_payload_variants(payload_candidates)
        if not endpoint_candidates:
            raise RuntimeError('OpenAI 兼容请求失败：未生成可用端点，请检查 API_BASE_URL')
        if not payload_candidates:
            raise RuntimeError('OpenAI 兼容请求失败：未生成可用请求体，请检查模型配置')
        if payload_images and is_gemini_target:
            logger.debug("[APIClient] 检测到 Gemini 兼容目标，启用多模态兼容变体（%d 个payload）",
                         len(payload_candidates))
        elif payload_images and not allow_reasoning_extensions:
            logger.debug("[APIClient] 当前网关非 Qwen 扩展生态，已禁用思考扩展字段（%d 个payload）",
                         len(payload_candidates))

        last_http_error: requests.HTTPError | None = None
        last_exception: Exception | None = None

        for endpoint in endpoint_candidates:
            for payload in payload_candidates:
                if "tools" in payload and not self._native_tools_available(base_url, model):
                    continue
                for attempt in range(1, api_retry_times + 1):
                    resp = None
                    try:
                        request_started_at = time.monotonic()
                        resp = self._request_with_proxy_fallback(
                            "POST",
                            endpoint,
                            disable_env_proxy=api_disable_env_proxy,
                            headers=headers,
                            json=payload,
                            stream=True,
                            timeout=(api_connect_timeout, api_read_timeout),
                        )
                        response_ready_at = time.monotonic()
                        logger.debug(
                            "[APIClient] 响应头就绪: endpoint=%s dt=%.3fs attempt=%d",
                            endpoint,
                            response_ready_at - request_started_at,
                            attempt,
                        )
                        if not resp.ok:
                            resp.content
                        resp.raise_for_status()
                        deadline = time.monotonic() + _STREAM_MAX_SECS
                        result = self._consume_openai_stream(
                            resp,
                            on_chunk_emit,
                            deadline,
                            request_started_at=request_started_at,
                            endpoint=endpoint,
                            on_tool_call=on_tool_call,
                        )
                        return result
                    except requests.HTTPError as e:
                        last_http_error = e
                        status = e.response.status_code if e.response is not None else 0
                        try:
                            error_detail = self._extract_error(e).lower()
                        except Exception:
                            error_detail = str(e).lower()
                        rejected_tools = any(
                            token in error_detail
                            for token in ("tools", "tool_choice", "function calling", "function_call")
                        )
                        if "tools" in payload and status in (400, 422) and rejected_tools:
                            if self._mark_native_tools_unsupported(base_url, model):
                                logger.info(
                                    "[APIClient] 当前 OpenAI 兼容目标不接受原生 tools，后续使用文本协议: %s %s",
                                    base_url,
                                    model,
                                )
                        should_retry = status in (408, 425, 429) or status >= 500
                        if should_retry and attempt < api_retry_times:
                            logger.warning(
                                "[APIClient] OpenAI兼容请求失败，准备重试: endpoint=%s status=%s attempt=%d/%d",
                                endpoint,
                                status,
                                attempt,
                                api_retry_times,
                            )
                            if api_retry_backoff > 0:
                                time.sleep(api_retry_backoff * attempt)
                            continue
                        try:
                            err = self._extract_error(e)
                        except Exception:
                            err = str(e)
                        logger.debug("[APIClient] OpenAI兼容请求失败 endpoint=%s: %s", endpoint, err)
                        break
                    except (requests.Timeout, requests.ConnectionError) as e:
                        last_exception = e
                        if attempt < api_retry_times:
                            logger.warning(
                                "[APIClient] OpenAI网络超时/连接失败，准备重试: endpoint=%s attempt=%d/%d err=%s",
                                endpoint,
                                attempt,
                                api_retry_times,
                                e,
                            )
                            if api_retry_backoff > 0:
                                time.sleep(api_retry_backoff * attempt)
                            continue
                        logger.debug("[APIClient] OpenAI兼容请求异常 endpoint=%s: %s", endpoint, e)
                        break
                    except Exception as e:
                        last_exception = e
                        if attempt < api_retry_times:
                            logger.warning(
                                "[APIClient] OpenAI请求异常，准备重试: endpoint=%s attempt=%d/%d err=%s",
                                endpoint,
                                attempt,
                                api_retry_times,
                                e,
                            )
                            if api_retry_backoff > 0:
                                time.sleep(api_retry_backoff * attempt)
                            continue
                        logger.debug("[APIClient] OpenAI兼容请求异常 endpoint=%s: %s", endpoint, e)
                        break
                    finally:
                        self._close_response(resp)

        if last_http_error is not None:
            raise last_http_error
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("OpenAI 兼容请求失败：无可用端点")
