"""主站正文的 Markdown 降级渲染。"""

from __future__ import annotations

import unittest

from lib.core.forum_markdown import (
    BLOCK_CODE,
    BLOCK_DIVIDER,
    BLOCK_HEADING,
    BLOCK_LIST,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    IMAGE_PLACEHOLDER,
    ForumImageToken,
    excerpt,
    image_id_from_url,
    plain_text,
    render_blocks,
    split_images,
)

FENCE = "`" * 3


def kinds(content):
    return [block.kind for block in render_blocks(content)]


class RenderTests(unittest.TestCase):
    def test_headings_lists_quotes_and_rules(self) -> None:
        blocks = render_blocks(
            "# 大标题\n\n普通段落\n\n> 引用\n\n- 甲\n- 乙\n\n---\n\n1. 第一\n"
        )
        self.assertEqual(
            [block.kind for block in blocks],
            [BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_QUOTE, BLOCK_LIST, BLOCK_LIST, BLOCK_DIVIDER, BLOCK_LIST],
        )
        self.assertEqual(blocks[0].level, 1)
        self.assertEqual(blocks[3].marker, "•")
        self.assertEqual(blocks[6].marker, "1.")

    def test_code_fences_keep_their_content_verbatim(self) -> None:
        blocks = render_blocks(f"说明\n\n{FENCE}python\n**不要**洗掉\n{FENCE}\n\n收尾")
        self.assertEqual([block.kind for block in blocks], [BLOCK_PARAGRAPH, BLOCK_CODE, BLOCK_PARAGRAPH])
        self.assertEqual(blocks[1].text, "**不要**洗掉")

    def test_unclosed_code_fence_still_renders(self) -> None:
        blocks = render_blocks(f"开头\n{FENCE}\n留下的代码")
        self.assertEqual([block.kind for block in blocks], [BLOCK_PARAGRAPH, BLOCK_CODE])

    def test_inline_markers_are_stripped(self) -> None:
        blocks = render_blocks("**粗** 与 *斜* 与 __下划线__ 与 ~~删除~~ 与 `代码`")
        self.assertEqual(blocks[0].text, "粗 与 斜 与 下划线 与 删除 与 代码")

    def test_images_become_placeholders_and_links_keep_short_urls(self) -> None:
        blocks = render_blocks("看图 ![截图](/api/images/9f3a) 和 [官网](https://a.example/b)")
        self.assertIn(f"{IMAGE_PLACEHOLDER}截图", blocks[0].text)
        self.assertIn("官网（https://a.example/b）", blocks[0].text)

    def test_long_urls_are_trimmed(self) -> None:
        blocks = render_blocks(f"[链接]({'https://example.com/' + 'x' * 200})")
        self.assertLessEqual(len(blocks[0].text), 120)
        self.assertIn("…", blocks[0].text)

    def test_html_tags_do_not_survive_as_markup(self) -> None:
        blocks = render_blocks("<script>alert(1)</script>正文")
        self.assertNotIn("<script>", blocks[0].text)
        self.assertTrue(blocks[0].text.endswith("正文"))

class LayoutParagraphTests(unittest.TestCase):
    """带排版令牌的行各成一段，不再按 Markdown 软换行并成一段。"""

    def test_each_token_line_becomes_its_own_block(self) -> None:
        blocks = render_blocks("[center]第一行\n[center]第二行\n[center]第三行")
        self.assertEqual([block.text for block in blocks], ["第一行", "第二行", "第三行"])
        self.assertEqual([block.align for block in blocks], ["center"] * 3)

    def test_blank_lines_still_separate_paragraphs(self) -> None:
        blocks = render_blocks("[center]第一行\n\n[center]第二行")
        self.assertEqual([block.text for block in blocks], ["第一行", "第二行"])

    def test_a_plain_line_after_a_token_line_is_its_own_block(self) -> None:
        blocks = render_blocks("[center]居中\n普通一段")
        self.assertEqual([block.text for block in blocks], ["居中", "普通一段"])
        self.assertEqual([block.align for block in blocks], ["center", ""])

    def test_soft_wrapping_still_joins_untokened_lines(self) -> None:
        blocks = render_blocks("第一行\n第二行")
        self.assertEqual([block.text for block in blocks], ["第一行 第二行"])

    def test_sizes_on_each_line_are_kept(self) -> None:
        blocks = render_blocks("[size=12]小\n[size=32]大")
        self.assertEqual([block.size for block in blocks], [12, 32])


    def test_table_rows_collapse_into_one_line(self) -> None:
        blocks = render_blocks("| 名称 | 值 |\n| --- | --- |\n| a | 1 |")
        self.assertEqual([block.text for block in blocks], ["名称 | 值", "a | 1"])

    def test_blank_input_has_no_blocks(self) -> None:
        self.assertEqual(render_blocks("   \n\n  "), ())
        self.assertEqual(render_blocks(None), ())

    def test_long_content_is_capped_with_a_notice(self) -> None:
        blocks = render_blocks("\n\n".join(f"第 {index} 段" for index in range(400)), max_blocks=10)
        self.assertEqual(len(blocks), 11)
        self.assertIn("正文过长", blocks[-1].text)

    def test_single_block_is_capped(self) -> None:
        blocks = render_blocks("字" * 5000, max_block_chars=100)
        self.assertEqual(len(blocks[0].text), 100)


