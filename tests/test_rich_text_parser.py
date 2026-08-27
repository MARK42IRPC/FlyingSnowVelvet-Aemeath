import unittest

from lib.core.graphics.application_visuals import build_bubble_visual
from lib.core.graphics.commands import RectCommand, TextCommand
from lib.core.graphics.rich_text_parser import (
    TextSegment,
    contains_rich_text,
    parse_markdown_inline,
    rich_text_to_plain_text,
    segments_to_plain_text,
)
from lib.core.graphics.types import Color, FontSpec
from lib.script.chat.handler_stream_presenter import (
    _build_ai_voice_text,
    _strip_tool_commands_for_display,
)


class _BubbleMetrics:
    default_font = FontSpec("UI", 10)
    digit_font = FontSpec("Digits", 10)
    default_line_height = 14
    digit_line_height = 14
    default_ascent = 10
    default_descent = 3
    digit_ascent = 10
    digit_descent = 3

    def measure(self, text: str, *, digit: bool = False) -> float:
        return len(text) * (4 if digit else 6)

    def measure_segment(self, segment: TextSegment) -> float:
        return self.measure(segment.text) * segment.scale


class RichTextParserTests(unittest.TestCase):
    def test_existing_markdown_and_scale_syntax_remain_supported(self):
        source = (
            r"**bold** *italic* ***both*** `code` "
            r"\scale{1.5}{large}"
        )

        segments = parse_markdown_inline(source)

        by_text = {segment.text: segment for segment in segments if segment.text.strip()}
        self.assertEqual(by_text["bold"].style, "bold")
        self.assertEqual(by_text["italic"].style, "italic")
        self.assertEqual(by_text["both"].style, "bold_italic")
        self.assertEqual(by_text["code"].style, "code")
        self.assertEqual(by_text["large"].scale, 1.5)

    def test_latex_after_plain_prefix_is_parsed_with_color_and_size(self):
        source = r"prefix \textcolor{pink}{\small \text{colored}} suffix"

        self.assertTrue(contains_rich_text(source))
        segments = parse_markdown_inline(source)

        self.assertEqual(segments_to_plain_text(segments), "prefix colored suffix")
        colored = next(segment for segment in segments if segment.text == "colored")
        self.assertEqual(colored.color, (255, 105, 180))
        self.assertEqual(colored.scale, 0.9)

    def test_nested_persona_example_preserves_styles_without_commands(self):
        source = (
            r"\(\scalebox{2}{\colorbox{black}{\text{"
            r"\rotatebox{-12}{\textcolor{#4A0000}{A}}"
            r"\rotatebox{8}{\textcolor{blue}{B}}}}}\)"
        )

        segments = parse_markdown_inline(source)

        self.assertEqual(segments_to_plain_text(segments), "AB")
        self.assertEqual([segment.scale for segment in segments], [2.0, 2.0])
        self.assertEqual(
            [segment.color for segment in segments],
            [(74, 0, 0), (0, 0, 255)],
        )
        self.assertEqual(
            [segment.background_color for segment in segments],
            [(0, 0, 0), (0, 0, 0)],
        )

    def test_escaped_model_output_and_incomplete_nested_content_are_readable(self):
        escaped = r"\\textcolor{cyan}{\\text{wind}}"
        incomplete = r"\textcolor{purple}{\text{unfinished"

        self.assertEqual(rich_text_to_plain_text(escaped), "wind")
        self.assertEqual(rich_text_to_plain_text(incomplete), "unfinished")

    def test_annotations_and_arrows_flatten_without_latex_leaking(self):
        source = (
            r"\underset{\textcolor{pink}{me}}{\text{Aemeath}} "
            r"\xrightarrow{\text{for you}} "
            r"\underset{\textcolor{blue}{you}}{\text{Wanderer}}"
        )

        plain = rich_text_to_plain_text(source)

        for expected in ("Aemeath", "me", "for you", "Wanderer", "you"):
            self.assertIn(expected, plain)
        self.assertNotIn("\\", plain)
        self.assertNotIn("{", plain)
        self.assertNotIn("}", plain)

    def test_colorbox_chain_keeps_each_background_color(self):
        source = (
            r"\scalebox{2}{"
            r"\colorbox{red}{R}\colorbox{orange}{O}"
            r"\colorbox{yellow}{Y}\colorbox{green}{G}\colorbox{blue}{B}}"
        )

        segments = parse_markdown_inline(source)

        self.assertEqual(segments_to_plain_text(segments), "ROYGB")
        self.assertEqual(
            [segment.background_color for segment in segments],
            [
                (255, 0, 0),
                (255, 165, 0),
                (255, 255, 0),
                (0, 128, 0),
                (0, 0, 255),
            ],
        )


class RichTextBubbleVisualTests(unittest.TestCase):
    def test_latex_commands_select_rich_presenter_and_draw_colors(self):
        visual = build_bubble_visual(
            r"intro \textcolor{cyan}{\Huge \colorbox{black}{X}}",
            _BubbleMetrics(),
            max_width=180,
            padding=8,
            border_width=2,
        )

        text_commands = [
            command for command in visual.batch.commands
            if isinstance(command, TextCommand)
        ]
        self.assertEqual("".join(command.text for command in text_commands), "intro X")
        self.assertFalse(any("\\" in command.text for command in text_commands))
        styled = next(command for command in text_commands if command.text == "X")
        self.assertEqual(styled.color, Color(0, 255, 255))
        self.assertEqual(styled.font.pixel_size, 25)
        self.assertGreaterEqual(visual.size.height, 49)
        self.assertTrue(any(
            isinstance(command, RectCommand) and command.fill == Color(0, 0, 0)
            and command.z == 3
            for command in visual.batch.commands
        ))

    def test_long_rich_text_wraps_inside_content_width(self):
        visual = build_bubble_visual(
            r"prefix \textcolor{blue}{abcdefghijklmnop} suffix",
            _BubbleMetrics(),
            max_width=54,
            padding=4,
            border_width=2,
        )

        self.assertGreater(len(visual.lines), 1)
        for line in visual.lines:
            self.assertLessEqual(
                sum(_BubbleMetrics().measure_segment(segment) for segment in line),
                46,
            )


class RichTextDisplayPipelineTests(unittest.TestCase):
    def test_display_keeps_size_commands_and_voice_uses_plain_text(self):
        source = r"///poem///\(\textcolor{pink}{\Huge \text{hello}}\)"

        display = _strip_tool_commands_for_display(source)

        self.assertIn(r"\Huge", display)
        self.assertEqual(_build_ai_voice_text(source), "hello")


if __name__ == "__main__":
    unittest.main()
