"""Rich text parser for bubble messages with Markdown and LaTeX support."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Literal


TextStyle = Literal["normal", "bold", "italic", "code", "bold_italic"]
RgbColor = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class TextSegment:
    """One resolved text run consumed by the shared bubble presenter."""

    text: str
    style: TextStyle = "normal"
    scale: float = 1.0
    color: RgbColor | None = None
    background_color: RgbColor | None = None


@dataclass(frozen=True, slots=True)
class _TextContext:
    style: TextStyle = "normal"
    scale: float = 1.0
    color: RgbColor | None = None
    background_color: RgbColor | None = None


_COMMAND_PATTERN = re.compile(r"\\([A-Za-z]+|.)")
_DOUBLE_ESCAPE_PATTERN = re.compile(r"\\\\(?=[A-Za-z()\[\],;!])")
_MARKDOWN_PATTERN = re.compile(
    r"`[^`\n]+`|\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|(?<!\*)\*[^*\n]+\*(?!\*)"
)

_NAMED_COLORS: dict[str, RgbColor] = {
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "brown": (165, 42, 42),
    "cyan": (0, 255, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "green": (0, 128, 0),
    "lime": (0, 255, 0),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "navy": (0, 0, 128),
    "olive": (128, 128, 0),
    "orange": (255, 165, 0),
    "pink": (255, 105, 180),
    "purple": (128, 0, 128),
    "red": (255, 0, 0),
    "silver": (192, 192, 192),
    "teal": (0, 128, 128),
    "violet": (238, 130, 238),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
}

_SIZE_SCALES = {
    "tiny": 0.5,
    "scriptsize": 0.7,
    "footnotesize": 0.8,
    "small": 0.9,
    "normalsize": 1.0,
    "large": 1.2,
    "Large": 1.44,
    "LARGE": 1.73,
    "huge": 2.07,
    "Huge": 2.49,
}

_SINGLE_ARGUMENT_COMMANDS = {
    "emph",
    "mathbf",
    "mathrm",
    "notabox",
    "overline",
    "sqrt",
    "text",
    "textbf",
    "textit",
    "underline",
}
_DOUBLE_ARGUMENT_COMMANDS = {
    "colorbox",
    "frac",
    "overset",
    "rotatebox",
    "scale",
    "scalebox",
    "textcolor",
    "underset",
}
_DECLARATION_COMMANDS = {"bfseries", "itshape", "color", *_SIZE_SCALES}
_SYMBOL_COMMANDS = {
    "cdot": " dot ",
    "geq": ">=",
    "leftarrow": " <- ",
    "leq": "<=",
    "neq": "!=",
    "rightarrow": " -> ",
    "sim": "~",
    "times": " x ",
}
_SPACING_COMMANDS = {
    ",": " ",
    ":": " ",
    ";": " ",
    "!": "",
    "quad": "  ",
    "qquad": "    ",
}
_RICH_COMMANDS = (
    _SINGLE_ARGUMENT_COMMANDS
    | _DOUBLE_ARGUMENT_COMMANDS
    | _DECLARATION_COMMANDS
    | set(_SYMBOL_COMMANDS)
    | set(_SPACING_COMMANDS)
    | {"xrightarrow"}
)

_MIN_SCALE = 0.25
_MAX_SCALE = 3.0


def _normalize_latex_escapes(text: str) -> str:
    """Collapse model-produced doubled escapes before known LaTeX tokens."""

    normalized = str(text or "")
    for _ in range(3):
        collapsed = _DOUBLE_ESCAPE_PATTERN.sub(lambda _match: "\\", normalized)
        if collapsed == normalized:
            break
        normalized = collapsed
    return normalized


def contains_rich_text(text: str) -> bool:
    """Return whether text should pass through the rich-text presenter."""

    normalized = _normalize_latex_escapes(text)
    if not normalized:
        return False
    if _MARKDOWN_PATTERN.search(normalized):
        return True
    if any(marker in normalized for marker in (r"\(", r"\)", r"\[", r"\]")):
        return True
    return any(
        match.group(1) in _RICH_COMMANDS
        for match in _COMMAND_PATTERN.finditer(normalized)
    )


def _is_escaped(text: str, position: int) -> bool:
    slash_count = 0
    index = position - 1
    while index >= 0 and text[index] == "\\":
        slash_count += 1
        index -= 1
    return slash_count % 2 == 1


def _extract_braced_content(text: str, start_pos: int) -> tuple[str, int] | None:
    """Extract a balanced braced argument, including nested commands."""

    if start_pos >= len(text) or text[start_pos] != "{":
        return None

    depth = 0
    for index in range(start_pos, len(text)):
        char = text[index]
        if char == "{" and not _is_escaped(text, index):
            depth += 1
        elif char == "}" and not _is_escaped(text, index):
            depth -= 1
            if depth == 0:
                return text[start_pos + 1:index], index + 1
    return None


def _read_braced_argument(
    text: str,
    start_pos: int,
) -> tuple[str, int, bool] | None:
    position = start_pos
    while position < len(text) and text[position].isspace():
        position += 1
    if position >= len(text) or text[position] != "{":
        return None

    extracted = _extract_braced_content(text, position)
    if extracted is not None:
        content, end_pos = extracted
        return content, end_pos, True
    return text[position + 1:], len(text), False


def _parse_color(value: str) -> RgbColor | None:
    token = str(value or "").strip().lower()
    if token in _NAMED_COLORS:
        return _NAMED_COLORS[token]
    if not token.startswith("#"):
        return None

    digits = token[1:]
    if len(digits) in (3, 4):
        digits = "".join(char * 2 for char in digits[:3])
    elif len(digits) in (6, 8):
        digits = digits[:6]
    else:
        return None
    if not re.fullmatch(r"[0-9a-f]{6}", digits):
        return None
    return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))


def _clamp_scale(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return max(_MIN_SCALE, min(_MAX_SCALE, value))


def _with_scale(context: _TextContext, factor: str | float) -> _TextContext:
    try:
        parsed = float(factor)
    except (TypeError, ValueError):
        return context
    return replace(context, scale=_clamp_scale(context.scale * parsed))


def _combine_style(current: TextStyle, applied: TextStyle) -> TextStyle:
    if current == "code" or applied == "code":
        return "code"
    bold = current in ("bold", "bold_italic") or applied in ("bold", "bold_italic")
    italic = current in ("italic", "bold_italic") or applied in ("italic", "bold_italic")
    if bold and italic:
        return "bold_italic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "normal"


def _with_style(context: _TextContext, style: TextStyle) -> _TextContext:
    return replace(context, style=_combine_style(context.style, style))


def _append_segment(segments: list[TextSegment], text: str, context: _TextContext) -> None:
    if not text:
        return
    segment = TextSegment(
        text,
        context.style,
        context.scale,
        context.color,
        context.background_color,
    )
    if segments:
        previous = segments[-1]
        if (
            previous.style == segment.style
            and previous.scale == segment.scale
            and previous.color == segment.color
            and previous.background_color == segment.background_color
        ):
            segments[-1] = replace(previous, text=previous.text + text)
            return
    segments.append(segment)


def _extend_segments(target: list[TextSegment], additions: list[TextSegment]) -> None:
    for segment in additions:
        context = _TextContext(
            segment.style,
            segment.scale,
            segment.color,
            segment.background_color,
        )
        _append_segment(target, segment.text, context)


def _parse_markdown_at(
    text: str,
    position: int,
    context: _TextContext,
) -> tuple[list[TextSegment], int] | None:
    if text[position] == "`":
        end_pos = text.find("`", position + 1)
        if end_pos > position + 1:
            code_context = _with_style(context, "code")
            return [TextSegment(
                text[position + 1:end_pos],
                code_context.style,
                code_context.scale,
                code_context.color,
                code_context.background_color,
            )], end_pos + 1
        return None

    for marker, style in (
        ("***", "bold_italic"),
        ("**", "bold"),
        ("*", "italic"),
    ):
        if not text.startswith(marker, position):
            continue
        end_pos = text.find(marker, position + len(marker))
        if end_pos <= position + len(marker):
            continue
        nested = _parse_inline(
            text[position + len(marker):end_pos],
            _with_style(context, style),
        )
        return nested, end_pos + len(marker)
    return None


def _parse_single_argument(
    text: str,
    start_pos: int,
    context: _TextContext,
) -> tuple[list[TextSegment], int] | None:
    argument = _read_braced_argument(text, start_pos)
    if argument is None:
        return None
    content, end_pos, _complete = argument
    return _parse_inline(content, context), end_pos


def _parse_two_arguments(
    text: str,
    start_pos: int,
) -> tuple[str, str | None, int] | None:
    first = _read_braced_argument(text, start_pos)
    if first is None:
        return None
    first_content, first_end, first_complete = first
    if not first_complete:
        return first_content, None, first_end

    second = _read_braced_argument(text, first_end)
    if second is None:
        return first_content, None, first_end
    second_content, second_end, _second_complete = second
    return first_content, second_content, second_end


def _parse_annotation(
    base_text: str,
    annotation_text: str,
    context: _TextContext,
) -> list[TextSegment]:
    segments = _parse_inline(base_text, context)
    annotation = _parse_inline(annotation_text, _with_scale(context, 0.75))
    if not any(segment.text for segment in annotation):
        return segments
    _append_segment(segments, " (", context)
    _extend_segments(segments, annotation)
    _append_segment(segments, ")", context)
    return segments


def _parse_latex_at(
    text: str,
    position: int,
    context: _TextContext,
    base_context: _TextContext,
) -> tuple[list[TextSegment], int, _TextContext] | None:
    match = _COMMAND_PATTERN.match(text, position)
    if match is None:
        return None

    command = match.group(1)
    end_pos = match.end()
    if command in ("(", ")", "[", "]"):
        return [], end_pos, context
    if command in ("{", "}", "_", "%", "$", "#", "&", "~", "\\"):
        return [TextSegment(
            command,
            context.style,
            context.scale,
            context.color,
            context.background_color,
        )], end_pos, context
    if command in _SPACING_COMMANDS:
        spacing = _SPACING_COMMANDS[command]
        return ([TextSegment(
            spacing,
            context.style,
            context.scale,
            context.color,
            context.background_color,
        )] if spacing else []), end_pos, context
    if command in _SYMBOL_COMMANDS:
        return [TextSegment(
            _SYMBOL_COMMANDS[command],
            context.style,
            context.scale,
            context.color,
            context.background_color,
        )], end_pos, context

    if command in _SIZE_SCALES:
        next_context = replace(
            context,
            scale=_clamp_scale(base_context.scale * _SIZE_SCALES[command]),
        )
        while end_pos < len(text) and text[end_pos].isspace():
            end_pos += 1
        return [], end_pos, next_context
    if command == "bfseries":
        return [], end_pos, _with_style(context, "bold")
    if command == "itshape":
        return [], end_pos, _with_style(context, "italic")
    if command == "color":
        argument = _read_braced_argument(text, end_pos)
        if argument is None:
            return [], end_pos, context
        color_name, final_pos, _complete = argument
        color = _parse_color(color_name)
        return [], final_pos, replace(context, color=color or context.color)

    if command in _SINGLE_ARGUMENT_COMMANDS:
        nested_context = context
        if command in ("mathbf", "textbf"):
            nested_context = _with_style(context, "bold")
        elif command in ("textit", "emph"):
            nested_context = _with_style(context, "italic")
        parsed = _parse_single_argument(text, end_pos, nested_context)
        if parsed is None:
            return [], end_pos, context
        nested, final_pos = parsed
        return nested, final_pos, context

    if command == "xrightarrow":
        parsed = _parse_single_argument(text, end_pos, _with_scale(context, 0.75))
        if parsed is None:
            return [TextSegment(" -> ", context.style, context.scale, context.color,
                                context.background_color)], end_pos, context
        label, final_pos = parsed
        segments: list[TextSegment] = []
        _append_segment(segments, " -[", context)
        _extend_segments(segments, label)
        _append_segment(segments, "]-> ", context)
        return segments, final_pos, context

    if command in _DOUBLE_ARGUMENT_COMMANDS:
        arguments = _parse_two_arguments(text, end_pos)
        if arguments is None:
            return [], end_pos, context
        first, second, final_pos = arguments
        if second is None:
            if command == "frac":
                return _parse_inline(first, context), final_pos, context
            return [], final_pos, context

        if command in ("scale", "scalebox"):
            return _parse_inline(second, _with_scale(context, first)), final_pos, context
        if command == "textcolor":
            color = _parse_color(first)
            nested_context = replace(context, color=color or context.color)
            return _parse_inline(second, nested_context), final_pos, context
        if command == "colorbox":
            color = _parse_color(first)
            nested_context = replace(
                context,
                background_color=color or context.background_color,
            )
            return _parse_inline(second, nested_context), final_pos, context
        if command == "rotatebox":
            return _parse_inline(second, context), final_pos, context
        if command == "frac":
            segments = _parse_inline(first, context)
            _append_segment(segments, "/", context)
            _extend_segments(segments, _parse_inline(second, context))
            return segments, final_pos, context
        if command in ("underset", "overset"):
            return _parse_annotation(second, first, context), final_pos, context

    # Unknown wrappers are flattened when they have a braced payload. This
    # keeps model-generated extensions readable without interpreting them.
    parsed = _parse_single_argument(text, end_pos, context)
    if parsed is not None:
        nested, final_pos = parsed
        return nested, final_pos, context
    return None


def _parse_inline(text: str, context: _TextContext) -> list[TextSegment]:
    segments: list[TextSegment] = []
    position = 0
    current_context = context

    while position < len(text):
        char = text[position]
        if char in ("`", "*"):
            markdown = _parse_markdown_at(text, position, current_context)
            if markdown is not None:
                nested, position = markdown
                _extend_segments(segments, nested)
                continue
        if char == "\\":
            latex = _parse_latex_at(
                text,
                position,
                current_context,
                context,
            )
            if latex is not None:
                nested, position, current_context = latex
                _extend_segments(segments, nested)
                continue
        if char == "~":
            _append_segment(segments, " ", current_context)
        else:
            _append_segment(segments, char, current_context)
        position += 1

    return segments


def parse_markdown_inline(text: str) -> list[TextSegment]:
    """Parse one Markdown/LaTeX line into resolved text runs."""

    normalized = _normalize_latex_escapes(text)
    segments = _parse_inline(normalized, _TextContext())
    return segments if segments else [TextSegment("")]


def parse_rich_text(text: str) -> list[list[TextSegment]]:
    """Parse text into lines of backend-neutral styled segments."""

    normalized = _normalize_latex_escapes(text)
    return [
        (_parse_inline(line, _TextContext()) or [TextSegment("")])
        for line in normalized.split("\n")
    ]


def segments_to_plain_text(segments: list[TextSegment]) -> str:
    """Join one parsed line without formatting metadata."""

    return "".join(segment.text for segment in segments)


def rich_text_to_plain_text(text: str) -> str:
    """Flatten supported rich text while preserving line breaks and content."""

    return "\n".join(
        segments_to_plain_text(segments)
        for segments in parse_rich_text(text)
    )


__all__ = [
    "TextSegment",
    "contains_rich_text",
    "parse_markdown_inline",
    "parse_rich_text",
    "rich_text_to_plain_text",
    "segments_to_plain_text",
]
