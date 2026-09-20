"""雪绒论坛正文支持的行内标记：解析、渲染片段与发帖框的输入辅助。

支持的写法与发帖框的四个复选按钮一一对应：

- `**粗体**`
- `*斜体*`
- `__下划线__`
- `~~删除线~~`

`***粗斜体***` 这个叠加写法只是 `**` 与 `*` 同时生效，没有单独的按钮。标记是纯文本，
原样存在服务端：别的客户端看到的是带标记的原文，只有雪绒论坛把它渲染成格式，所以这里不
做任何服务端清洗。解析只认成对的标记，没配对的标记按普通字符显示。

除了四种行内标记，正文里还能写「效果令牌」（`FORUM_EFFECT_TOKENS`，目前只有 `[雪豹]`）：
令牌不是格式而是指令，渲染时整段洗掉，由卡片底部另贴一张动图（令牌与贴图资源的对应关系在
`lib/script/ui/forum_sticker.py`，两边成对）。令牌同样原样存在服务端，这里只负责认出来并
洗掉，不做服务端清洗。

颜色令牌（``[color=#rrggbb]`` / ``[outline=#rrggbb]``）与段落排版令牌（``[size=NN]`` /
``[left]`` / ``[center]`` / ``[right]``）是另一套东西：它们由核心层的
`lib/core/forum_colors.py` 与 `lib/core/forum_layout.py` 认，因为发送前的本地过滤也要先把
令牌洗掉再判违规。这里只负责在渲染与数字数时把它们一起洗掉。

模块不导入任何 GUI 库：`to_html()` 给出正文用的富文本片段（Qt 富文本子集），`visible_text()`
供字号自适应按可见字数计算，`span_at_cursor()` / `toggle()` 供发帖框按钮在光标处加减标记。
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from lib.core.forum_colors import strip_color_tokens
from lib.core.forum_layout import strip_layout_tokens


@dataclass(frozen=True, slots=True)
class ForumMarkupFormat:
    """一种行内格式：按钮字符、成对标记与渲染标签都从这里取，避免几处各写一份。"""

    key: str
    label: str
    marker: str
    button: str
    tags: tuple[str, ...]


FORUM_MARKUP_FORMATS = (
    ForumMarkupFormat("bold", "粗体", "**", "B", ("b",)),
    ForumMarkupFormat("italic", "斜体", "*", "I", ("i",)),
    ForumMarkupFormat("underline", "下划线", "__", "U", ("u",)),
    ForumMarkupFormat("strike", "删除线", "~~", "S", ("s",)),
)
FORMAT_BY_KEY = {fmt.key: fmt for fmt in FORUM_MARKUP_FORMATS}

#: 正文里的「效果令牌」：不是格式而是一条指令——渲染时整段洗掉，由卡片底部另贴一张动图。
#: 令牌到贴图资源的对应关系长在 `lib/script/ui/forum_sticker.py`，新增令牌要同时补那边。
FORUM_EFFECT_TOKENS: tuple[str, ...] = ("[雪豹]",)

#: 解析顺序即标签优先级：`***` 必须排在 `**` 前、`**` 排在 `*` 前。
_TAG_GROUPS = (
    ("bold_italic", ("b", "i")),
    ("bold", ("b",)),
    ("underline", ("u",)),
    ("strike", ("s",)),
    ("italic", ("i",)),
)
_PATTERN = re.compile(
    r"\*\*\*(?P<bold_italic>.+?)\*\*\*"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|__(?P<underline>.+?)__"
    r"|~~(?P<strike>.+?)~~"
    r"|\*(?P<italic>.+?)\*",
    re.DOTALL,
)

_ESCAPE_TABLE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
#: 只有第二个空格起才需要 `&nbsp;`，否则富文本会把连续空格并成一个。
_SPACE_RUN = re.compile(r"  +")


def escape_text(text) -> str:
    """转义成富文本片段：`&`、`<`、`>` 转义，换行变 `<br>`，第二个空格起补 `&nbsp;`。

    这是 `to_html()` 的第一步，也是「没有标记的纯文字该长什么样」的尺子：帖子正文据此判断
    某一块要不要走富文本渲染（`lib/core/forum_markdown.py` 的 `ForumBlock.rich`）。
    """
    escaped = str(text or "").translate(_ESCAPE_TABLE)
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return _SPACE_RUN.sub(lambda match: " " + "&nbsp;" * (len(match.group(0)) - 1), escaped)


def to_html(text) -> str:
    """把正文转成 QLabel / QTextEdit 的富文本片段：先转义原文，再把成对标记换成标签，最后洗掉效果令牌。

    效果令牌放在标记渲染**之后**才洗：`**[雪豹]**` 先变成 `<b>[雪豹]</b>`、再洗成 `<b></b>`；
    反过来先洗就只剩一对没有内容的星号，卡片上会直接印出 `****`。
    """
    escaped = escape_text(text)
    return strip_layout_tokens(
        strip_color_tokens(strip_effect_tokens(_PATTERN.sub(_render_match, escaped)))
    )


def visible_text(text) -> str:
    """去掉成对标记、效果令牌与颜色 / 排版令牌后的可见文字；没配对的标记按普通字符保留。"""
    return strip_layout_tokens(
        strip_color_tokens(strip_effect_tokens(_PATTERN.sub(_strip_match, str(text or ""))))
    )


def effect_tokens(text) -> tuple[str, ...]:
    """正文里出现过的效果令牌，按首次出现顺序去重。"""
    raw = str(text or "")
    found = sorted(
        (raw.index(token), token)
        for token in FORUM_EFFECT_TOKENS
        if token and token in raw
    )
    return tuple(token for _spot, token in found)


def strip_effect_tokens(text) -> str:
    """洗掉正文里的效果令牌：渲染出来的文字里不留令牌，贴图另走卡片底部。"""
    result = str(text or "")
    for token in FORUM_EFFECT_TOKENS:
        if token:
            result = result.replace(token, "")
    return result


def marker_positions(text, marker: str) -> tuple[int, ...]:
    """标记可能的起点位置。

    `*` 只认奇数长度星号串里贴着文字的那一颗，所以 `**粗体**` 不会被算成一对斜体；
    `**` / `__` / `~~` 取每一处出现位置，`****` 这种还没输入文字的空标记对也能认出来。
    """
    text = str(text or "")
    if marker != "*":
        return tuple(spot for spot in range(len(text)) if text.startswith(marker, spot))
    positions: list[int] = []
    for start, end in _runs(text, "*"):
        if (end - start + 1) % 2 == 1:
            positions.extend((start, end))
    return tuple(sorted(set(positions)))


def span_at_cursor(text, caret: int, marker: str) -> tuple[int, int] | None:
    """光标所在的标记对 `(开标记位置, 闭标记位置)`；光标不在任何一对里时返回 None。"""
    text = str(text or "")
    carets = max(0, min(int(caret), len(text)))
    size = len(marker)
    positions = marker_positions(text, marker)
    opening = max((spot for spot in positions if spot + size <= carets), default=None)
    if opening is None:
        return None
    closing = next(
        (spot for spot in positions if spot >= carets and spot >= opening + size), None
    )
    if closing is None:
        return None
    return opening, closing


def toggle(text, start: int, end: int, marker: str) -> tuple[str, int, int]:
    """在选区或光标处加减一对标记，返回 `(新正文, 新选区起点, 新选区终点)`。

    选区/光标已经在这对标记里就去掉它，否则加上；光标处加上后光标停在标记中间，接着打的字
    自动落在标记里——这就是发帖框复选按钮的行为。
    """
    text = str(text or "")
    start, end = sorted(
        (max(0, min(int(start), len(text))), max(0, min(int(end), len(text))))
    )
    size = len(marker)
    span = span_at_cursor(text, start, marker)
    if span is not None and end <= span[1]:
        opening, closing = span
        trimmed = text[:opening] + text[opening + size:closing] + text[closing + size:]
        return trimmed, start - size, end - size
    selected = text[start:end]
    if end - start >= 2 * size and selected.startswith(marker) and selected.endswith(marker):
        inner = selected[size:-size]
        return text[:start] + inner + text[end:], start, start + len(inner)
    wrapped = text[:start] + marker + selected + marker + text[end:]
    return wrapped, start + size, end + size


def _runs(text: str, char: str) -> list[tuple[int, int]]:
    """连续同一个字符的区间 `(起点, 终点)`，用来判断标记是不是成对。"""
    runs: list[tuple[int, int]] = []
    if not char:
        return runs
    index = 0
    while True:
        start = text.find(char, index)
        if start < 0:
            return runs
        end = start
        while end + 1 < len(text) and text[end + 1] == char:
            end += 1
        runs.append((start, end))
        index = end + 1


def _render_match(match: re.Match) -> str:
    for group, tags in _TAG_GROUPS:
        inner = match.group(group)
        if inner is None:
            continue
        body = _PATTERN.sub(_render_match, inner)
        for tag in reversed(tags):
            body = f"<{tag}>{body}</{tag}>"
        return body
    return match.group(0)


def _strip_match(match: re.Match) -> str:
    for group, _tags in _TAG_GROUPS:
        inner = match.group(group)
        if inner is not None:
            return _PATTERN.sub(_strip_match, inner)
    return match.group(0)


__all__ = [
    "FORUM_EFFECT_TOKENS",
    "FORUM_MARKUP_FORMATS",
    "FORMAT_BY_KEY",
    "ForumMarkupFormat",
    "effect_tokens",
    "escape_text",
    "marker_positions",
    "span_at_cursor",
    "strip_effect_tokens",
    "to_html",
    "toggle",
    "visible_text",
]
