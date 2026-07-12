"""ChatHandler ???????????"""

import os
from datetime import datetime

from config.config import BUBBLE_CONFIG
from lib.core.logger import get_logger

from .handler_stream_presenter import _strip_tool_commands_for_display

logger = get_logger(__name__)

DEFAULT_PERSONA_FILE = BUBBLE_CONFIG.get('default_persona_file', 'resc/persona.txt')
RECENT_CONTEXT_MESSAGES = 12
DEFAULT_MEMORY_CONTEXT_LIMIT = 12


class ChatHandlerPersonaMixin:
    @staticmethod
    def _memory_context_limit() -> int:
        try:
            import config.ollama_config as oc

            raw_limit = oc.OLLAMA.get("memory_context_limit", DEFAULT_MEMORY_CONTEXT_LIMIT)
            limit = int(raw_limit)
        except Exception:
            limit = DEFAULT_MEMORY_CONTEXT_LIMIT
        return max(0, min(48, limit))

    def _build_recent_memory_block(self) -> str:
        limit = self._memory_context_limit()
        if limit <= 0:
            return ""

        try:
            from .memory import get_stream_memory

            entries = get_stream_memory().get_recent_entries(limit)
        except Exception as e:
            logger.debug("[ChatHandler] 读取 recent memory 失败: %s", e)
            return ""

        lines: list[str] = []
        for item in entries:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            topic = str(item.get("topic") or "").strip()
            role = str(item.get("role") or "").strip().lower()
            role_label = "用户" if role == "user" else "你" if role in ("you", "assistant") else "记录"
            prefix = f"[{topic}][{role_label}]" if topic else f"[{role_label}]"
            lines.append(f"- {prefix} {content}")

        if not lines:
            return ""

        return (
            "[默认记忆]\n"
            "以下是记忆系统最近记录的内容，仅作为背景上下文参考，不要机械逐条复述：\n"
            + "\n".join(lines)
        )

    def _load_persona(self) -> str:
        """
        仅从人格文件加载 system prompt，不注入任何硬编码人设文案。
        """
        from config.ollama_config import PERSONA_FILE
        from config.config import CHAT

        # 优先使用 ollama_config.PERSONA_FILE；为空时兼容旧配置 CHAT['persona_file']。
        persona_file = (PERSONA_FILE or "").strip()
        if not persona_file:
            persona_file = CHAT.get("persona_file", "").strip() or DEFAULT_PERSONA_FILE

        if os.path.isfile(persona_file):
            try:
                # utf-8-sig 自动剔除 Windows BOM（\ufeff），避免发送给 Ollama 时触发 JSON 解析错误
                with open(persona_file, "r", encoding="utf-8-sig") as handle:
                    content = handle.read().strip()
                if content:
                    logger.info("[ChatHandler] 已加载人格文件: %s", persona_file)
                    return content
                logger.error("[ChatHandler] 人格文件为空: %s", persona_file)
            except Exception as e:
                logger.error("[ChatHandler] 读取人格文件失败: %s", e)
                return ""
        else:
            logger.error("[ChatHandler] 人格文件不存在: %s", persona_file)

        return ""

    def _build_runtime_persona(self, *, skip_memory_block: bool = False) -> str:
        """
        生成本次请求使用的人格词，并附带当前时间（精确到秒）。
        """
        now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        time_suffix = f'当前时间：{now_text}'
        base = (self._persona or '').strip()
        memory_block = "" if skip_memory_block else self._build_recent_memory_block()

        sections: list[str] = []
        if base:
            sections.append(base)
        sections.append(f'[系统时间]\n{time_suffix}')
        if memory_block:
            sections.append(memory_block)
            logger.debug("[ChatHandler] 已附带 %s 条 recent memory 到 persona", self._memory_context_limit())
        return '\n\n'.join(section for section in sections if section)

    def _append_recent_context(self, role: str, text: str):
        normalized_role = str(role or '').strip().lower()
        if normalized_role not in ('user', 'assistant'):
            return
        cleaned = _strip_tool_commands_for_display(str(text or '')) if normalized_role == 'assistant' else str(text or '').strip()
        if not cleaned:
            return
        self._recent_context.append({
            'role': normalized_role,
            'content': cleaned,
        })

    def _get_recent_context_snapshot(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._recent_context if item.get('content')]

