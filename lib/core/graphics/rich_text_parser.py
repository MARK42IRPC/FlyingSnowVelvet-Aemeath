"""Rich text parser for bubble messages with Markdown and LaTeX support."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# 文本样式类型
TextStyle = Literal["normal", "bold", "italic", "code", "bold_italic"]


@dataclass(frozen=True, slots=True)
class TextSegment:
    """单个文本片段，包含样式信息"""
    text: str
    style: TextStyle = "normal"
    scale: float = 1.0  # 文本缩放比例
    color: tuple[int, int, int] | None = None  # RGB颜色，None表示使用默认颜色


def parse_markdown_inline(text: str) -> list[TextSegment]:
    """
    解析行内Markdown格式：
    - **粗体**
    - *斜体*
    - ***粗斜体***
    - `代码`
    - \\scale{1.5}{放大文本}
    """
    segments: list[TextSegment] = []
    pos = 0
    text = str(text or "")

    # 正则模式：按优先级匹配
    patterns = [
        # 粗斜体 (必须在粗体和斜体之前)
        (r'\*\*\*(.+?)\*\*\*', "bold_italic"),
        # 粗体
        (r'\*\*(.+?)\*\*', "bold"),
        # 斜体
        (r'\*(.+?)\*', "italic"),
        # 代码
        (r'`(.+?)`', "code"),
        # 缩放文本 \scale{1.5}{文本}
        (r'\\scale\{([\d.]+)\}\{(.+?)\}', "scale"),
    ]

    while pos < len(text):
        # 尝试匹配所有模式
        earliest_match = None
        earliest_pos = len(text)
        matched_pattern = None

        for pattern, style_type in patterns:
            match = re.search(pattern, text[pos:])
            if match and match.start() < earliest_pos:
                earliest_match = match
                earliest_pos = match.start()
                matched_pattern = style_type

        if earliest_match is None:
            # 没有找到任何格式，剩余文本作为普通文本
            if pos < len(text):
                segments.append(TextSegment(text[pos:], "normal"))
            break

        # 添加格式之前的普通文本
        if earliest_pos > 0:
            segments.append(TextSegment(text[pos:pos + earliest_pos], "normal"))

        # 处理匹配的格式
        if matched_pattern == "scale":
            scale_factor = float(earliest_match.group(1))
            content = earliest_match.group(2)
            segments.append(TextSegment(content, "normal", scale=scale_factor))
        else:
            content = earliest_match.group(1)
            segments.append(TextSegment(content, matched_pattern))

        # 移动位置
        pos += earliest_pos + len(earliest_match.group(0))

    return segments if segments else [TextSegment("", "normal")]


def parse_rich_text(text: str) -> list[list[TextSegment]]:
    """
    解析整个文本，返回按行分组的文本段列表
    每行都经过Markdown解析
    """
    lines = text.split('\n')
    return [parse_markdown_inline(line) for line in lines]


def segments_to_plain_text(segments: list[TextSegment]) -> str:
    """将文本段列表转换为纯文本（用于测试）"""
    return ''.join(seg.text for seg in segments)
