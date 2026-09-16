"""留言与帖子正文里的颜色令牌：``[color=#rrggbb]`` 声明正文色、``[outline=#rrggbb]`` 声明描边色。

令牌和四种行内标记一样只是纯文本：服务端不认这种写法，别的客户端看到的是带令牌的原文，
只有雪绒论坛把它解释成颜色。四处必须认同一份语法，所以放在核心层：

- 留言墙发送时用 ``build_color_tokens()`` 把令牌拼在正文最前面（整张卡片一个色）；
- 发帖页用 ``apply_color_tokens()`` 把选中的一段包起来，一篇帖子因此可以有几段**不同的**颜色；
- 发送前的本地过滤先 ``strip_color_tokens()`` 再判违规——不然 ``[color=#000000]`` 里的六位数字
  会被当成手机号拦下来，颜色越"规整"越容易踩到；
- 渲染时 ``text_colors()``（整段一个色，留言墙用）或 ``color_runs()``（按段切，帖子详情页用）
  读出颜色，正文里的令牌整段洗掉。

令牌从它出现的位置起生效，直到同类的下一个令牌；``[/color]`` / ``[/outline]`` 是结束令牌，
从它往后回到默认色。留言墙只在开头拼一对令牌，所以「整段一个色」与「按段切」在那边没有区别。

模块不导入任何 GUI 库。
"""

from __future__ import annotations

from dataclasses import dataclass
import re

#: 颜色令牌的写法：开头 ``[color=#rrggbb]`` / ``[outline=#rrggbb]``（容忍大小写与空格），
#: 结束 ``[/color]`` / ``[/outline]``（回到默认色）。
FORUM_COLOR_TOKEN_RE = re.compile(
    r"\[(color|outline)\s*=\s*#([0-9a-fA-F]{6})\]|\[/(color|outline)\]",
    re.IGNORECASE,
)
#: 规范颜色：``#`` 加六位十六进制。
_COLOR_RE = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True, slots=True)
class ForumTextRun:
    """一段同色的正文；两个颜色都是空串时，这一段用主题默认色。"""

    text: str = ""
    color: str = ""
    outline: str = ""


def iter_color_tokens(text):
    """正文里的颜色令牌，按出现顺序给出 `(起点, 终点, 类型, 颜色)`。

    类型是 `"color"` / `"outline"`；颜色为空串表示这是一条**结束**令牌（`[/color]`），
    从它往后回到默认色。
    """
    for match in FORUM_COLOR_TOKEN_RE.finditer(str(text or "")):
        opening, value, closing = match.group(1), match.group(2), match.group(3)
        kind = str(opening or closing or "").lower()
        yield match.start(), match.end(), kind, (f"#{value.lower()}" if value else "")


def strip_color_tokens(text) -> str:
    """洗掉正文里的颜色令牌：文字里不留令牌，颜色另走 `text_colors()` / `color_runs()`。"""
    return FORUM_COLOR_TOKEN_RE.sub("", str(text or ""))


def text_colors(text) -> tuple[str, str]:
    """（文字色, 描边色）：正文**开头**生效的那一对，留言墙拿它给整张卡片上色。

    缺一项就返回空串，调用方据此回退主题色。结束令牌不参与「开头是什么色」，
    所以 `[color=#ff0000]红[/color]` 仍然是一张红卡片。
    """
    color = ""
    outline = ""
    for _start, _end, kind, value in iter_color_tokens(text):
        if not value:
            continue
        if kind == "color" and not color:
            color = value
        elif kind == "outline" and not outline:
            outline = value
    return color, outline


def color_runs(text) -> tuple[ForumTextRun, ...]:
    """按令牌把正文切成若干段（令牌本身不进文字）。

    每段的颜色取「该段起点上生效」的那一对令牌；令牌之间的文字各成一段，空段不出现。
    没有令牌的正文只有一段，两个颜色都是空串——调用方据此走「不用着色」的快路径。
    """
    raw = str(text or "")
    runs: list[ForumTextRun] = []
    color = ""
    outline = ""
    cursor = 0
    for start, end, kind, value in iter_color_tokens(raw):
        if start > cursor:
            runs.append(ForumTextRun(raw[cursor:start], color, outline))
        if kind == "color":
            color = value
        else:
            outline = value
        cursor = end
    if cursor < len(raw):
        runs.append(ForumTextRun(raw[cursor:], color, outline))
    return tuple(runs)


