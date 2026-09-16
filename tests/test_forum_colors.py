"""颜色令牌的语法：整段取色、按段切段、发帖页的「上色」。"""

from __future__ import annotations

import unittest

from lib.core.forum_colors import (
    ForumTextRun,
    apply_color_tokens,
    build_color_tokens,
    color_runs,
    strip_color_tokens,
    text_colors,
)


class TokenSyntaxTests(unittest.TestCase):
    def test_both_open_and_close_shapes_are_tokens(self) -> None:
        text = "[color=#ff0000]红[/color][outline=#00FF00]边[/outline]"
        self.assertEqual(strip_color_tokens(text), "红边")

    def test_capitalisation_and_spaces_are_tolerated(self) -> None:
        self.assertEqual(strip_color_tokens("[COLOR = #AB12cd]字[/Color]"), "字")

    def test_unfinished_tokens_stay_plain_text(self) -> None:
        for text in ("[color=#ff00]短了", "[color=red]", "[color=#ff0000"):
            self.assertEqual(strip_color_tokens(text), text, text)

    def test_plain_text_is_untouched(self) -> None:
        self.assertEqual(strip_color_tokens("没有令牌的一句话"), "没有令牌的一句话")
        self.assertEqual(strip_color_tokens(""), "")


class WallColorTests(unittest.TestCase):
    """留言墙的口径：整张卡片一个色，取开头生效的那一对。"""

    def test_build_and_read_back(self) -> None:
        tokens = build_color_tokens("#ff0000", "#00ff00")
        self.assertEqual(tokens, "[color=#ff0000][outline=#00ff00]")
        self.assertEqual(text_colors(f"{tokens}整条一句话"), ("#ff0000", "#00ff00"))

    def test_missing_pair_gives_empty_strings(self) -> None:
        self.assertEqual(text_colors("没有颜色"), ("", ""))
        self.assertEqual(text_colors(build_color_tokens(None, "#123456")), ("", "#123456"))

    def test_the_opening_pair_wins_not_the_last_one(self) -> None:
        text = "[color=#ff0000]红[/color]普通[color=#00ff00]绿[/color]"
        self.assertEqual(text_colors(text), ("#ff0000", ""))

    def test_build_normalises_and_skips_empty(self) -> None:
        self.assertEqual(build_color_tokens("FF0000", None), "[color=#ff0000]")
        self.assertEqual(build_color_tokens(None, None), "")


class ColorRunTests(unittest.TestCase):
    def test_plain_text_is_one_uncoloured_run(self) -> None:
        self.assertEqual(color_runs("普通一句话"), (ForumTextRun("普通一句话"),))

    def test_tokens_split_the_text(self) -> None:
        runs = color_runs("[color=#ff0000]红[/color]黑")
        self.assertEqual([(run.text, run.color) for run in runs], [("红", "#ff0000"), ("黑", "")])

    def test_a_colour_holds_until_the_next_token(self) -> None:
        runs = color_runs("[color=#ff0000]红字还很长 一直到换色[color=#00ff00]绿了")
        self.assertEqual(
            [(run.text, run.color) for run in runs],
            [("红字还很长 一直到换色", "#ff0000"), ("绿了", "#00ff00")],
        )

    def test_text_colour_and_outline_are_independent(self) -> None:
        runs = color_runs("[color=#ff0000][outline=#0000ff]两个都改[/outline]只剩文字色")
        self.assertEqual(
            [(run.text, run.color, run.outline) for run in runs],
            [("两个都改", "#ff0000", "#0000ff"), ("只剩文字色", "#ff0000", "")],
        )

    def test_token_only_or_empty_text_has_no_runs(self) -> None:
        self.assertEqual(color_runs("[color=#ff0000]"), ())
        self.assertEqual(color_runs(""), ())

    def test_runs_join_back_to_the_stripped_text(self) -> None:
        text = "[color=#ff0000]红[/color]黑[outline=#00ff00]边[/outline]尾"
        self.assertEqual("".join(run.text for run in color_runs(text)), strip_color_tokens(text))


class ApplyColorTests(unittest.TestCase):
    """发帖页的上色按钮：包一段、换色、取消，都不该越点越长。"""

    def test_wrapping_a_selection_keeps_it_selected(self) -> None:
        text, start, end = apply_color_tokens("整句红色警告", 2, 4, color="#ff0000")
        self.assertEqual(text, "整句[color=#ff0000]红色[/color]警告")
        self.assertEqual(text[start:end], "红色")

    def test_an_empty_selection_puts_the_caret_between_the_tokens(self) -> None:
        text, start, end = apply_color_tokens("", 0, 0, color="#123456")
        self.assertEqual(text, "[color=#123456][/color]")
        self.assertEqual((start, end), (15, 15))

    def test_recolouring_replaces_the_pair_instead_of_nesting(self) -> None:
        text, start, end = apply_color_tokens("整句红色警告", 2, 4, color="#ff0000")
        again, start, end = apply_color_tokens(text, start, end, color="#0000ff")
        self.assertEqual(again, "整句[color=#0000ff]红色[/color]警告")
        self.assertEqual(again[start:end], "红色")

    def test_clicking_twice_with_the_same_colour_does_not_grow(self) -> None:
        text, start, end = apply_color_tokens("只有一个字", 0, 1, color="#ff0000")
        once = text
        again, _start, _end = apply_color_tokens(text, start, end, color="#ff0000")
        self.assertEqual(again, once)

    def test_an_empty_value_cancels_the_colour(self) -> None:
        text, start, end = apply_color_tokens("整句红色警告", 2, 4, color="#ff0000")
        plain, start2, end2 = apply_color_tokens(text, start, end, color="")
        self.assertEqual(plain, "整句红色警告")
        self.assertEqual(plain[start2:end2], "红色")

    def test_none_leaves_the_other_colour_alone(self) -> None:
        text, start, end = apply_color_tokens("描边字", 0, 3, outline="#00ff00")
        both, start, end = apply_color_tokens(text, start, end, color="#ff0000")
        self.assertEqual(both, "[outline=#00ff00][color=#ff0000]描边字[/color][/outline]")
        self.assertEqual(both[start:end], "描边字")
        # 换文字色时描边那一对不能跟着丢，也不该再套一层
        again, _start, _end = apply_color_tokens(both, start, end, color="#0000ff")
        self.assertEqual(again, "[outline=#00ff00][color=#0000ff]描边字[/color][/outline]")
        # 取消文字色，描边留着
        only_outline, start, end = apply_color_tokens(again, start, end, color="")
        self.assertEqual(only_outline, "[outline=#00ff00]描边字[/outline]")
        self.assertEqual(only_outline[start:end], "描边字")
        # 取消描边（文字色本来就没有了）
        self.assertEqual(apply_color_tokens(only_outline, start, end, outline="")[0], "描边字")

    def test_out_of_range_selection_is_clamped(self) -> None:
        text, start, end = apply_color_tokens("短", -5, 99, color="#ff0000")
        self.assertEqual(text, "[color=#ff0000]短[/color]")
        self.assertEqual(text[start:end], "短")


if __name__ == "__main__":
    unittest.main()
