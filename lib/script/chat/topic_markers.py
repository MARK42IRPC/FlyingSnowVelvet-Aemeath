"""模型回复里的主题标记解析。

人格词要求回复以 ``[主题]正文`` 开头（``resc/persona.txt``），气泡与记忆共用同一套规则：
开头的方括号（半角 ``[]`` 或全角 ``【】``）算主题标记，正文从右括号之后开始。旧版人格词
教的是 ``///主题///``，运行期仍按同一种标记剥掉，避免老用户的气泡里漏出标记本身。
"""

from __future__ import annotations

import re

TOPIC_MARKER_PATTERN = re.compile(
    r'^\s*(?:\[([^\[\]\r\n]{1,16})\]|【([^【】\r\n]{1,16})】|///\s*([^/#\r\n]{1,16}?)\s*(?:///|//|###))\s*'
)


def split_topic_marker(text: object) -> tuple[str, str]:
    """拆出开头的主题标记，返回 ``(主题, 正文)``；没有标记时主题是空串。"""
    source = str(text or '')
    match = TOPIC_MARKER_PATTERN.match(source)
    if not match:
        return '', source
    topic = next((group.strip() for group in match.groups() if group), '')
    return topic, source[match.end():]


__all__ = ['TOPIC_MARKER_PATTERN', 'split_topic_marker']