class ColorRunTests(unittest.TestCase):
    """颜色令牌不是标记而是分段信息：`text` 只留可见文字，颜色走 `runs`。"""

    def test_tokens_are_not_shown_as_text(self) -> None:
        blocks = render_blocks("[color=#ff0000]红[/color]黑字")
        self.assertEqual(blocks[0].text, "红黑字")
        self.assertNotIn("color", plain_text("[color=#ff0000]红[/color]黑字"))
        self.assertNotIn("color", excerpt("[color=#ff0000]红[/color]黑字"))

    def test_runs_carry_each_paragraphs_colours(self) -> None:
        blocks = render_blocks("[color=#ff0000]红[/color]黑\n\n> [outline=#00ff00]绿边[/outline]尾")
        self.assertEqual(
            [(run.text, run.color, run.outline) for run in blocks[0].runs],
            [("红", "#ff0000", ""), ("黑", "", "")],
        )
        self.assertEqual(
            [(run.text, run.color, run.outline) for run in blocks[1].runs],
            [("绿边", "", "#00ff00"), ("尾", "", "")],
        )

    def test_blocks_without_tokens_have_no_runs(self) -> None:
        """没有令牌时 `runs` 保持空元组：渲染方据此走「不用着色」的快路径。"""
        blocks = render_blocks("# 标题\n\n正文")
        self.assertEqual([block.runs for block in blocks], [(), ()])

    def test_joined_runs_always_match_the_block_text(self) -> None:
        content = "# [color=#ff0000]标题[/color]\n\n- [outline=#0000ff]甲[/outline]\n\n```\n[color=#ffffff]代码[/color]\n```"
        for block in render_blocks(content):
            if block.runs:
                self.assertEqual("".join(run.text for run in block.runs), block.text)


class PlainTextTests(unittest.TestCase):
    def test_plain_text_joins_blocks_on_one_line(self) -> None:
        self.assertEqual(plain_text("# 标题\n\n正文一\n\n- 正文二"), "标题 正文一 正文二")

    def test_limit_appends_an_ellipsis(self) -> None:
        text = plain_text("字" * 100, limit=10)
        self.assertEqual(text, "字" * 10 + "…")

    def test_excerpt_uses_the_default_budget(self) -> None:
        self.assertEqual(excerpt("短"), "短")
        self.assertTrue(excerpt("字" * 200).endswith("…"))


class SplitImagesTests(unittest.TestCase):
    """`split_images()`：把一段原文按「能取字节的图片」切成文字段与图片段。"""

    ID = "76ab7378bad3dc2e9aba9fac929c7a87"

    def test_a_lone_image_becomes_a_token(self) -> None:
        parts = split_images(f"![图片](/api/images/{self.ID})")
        self.assertEqual(len(parts), 1)
        self.assertIsInstance(parts[0], ForumImageToken)
        self.assertEqual(parts[0].image_id, self.ID)
        self.assertEqual(parts[0].alt, "图片")
        self.assertEqual(parts[0].raw, f"![图片](/api/images/{self.ID})")

    def test_text_around_an_image_keeps_its_order(self) -> None:
        parts = split_images(f"看图：![图](/api/images/{self.ID})后文")
        self.assertEqual(parts[0], "看图：")
        self.assertIsInstance(parts[1], ForumImageToken)
        self.assertEqual(parts[2], "后文")

    def test_an_image_without_a_usable_id_stays_text(self) -> None:
        """外站地址 / 写坏的 id 取不到字节，仍旧当文字留在原地（渲染方走纯文字路径）。"""
        for content in (
            "外站 ![x](https://example.com/a.png) 不动",
            "写坏的 ![x](/api/images/短) 不动",
            "没有图片的正文",
        ):
            with self.subTest(content=content):
                self.assertEqual(split_images(content), (content,))

    def test_several_images_become_several_tokens(self) -> None:
        parts = split_images(f"![甲](/api/images/{self.ID})\n![乙](/api/images/{'b' * 32})")
        self.assertEqual(
            [part.image_id for part in parts if isinstance(part, ForumImageToken)],
            [self.ID, "b" * 32],
        )

    def test_image_id_from_url_only_takes_our_own_images(self) -> None:
        self.assertEqual(image_id_from_url(f"/api/images/{self.ID}"), self.ID)
        self.assertEqual(image_id_from_url(f"https://forum.example/api/images/{self.ID}"), self.ID)
        self.assertEqual(image_id_from_url("https://example.com/a.png"), "")
        self.assertEqual(image_id_from_url("/api/images/短"), "")
        self.assertEqual(image_id_from_url(""), "")

    def test_empty_input_has_no_parts(self) -> None:
        self.assertEqual(split_images(""), ("",))
        self.assertEqual(split_images(None), ("",))


if __name__ == "__main__":
    unittest.main()
