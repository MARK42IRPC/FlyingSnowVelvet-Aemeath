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
    excerpt,
    plain_text,
    render_blocks,
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


if __name__ == "__main__":
    unittest.main()