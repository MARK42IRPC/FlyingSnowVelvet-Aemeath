"""自动陪伴内容的来源与记忆主题标记。

自动陪伴不是用户发起的对话：如果把固定提问和回复原样回灌进会话历史或人格记忆，
模型会不断模仿上一轮，最终变成复读机。自动观察仍然写入记忆以便按主题回忆，但使用
独立主题，并在默认人格记忆、会话历史和默认回忆中过滤掉。
"""

from __future__ import annotations

AUTO_COMPANION_SOURCE = "auto_companion"
AUTO_COMPANION_TOPIC = "自动观察"

_TOPIC_MARKER = f"///{AUTO_COMPANION_TOPIC}///"


def tag_auto_companion_text(text: object) -> str:
    """给自动陪伴文本加上记忆主题标记。"""
    return f"{_TOPIC_MARKER}{str(text or '').strip()}"


def is_auto_companion_topic(topic: object) -> bool:
    return str(topic or '').strip() == AUTO_COMPANION_TOPIC


__all__ = [
    'AUTO_COMPANION_SOURCE',
    'AUTO_COMPANION_TOPIC',
    'is_auto_companion_topic',
    'tag_auto_companion_text',
]
