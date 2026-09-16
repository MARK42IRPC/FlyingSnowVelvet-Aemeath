"""留言正文里的颜色令牌：``[color=#rrggbb]`` 声明正文色、``[outline=#rrggbb]`` 声明描边色。

令牌和四种行内标记一样只是纯文本：服务端不认这种写法，别的客户端看到的是带令牌的原文，
只有雪绒论坛把它解释成颜色。三处必须认同一份语法，所以放在核心层：

- 发帖时由发帖框 ``build_color_tokens()`` 拼在正文最前面；
- 发送前的本地过滤先 ``strip_color_tokens()`` 再判违规——不然 ``[color=#000000]`` 里的六位数字
  会被当成手机号拦下来，颜色越"规整"越容易踩到；
- 渲染时 ``text_colors()`` 读出两个颜色，正文里的令牌整段洗掉。

模块不导入任何 GUI 库。
"""

from __future__ import annotations

import re

#: 颜色令牌的写法：``[color=#rrggbb]`` / ``[outline=#rrggbb]``，容忍大小写、空格与全角括号外的空白。
FORUM_COLOR_TOKEN_RE = re.compile(
    r"\[(?:color|outline)\s*=\s*#[0-9a-fA-F]{6}\]",
    re.IGNORECASE,
)


def strip_color_tokens(text) -> str:
    """洗掉正文里的颜色令牌：文字里不留令牌，颜色另走 `text_colors()`。"""
    return FORUM_COLOR_TOKEN_RE.sub("", str(text or ""))


def text_colors(text) -> tuple[str, str]:
    """读出正文里的（文字色, 描边色）；缺一项就返回空串，调用方据此回退主题色。"""
    color = ""
    outline = ""
    for match in FORUM_COLOR_TOKEN_RE.finditer(str(text or "")):
        token = match.group(0).lower()
        value = token.rsplit("#", 1)[-1].rstrip("]")
        if token.startswith("[color"):
            color = f"#{value}"
        else:
            outline = f"#{value}"
    return color, outline


def build_color_tokens(color: str | None, outline: str | None) -> str:
    """把选中的颜色拼成正文开头的令牌串；两端都为空时返回空串。"""
    parts: list[str] = []
    if color:
        parts.append(f"[color={color}]")
    if outline:
        parts.append(f"[outline={outline}]")
    return "".join(parts)


__all__ = [
    "FORUM_COLOR_TOKEN_RE",
    "build_color_tokens",
    "strip_color_tokens",
    "text_colors",
]
