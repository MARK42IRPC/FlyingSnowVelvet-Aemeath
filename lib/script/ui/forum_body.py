"""主论坛页的「详情正文渲染」：正文块、行内标记与正文配图。

批次 3 后续轮次从 `forum_board.ForumBoardPage` 切出。详情页的骨架（`_build_detail`）已在
`forum_detail` 模块，本轮把**正文渲染**那一半也搬出来——`_render_body()` 按块类型铺正文、
`_add_body_source()` 把块里的图片 Markdown 就地换成真图、纯文字段与富文本段的分流
（`_add_body_label` / `_block_needs_rich` / `_add_body_rich`），以及正文底下那行兜底缩略图
（`_add_body_images`）与正文里就地铺开的整幅图（`_add_body_image`）。

切分保持逐行等价：`ForumBodyMixin` 的方法体与搬出前一致（缩进也未变），`ForumBoardPage`
只是多继承本 mixin。正文渲染依赖的看板状态（`_detail_body` / `_detail_host` /
`_thumb_data` / `_service` 等）仍由 `ForumBoardPage.__init__` 建立，混入方法与之同源；
`_clear_detail_body()` 与 `_refresh_thumbs()` 留在看板本体，混入方法按 `self` 解析调用它们。
`_BLOCK_FONT_DEFAULT` / `_HEADING_FONT_SIZES` 只服务正文渲染，随之一并搬到这里。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_IMAGES_PER_POST,
    ForumPost,
)
from lib.core.forum_colors import ForumTextRun
from lib.core.forum_markdown import (
    BLOCK_CODE,
    ForumImageToken,
    BLOCK_DIVIDER,
    BLOCK_HEADING,
    BLOCK_LIST,
    BLOCK_QUOTE,
    render_blocks,
    split_images,
)
from lib.core.render.backends.qt.widgets.forum_images import (
    BODY_WIDTH_HINT,
    LIST_THUMB_SIZE,
    ForumDetailImage,
    ForumImageThumb,
    _block_alignment,
    _font,
)
from lib.script.ui.forum_markup import escape_text, to_html
from lib.script.ui.forum_style import forum_card_text_color, forum_muted_text_color
from lib.script.ui.forum_text import MarkupText

#: 正文块的默认字号；列表、引用、代码各一档，其余用默认。
_BLOCK_FONT_DEFAULT = 13
#: 标题字号档位。
_HEADING_FONT_SIZES = {1: 18, 2: 16, 3: 15, 4: 14, 5: 14, 6: 13}


class ForumBodyMixin:
    """详情正文的渲染；由 `ForumBoardPage` 混入。"""

    def _render_body(self, post: ForumPost) -> None:
        self._clear_detail_body()
        blocks = render_blocks(post.content)
        if not blocks:
            self._add_body_label("（这篇帖子没有正文）", "ForumPostText", 12)
            return
        #: 正文里已经就地铺出来的图片 id：最底下那行兜底缩略图据此跳过它们，同一张图不铺两遍。
        inlined: set[str] = set()
        for block in blocks:
            if block.kind == BLOCK_DIVIDER:
                line = QFrame(self._detail_host)
                line.setObjectName("ForumDivider")
                line.setFrameShape(QFrame.HLine)
                line.setFixedHeight(scale_px(1, min_abs=1))
                self._detail_body.addWidget(line)
            elif block.kind == BLOCK_HEADING:
                size = _HEADING_FONT_SIZES.get(int(block.level or 1), 14)
                inlined |= self._add_body_source(
                    block, block.raw, "ForumPostHeading", block.size or size, bold=True
                )
            elif block.kind == BLOCK_QUOTE:
                inlined |= self._add_body_source(
                    block,
                    block.raw,
                    "ForumPostQuote",
                    block.size or 12,
                    prefix="“",
                    suffix="”",
                    color=forum_muted_text_color(),
                )
            elif block.kind == BLOCK_CODE:
                # 代码块里的图片 Markdown 就是要原样显示，不做就地替换。
                self._add_body_label(block.text, "ForumPostCode", 11)
            elif block.kind == BLOCK_LIST:
                marker = f"{block.marker} " if block.marker else ""
                indent = "　" * max(0, int(block.level))
                inlined |= self._add_body_source(
                    block,
                    block.raw,
                    "ForumPostText",
                    block.size or _BLOCK_FONT_DEFAULT,
                    prefix=f"{indent}{marker}",
                )
            else:
                inlined |= self._add_body_source(
                    block,
                    block.raw,
                    "ForumPostText",
                    block.size or _BLOCK_FONT_DEFAULT,
                )
        self._add_body_images(post, inlined)

    def _add_body_source(
        self,
        block,
        source: str,
        name: str,
        size: int,
        *,
        bold: bool = False,
        prefix: str = "",
        suffix: str = "",
        color: str = "",
    ) -> set[str]:
        """铺正文的一块，块里的图片 Markdown 就地换成真图；返回换掉的图片 id。

        整块没有可取图片（外站地址也算没有）时走原来那条纯文字路径——一个 QLabel 或一个
        `MarkupText`，观感与以前一字不差。图前 / 图后的文字段是**原文**（行内标记还在），
        得逐段再跑一遍 `render_blocks()` 才能把标记解释成富文本；列表的项目符号与引用的书名号
        只挂在第一段 / 最后一段文字上（整块就一张图时它们没地方挂，也就不显示了）。
        """
        parts = split_images(source)
        if len(parts) == 1 and isinstance(parts[0], str):
            self._add_body_label(
                f"{prefix}{block.text}{suffix}",
                name,
                size,
                bold=bold,
                block=block,
                prefix=prefix,
                suffix=suffix,
                color=color,
            )
            return set()
        texts = [part for part in parts if isinstance(part, str) and part.strip()]
        inlined: set[str] = set()
        text_index = 0
        for part in parts:
            if isinstance(part, ForumImageToken):
                self._add_body_image(part.image_id)
                inlined.add(part.image_id)
                continue
            text = part.strip()
            if not text:
                continue
            first = text_index == 0
            last = text_index == len(texts) - 1
            text_index += 1
            pieces = render_blocks(text)
            for index, piece in enumerate(pieces):
                head = prefix if first and index == 0 else ""
                tail = suffix if last and index == len(pieces) - 1 else ""
                self._add_body_label(
                    f"{head}{piece.text}{tail}",
                    name,
                    size,
                    bold=bold,
                    block=piece,
                    prefix=head,
                    suffix=tail,
                    color=color,
                )
        return inlined

    def _add_body_image(self, image_id: str) -> None:
        """正文里的一句图片 Markdown 就地铺成整幅图。

        占位符在哪，图就在哪；宽度跟着正文栏走、只等比缩放（`ForumDetailImage`），所以图在栏内
        尽可能大，又不会被拉伸或旋转。
        """
        image = ForumDetailImage(image_id, width_hint=BODY_WIDTH_HINT, parent=self._detail_host)
        self._detail_body.addWidget(image, 0, Qt.AlignLeft)
        self._refresh_thumbs()

    def _add_body_label(
        self,
        text: str,
        name: str,
        size: int,
        *,
        bold: bool = False,
        block=None,
        prefix: str = "",
        suffix: str = "",
        color: str = "",
    ) -> None:
        """正文的一段。

        纯文字段用 QLabel（样式表管字体与颜色，最省事）；带行内标记或颜色令牌的段换成
        `MarkupText`——QLabel 的富文本能给文字上色，但给不出**描边**，而描边色是发帖页的
        一个按钮，漏掉它就成了「发了带描边的帖子，看起来却没有描边」。两条路径的字体、字号
        与颜色取自同一处，观感一致。
        """
        if block is not None and self._block_needs_rich(block):
            self._add_body_rich(
                block, name, size, bold=bold, prefix=prefix, suffix=suffix, color=color
            )
            return
        label = QLabel(text, self._detail_host)
        label.setObjectName(name)
        label.setFont(_font(size, bold=bold))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_body.addWidget(label, 0, _block_alignment(block))

    @staticmethod
    def _block_needs_rich(block) -> bool:
        """这一段光靠 QLabel 显示不出原样吗（有颜色令牌、行内标记，或段落排版令牌）。"""
        if block.kind == BLOCK_CODE:
            # 代码块里的 `**` 就是要原样显示，别当成格式解释。
            return False
        if getattr(block, "size", 0) or getattr(block, "align", ""):
            # 字号要写进字符格式、对齐要跟块格式一起铺，QLabel 做不到这两件事。
            return True
        if any(run.color or run.outline for run in block.runs):
            return True
        html = to_html(block.raw)
        return bool(html) and html != escape_text(block.text)

    def _add_body_rich(
        self, block, name: str, size: int, *, bold: bool, prefix: str, suffix: str, color: str
    ) -> None:
        """带标记 / 带颜色的段：交给 `MarkupText` 逐段着色（连描边一起）。

        前缀（列表的项目符号与缩进、引用的书名号）不进 `block.runs`，所以要自己补一段没有
        颜色的 run，段的边界才对得上——`MarkupText` 是按累计字符数定位的。
        """
        runs = block.runs
        if prefix or suffix:
            runs = (ForumTextRun(prefix),) + runs + (ForumTextRun(suffix),)
        widget = MarkupText(
            escape_text(prefix) + to_html(block.raw) + escape_text(suffix),
            font=_font(size, bold=bold),
            color=color or forum_card_text_color(),
            width_hint=BODY_WIDTH_HINT,
            runs=runs,
            align=_block_alignment(block),
            size=getattr(block, "size", 0),
            object_name="ForumBodyText",
            parent=self._detail_host,
        )
        widget.setProperty("forumBlock", name)
        self._detail_body.addWidget(widget)

    def _add_body_images(self, post: ForumPost, inlined: set[str] | None = None) -> None:
        """兜底：这一帖挂了图、正文里却没能就地铺出来的，在正文底下补一行小缩略图。

        正文里认得出的图片已经铺成整幅图了（`_add_body_image()`），`inlined` 就是那些 id；
        这里只收漏网之鱼（正文没写图片 Markdown、或者写的是外站地址），同一张图不铺第二遍。
        """
        shown = inlined or set()
        rest = [
            image.id
            for image in post.images[:FORUM_IMAGES_PER_POST]
            if image.id not in shown
        ]
        if not rest:
            return
        strip = QHBoxLayout()
        strip.setContentsMargins(0, scale_px(3, min_abs=2), 0, 0)
        strip.setSpacing(scale_px(6, min_abs=5))
        for ident in rest:
            strip.addWidget(
                ForumImageThumb(ident, size=LIST_THUMB_SIZE, parent=self._detail_host), 0
            )
        strip.addStretch(1)
        self._detail_body.addLayout(strip)
        self._refresh_thumbs()


__all__ = [
    "ForumBodyMixin",
]
