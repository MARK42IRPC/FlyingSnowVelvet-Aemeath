"""帖子正文的段落级排版令牌：``[size=NN]`` 字号、``[left]`` / ``[center]`` / ``[right]`` 对齐。

行内标记（``**粗体**`` 一类）与颜色令牌都只作用于一串字符，而字号和对齐是**段落**属性：
一行字整体多大、靠哪边站。所以这里换一套写法——令牌写在段落里，作用范围是整个段落
（在帖子里就是被空行分开的那一段），令牌本身不进正文。

令牌和颜色令牌一样只是纯文本，服务端不认这种写法：别的客户端看到的是带令牌的原文，
只有雪绒论坛把它解释成排版。四处必须认同一份语法，所以放在核心层：

- 发帖页用 ``set_layout_tokens()`` 给选区覆盖的每个段落设置字号 / 对齐；
- 正文解析用 ``parse_layout_tokens()`` 把一段的首个字号与首个对齐读出来，并把令牌洗掉；
- 发送前的本地过滤先 ``strip_layout_tokens()`` 再判违规——不然 ``[size=...]`` 里的数字
  会进长数字规则的判定（与颜色令牌同理）；
- 渲染方按读出来的字号与对齐排版。

一条段落里同一种令牌出现多次时取**第一个**：多写的那几个只是被洗掉，不参与判定，所以
「手抖多打一个 ``[center]``」不会让排版跳来跳去。认不出来的字号（超出 ``FORUM_SIZE_MIN``
~ ``FORUM_SIZE_MAX``、或不是整数）按「没写」处理，整条令牌洗掉但排版不变。

**段落边界**：渲染侧原本只在空行处分段（连续行按 Markdown 软换行并成一段），而发帖框是
按**行**写令牌的——光标在哪一行，令牌就写在哪一行的开头。两边对不上时，第 2 行起的令牌会被
当成同段里的「重复令牌」洗掉、几行还会并成一行显示。``starts_layout_paragraph()`` 正是给
渲染方判这一行的：带令牌的行（以及它后面紧跟的那一行）另起一段，两边的段落定义就此对齐。

模块不导入任何 GUI 库。
"""

from __future__ import annotations

import re

#: 允许的字号范围（像素）。太小看不见，太大一行放不下几个字。
FORUM_SIZE_MIN = 10
FORUM_SIZE_MAX = 48

#: 三种段落对齐；``left`` 是默认值，写出来只为了把上一段的对齐改回来。
FORUM_ALIGN_LEFT = "left"
FORUM_ALIGN_CENTER = "center"
FORUM_ALIGN_RIGHT = "right"
FORUM_ALIGNMENTS: tuple[str, ...] = (FORUM_ALIGN_LEFT, FORUM_ALIGN_CENTER, FORUM_ALIGN_RIGHT)

#: 段落级排版令牌的写法：``[size=20]``、``[center]`` / ``[left]`` / ``[right]``（容忍大小写与空格）。
FORUM_LAYOUT_TOKEN_RE = re.compile(
    r"\[\s*(?:size\s*=\s*(\d{1,3})|(left|center|right))\s*\]",
    re.IGNORECASE,
)


def strip_layout_tokens(text) -> str:
    """洗掉正文里的排版令牌：文字里不留令牌，字号与对齐另走 `parse_layout_tokens()`。

    写法认得出来就整条洗掉——包括 ``[size=999]`` 这种超范围的字号：它反正是要当「没写」
    处理的，留着只会在正文里印出一串方括号，还会被违规规则里的长数字判定误伤。
    """
    return FORUM_LAYOUT_TOKEN_RE.sub("", str(text or ""))


def parse_layout_tokens(text) -> tuple[str, int, str]:
    """读出一段正文的字号与对齐，返回 `(洗掉令牌的正文, 字号, 对齐)`。

    字号为 0、对齐为空串表示「这一段没写」，渲染方据此沿用块自己的默认值（标题的字号、
    正文的左对齐）。同一种令牌取第一个；认不出来的字号按没写处理。
    """
    raw = str(text or "")
    size = 0
    align = ""
    for match in FORUM_LAYOUT_TOKEN_RE.finditer(raw):
        digits, name = match.group(1), match.group(2)
        if digits is not None:
            if not size:
                size = _clamp_size(digits)
            continue
        if not align:
            align = str(name or "").lower()
    return strip_layout_tokens(raw), size, align


