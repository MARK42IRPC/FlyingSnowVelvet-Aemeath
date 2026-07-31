"""Native function-calling contract shared by chat providers and tool dispatch."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


NATIVE_TOOL_SYSTEM_NOTE = (
    "当前请求提供原生函数工具。需要执行工具时必须直接调用对应函数，"
    "不要在正文中输出任何 ###指令###；不需要工具时只返回正常正文。"
)


def _function_tool(name: str, description: str, properties: dict | None = None,
                   required: tuple[str, ...] = ()) -> dict:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = list(required)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


_COUNT_PROPERTY = {
    "count": {
        "type": "integer",
        "minimum": 1,
        "maximum": 20,
        "description": "生成数量，未指定时为 1。",
    }
}

_NATIVE_TOOL_DEFINITIONS = (
    _function_tool(
        "play_music",
        "仅当用户明确要求播放音乐时调用。按歌名搜索并播放；未指定歌名时可省略 query。",
        {"query": {"type": "string", "description": "用户要求播放的歌名。"}},
    ),
    _function_tool("next_track", "仅当用户明确要求下一曲时调用。"),
    _function_tool("toggle_play_pause", "仅当用户明确要求播放或暂停切换时调用。"),
    _function_tool(
        "recall_memory",
        "当用户询问过去说过的内容时调用。可按主题或起止时间回忆。",
        {
            "topic": {"type": "string", "description": "可选的记忆主题。"},
            "start_time": {"type": "string", "description": "可选开始时间，如 2026-07-31 10:00:00。"},
            "end_time": {"type": "string", "description": "可选结束时间，如 2026-07-31 11:00:00。"},
        },
    ),
    _function_tool("spawn_snow_leopard", "仅当用户明确要求生成雪豹时调用。", _COUNT_PROPERTY),
    _function_tool("spawn_sofa", "仅当用户明确要求生成沙发时调用。", _COUNT_PROPERTY),
    _function_tool("spawn_motorcycle", "仅当用户明确要求生成摩托时调用。", _COUNT_PROPERTY),
    _function_tool(
        "start_timer",
        "仅当用户明确要求倒计时或闹钟时调用。",
        {
            "seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 359999,
                "description": "倒计时总秒数，未指定时为 30。",
            }
        },
    ),
    _function_tool(
        "set_volume",
        "仅当用户明确要求把音量设为某个值时调用。",
        {"percent": {"type": "number", "minimum": 0, "maximum": 100, "description": "目标音量百分比。"}},
        ("percent",),
    ),
    _function_tool(
        "change_volume",
        "仅当用户明确要求调高或调低音量时调用。",
        {"delta_percent": {"type": "number", "minimum": -100, "maximum": 100, "description": "音量百分比变化量。"}},
        ("delta_percent",),
    ),
    _function_tool(
        "teleport_pet",
        "仅当用户明确要求桌宠瞬移时调用。x/y 都在 0 到 1 之间，1 表示左或上，0 表示右或下。",
        {
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
        },
        ("x", "y"),
    ),
    _function_tool(
        "open_browser",
        "仅当用户明确要求打开网页、网址或链接时调用。只允许 HTTP(S) 地址。",
        {"url": {"type": "string", "description": "用户要求打开的网址。"}},
        ("url",),
    ),
    _function_tool("inspect_screen", "当用户明确要求查看当前屏幕或桌面内容时调用。"),
)

_SUPPORTED_NATIVE_TOOL_NAMES = frozenset(
    item["function"]["name"] for item in _NATIVE_TOOL_DEFINITIONS
)


def get_native_tool_definitions() -> list[dict]:
    """Return an isolated copy suitable for an OpenAI/Ollama request payload."""
    return deepcopy(list(_NATIVE_TOOL_DEFINITIONS))


def add_native_tool_instruction(messages: list[dict]) -> list[dict]:
    """Clone messages and add the native-tool rule without changing legacy payloads."""
    cloned = deepcopy(messages)
    for message in cloned:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = str(message.get("content") or "").rstrip()
        message["content"] = f"{content}\n\n{NATIVE_TOOL_SYSTEM_NOTE}" if content else NATIVE_TOOL_SYSTEM_NOTE
        return cloned

    for message in reversed(cloned):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{NATIVE_TOOL_SYSTEM_NOTE}\n\n{content}"
            return cloned
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("text", "input_text"):
                    text = str(block.get("text") or "")
                    block["text"] = f"{NATIVE_TOOL_SYSTEM_NOTE}\n\n{text}"
                    return cloned
    return cloned


def normalize_native_tool_call(raw: Any) -> dict | None:
    """Validate one provider tool call and normalize its JSON arguments."""
    if not isinstance(raw, dict):
        return None
    function = raw.get("function")
    source = function if isinstance(function, dict) else raw
    name = str(source.get("name") or raw.get("name") or "").strip()
    if name not in _SUPPORTED_NATIVE_TOOL_NAMES:
        return None

    arguments = source.get("arguments", raw.get("arguments", {}))
    if arguments in (None, ""):
        parsed_arguments: dict = {}
    elif isinstance(arguments, dict):
        parsed_arguments = dict(arguments)
    elif isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(decoded, dict):
            return None
        parsed_arguments = decoded
    else:
        return None
    return {"name": name, "arguments": parsed_arguments}


def _merge_fragment(previous: str, incoming: str) -> str:
    if not incoming:
        return previous
    if not previous:
        return incoming
    if incoming == previous or previous.endswith(incoming):
        return previous
    if incoming.startswith(previous):
        return incoming
    return previous + incoming


class NativeToolCallAccumulator:
    """Collect fragmented OpenAI or Ollama tool calls from a streamed response."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def _consume_calls(self, calls: Any) -> None:
        if not isinstance(calls, list):
            return
        for position, item in enumerate(calls):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index", position))
            except (TypeError, ValueError):
                index = position
            target = self._calls.setdefault(index, {"name": "", "arguments": ""})
            function = item.get("function")
            source = function if isinstance(function, dict) else item
            target["name"] = _merge_fragment(target["name"], str(source.get("name") or ""))
            arguments = source.get("arguments")
            if isinstance(arguments, dict):
                target["arguments"] = dict(arguments)
            elif arguments is not None and not isinstance(target["arguments"], dict):
                target["arguments"] = _merge_fragment(target["arguments"], str(arguments))

    def consume_openai_chunk(self, chunk: Any) -> None:
        if not isinstance(chunk, dict):
            return
        nested = chunk.get("data")
        if isinstance(nested, dict):
            self.consume_openai_chunk(nested)

        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            for container_name in ("delta", "message"):
                container = first.get(container_name)
                if isinstance(container, dict):
                    self._consume_calls(container.get("tool_calls"))

        output = chunk.get("output")
        if isinstance(output, dict):
            self.consume_openai_chunk(output)
        message = chunk.get("message")
        if isinstance(message, dict):
            self._consume_calls(message.get("tool_calls"))

    def consume_ollama_message(self, message: Any) -> None:
        if isinstance(message, dict):
            self._consume_calls(message.get("tool_calls"))

    def first(self) -> dict | None:
        for index in sorted(self._calls):
            candidate = normalize_native_tool_call(self._calls[index])
            if candidate is not None:
                return candidate
        return None


