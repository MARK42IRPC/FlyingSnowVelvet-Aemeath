"""Backend-neutral visual descriptions for application-owned panels."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Protocol

from PIL import Image

from config.config_ui import COLORS, UI_THEME
from config.font_config import FONT, get_digit_font_family, get_ui_font_family
from config.scale import scale_px
from lib.core.layer import Layer

from .commands import (
    DrawBatch,
    RectCommand,
    ResourceRevision,
    SpriteCommand,
    TextAlignment,
    TextCommand,
)
from .resources import ImageResource, RasterFrame
from .rich_text_parser import TextSegment, parse_rich_text
from .screen import clamp_rect_position
from .types import Color, FontSpec, Point, Rect, Size


@dataclass(frozen=True, slots=True)
class QrPanelLayout:
    size: Size
    inner_rect: Rect
    title_rect: Rect
    qr_rect: Rect
    status_rect: Rect
    action_rect: Rect


@dataclass(frozen=True, slots=True)
class ApplicationPanelVisual:
    size: Size
    batch: DrawBatch
    action_rect: Rect | None = None


COMMAND_ACTION_BUTTONS = (
    ("clickthrough", "鼠标穿透", 80, 32),
    ("scale_up", "+", 40, 32),
    ("scale_down", "-", 40, 32),
    ("close", "关闭桌宠", 80, 32),
    ("launch_wuwa", "启动鸣潮", 80, 32),
    ("chat_mode", "文字模式", 80, 32),
    ("interaction_mode", "陪伴模式", 80, 32),
    ("more_functions", "更多功能", 80, 32),
)


@dataclass(frozen=True, slots=True)
class CommandActionPanelLayout:
    size: Size
    rects: tuple[tuple[str, Rect], ...]


@dataclass(frozen=True, slots=True)
class CommandActionPanelVisual:
    layout: CommandActionPanelLayout
    batch: DrawBatch


class BubbleTextMetrics(Protocol):
    """Low-level font metrics supplied by a rendering adapter.

    Wrapping and placement remain in the shared presenter. Adapters only
    provide the measured glyph widths and vertical metrics of their rasterizer.
    """

    default_font: FontSpec
    digit_font: FontSpec
    default_line_height: float
    digit_line_height: float
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float

    def measure(self, text: str, *, digit: bool = False) -> float:
        ...

    def measure_segment(self, segment: TextSegment) -> float:
        """Measure a rich text segment with style and scale."""
        base_width = self.measure(segment.text, digit=False)
        return base_width * segment.scale


class CommandHintTextMetrics(Protocol):
    """Low-level glyph metrics used by the shared command-hint presenter."""

    default_font: FontSpec
    digit_font: FontSpec
    side_font: FontSpec
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float

    def measure(
        self,
        text: str,
        *,
        digit: bool = False,
        side: bool = False,
    ) -> float:
        ...


def _theme_color(name: str, fallback: Color) -> Color:
    value = UI_THEME.get(name, fallback)
    return value if isinstance(value, Color) else fallback


def qr_panel_size() -> tuple[int, int]:
    return (
        scale_px(320, min_abs=1),
        scale_px(430, min_abs=1),
    )


def notice_panel_size() -> tuple[int, int]:
    return (
        scale_px(360, min_abs=1),
        scale_px(120, min_abs=1),
    )


def qr_panel_action_text(panel_kind: str) -> str:
    """Resolve product copy shared by Qt and native QR panel hosts."""
    return "退出扫码" if str(panel_kind or "").strip() == "music-login" else "关闭窗口"


def resolve_qr_panel_layout(size: tuple[float, float] | None = None) -> QrPanelLayout:
    width, height = size or qr_panel_size()
    width = max(1, int(round(float(width))))
    height = max(1, int(round(float(height))))
    layer = scale_px(2, min_abs=1)
    border = layer * 2
    title_height = scale_px(36, min_abs=1)
    qr_size = scale_px(240, min_abs=1)
    button_width = scale_px(132, min_abs=1)
    button_height = scale_px(30, min_abs=1)
    button_bottom = scale_px(12, min_abs=1)
    status_gap = scale_px(8, min_abs=1)

    inner = Rect(border, border, max(0, width - border * 2), max(0, height - border * 2))
    title = Rect(inner.x, inner.y, inner.width, title_height)
    qr_x = inner.x + (inner.width - qr_size) / 2.0
    qr_y = title.y + title.height + scale_px(10, min_abs=1)
    qr = Rect(qr_x, qr_y, qr_size, qr_size)
    action = Rect(
        inner.x + (inner.width - button_width) / 2.0,
        inner.y + inner.height - button_bottom - button_height,
        button_width,
        button_height,
    )
    status_top = qr.y + qr.height + scale_px(10, min_abs=1)
    status_bottom = action.y - status_gap
    status_height = max(
        scale_px(24, min_abs=1),
        min(scale_px(80, min_abs=1), status_bottom - status_top),
    )
    status = Rect(
        inner.x + scale_px(10, min_abs=1),
        status_top,
        max(0, inner.width - scale_px(20, min_abs=1)),
        max(0, status_height),
    )
    return QrPanelLayout(Size(width, height), inner, title, qr, status, action)


def decode_panel_image(payload: bytes, *, resource_prefix: str = "panel-image") -> ImageResource | None:
    data = bytes(payload or b"")
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            image = source.convert("RGBA")
            width, height = image.size
            frame = RasterFrame(width, height, image.tobytes("raw", "RGBA"))
    except (OSError, ValueError):
        return None
    digest = hashlib.sha256(data).hexdigest()[:16]
    return ImageResource(f"{resource_prefix}:{digest}", (frame,))


def _split_digit_segments(text: str) -> tuple[tuple[str, bool], ...]:
    segments: list[tuple[str, bool]] = []
    current = ""
    current_is_digit: bool | None = None
    for char in str(text or ""):
        is_digit = char.isdigit()
        if current and is_digit != current_is_digit:
            segments.append((current, bool(current_is_digit)))
            current = ""
        current += char
        current_is_digit = is_digit
    if current:
        segments.append((current, bool(current_is_digit)))
    return tuple(segments)


def _contains_markdown(text: str) -> bool:
    """检测文本是否包含 Markdown 或富文本标记."""
    if not text:
        return False
    # 检测常见 Markdown 标记
    markdown_patterns = ['**', '*', '`', '\\scale{']
    return any(pattern in text for pattern in markdown_patterns)


def _wrap_rich_text_lines(
    segments_by_line: list[list[TextSegment]],
    max_width: float,
    metrics: BubbleTextMetrics,
) -> tuple[tuple[TextSegment, ...], ...]:
    """Wrap rich text segments into lines that fit within max_width."""
    if max_width <= 0:
        return ((),)

    wrapped_lines: list[tuple[TextSegment, ...]] = []

    for line_segments in segments_by_line:
        if not line_segments:
            wrapped_lines.append(())
            continue

        current_line: list[TextSegment] = []
        current_width = 0.0

        for segment in line_segments:
            segment_width = metrics.measure_segment(segment)

            # 如果当前段落可以放进当前行
            if not current_line or current_width + segment_width <= max_width:
                current_line.append(segment)
                current_width += segment_width
            else:
                # 需要换行
                if current_line:
                    wrapped_lines.append(tuple(current_line))
                current_line = [segment]
                current_width = segment_width

        if current_line:
            wrapped_lines.append(tuple(current_line))

    return tuple(wrapped_lines or ((),))


def _wrap_bubble_lines(text: str, max_width: float, metrics: BubbleTextMetrics) -> tuple[str, ...]:
    if max_width <= 0:
        return ("",)
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        current_width = 0.0
        for char in paragraph:
            width = metrics.measure(char, digit=char.isdigit())
            if current and current_width + width > max_width:
                lines.append(current)
                current = char
                current_width = width
            else:
                current += char
                current_width += width
        lines.append(current)
    return tuple(lines or ("",))


@dataclass(frozen=True, slots=True)
class BubbleVisualDescription:
    """Resolved bubble geometry and draw commands for one message."""

    size: Size
    content_rect: Rect
    lines: tuple[str, ...] | tuple[tuple[TextSegment, ...], ...]
    batch: DrawBatch


def _get_font_for_segment(segment: TextSegment, metrics: BubbleTextMetrics) -> FontSpec:
    """根据 TextSegment 的样式返回对应的 FontSpec."""
    base_font = metrics.default_font
    font_size = int(base_font.size * segment.scale)

    if segment.style == "bold":
        return FontSpec(base_font.family, font_size, bold=True)
    elif segment.style == "italic":
        return FontSpec(base_font.family, font_size, italic=True)
    elif segment.style == "bold_italic":
        return FontSpec(base_font.family, font_size, bold=True, italic=True)
    elif segment.style == "code":
        # 代码使用等宽字体
        return FontSpec("Consolas", font_size, bold=False)
    else:
        return FontSpec(base_font.family, font_size)


def build_bubble_visual_rich(
    text: str,
    metrics: BubbleTextMetrics,
    *,
    max_width: float,
    padding: float,
    border_width: float,
    align: str = "center",
    layer: int = int(Layer.PET_UI),
) -> BubbleVisualDescription:
    """构建支持富文本的气泡视觉描述."""
    max_width = max(1, int(round(float(max_width))))
    padding = max(0, int(round(float(padding))))
    border_width = max(1, int(round(float(border_width))))
    content_width = max(1, max_width - border_width * 4)

    # 解析富文本
    parsed_lines = parse_rich_text(text)
    lines = _wrap_rich_text_lines(parsed_lines, content_width, metrics)

    line_height = max(
        1.0,
        float(metrics.default_line_height),
        float(metrics.digit_line_height),
    )

    # 计算最大文本宽度
    text_width = max(
        (sum(metrics.measure_segment(seg) for seg in line) for line in lines),
        default=0.0,
    )

    width = (
        max_width
        if len(lines) > 1
        else max(1, int(round(min(text_width, content_width) + padding * 2)))
    )
    height = max(1, int(round(len(lines) * line_height + padding * 2)))
    content = Rect(
        border_width * 2,
        border_width * 2,
        max(0, width - border_width * 4),
        max(0, height - border_width * 4),
    )

    border = _theme_color("border", Color(0, 0, 0))
    middle = _theme_color("mid", Color(173, 216, 230))
    background = _theme_color("bg", Color(255, 182, 193))
    text_color = _theme_color("text", Color(0, 0, 0))
    commands: list[object] = [
        RectCommand(Rect(0, 0, width, height), fill=border, layer=layer),
        RectCommand(
            Rect(border_width, border_width, width - border_width * 2, height - border_width * 2),
            fill=middle,
            layer=layer,
            z=1,
        ),
        RectCommand(content, fill=background, layer=layer, z=2),
    ]

    total_height = len(lines) * line_height
    line_top = content.y + (content.height - total_height) / 2.0
    max_ascent = max(float(metrics.default_ascent), float(metrics.digit_ascent))
    max_descent = max(float(metrics.default_descent), float(metrics.digit_descent))
    align_left = str(align or "center").lower() == "left"

    for index, line in enumerate(lines):
        line_width = sum(metrics.measure_segment(seg) for seg in line)
        x = content.x if align_left else content.x + (content.width - line_width) / 2.0
        line_y = line_top + index * line_height
        target_baseline = line_y + (line_height + max_ascent - max_descent) / 2.0

        for segment in line:
            font = _get_font_for_segment(segment, metrics)
            ascent = float(metrics.default_ascent) * segment.scale
            descent = float(metrics.default_descent) * segment.scale
            seg_width = metrics.measure_segment(segment)
            seg_top = target_baseline - (line_height + ascent - descent) / 2.0

            # 使用段落自定义颜色或默认文本颜色
            seg_color = Color(*segment.color) if segment.color else text_color

            commands.append(TextCommand(
                segment.text,
                font,
                seg_color,
                Rect(round(x), seg_top, seg_width, line_height * segment.scale),
                alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
                layer=layer,
                z=3,
            ))
            x += seg_width

    return BubbleVisualDescription(
        Size(width, height),
        content,
        lines,
        DrawBatch(tuple(commands)),
    )


def build_bubble_visual(
    text: str,
    metrics: BubbleTextMetrics,
    *,
    max_width: float,
    padding: float,
    border_width: float,
    align: str = "center",
    layer: int = int(Layer.PET_UI),
    enable_rich_text: bool = True,
) -> BubbleVisualDescription:
    """Resolve Qt-baseline bubble layout into a backend-neutral batch."""
    # 如果启用富文本且文本包含 Markdown 标记，使用富文本渲染
    if enable_rich_text and _contains_markdown(text):
        return build_bubble_visual_rich(
            text,
            metrics,
            max_width=max_width,
            padding=padding,
            border_width=border_width,
            align=align,
            layer=layer,
        )

    # 否则使用原有的纯文本渲染
    max_width = max(1, int(round(float(max_width))))
    padding = max(0, int(round(float(padding))))
    border_width = max(1, int(round(float(border_width))))
    content_width = max(1, max_width - border_width * 4)
    lines = _wrap_bubble_lines(text, content_width, metrics)
    line_height = max(
        1.0,
        float(metrics.default_line_height),
        float(metrics.digit_line_height),
    )
    text_width = max(
        (sum(metrics.measure(part, digit=is_digit) for part, is_digit in _split_digit_segments(line))
         for line in lines),
        default=0.0,
    )
    width = (
        max_width
        if len(lines) > 1
        else max(1, int(round(min(text_width, content_width) + padding * 2)))
    )
    height = max(1, int(round(len(lines) * line_height + padding * 2)))
    content = Rect(
        border_width * 2,
        border_width * 2,
        max(0, width - border_width * 4),
        max(0, height - border_width * 4),
    )

    border = _theme_color("border", Color(0, 0, 0))
    middle = _theme_color("mid", Color(173, 216, 230))
    background = _theme_color("bg", Color(255, 182, 193))
    text_color = _theme_color("text", Color(0, 0, 0))
    commands: list[object] = [
        RectCommand(Rect(0, 0, width, height), fill=border, layer=layer),
        RectCommand(
            Rect(border_width, border_width, width - border_width * 2, height - border_width * 2),
            fill=middle,
            layer=layer,
            z=1,
        ),
        RectCommand(content, fill=background, layer=layer, z=2),
    ]

    total_height = len(lines) * line_height
    line_top = content.y + (content.height - total_height) / 2.0
    max_ascent = max(float(metrics.default_ascent), float(metrics.digit_ascent))
    max_descent = max(float(metrics.default_descent), float(metrics.digit_descent))
    align_left = str(align or "center").lower() == "left"
    for index, line in enumerate(lines):
        parts = _split_digit_segments(line)
        line_width = sum(metrics.measure(part, digit=is_digit) for part, is_digit in parts)
        x = content.x if align_left else content.x + (content.width - line_width) / 2.0
        line_y = line_top + index * line_height
        target_baseline = line_y + (line_height + max_ascent - max_descent) / 2.0
        for part, is_digit in parts:
            font = metrics.digit_font if is_digit else metrics.default_font
            ascent = float(metrics.digit_ascent if is_digit else metrics.default_ascent)
            descent = float(metrics.digit_descent if is_digit else metrics.default_descent)
            part_width = metrics.measure(part, digit=is_digit)
            part_top = target_baseline - (line_height + ascent - descent) / 2.0
            commands.append(TextCommand(
                part,
                font,
                text_color,
                Rect(round(x), part_top, part_width, line_height),
                alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
                layer=layer,
                z=3,
            ))
            x += part_width

    return BubbleVisualDescription(
        Size(width, height),
        content,
        lines,
        DrawBatch(tuple(commands)),
    )


def resolve_bubble_geometry(
    anchor: Point,
    size: Size,
    screen: Rect,
    *,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> Rect:
    """Place the bubble's bottom-center on the pet's top anchor."""
    width = max(1, int(round(float(size.width))))
    height = max(1, int(round(float(size.height))))
    x = int(round(float(anchor.x))) - width // 2 + int(round(float(offset_x)))
    y = int(round(float(anchor.y))) - height + int(round(float(offset_y)))
    x, y, _ = clamp_rect_position(x, y, width, height, screen)
    return Rect(x, y, width, height)


def build_qr_panel_visual(
    title: str,
    status: str,
    placeholder: str,
    qr_resource: ImageResource | None = None,
    *,
    size: tuple[float, float] | None = None,
    layer: int = int(Layer.DIALOG),
    status_bold: bool = True,
    qr_background: bool = True,
    status_font_size: int | None = None,
    action_text: str = "",
    action_state: str = "normal",
    action_enabled: bool = True,
) -> ApplicationPanelVisual:
    layout = resolve_qr_panel_layout(size)
    width = layout.size.width
    height = layout.size.height
    border_width = scale_px(2, min_abs=1)
    font_size = max(1, int(FONT.get("ui_size", 12)))
    family = get_ui_font_family()
    border = _theme_color("border", Color(0, 0, 0))
    middle = _theme_color("mid", Color(173, 216, 230))
    background = _theme_color("bg", Color(255, 182, 193))
    text_color = _theme_color("text", Color(0, 0, 0))
    commands = [
        RectCommand(Rect(0, 0, width, height), fill=border, layer=layer),
        RectCommand(
            Rect(border_width, border_width, width - border_width * 2, height - border_width * 2),
            fill=middle,
            layer=layer,
            z=1,
        ),
        RectCommand(layout.inner_rect, fill=background, layer=layer, z=2),
        TextCommand(
            str(title or ""),
            FontSpec(family, font_size, True),
            text_color,
            layout.title_rect,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            layer=layer,
            z=3,
        ),
    ]
    if qr_background:
        commands.append(RectCommand(
            layout.qr_rect,
            fill=Color(255, 255, 255),
            layer=layer,
            z=3,
        ))
    revisions = []
    if qr_resource is not None:
        frame = qr_resource.frames[0]
        scale = min(layout.qr_rect.width / frame.width, layout.qr_rect.height / frame.height)
        target = Size(max(1, frame.width * scale), max(1, frame.height * scale))
        position = Point(
            layout.qr_rect.x + (layout.qr_rect.width - target.width) / 2.0,
            layout.qr_rect.y + (layout.qr_rect.height - target.height) / 2.0,
        )
        commands.append(SpriteCommand(
            qr_resource.resource_id,
            1,
            0,
            frame,
            position,
            1.0,
            False,
            1.0,
            layer,
            4,
            4,
            target_size=target,
        ))
        revisions.append(ResourceRevision(qr_resource.resource_id, 1))
    else:
        commands.append(TextCommand(
            str(placeholder or ""),
            FontSpec(family, font_size),
            text_color,
            layout.qr_rect,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            layer=layer,
            z=4,
        ))
    commands.append(TextCommand(
        str(status or ""),
        FontSpec(family, status_font_size or font_size, status_bold),
        text_color,
        layout.status_rect,
        alignment=int(
            TextAlignment.HCENTER | TextAlignment.VCENTER | TextAlignment.WORD_WRAP
        ),
        layer=layer,
        z=4,
    ))
    action_text = str(action_text or "").strip()
    if action_text:
        state = str(action_state or "normal").strip().lower()
        if not action_enabled:
            state = "disabled"
        if state not in {"normal", "hover", "pressed", "disabled"}:
            state = "normal"
        action_border = _theme_color("border", Color(0, 0, 0))
        if state == "hover":
            action_fill = Color(255, 200, 210)
        elif state == "pressed":
            action_fill = Color(255, 170, 190)
        elif state == "disabled":
            action_fill = _theme_color("mid", Color(173, 216, 230))
        else:
            action_fill = _theme_color("bg", Color(255, 182, 193))
        action_inset = scale_px(2, min_abs=1)
        action_content = Rect(
            layout.action_rect.x + action_inset,
            layout.action_rect.y + action_inset,
            max(0, layout.action_rect.width - action_inset * 2),
            max(0, layout.action_rect.height - action_inset * 2),
        )
        commands.extend((
            RectCommand(layout.action_rect, fill=action_border, layer=layer, z=5),
            RectCommand(action_content, fill=action_fill, layer=layer, z=6),
            TextCommand(
                action_text,
                FontSpec(family, font_size, True),
                text_color,
                action_content,
                alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                alpha=0.55 if state == "disabled" else 1.0,
                layer=layer,
                z=7,
            ),
        ))
    return ApplicationPanelVisual(
        layout.size,
        DrawBatch(tuple(commands), tuple(revisions)),
        layout.action_rect,
    )


def build_notice_panel_visual(
    text: str,
    *,
    title: str = "飞行雪绒",
    size: tuple[float, float] | None = None,
    layer: int = int(Layer.TOOLTIP),
) -> ApplicationPanelVisual:
    width, height = size or notice_panel_size()
    width = max(1, int(round(float(width))))
    height = max(1, int(round(float(height))))
    border_width = scale_px(2, min_abs=1)
    inset = border_width * 2
    family = get_ui_font_family()
    font_size = max(1, int(FONT.get("ui_size", 12)))
    border = _theme_color("border", Color(0, 0, 0))
    middle = _theme_color("mid", Color(173, 216, 230))
    background = _theme_color("bg", Color(255, 182, 193))
    text_color = _theme_color("text", Color(0, 0, 0))
    batch = DrawBatch((
        RectCommand(Rect(0, 0, width, height), fill=border, layer=layer),
        RectCommand(
            Rect(border_width, border_width, width - border_width * 2, height - border_width * 2),
            fill=middle,
            layer=layer,
            z=1,
        ),
        RectCommand(
            Rect(inset, inset, width - inset * 2, height - inset * 2),
            fill=background,
            layer=layer,
            z=2,
        ),
        TextCommand(
            str(title or ""),
            FontSpec(family, font_size, True),
            text_color,
            Rect(inset + 10, inset + 6, width - (inset + 10) * 2, 24),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            layer=layer,
            z=3,
        ),
        TextCommand(
            str(text or ""),
            FontSpec(family, font_size),
            text_color,
            Rect(inset + 10, inset + 32, width - (inset + 10) * 2, height - inset - 40),
            alignment=int(TextAlignment.LEFT | TextAlignment.TOP | TextAlignment.WORD_WRAP),
            layer=layer,
            z=3,
        ),
    ))
    return ApplicationPanelVisual(Size(width, height), batch)


COMMAND_HINT_PAGE_SIZE = 5
COMMAND_HINT_DEFAULT_ITEMS = (
    "/-在CMD窗口中执行命令",
    "#-执行玩法命令",
    "聊天-与爱弥斯聊天",
)
COMMAND_HINT_DEFAULT_PICKS = ("/", "#", "你好啊,爱弥斯")
_COMMAND_HINT_SIDE_LABEL = "Aemeath"
_COMMAND_HINT_SIDE_LABEL_HIGHLIGHT = "RUNcmd"


@dataclass(frozen=True, slots=True)
class CommandHintVisualDescription:
    size: Size
    row_rects: tuple[Rect, ...]
    page_indicator_rect: Rect | None
    batch: DrawBatch


@dataclass(frozen=True, slots=True)
class PortableCommandHintTextMetrics:
    """Qt-free glyph metrics used until native DirectWrite measure is exposed."""

    default_font: FontSpec
    digit_font: FontSpec
    side_font: FontSpec
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float

    def measure(
        self,
        text: str,
        *,
        digit: bool = False,
        side: bool = False,
    ) -> float:
        font = self.side_font if side else (self.digit_font if digit else self.default_font)
        width = 0.0
        for character in str(text or ""):
            if character.isspace():
                factor = 0.34
            elif ord(character) < 128:
                factor = 0.62
            else:
                factor = 1.0
            width += font.pixel_size * factor
        return width


@dataclass(frozen=True, slots=True)
class PortableBubbleTextMetrics:
    """Deterministic Qt-free bubble metrics for native hosts."""

    default_font: FontSpec
    digit_font: FontSpec
    default_line_height: float
    digit_line_height: float
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float

    def measure(self, text: str, *, digit: bool = False) -> float:
        font = self.digit_font if digit else self.default_font
        width = 0.0
        for character in str(text or ""):
            if character.isspace():
                factor = 0.34
            elif ord(character) < 128:
                factor = 0.62
            else:
                factor = 1.0
            width += font.pixel_size * factor
        return width

    def measure_segment(self, segment: TextSegment) -> float:
        """Measure a rich text segment with style and scale."""
        base_width = self.measure(segment.text, digit=False)
        return base_width * segment.scale


def create_portable_bubble_text_metrics() -> PortableBubbleTextMetrics:
    """Build deterministic metrics until a native DirectWrite adapter is available."""
    default_size = max(1, int(FONT.get("ui_size", 12)))
    ascent = default_size * 0.8
    descent = default_size * 0.2
    line_height = max(default_size + 2, default_size * 1.25)
    return PortableBubbleTextMetrics(
        FontSpec(get_ui_font_family(), default_size, True),
        FontSpec(get_digit_font_family(), default_size),
        line_height,
        line_height,
        ascent,
        descent,
        ascent,
        descent,
    )


def create_portable_command_hint_metrics() -> PortableCommandHintTextMetrics:
    """Build deterministic Qt-free metrics for native command-hint hosts."""
    default_size = max(1, int(FONT.get("ui_size", 12)))
    side_size = command_hint_side_font_size(default_size)
    default_font = FontSpec(get_ui_font_family(), default_size, True)
    digit_font = FontSpec(get_digit_font_family(), default_size)
    return PortableCommandHintTextMetrics(
        default_font,
        digit_font,
        FontSpec(get_digit_font_family(), side_size),
        default_size * 0.8,
        default_size * 0.2,
        default_size * 0.8,
        default_size * 0.2,
    )


def command_hint_default_pick(index: int) -> str:
    try:
        return COMMAND_HINT_DEFAULT_PICKS[int(index)]
    except (IndexError, TypeError, ValueError):
        return ""


def command_hint_side_font_size(default_size: int) -> int:
    return max(
        int(default_size) + scale_px(3, min_abs=1),
        scale_px(14, min_abs=1),
    )


def _command_hint_hash_text(item: object) -> str:
    if not isinstance(item, (tuple, list)) or not item:
        return str(item or "")
    name = str(item[0] or "")
    usage = str(item[1] or "") if len(item) > 1 else ""
    description = str(item[2] or "") if len(item) > 2 else ""
    text = f"#{name}"
    if usage:
        text += f" {usage}"
    if description:
        text += f"  {description}"
    return text


def _measure_command_hint_text(text: str, metrics: CommandHintTextMetrics) -> float:
    return sum(
        metrics.measure(segment, digit=is_digit)
        for segment, is_digit in _split_digit_segments(text)
    )


def _elide_command_hint_text(
    text: str,
    max_width: float,
    metrics: CommandHintTextMetrics,
) -> str:
    if max_width <= 0:
        return ""
    if _measure_command_hint_text(text, metrics) <= max_width:
        return text
    ellipsis = "..."
    if _measure_command_hint_text(ellipsis, metrics) > max_width:
        return ""
    kept = ""
    for char in text:
        if _measure_command_hint_text(kept + char + ellipsis, metrics) > max_width:
            break
        kept += char
    return kept + ellipsis


def _append_command_hint_mixed_text(
    commands: list[object],
    text: str,
    rect: Rect,
    metrics: CommandHintTextMetrics,
    color: Color,
    *,
    centered: bool,
    layer: int,
    z: int,
) -> None:
    parts = _split_digit_segments(text)
    total_width = _measure_command_hint_text(text, metrics)
    x = rect.x + (rect.width - total_width) / 2.0 if centered else rect.x
    max_ascent = max(metrics.default_ascent, metrics.digit_ascent)
    max_descent = max(metrics.default_descent, metrics.digit_descent)
    baseline = rect.y + (rect.height + max_ascent - max_descent) / 2.0
    for segment, is_digit in parts:
        font = metrics.digit_font if is_digit else metrics.default_font
        ascent = metrics.digit_ascent if is_digit else metrics.default_ascent
        descent = metrics.digit_descent if is_digit else metrics.default_descent
        width = metrics.measure(segment, digit=is_digit)
        top = baseline - (rect.height + ascent - descent) / 2.0
        commands.append(TextCommand(
            segment,
            font,
            color,
            Rect(round(x), top, width, rect.height),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            layer=layer,
            z=z,
        ))
        x += width


def build_command_hint_visual(
    mode: str,
    items: tuple[object, ...] | list[object],
    selected: int,
    page: int,
    metrics: CommandHintTextMetrics,
    *,
    layer: int = int(Layer.PET_UI),
) -> CommandHintVisualDescription:
    """Resolve command-hint layout and product visuals from pure state."""
    mode = "hash" if str(mode).lower() == "hash" else "default"
    all_items = tuple(items)
    max_page = max(0, (len(all_items) - 1) // COMMAND_HINT_PAGE_SIZE)
    page = max(0, min(int(page), max_page))
    start = page * COMMAND_HINT_PAGE_SIZE
    page_items = all_items[start:start + COMMAND_HINT_PAGE_SIZE]
    has_pages = len(all_items) > COMMAND_HINT_PAGE_SIZE

    layer_width = scale_px(2, min_abs=1)
    border = layer_width * 2
    row_height = scale_px(20, min_abs=1)
    padding_x = scale_px(6, min_abs=1)
    separator_height = scale_px(5, min_abs=1)
    separator_black_height = scale_px(1, min_abs=1)
    min_width = scale_px(240, min_abs=1)
    max_width = scale_px(360, min_abs=1)

    if mode == "default":
        measure_texts = tuple(str(item) for item in all_items)
        row_count = len(all_items)
        side_label_width = max(
            metrics.measure(_COMMAND_HINT_SIDE_LABEL, side=True),
            metrics.measure(_COMMAND_HINT_SIDE_LABEL_HIGHLIGHT, side=True),
        )
        side_reserve = (
            side_label_width
            + scale_px(8, min_abs=1)
            + scale_px(6, min_abs=1)
        )
    else:
        measure_texts = (
            tuple(_command_hint_hash_text(item) for item in page_items)
            if page_items
            else ("(无匹配命令)",)
        )
        row_count = max(1, len(page_items))
        side_label_width = 0.0
        side_reserve = 0.0
        if has_pages:
            measure_texts += (f"◀ {page + 1}/{max_page + 1} ▶",)
            row_count += 1

    measured_width = max(
        (_measure_command_hint_text(text, metrics) for text in measure_texts),
        default=scale_px(60, min_abs=1),
    )
    if mode == "default":
        measured_width += side_reserve
    width = int(max(min_width, min(max_width, measured_width + border * 2 + padding_x * 2)))
    if mode == "default":
        height = border * 2 + row_count * row_height + max(0, row_count - 1) * separator_height
    else:
        height = border * 2 + row_count * row_height

    outer = COLORS["black"]
    middle = COLORS["cyan"]
    background = COLORS["pink"]
    text_color = COLORS["text"]
    commands: list[object] = [
        RectCommand(Rect(0, 0, width, height), fill=outer, layer=layer),
        RectCommand(
            Rect(layer_width, layer_width, width - layer_width * 2, height - layer_width * 2),
            fill=middle,
            layer=layer,
            z=1,
        ),
        RectCommand(
            Rect(border, border, width - border * 2, height - border * 2),
            fill=background,
            layer=layer,
            z=2,
        ),
    ]
    content_width = width - border * 2
    row_rects: list[Rect] = []
    y = border
    if mode == "default":
        for index, item in enumerate(all_items):
            row_rect = Rect(border, y, content_width, row_height)
            row_rects.append(row_rect)
            if index == selected:
                commands.append(RectCommand(row_rect, fill=middle, layer=layer, z=3))
            side_rect = Rect(
                border + content_width - scale_px(6, min_abs=1) - side_label_width,
                y,
                side_label_width,
                row_height,
            )
            text_rect = Rect(
                border + padding_x,
                y,
                max(0, content_width - padding_x * 2 - side_reserve),
                row_height,
            )
            commands.append(TextCommand(
                _elide_command_hint_text(str(item), text_rect.width, metrics),
                metrics.default_font,
                text_color,
                text_rect,
                alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
                layer=layer,
                z=4,
            ))
            commands.append(TextCommand(
                _COMMAND_HINT_SIDE_LABEL_HIGHLIGHT if index == selected else _COMMAND_HINT_SIDE_LABEL,
                metrics.side_font,
                _theme_color("deep_cyan", Color(129, 198, 221))
                if index == selected
                else _theme_color("deep_pink", Color(255, 149, 164)),
                side_rect,
                alignment=int(TextAlignment.RIGHT | TextAlignment.VCENTER),
                layer=layer,
                z=4,
            ))
            y += row_height
            if index < len(all_items) - 1:
                commands.append(RectCommand(
                    Rect(border, y, content_width, separator_height),
                    fill=middle,
                    layer=layer,
                    z=3,
                ))
                black_y = y + (separator_height - separator_black_height) // 2
                commands.append(RectCommand(
                    Rect(0, black_y, width, separator_black_height),
                    fill=outer,
                    layer=layer,
                    z=4,
                ))
                y += separator_height
    else:
        display_items = page_items if page_items else ("(无匹配命令)",)
        for index, item in enumerate(display_items):
            row_rect = Rect(border, y, content_width, row_height)
            row_rects.append(row_rect)
            if page_items and index == selected:
                commands.append(RectCommand(row_rect, fill=middle, layer=layer, z=3))
            text_rect = Rect(
                border + padding_x,
                y,
                content_width - padding_x * 2,
                row_height,
            )
            text = _command_hint_hash_text(item) if page_items else str(item)
            text = _elide_command_hint_text(text, text_rect.width, metrics)
            _append_command_hint_mixed_text(
                commands,
                text,
                text_rect,
                metrics,
                text_color,
                centered=False,
                layer=layer,
                z=4,
            )
            y += row_height

    page_indicator_rect = None
    if mode == "hash" and has_pages:
        page_indicator_rect = Rect(
            border + padding_x,
            y,
            content_width - padding_x * 2,
            row_height,
        )
        _append_command_hint_mixed_text(
            commands,
            f"{page + 1}/{max_page + 1}",
            page_indicator_rect,
            metrics,
            text_color,
            centered=True,
            layer=layer,
            z=4,
        )

    return CommandHintVisualDescription(
        Size(width, height),
        tuple(row_rects),
        page_indicator_rect,
        DrawBatch(tuple(commands)),
    )


def build_rect_action_button_visual(
    width: float,
    height: float,
    text: str,
    font: FontSpec,
    *,
    hovered: bool = False,
    pressed: bool = False,
    enabled: bool = True,
    layer: int = int(Layer.PET_UI),
) -> ApplicationPanelVisual:
    """Build the shared Qt-reference visual for pet action buttons."""
    width = max(1, int(round(float(width))))
    height = max(1, int(round(float(height))))
    inset = scale_px(2, min_abs=1)
    outer = Rect(0, 0, width, height)
    middle_rect = Rect(
        inset,
        inset,
        max(0, width - inset * 2),
        max(0, height - inset * 2),
    )
    commands: list[object] = [
        RectCommand(outer, fill=COLORS["black"], layer=layer),
        RectCommand(middle_rect, fill=COLORS["cyan"], layer=layer, z=1),
    ]
    if hovered:
        hover_rect = Rect(
            inset * 2,
            inset * 2,
            max(0, width - inset * 4),
            max(0, height - inset * 4),
        )
        commands.append(RectCommand(
            hover_rect,
            fill=_theme_color("deep_pink", Color(255, 149, 164)),
            layer=layer,
            z=2,
        ))
        content_rect = Rect(
            inset * 3,
            inset * 3,
            max(0, width - inset * 6),
            max(0, height - inset * 6),
        )
        content_z = 3
    else:
        content_rect = Rect(
            inset * 2,
            inset * 2,
            max(0, width - inset * 4),
            max(0, height - inset * 4),
        )
        content_z = 2
    if not enabled:
        content_fill = _theme_color("mid", Color(173, 216, 230))
        text_alpha = 0.55
    elif pressed:
        content_fill = _theme_color("highlight", Color(255, 200, 210))
        text_alpha = 1.0
    else:
        content_fill = COLORS["pink"]
        text_alpha = 1.0
    commands.extend((
        RectCommand(content_rect, fill=content_fill, layer=layer, z=content_z),
        TextCommand(
            str(text or ""),
            font,
            COLORS["black"],
            content_rect,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            alpha=text_alpha,
            layer=layer,
            z=content_z + 1,
        ),
    ))
    return ApplicationPanelVisual(Size(width, height), DrawBatch(tuple(commands)))


def resolve_command_action_panel_layout(command_rect: Rect) -> CommandActionPanelLayout:
    """Resolve the Qt-baseline three-row action panel around the command box."""
    x = float(command_rect.x)
    y = float(command_rect.y)
    top = y - 34
    upper = (("clickthrough", 0, 80), ("scale_up", 80, 40),
             ("scale_down", 120, 40), ("close", 160, 80))
    middle = (("launch_wuwa", 0, 80), ("chat_mode", 80, 80),
              ("interaction_mode", 160, 80))
    rects = [(name, Rect(x + offset, top, width, 32)) for name, offset, width in upper]
    rects.extend((name, Rect(x + offset, y - 66, width, 32)) for name, offset, width in middle)
    rects.append(("more_functions", Rect(x, y - 98, 80, 32)))
    width = max((rect.x + rect.width for _, rect in rects), default=x) - x
    return CommandActionPanelLayout(Size(width, 96), tuple(rects))


def build_command_action_panel_visual(
    command_rect: Rect,
    *,
    hovered: str = "",
    pressed: str = "",
    interaction_mode: str = "companion",
    layer: int = int(Layer.PET_UI),
) -> CommandActionPanelVisual:
    layout = resolve_command_action_panel_layout(command_rect)
    origin_x = min((rect.x for _name, rect in layout.rects), default=0.0)
    origin_y = min((rect.y for _name, rect in layout.rects), default=0.0)
    labels = {name: text for name, text, _w, _h in COMMAND_ACTION_BUTTONS}
    labels["interaction_mode"] = (
        "办公模式" if str(interaction_mode).lower() == "office" else "陪伴模式"
    )
    sizes = {name: (w, h) for name, _text, w, h in COMMAND_ACTION_BUTTONS}
    commands: list[object] = []
    for name, rect in layout.rects:
        width, height = sizes[name]
        visual = build_rect_action_button_visual(
            width, height, labels[name],
            FontSpec(get_ui_font_family(), max(1, int(FONT.get("ui_size", 12))), True),
            hovered=name == hovered,
            pressed=name == pressed,
            layer=layer,
        )
        for command in visual.batch.commands:
            if hasattr(command, "rect"):
                command = replace(
                    command,
                    rect=Rect(
                        command.rect.x + rect.x - origin_x,
                        command.rect.y + rect.y - origin_y,
                        command.rect.width,
                        command.rect.height,
                    ),
                    layer=layer,
                )
            commands.append(command)
    return CommandActionPanelVisual(layout, DrawBatch(tuple(commands)))


__all__ = [
    "ApplicationPanelVisual",
    "BubbleTextMetrics",
    "BubbleVisualDescription",
    "COMMAND_HINT_PAGE_SIZE",
    "COMMAND_HINT_DEFAULT_ITEMS",
    "COMMAND_HINT_DEFAULT_PICKS",
    "CommandHintTextMetrics",
    "CommandHintVisualDescription",
    "PortableCommandHintTextMetrics",
    "PortableBubbleTextMetrics",
    "QrPanelLayout",
    "build_bubble_visual",
    "build_command_hint_visual",
    "command_hint_default_pick",
    "build_notice_panel_visual",
    "build_qr_panel_visual",
    "build_rect_action_button_visual",
    "COMMAND_ACTION_BUTTONS",
    "CommandActionPanelLayout",
    "CommandActionPanelVisual",
    "resolve_command_action_panel_layout",
    "build_command_action_panel_visual",
    "decode_panel_image",
    "notice_panel_size",
    "qr_panel_size",
    "qr_panel_action_text",
    "resolve_bubble_geometry",
    "resolve_qr_panel_layout",
    "command_hint_side_font_size",
    "create_portable_command_hint_metrics",
    "create_portable_bubble_text_metrics",
]
