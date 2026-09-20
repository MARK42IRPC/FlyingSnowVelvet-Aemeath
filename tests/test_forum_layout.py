"""段落级排版令牌：字号与对齐的读写、洗令牌，以及与段落边界的配合。"""
from __future__ import annotations

import unittest

from lib.core.forum_layout import (
    FORUM_ALIGN_CENTER,
    FORUM_ALIGN_LEFT,
    FORUM_ALIGN_RIGHT,
    FORUM_SIZE_MAX,
    FORUM_SIZE_MIN,
    build_layout_tokens,
    paragraph_spans,
    parse_layout_tokens,
    set_layout_tokens,
    strip_layout_tokens,
)


class StripTests(unittest.TestCase):
    def test_tokens_do_not_show_up_as_text(self):
        self.assertEqual(strip_layout_tokens("[center][size=20]标题[/size]"), "标题[/size]")
        self.assertEqual(strip_layout_tokens("[LEFT]正文"), "正文")
        self.assertEqual(strip_layout_tokens("[ size = 18 ]字"), "字")

    def test_lines_without_tokens_are_untouched(self):
        for text in ("没有令牌", "", "方括号 [不是令牌]"):
            self.assertEqual(strip_layout_tokens(text), text)

    def test_unknown_tokens_are_left_alone(self):
        # 认不出来的写法不是排版令牌：别的客户端写的原文要原样留着。
        self.assertEqual(strip_layout_tokens("[size=abc]"), "[size=abc]")
        self.assertEqual(strip_layout_tokens("[big]"), "[big]")

    def test_out_of_range_size_tokens_are_still_stripped(self):
        # 写法认得出来就洗掉：超范围字号按「没写」处理，留着只会在正文里印出方括号。
        self.assertEqual(strip_layout_tokens("[size=999]字"), "字")
        self.assertEqual(parse_layout_tokens("[size=999]字"), ("字", 0, ""))


class ParseTests(unittest.TestCase):
    def test_size_and_alignment_are_read_back(self):
        text, size, align = parse_layout_tokens("[right][size=24]字")
        self.assertEqual(text, "字")
        self.assertEqual(size, 24)
        self.assertEqual(align, FORUM_ALIGN_RIGHT)

    def test_first_token_of_each_kind_wins(self):
        _text, size, align = parse_layout_tokens("[center][right][size=20][size=30]字")
        self.assertEqual(size, 20)
        self.assertEqual(align, FORUM_ALIGN_CENTER)

    def test_out_of_range_size_reads_as_absent(self):
        for raw in ("[size=9]", "[size=99]", f"[size={FORUM_SIZE_MIN - 1}]"):
            _text, size, align = parse_layout_tokens(f"{raw}字")
            self.assertEqual(size, 0)
            self.assertEqual(align, "")

    def test_limits_are_inclusive(self):
        self.assertEqual(parse_layout_tokens(f"[size={FORUM_SIZE_MIN}]字")[1], FORUM_SIZE_MIN)
        self.assertEqual(parse_layout_tokens(f"[size={FORUM_SIZE_MAX}]字")[1], FORUM_SIZE_MAX)

    def test_no_tokens_reads_as_left_default(self):
        self.assertEqual(parse_layout_tokens("普通一段"), ("普通一段", 0, ""))


class BuildTests(unittest.TestCase):
    def test_alignment_comes_before_size(self):
        self.assertEqual(build_layout_tokens(size=20, align=FORUM_ALIGN_CENTER), "[center][size=20]")

    def test_nothing_requested_builds_nothing(self):
        self.assertEqual(build_layout_tokens(), "")
        self.assertEqual(build_layout_tokens(size=999), "")

    def test_unknown_alignment_is_dropped(self):
        self.assertEqual(build_layout_tokens(size=20, align="justify"), "[size=20]")


class ParagraphSpanTests(unittest.TestCase):
    def test_span_covers_the_whole_paragraph(self):
        body = "第一段\n第二段\n第三段"
        self.assertEqual(paragraph_spans(body, 1, 2), ((0, 3),))
        self.assertEqual(paragraph_spans(body, 5, 5), ((4, 7),))

    def test_selection_across_paragraphs_covers_all_of_them(self):
        body = "甲\n乙\n丙"
        self.assertEqual(paragraph_spans(body, 2, 4), ((2, 3), (4, 5)))

    def test_selection_ending_at_a_boundary_stays_in_that_paragraph(self):
        body = "甲\n乙"
        self.assertEqual(paragraph_spans(body, 0, 1), ((0, 1),))

    def test_empty_body_still_reports_one_paragraph(self):
        self.assertEqual(paragraph_spans("", 0, 0), ((0, 0),))


class SetTests(unittest.TestCase):
    def test_size_is_applied_to_the_paragraph(self):
        text, start, stop = set_layout_tokens("一段话", 0, 0, size=24)
        self.assertEqual(text, "[size=24]一段话")
        self.assertEqual(text[start:stop], "一段话")

    def test_alignment_is_applied_to_the_paragraph(self):
        text, _start, _stop = set_layout_tokens("一段话", 3, 3, align=FORUM_ALIGN_CENTER)
        self.assertEqual(text, "[center]一段话")

    def test_setting_both_keeps_a_stable_order(self):
        text, _s, _e = set_layout_tokens("字", 0, 0, size=18, align=FORUM_ALIGN_RIGHT)
        self.assertEqual(text, "[right][size=18]字")

    def test_repeated_calls_do_not_stack_tokens(self):
        text, start, stop = set_layout_tokens("字", 0, 0, size=18)
        for size in (20, 22, 24):
            text, start, stop = set_layout_tokens(text, start, stop, size=size)
        self.assertEqual(text, "[size=24]字")
        self.assertEqual(strip_layout_tokens(text), "字")

    def test_zero_and_empty_clear_the_token(self):
        text, start, stop = set_layout_tokens("字", 0, 0, size=20, align=FORUM_ALIGN_CENTER)
        cleared, _s, _e = set_layout_tokens(text, start, stop, size=0, align="")
        self.assertEqual(cleared, "字")

    def test_none_leaves_the_other_property_alone(self):
        text, start, stop = set_layout_tokens("字", 0, 0, size=20, align=FORUM_ALIGN_CENTER)
        text, _s, _e = set_layout_tokens(text, start, stop, size=28)
        self.assertEqual(text, "[center][size=28]字")
        text, _s, _e = set_layout_tokens(text, 0, 0, align=FORUM_ALIGN_LEFT)
        self.assertEqual(text, "[left][size=28]字")

    def test_every_selected_paragraph_is_updated(self):
        body = "甲\n乙\n丙"
        text, _start, _stop = set_layout_tokens(body, 0, len(body), size=16)
        self.assertEqual(text, "[size=16]甲\n[size=16]乙\n[size=16]丙")
        self.assertEqual(strip_layout_tokens(text), "甲\n乙\n丙")

    def test_returned_selection_covers_the_edited_paragraph(self):
        text, start, stop = set_layout_tokens("甲\n乙", 1, 1, size=16)
        self.assertEqual(text, "[size=16]甲\n乙")
        self.assertEqual(text[start:stop], "甲")

    def test_non_numeric_size_clears_instead_of_crashing(self):
        text, _s, _e = set_layout_tokens("字", 0, 0, size=20)
        cleared, _s, _e = set_layout_tokens(text, 0, 0, size="x")
        self.assertEqual(cleared, "字")


if __name__ == "__main__":
    unittest.main()
