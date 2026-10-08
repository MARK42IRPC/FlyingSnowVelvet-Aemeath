"""办公聊天气泡的 Markdown -> 富文本转换（后端中立）。

`OfficeConversationView` 把每条消息渲染成 `QLabel` 的富文本；**怎么把 Markdown 变成
那段 HTML** 与 Qt 无关——它只做正则替换与 `html.escape`，产物是字符串。这段逻辑原先
和控件一起住在 `lib/script/ui/office_chat_view.py`，于是「一行正则表」也拖着一份
`PyQt5`。现在两者分开：本模块只依赖标准库。

颜色不是这里决定的：调用方（产品面）按工作台主题取出代码底色 / 前景色再传进来，
所以本模块不 import `lib.script`，也不 import `config`。
"""

from __future__ import annotations

import re
from html import escape

_CODE_FONT = "Consolas"

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+.-]*\s*$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")


def _code_span(text: str, bg: str, fg: str) -> str:
    return (
        f"<span style=\"font-family:'{_CODE_FONT}',monospace;"
        f"background-color:{bg}; color:{fg};\">{text}</span>"
    )


def _fence_html(lines: list[str], bg: str, fg: str) -> str:
    body = "\n".join(escape(line) for line in lines)
    return (
        f"<pre style=\"font-family:'{_CODE_FONT}',monospace;"
        f"background-color:{bg}; color:{fg}; padding:6px;"
        f"border-radius:4px;\">{body}</pre>"
    )


def _inline_md(text: str, bg: str, fg: str) -> str:
    escaped = escape(text)
    escaped = _INLINE_CODE_RE.sub(lambda m: _code_span(m.group(1), bg, fg), escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_RE.sub(r"<i>\1</i>", escaped)
    return escaped


def md_to_rich(text: str, bg: str, fg: str) -> str:
    if not text:
        return ""
    blocks: list[str] = []
    fence: list[str] | None = None
    list_items: list[str] | None = None
    paragraph: list[str] | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            content = "<br/>".join(_inline_md(p, bg, fg) for p in paragraph)
            blocks.append(f"<p>{content}</p>")
            paragraph = None

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items = None

    for raw in str(text).split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                blocks.append(_fence_html(fence, bg, fg))
                fence = None
            else:
                fence.append(line)
            continue
        if _FENCE_RE.match(stripped):
            flush_paragraph()
            flush_list()
            fence = []
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            flush_list()
            heading = _inline_md(stripped.lstrip("#").strip(), bg, fg)
            blocks.append(f"<b style='font-size:115%'>{heading}</b>")
            continue
        bullet = None
        bullet_match = _BULLET_RE.match(stripped)
        if bullet_match is not None:
            bullet = bullet_match.group(1).strip()
        else:
            number_match = _NUMBER_RE.match(stripped)
            if number_match is not None:
                bullet = number_match.group(1).strip()
        if bullet is not None:
            flush_paragraph()
            if list_items is None:
                list_items = []
            list_items.append(_inline_md(bullet, bg, fg))
            continue
        flush_list()
        if paragraph is None:
            paragraph = []
        paragraph.append(line.strip())

    if fence is not None:
        blocks.append(_fence_html(fence, bg, fg))
    flush_list()
    flush_paragraph()
    return "".join(blocks)


__all__ = [
    "md_to_rich",
]
