"""聊天相关工具函数模块"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.const import MODEL_MAPPING
from src.schemas.chat import ChatCompletionChunk, Choice, ChoiceDelta, Message


def get_model_info(model_name: str) -> Optional[Dict]:
    """获取模型信息

    Args:
        model_name: 模型名称

    Returns:
        Optional[Dict]: 模型映射信息，不存在返回 None
    """
    return MODEL_MAPPING.get(model_name.lower(), None)


def _extract_message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float)):
        return str(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _extract_message_text(item)
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        if item_type in ("image_url", "input_image"):
            return ""
        for key in ("text", "content", "value", "output_text", "msg"):
            if key in content:
                text = _extract_message_text(content.get(key))
                if text:
                    return text
    return ""


def parse_messages(messages: List[Message]) -> str:
    """解析消息列表为提示词

    Args:
        messages: 消息列表

    Returns:
        str: 解析后的提示词
    """
    only_user_message = True
    for m in messages:
        if m.role != "user":
            only_user_message = False
            break
    if only_user_message:
        prompt = "\n".join([f"{m.role}: {_extract_message_text(m.content)}" for m in messages])
    else:
        prompt = "\n".join([_extract_message_text(m.content) for m in messages])
    return prompt


async def process_response_stream(response: httpx.Response, model_id: str) -> AsyncGenerator[str, None]:
    """处理响应流，转换为 OpenAI 格式

    Args:
        response: HTTP 响应对象
        model_id: 模型 ID

    Yields:
        str: SSE 格式的数据块
    """

    def _create_chunk(content: str, finish_reason: Optional[str] = None) -> str:
        choice_delta = ChoiceDelta(content=content)
        choice = Choice(delta=choice_delta, finish_reason=finish_reason)
        chunk = ChatCompletionChunk(created=int(time.time()), model=model_id, choices=[choice])
        return chunk.model_dump_json(exclude_unset=True)

    def _extract_text_content(payload) -> str:
        if isinstance(payload, list):
            return "".join(_extract_text_content(item) for item in payload)
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ""

        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "text":
            for key in ("msg", "text", "content", "value", "output_text"):
                value = payload.get(key)
                if isinstance(value, (str, int, float)):
                    return str(value)
                if isinstance(value, (dict, list)):
                    text = _extract_text_content(value)
                    if text:
                        return text
            return ""

        content = payload.get("content")
        if isinstance(content, (str, dict, list)):
            return _extract_text_content(content)
        return ""

    def _normalize_stream_content(previous_text: str, content: str) -> tuple[str, str]:
        if not content:
            return previous_text, ""
        if not previous_text:
            return content, content
        if content == previous_text:
            return previous_text, ""
        if content.startswith(previous_text):
            return content, content[len(previous_text) :]
        if previous_text.endswith(content):
            return previous_text, ""
        if len(content) >= 4 and previous_text.startswith(content):
            return previous_text, ""

        max_overlap = min(len(previous_text), len(content))
        for overlap in range(max_overlap, 0, -1):
            if previous_text.endswith(content[:overlap]):
                delta = content[overlap:]
                return previous_text + delta, delta
        return previous_text + content, content

    finish_reason = "stop"
    accumulated_text = ""
    saw_chunk = False
    sent_terminal = False
    pending_raw_data = ""
    current_event_name = ""
    current_event_data: list[str] = []

    def _is_control_payload(payload: str) -> bool:
        return payload.startswith("[plugin:") or payload.startswith("[MSGINDEX:") or payload.startswith("[TRACEID:")

    async def _emit_terminal() -> AsyncGenerator[str, None]:
        nonlocal sent_terminal
        if sent_terminal:
            return
        sent_terminal = True
        yield _create_chunk("", finish_reason)
        yield "[DONE]"

    async def _consume_payload(
        payload_text: str,
        event_name: str = "",
        allow_incomplete_json: bool = False,
    ) -> AsyncGenerator[str, None]:
        nonlocal accumulated_text, finish_reason, pending_raw_data, saw_chunk

        payload = str(payload_text or "").strip()
        if not payload:
            pending_raw_data = ""
            return

        if payload == "[DONE]":
            pending_raw_data = ""
            async for item in _emit_terminal():
                yield item
            return

        normalized_event = str(event_name or "").strip().lower()
        if normalized_event == "speech_type" or _is_control_payload(payload):
            pending_raw_data = ""
            saw_chunk = True
            return

        should_try_json = payload.startswith("{") or (payload.startswith("[") and not _is_control_payload(payload))
        if should_try_json:
            try:
                chunk_data = json.loads(payload)
            except json.JSONDecodeError:
                if allow_incomplete_json:
                    pending_raw_data = payload
                else:
                    pending_raw_data = ""
                return

            pending_raw_data = ""
            saw_chunk = True
            if isinstance(chunk_data, dict) and chunk_data.get("stopReason"):
                finish_reason = chunk_data["stopReason"]
            content = _extract_text_content(chunk_data)
            if content:
                accumulated_text, delta = _normalize_stream_content(accumulated_text, content)
                if delta:
                    yield _create_chunk(delta)
            return

        pending_raw_data = ""
        saw_chunk = True
        accumulated_text, delta = _normalize_stream_content(accumulated_text, payload)
        if delta:
            yield _create_chunk(delta)

    async def _flush_event_payload() -> AsyncGenerator[str, None]:
        nonlocal current_event_name, current_event_data
        if current_event_name or current_event_data:
            payload = "\n".join(current_event_data)
            event_name = current_event_name
            current_event_name = ""
            current_event_data = []
            async for item in _consume_payload(payload, event_name=event_name, allow_incomplete_json=False):
                yield item

    async for line in response.aiter_lines():
        text = str(line or "")
        stripped = text.strip()

        if not stripped:
            async for item in _flush_event_payload():
                yield item
            continue

        if stripped.startswith(":"):
            continue

        if stripped.startswith("event:"):
            async for item in _flush_event_payload():
                yield item
            if sent_terminal:
                break
            if pending_raw_data:
                async for item in _consume_payload(pending_raw_data, allow_incomplete_json=False):
                    yield item
                if sent_terminal:
                    break
            if sent_terminal:
                break
            current_event_name = stripped[6:].strip()
            continue

        if stripped.startswith("data:"):
            if pending_raw_data:
                async for item in _consume_payload(pending_raw_data, allow_incomplete_json=False):
                    yield item
                if sent_terminal:
                    break
            if sent_terminal:
                break
            current_event_data.append(stripped[5:].lstrip())
            continue

        if current_event_name or current_event_data:
            current_event_data.append(stripped)
            continue

        candidate = f"{pending_raw_data}\n{stripped}" if pending_raw_data else stripped
        async for item in _consume_payload(candidate, allow_incomplete_json=True):
            yield item
        if sent_terminal:
            break

    if not sent_terminal:
        async for item in _flush_event_payload():
            yield item

    if pending_raw_data and not sent_terminal:
        async for item in _consume_payload(pending_raw_data, allow_incomplete_json=False):
            yield item

    if saw_chunk and not sent_terminal:
        async for item in _emit_terminal():
            yield item