def _plain_string(value: Any) -> str:
    return str(value or "").strip()


def _number_string(value: Any, default: str) -> str:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return str(int(number)) if number.is_integer() else str(number)


def _required_number_string(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return str(int(number)) if number.is_integer() else str(number)


def native_tool_to_dispatch(raw: Any) -> tuple[str, str] | None:
    """Convert a validated native call to the existing dispatcher command contract."""
    call = normalize_native_tool_call(raw)
    if call is None:
        return None
    name = call["name"]
    arguments = call["arguments"]

    if name == "play_music":
        return "音乐", _plain_string(arguments.get("query"))
    if name == "next_track":
        return "下一曲", ""
    if name == "toggle_play_pause":
        return "暂停", ""
    if name == "recall_memory":
        topic = _plain_string(arguments.get("topic"))
        start = _plain_string(arguments.get("start_time"))
        end = _plain_string(arguments.get("end_time"))
        parts = [part for part in (start, "到" if start and end else "", end, topic) if part]
        return "回忆", " ".join(parts)
    if name == "spawn_snow_leopard":
        return "雪豹", _number_string(arguments.get("count"), "1")
    if name == "spawn_sofa":
        return "沙发", _number_string(arguments.get("count"), "1")
    if name == "spawn_motorcycle":
        return "摩托", _number_string(arguments.get("count"), "1")
    if name == "start_timer":
        return "计时", _number_string(arguments.get("seconds"), "30")
    if name == "set_volume":
        percent = _required_number_string(arguments.get("percent"))
        return ("音量", percent) if percent is not None else None
    if name == "change_volume":
        delta = _required_number_string(arguments.get("delta_percent"))
        if delta is None:
            return None
        if not delta.startswith(("+", "-")):
            delta = f"+{delta}"
        return "音量", delta
    if name == "teleport_pet":
        x = _required_number_string(arguments.get("x"))
        y = _required_number_string(arguments.get("y"))
        if x is None or y is None:
            return None
        return "瞬移", f"{x} {y}"
    if name == "open_browser":
        url = _plain_string(arguments.get("url"))
        return ("浏览器", url) if url else None
    if name == "inspect_screen":
        return "窥屏", ""
    return None


def default_native_tool_reply(raw: Any) -> str:
    call = normalize_native_tool_call(raw)
    if call is None:
        return ""
    return {
        "play_music": "///音乐///这就播放给你听。",
        "next_track": "///音乐///好，换下一首。",
        "toggle_play_pause": "///音乐///好，交给我。",
        "recall_memory": "///回忆///让我认真想想。",
        "spawn_snow_leopard": "///日常///这就召唤雪豹。",
        "spawn_sofa": "///日常///这就放好沙发。",
        "spawn_motorcycle": "///日常///摩托马上就到。",
        "start_timer": "///计时///倒计时交给我。",
        "set_volume": "///音乐///音量已经交给我。",
        "change_volume": "///音乐///好，我来调整音量。",
        "teleport_pet": "///日常///这就过去找你。",
        "open_browser": "///日常///这就为你打开。",
        "inspect_screen": "///日常///让我看看你在做什么。",
    }.get(call["name"], "")