def apply_color_tokens(
    text, start, end, *, color: str | None = None, outline: str | None = None
) -> tuple[str, int, int]:
    """把选区包进颜色令牌（发帖页的「上色」按钮）。

    两个颜色各自独立：**空串**表示「把这一段这一种颜色取消掉」（选区外面包着的那一对
    摘掉），**None** 表示这一次根本不碰它（不传就是 None）。本来是包着的那一对先摘掉、
    再按需要包上新的，所以「换色」是重刷而不是套娃，点几次都不会越点越长。
    返回 `(新正文, 新选区起点, 新选区终点)`；什么都没改时原样返回。空选区会把光标留在
    两个令牌中间，接着打的字自动是选中的颜色。
    """
    body = str(text or "")
    size = len(body)
    start = max(0, min(int(start), size))
    end = max(start, min(int(end), size))
    for kind, value in (("color", color), ("outline", outline)):
        if value is None:
            continue
        body, start, end, _stripped = _strip_wrap(body, start, end, kind)
        if not value:
            continue
        opening = f"[{kind}={_normalize_color(value)}]"
        closing = f"[/{kind}]"
        body = body[:start] + opening + body[start:end] + closing + body[end:]
        start += len(opening)
        end += len(opening)
    return body, start, end


def build_color_tokens(color: str | None, outline: str | None) -> str:
    """把选中的颜色拼成正文开头的令牌串；两端都为空时返回空串。"""
    parts: list[str] = []
    if color:
        parts.append(f"[color={_normalize_color(color)}]")
    if outline:
        parts.append(f"[outline={_normalize_color(outline)}]")
    return "".join(parts)


def _normalize_color(value) -> str:
    """规范化成小写 `#rrggbb`；认不出来的原样返回（渲染时当纯文本，砸不了别的）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = f"#{text}"
    return text.lower() if _COLOR_RE.fullmatch(text.lower()) else text


@dataclass(frozen=True, slots=True)
class _Wrap:
    """一对成对的颜色令牌：`[kind=#rrggbb]` 与它的 `[/kind]`。"""

    kind: str
    value: str
    open_start: int
    open_end: int
    close_start: int
    close_end: int


def _wraps(text) -> tuple[_Wrap, ...]:
    """正文里成对的颜色令牌全解出来；没配对的开始令牌不算一对（它只按「从这儿起生效」算）。"""
    pending: dict[str, list[tuple[int, int, str]]] = {"color": [], "outline": []}
    wraps: list[_Wrap] = []
    for start, end, kind, value in iter_color_tokens(text):
        if kind not in pending:
            continue
        if value:
            pending[kind].append((start, end, value))
        elif pending[kind]:
            open_start, open_end, open_value = pending[kind].pop()
            wraps.append(_Wrap(kind, open_value, open_start, open_end, start, end))
    return tuple(wraps)


def _token_only(text, start, end) -> bool:
    """`text[start:end]` 是不是只由令牌拼成（中间没有其它文字）。"""
    if start >= end:
        return True
    covered = start
    for token_start, token_end, _kind, _value in iter_color_tokens(text):
        if token_end <= covered:
            continue
        if token_start > covered:
            return False
        covered = token_end
        if covered >= end:
            return True
    return covered >= end


def _enclosing_wrap(text, start, end, kind) -> _Wrap | None:
    """把选区整段包住的同类令牌对，取最靠里的一对。

    「包住」允许中间夹着别的令牌（`[color=…][outline=…]字[/outline][/color]` 里选中的
    是「字」），但不允许夹着别的文字——夹了文字就不是「这一段自己的颜色」了。
    """
    found: _Wrap | None = None
    for wrap in _wraps(text):
        if wrap.kind != kind or wrap.open_end > start or wrap.close_start < end:
            continue
        if not _token_only(text, wrap.open_end, start):
            continue
        if not _token_only(text, end, wrap.close_start):
            continue
        if found is None or wrap.open_start > found.open_start:
            found = wrap
    return found


def _strip_wrap(text, start, end, kind) -> tuple[str, int, int, bool]:
    """摘掉包着选区的这一对同类令牌；里面别的令牌原样留着。"""
    wrap = _enclosing_wrap(text, start, end, kind)
    if wrap is None:
        return text, start, end, False
    head = text[:wrap.open_start] + text[wrap.open_end:start]
    body = head + text[start:end] + text[end:wrap.close_start] + text[wrap.close_end:]
    return body, len(head), len(head) + (end - start), True


__all__ = [
    "FORUM_COLOR_TOKEN_RE",
    "ForumTextRun",
    "apply_color_tokens",
    "build_color_tokens",
    "color_runs",
    "iter_color_tokens",
    "strip_color_tokens",
    "text_colors",
]