def starts_layout_paragraph(text) -> bool:
    """这一行是不是以排版令牌开头（渲染方据此让它另起一段）。

    渲染侧的段落边界是空行——连续行按 Markdown 软换行并成一段；发帖框却按**行**写令牌。
    不在这里断开的话，`[center]甲\n[center]乙` 会被当成「一段里写了两个对齐令牌」，第二个
    只是被洗掉，两行还会并成一行显示。前导空白不算数（正文行本来就先 `strip()`）。
    """
    return FORUM_LAYOUT_TOKEN_RE.match(str(text or "").lstrip()) is not None


def build_layout_tokens(*, size: int = 0, align: str = "") -> str:
    """把字号与对齐拼成令牌串（对齐在前、字号在后，顺序固定）。"""
    parts: list[str] = []
    if align in FORUM_ALIGNMENTS:
        parts.append(f"[{align}]")
    value = _clamp_size(size)
    if value:
        parts.append(f"[size={value}]")
    return "".join(parts)


def paragraph_spans(text, start: int, end: int) -> tuple[tuple[int, int], ...]:
    """选区覆盖到的段落 `(段落起点, 段落终点)`；终点不含换行符本身。

    选区从段落中间开始、或停在段落中间时，整个段落都算被覆盖——排版是段落属性，
    没有「只改半行」这回事。
    """
    body = str(text or "")
    size = len(body)
    start = max(0, min(int(start), size))
    end = max(start, min(int(end), size))
    cursor = body.rfind("\n", 0, start) + 1
    spans: list[tuple[int, int]] = []
    while True:
        break_at = body.find("\n", cursor)
        stop = size if break_at < 0 else break_at
        spans.append((cursor, stop))
        if stop >= end or break_at < 0:
            break
        cursor = break_at + 1
    return tuple(spans)


def set_layout_tokens(
    text, start: int, end: int, *, size: int | None = None, align: str | None = None
) -> tuple[str, int, int]:
    """把选区覆盖的每个段落设成指定的字号 / 对齐，返回 `(新正文, 新选区起点, 新选区终点)`。

    两个参数各自独立：``None`` 表示这一次不碰它，``0`` / 空串表示「把这一段这一项去掉」。
    每一段都是先把旧的同类令牌摘掉、再按需要写上新的，所以换字号是重刷而不是叠加，
    点几次都不会越点越长。

    返回的选区覆盖被改动的**整个段落**（而不是原来的那一小截）：排版是段落属性，
    按钮点完把段落选起来，接着再点别的排版按钮才不会只改到半行。
    """
    body = str(text or "")
    spans = paragraph_spans(body, start, end)
    if not spans:
        return body, start, end
    wanted_size = None if size is None else _clamp_size(size)
    wanted_align = None if align is None else (align if align in FORUM_ALIGNMENTS else "")
    # 一段一段重铺：段落之间的原文（含换行）原样搬过去，每段只换掉开头的排版令牌。
    pieces: list[str] = []
    written = 0
    cursor = 0
    new_start = -1
    new_stop = 0
    for span_start, span_stop in spans:
        current_size, current_align = _read_span(body, span_start, span_stop)
        next_size = current_size if wanted_size is None else wanted_size
        next_align = current_align if wanted_align is None else wanted_align
        tokens = build_layout_tokens(size=next_size, align=next_align)
        kept = strip_layout_tokens(body[span_start:span_stop])
        head = body[cursor:span_start]
        pieces.append(head)
        written += len(head)
        pieces.append(tokens)
        written += len(tokens)
        if new_start < 0:
            new_start = written
        pieces.append(kept)
        written += len(kept)
        new_stop = written
        cursor = span_stop
    pieces.append(body[cursor:])
    result = "".join(pieces)
    if new_start < 0:
        new_start = 0
    limit = len(result)
    return result, min(new_start, limit), min(max(new_stop, new_start), limit)


def _read_span(body: str, span_start: int, span_stop: int) -> tuple[int, str]:
    """一段正文当前的字号与对齐（只认这一段里的首个同类令牌）。"""
    _clean, size, align = parse_layout_tokens(body[span_start:span_stop])
    return size, align


def _clamp_size(value) -> int:
    """字号夹进允许范围；不是整数（或超出范围）时返回 0，表示「没写」。"""
    try:
        size = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    if size < FORUM_SIZE_MIN or size > FORUM_SIZE_MAX:
        return 0
    return size


__all__ = [
    "FORUM_ALIGNMENTS",
    "FORUM_ALIGN_CENTER",
    "FORUM_ALIGN_LEFT",
    "FORUM_ALIGN_RIGHT",
    "FORUM_LAYOUT_TOKEN_RE",
    "FORUM_SIZE_MAX",
    "FORUM_SIZE_MIN",
    "build_layout_tokens",
    "paragraph_spans",
    "parse_layout_tokens",
    "set_layout_tokens",
    "starts_layout_paragraph",
    "strip_layout_tokens",
]
