"""主站正文的 Markdown 降级渲染。

主站的 `content` 是**原文**，服务端不做渲染（见 `.oprate/论坛使用指南.html`）。社区页
只做适合一个侧栏宽度的降级：标题、引用、列表、代码块、分隔线各自成块，行内标记洗成
纯文本，图片降级成「【图片】」占位符而不是下载字节。

颜色令牌（`[color=#rrggbb]` 一类）是唯一的例外：它不是标记而是**分段信息**，所以
`_strip_inline()` 不动它，由 `_with_runs()` 统一切成 `ForumTextRun`，`text` 只留可见
文字、`runs` 带着每段的颜色（详情页按段着色）。

刻意不引入 Markdown 库，也不生成 HTML：帖子正文可能带 `<script>` 一类标签，纯文本投影
既躲开注入，又让渲染成本与正文长度线性相关（一段一个 QLabel，超出上限就截断）。
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from lib.core.forum_colors import ForumTextRun, color_runs

BLOCK_PARAGRAPH = "paragraph"
BLOCK_HEADING = "heading"
BLOCK_QUOTE = "quote"
BLOCK_LIST = "list"
BLOCK_CODE = "code"
BLOCK_DIVIDER = "divider"

#: 参与渲染的块数上限与单块字数上限：正文接口允许 20000 字，全渲染会拖慢详情页。
MAX_BLOCKS = 200
MAX_BLOCK_CHARS = 1200
#: 摘要默认长度。
EXCERPT_LENGTH = 80

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]*)[^)]*\)")
_AUTOLINK_RE = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>\n]{0,80}>")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
_DIVIDER_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_EMPHASIS_RE = re.compile(r"(\*\*\*|\*\*|__|~~|\*|_)(?=\S)(.+?)(?<=\S)\1")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_SPACE_RE = re.compile(r"[ \t]{2,}")

#: 图片占位符：不下载字节，只标出「这里有一张图」。
IMAGE_PLACEHOLDER = "【图片】"
#: 图片 Markdown 的地址形如 `/api/images/<id>`（也可以带站点前缀）；id 认 8~64 位
#: 的字母数字串，服务端给的是 32 位十六进制。
_IMAGE_URL_RE = re.compile(r"^(?:https?://[^/]+)?/api/images/([^/?#\s]+)/?$")
_IMAGE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{8,64}$")
#: 链接里的 URL 最长显示这么多字符，再长就截断，免得一行全是地址。
MAX_URL_CHARS = 60


@dataclass(frozen=True, slots=True)
class ForumBlock:
    """详情页的一段：`kind` 决定字体与缩进，`text` 是已经洗掉标记的纯文本。

    `runs` 是按颜色切好的段（见 `lib/core/forum_colors.py`）：没有颜色令牌时是空元组，
    渲染方据此走「不用着色」的快路径；有颜色时 `"".join(run.text) == text`。

    `raw` 是这一段的原文（标记还在）。`text` 是纯文本投影，给列表摘要与「只铺字」的快路径用；
    要看行内标记或按段着色时，渲染方拿 `raw` 自己转富文本（`forum_markup.to_html()`）——那是
    界面层的事，核心层只负责把块切好、把原文留着。
    """

    kind: str = BLOCK_PARAGRAPH
    text: str = ""
    level: int = 0
    marker: str = ""
    runs: tuple[ForumTextRun, ...] = ()
    raw: str = ""


@dataclass(frozen=True, slots=True)
class ForumImageToken:
    """正文里的一句图片 Markdown：`![说明](/api/images/<id>)`。

    详情页据此把「【图片】」占位符就地换成真的图片控件。`image_id` 是能取字节的那串 id；
    `alt` 是方括号里的说明（没有就是空串）；`raw` 是这一句原文（要原样显示时用它）。
    认不出 id 的图片（外站地址、写坏的链接）不会变成 token，仍旧当文字留在原地。
    """

    image_id: str = ""
    alt: str = ""
    raw: str = ""


def image_id_from_url(url) -> str:
    """从图片 Markdown 的地址里取 id；不是本站图片就给空串（调用方按普通文字处理）。"""
    match = _IMAGE_URL_RE.match(str(url or "").strip())
    if match is None:
        return ""
    ident = match.group(1)
    return ident if _IMAGE_ID_RE.match(ident) else ""


def split_images(text) -> tuple[str | ForumImageToken, ...]:
    """把一段原文按图片 Markdown 切成「文字 / 图片」交替的顺序（顺序就是出现顺序）。

    文字段是**原文**（行内标记还在），渲染方再走一遍 `render_blocks()`；一句能取字节的图片
    单独成段，渲染方给它摆一个图片控件——这就是「占位符换真图」。整段没有可取的图片时返回
    `(原文,)`，渲染方据此走原来的纯文字路径。
    """
    body = str(text or "")
    parts: list[str | ForumImageToken] = []
    cursor = 0
    for match in _IMAGE_RE.finditer(body):
        ident = image_id_from_url(match.group(2))
        if not ident:
            continue
        if match.start() > cursor:
            parts.append(body[cursor:match.start()])
        parts.append(
            ForumImageToken(image_id=ident, alt=match.group(1).strip(), raw=match.group(0))
        )
        cursor = match.end()
    if not parts:
        return (body,)
    if cursor < len(body):
        parts.append(body[cursor:])
    return tuple(parts)


def _strip_inline(text: str) -> str:
    """洗掉行内标记：图片 / 链接 / 自动链接 / 行内代码 / 强调 / HTML 标签。"""
    def image_replacer(match: re.Match) -> str:
        alt = match.group(1).strip()
        return f"{IMAGE_PLACEHOLDER}{alt}" if alt else IMAGE_PLACEHOLDER

    def link_replacer(match: re.Match) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if not url:
            return label
        if len(url) > MAX_URL_CHARS:
            url = f"{url[:MAX_URL_CHARS]}…"
        return f"{label}（{url}）" if label else url

    text = _IMAGE_RE.sub(image_replacer, text)
    text = _LINK_RE.sub(link_replacer, text)
    text = _AUTOLINK_RE.sub(lambda match: match.group(1), text)
    text = _INLINE_CODE_RE.sub(lambda match: match.group(1), text)
    text = _HTML_TAG_RE.sub("", text)
    # 强调可能嵌套，多跑几轮直到稳定；上限防止病态输入把正则拖住。
    for _ in range(4):
        replaced = _EMPHASIS_RE.sub(lambda match: match.group(2), text)
        if replaced == text:
            break
        text = replaced
    return _SPACE_RE.sub(" ", text).strip()


def _with_runs(block: ForumBlock) -> ForumBlock:
    """给一块补上「按颜色切好的段」，顺手把令牌从 `text` 里去掉。

    颜色令牌是纯文本，`_strip_inline()` 不认识它：早先它会原样留在正文里，详情页把
    `[color=#ff0000]` 当普通字显示出来（列表摘要也一样）。这里统一按 `forum_colors`
    的语法切段：`text` 只留可见文字，颜色走 `runs`；没有令牌时原样返回，`runs` 保持空。
    """
    runs = color_runs(block.text)
    plain = "".join(run.text for run in runs)
    if plain == block.text:
        return block
    return ForumBlock(
        kind=block.kind,
        text=plain,
        level=block.level,
        marker=block.marker,
        runs=runs,
        raw=block.raw,
    )


def _table_row(text: str) -> str | None:
    """表格行按单元格拼成一行；纯分隔行（`|---|---|`）返回 None 表示丢弃。"""
    cells = [cell.strip() for cell in text.strip().strip("|").split("|")]
    if not cells:
        return None
    if all(re.fullmatch(r":?-{1,}:?", cell) for cell in cells if cell):
        return None
    return " | ".join(cell for cell in cells if cell)


def render_blocks(
    content,
    *,
    max_blocks: int = MAX_BLOCKS,
    max_block_chars: int = MAX_BLOCK_CHARS,
) -> tuple[ForumBlock, ...]:
    """把 Markdown 原文切成块；超长正文按上限截断并补一段说明。

    `max_blocks` 与 `max_block_chars` 是渲染成本的上限：正文接口允许 20000 字，
    整篇铺成 QLabel 会拖慢详情页，所以块数与单块字数都有天花板，截断时补一句提示。
    """
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    blocks: list[ForumBlock] = []
    paragraph: list[str] = []
    code_lines: list[str] = []
    in_code = False
    truncated = False

    limit = max(1, int(max_blocks))
    char_limit = max(16, int(max_block_chars))

    def flush_paragraph() -> None:
        if not paragraph:
            return
        raw = " ".join(paragraph)
        merged = _strip_inline(raw)
        paragraph.clear()
        if merged:
            blocks.append(
                ForumBlock(kind=BLOCK_PARAGRAPH, text=merged[:char_limit], raw=raw)
            )

    def flush_code() -> None:
        if code_lines:
            raw = "\n".join(code_lines)
            blocks.append(ForumBlock(kind=BLOCK_CODE, text=raw[:char_limit], raw=raw))
            code_lines.clear()

    for raw_line in text.split("\n"):
        if len(blocks) >= limit:
            truncated = True
            break
        line = raw_line.rstrip()
        stripped = line.strip()
        fence = stripped.startswith("```") or stripped.startswith("~~~")
        if in_code:
            if fence:
                in_code = False
                flush_code()
            else:
                code_lines.append(line)
            continue
        if fence:
            flush_paragraph()
            in_code = True
            continue
        if not stripped:
            flush_paragraph()
            continue
        if _DIVIDER_RE.match(line):
            flush_paragraph()
            blocks.append(ForumBlock(kind=BLOCK_DIVIDER))
            continue
        heading = _HEADING_RE.match(stripped)
        if heading is not None:
            flush_paragraph()
            blocks.append(
                ForumBlock(
                    kind=BLOCK_HEADING,
                    text=_strip_inline(heading.group(2))[:char_limit],
                    level=len(heading.group(1)),
                    raw=heading.group(2),
                )
            )
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            raw = stripped.lstrip(">").strip()
            quoted = _strip_inline(raw)
            if quoted:
                blocks.append(ForumBlock(kind=BLOCK_QUOTE, text=quoted[:char_limit], raw=raw))
            continue
        item = _LIST_RE.match(line)
        if item is not None:
            flush_paragraph()
            indent = len(item.group(1)) // 2
            marker = item.group(2)
            marker = "\u2022" if marker in ("-", "*", "+") else marker
            body = _strip_inline(item.group(3))
            if body:
                blocks.append(
                    ForumBlock(
                        kind=BLOCK_LIST,
                        text=body[:char_limit],
                        level=min(4, indent),
                        marker=marker,
                        raw=item.group(3),
                    )
                )
            continue
        if stripped.startswith("|") or (line.rstrip().endswith("|") and "|" in line):
            flush_paragraph()
            row = _table_row(line)
            if row:
                blocks.append(
                    ForumBlock(kind=BLOCK_LIST, text=_strip_inline(row)[:char_limit], raw=row)
                )
            continue
        paragraph.append(stripped)

    if not truncated:
        flush_paragraph()
        flush_code()
    if truncated:
        blocks.append(ForumBlock(kind=BLOCK_PARAGRAPH, text="（正文过长，这里只显示前面一段）"))
    return tuple(_with_runs(block) for block in blocks)


def plain_text(content, *, limit: int | None = None) -> str:
    """把正文压成一行纯文本；`limit` 是可见字符上限，超出补省略号。"""
    pieces = [
        block.text.replace("\n", " ")
        for block in render_blocks(content)
        if block.text
    ]
    text = _SPACE_RE.sub(" ", " ".join(pieces)).strip()
    if limit is not None:
        size = max(0, int(limit))
        if size and len(text) > size:
            return f"{text[:size]}…"
    return text


def excerpt(content, *, limit: int = EXCERPT_LENGTH) -> str:
    """帖子摘要：服务端给过就用自己的，没给才从正文里现算。"""
    return plain_text(content, limit=limit)


__all__ = [
    "BLOCK_CODE",
    "BLOCK_DIVIDER",
    "BLOCK_HEADING",
    "BLOCK_LIST",
    "BLOCK_PARAGRAPH",
    "BLOCK_QUOTE",
    "EXCERPT_LENGTH",
    "ForumBlock",
    "ForumImageToken",
    "IMAGE_PLACEHOLDER",
    "MAX_BLOCKS",
    "excerpt",
    "image_id_from_url",
    "plain_text",
    "render_blocks",
    "split_images",
]