"""Backend-neutral announcement dialog presenter."""
from __future__ import annotations

from dataclasses import dataclass

from config.config import UI
from config.font_config import FONT, get_ui_font_family
from config.scale import scale_px
from lib.core.announcement import AnnouncementDocument
from lib.core.layer import Layer

from .commands import DrawBatch, RectCommand, TextAlignment, TextCommand
from .speaker_visuals import SpeakerTextMetrics
from .types import Color, FontSpec, Rect, Size


ANNOUNCEMENT_SIZE = Size(
    scale_px(560, min_abs=500),
    scale_px(460, min_abs=400),
)
ANNOUNCEMENT_LINES_PER_PAGE = 13


# The announcement keeps the project's pink/cyan identity, but uses it as a
# restrained signal accent over the same quiet dark surfaces as the workbench.
ANNOUNCEMENT_DARK_COLORS = {
    "canvas": Color(13, 15, 18),
    "surface": Color(23, 25, 31),
    "surface_raised": Color(30, 33, 40),
    "surface_hover": Color(39, 43, 51),
    "border": Color(53, 58, 69),
    "border_strong": Color(74, 81, 95),
    "text": Color(244, 245, 247),
    "text_muted": Color(168, 173, 183),
    "text_dim": Color(119, 126, 139),
    "pink": Color(255, 149, 188),
    "pink_hover": Color(255, 177, 207),
    "cyan": Color(140, 210, 255),
    "danger": Color(255, 122, 146),
}

ANNOUNCEMENT_LIGHT_COLORS = {
    "canvas": Color(255, 248, 251),
    "surface": Color(255, 255, 255),
    "surface_raised": Color(255, 245, 248),
    "surface_hover": Color(255, 231, 240),
    "border": Color(231, 197, 210),
    "border_strong": Color(201, 158, 176),
    "text": Color(32, 52, 77),
    "text_muted": Color(52, 72, 99),
    "text_dim": Color(79, 98, 123),
    "pink": Color(233, 104, 157),
    "pink_hover": Color(245, 141, 183),
    "cyan": Color(145, 189, 216),
    "danger": Color(217, 94, 120),
}


def get_announcement_colors() -> dict[str, Color]:
    """Return the announcement palette matching the workbench theme."""
    return ANNOUNCEMENT_LIGHT_COLORS if bool(UI.get("workbench_light_theme", False)) else ANNOUNCEMENT_DARK_COLORS


@dataclass(frozen=True, slots=True)
class AnnouncementVisualDescription:
    size: Size
    action_rects: tuple[tuple[str, Rect], ...]
    page: int
    page_count: int
    batch: DrawBatch


def _contains(rect: Rect, x: float, y: float) -> bool:
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def announcement_hit_test(
    visual: AnnouncementVisualDescription,
    x: float,
    y: float,
) -> str:
    for action, rect in visual.action_rects:
        if _contains(rect, x, y):
            return action
    return ""


def _wrap_line(text: str, width: float, metrics: SpeakerTextMetrics) -> list[str]:
    source = str(text or "")
    if not source:
        return [""]
    lines: list[str] = []
    current = ""
    for character in source:
        if character == "\n":
            lines.append(current)
            current = ""
            continue
        if current and metrics.measure(current + character) > width:
            lines.append(current)
            current = character
        else:
            current += character
    lines.append(current)
    return lines


def _document_lines(
    document: AnnouncementDocument,
    width: float,
    metrics: SpeakerTextMetrics,
) -> tuple[tuple[str, str], ...]:
    lines: list[tuple[str, str]] = []
    for block in document.blocks:
        kind = "subtitle" if block.kind == "subtitle" else "text"
        for line in _wrap_line(block.text, width, metrics):
            lines.append((kind, line))
        if lines and lines[-1][1]:
            lines.append(("space", ""))
    if lines and lines[-1][0] == "space":
        lines.pop()
    return tuple(lines)


def _panel(rect: Rect, layer: int, z: int = 0, *, raised: bool = False) -> list[object]:
    colors = get_announcement_colors()
    inset = scale_px(1, min_abs=1)
    return [
        RectCommand(rect, fill=colors["border_strong"], layer=layer, z=z),
        RectCommand(
            Rect(rect.x + inset, rect.y + inset, rect.width - inset * 2, rect.height - inset * 2),
            fill=colors["surface_raised" if raised else "canvas"],
            layer=layer,
            z=z + 1,
        ),
    ]


def _button(
    rect: Rect,
    label: str,
    font: FontSpec,
    *,
    state: str,
    layer: int,
) -> list[object]:
    colors = get_announcement_colors()
    is_primary = label == "今日不再显示"
    fill = colors["pink"] if is_primary else colors["surface_raised"]
    border = colors["pink"] if is_primary else colors["border"]
    if state == "hover":
        fill = colors["pink_hover"] if is_primary else colors["surface_hover"]
        border = colors["pink_hover"] if is_primary else colors["cyan"]
    elif state == "pressed":
        fill = colors["cyan"] if is_primary else colors["border_strong"]
    commands = [RectCommand(rect, fill=border, layer=layer, z=0)]
    inset = scale_px(1, min_abs=1)
    content = Rect(rect.x + inset, rect.y + inset, rect.width - inset * 2, rect.height - inset * 2)
    commands.append(RectCommand(content, fill=fill, layer=layer, z=1))
    commands.append(TextCommand(
        label,
        font,
        colors["canvas"] if is_primary else colors["text"],
        content,
        alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER), layer=layer, z=4,
    ))
    return commands


def build_announcement_visual(
    metrics: SpeakerTextMetrics,
    *,
    mode: str,
    document: AnnouncementDocument | None = None,
    page: int = 0,
    hovered: str = "",
    pressed: str = "",
    layer: int = int(Layer.DIALOG),
) -> AnnouncementVisualDescription:
    width = ANNOUNCEMENT_SIZE.width
    height = ANNOUNCEMENT_SIZE.height
    colors = get_announcement_colors()
    horizontal_margin = scale_px(21, min_abs=17)
    top_margin = scale_px(18, min_abs=14)
    bottom_margin = scale_px(18, min_abs=14)
    header_height = scale_px(58, min_abs=48)
    button_height = scale_px(34, min_abs=30)
    button_gap = scale_px(8, min_abs=6)
    body_top = top_margin + header_height
    body_bottom = height - bottom_margin - button_height - scale_px(15, min_abs=11)
    body_rect = Rect(
        horizontal_margin,
        body_top,
        width - horizontal_margin * 2,
        body_bottom - body_top,
    )
    title_font = FontSpec(get_ui_font_family(), scale_px(18, min_abs=15), True)
    body_font = FontSpec(get_ui_font_family(), scale_px(13, min_abs=11), False)
    subtitle_font = FontSpec(get_ui_font_family(), scale_px(14, min_abs=12), True)
    button_font = FontSpec(get_ui_font_family(), max(1, int(FONT.get("ui_size", 12))), True)
    commands: list[object] = []
    commands.extend(_panel(Rect(0, 0, width, height), layer))
    commands.extend((
        RectCommand(Rect(horizontal_margin, top_margin, scale_px(3, min_abs=2), scale_px(42, min_abs=36)),
                    fill=colors["pink"], layer=layer, z=3),
        RectCommand(Rect(horizontal_margin + scale_px(3, min_abs=2), top_margin,
                         scale_px(1, min_abs=1), scale_px(42, min_abs=36)),
                    fill=colors["cyan"], layer=layer, z=3),
    ))
    title = document.title if document is not None and mode == "document" else "桌宠公告"
    commands.extend((
        TextCommand(
            title, title_font, colors["text"],
            Rect(horizontal_margin + scale_px(16, min_abs=12), top_margin,
                 width - horizontal_margin * 2 - 56, 30),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), layer=layer, z=4,
        ),
        TextCommand(
            "SYSTEM BROADCAST  /  FSV", body_font, colors["text_dim"],
            Rect(horizontal_margin + scale_px(16, min_abs=12), top_margin + 27,
                 width - horizontal_margin * 2, 18),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), alpha=0.65, layer=layer, z=4,
        ),
    ))
    commands.extend(_panel(body_rect, layer, z=3, raised=True))

    content_width = body_rect.width - scale_px(32, min_abs=24)
    pages: list[tuple[tuple[str, str], ...]] = []
    if mode == "document" and document is not None:
        lines = _document_lines(document, content_width, metrics)
        pages = [
            lines[index:index + ANNOUNCEMENT_LINES_PER_PAGE]
            for index in range(0, max(1, len(lines)), ANNOUNCEMENT_LINES_PER_PAGE)
        ] or [tuple()]
    else:
        status = (
            (("subtitle", "正在获取公告"), ("text", "正在连接公告服务器，请稍候。"))
            if mode == "loading"
            else (("subtitle", "公告暂时无法加载"), ("text", "没有可用的本地公告，请稍后重试。"))
        )
        pages = [status]
    page_count = max(1, len(pages))
    page = max(0, min(int(page), page_count - 1))
    line_height = max(scale_px(20, min_abs=18), 18)
    y = body_rect.y + scale_px(16, min_abs=12)
    for kind, text in pages[page]:
        if kind == "space":
            y += line_height // 2
            continue
        commands.append(TextCommand(
            text,
            subtitle_font if kind == "subtitle" else body_font,
            colors["cyan" if kind == "subtitle" else "text_muted"],
            Rect(body_rect.x + scale_px(16, min_abs=12), y, content_width, line_height),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            layer=layer,
            z=6,
        ))
        y += line_height
    if page_count > 1:
        commands.append(TextCommand(
            f"{page + 1}/{page_count}", body_font, colors["text_dim"],
            Rect(body_rect.x, body_rect.y + body_rect.height - 24, body_rect.width, 20),
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER), layer=layer, z=6,
        ))

    if page_count == 1:
        commands.append(TextCommand(
            "REMOTE CHANNEL  /  01",
            FontSpec(get_ui_font_family(), scale_px(9, min_abs=8), False),
            colors["text_dim"],
            Rect(
                horizontal_margin,
                height - bottom_margin - button_height,
                scale_px(170, min_abs=140),
                button_height,
            ),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            layer=layer,
            z=4,
        ))

    close_rect = Rect(width - horizontal_margin - 30, top_margin, 30, 30)
    action_specs: list[tuple[str, str, float]] = [("close", "x", 42)]
    if mode == "document":
        if page_count > 1:
            action_specs.extend((("page_prev", "上一页", 72), ("page_next", "下一页", 72)))
        action_specs.extend((("suppress_today", "今日不再显示", 112), ("suppress_forever", "永远不再显示", 126)))
    elif mode == "error":
        action_specs.extend((("retry", "重新加载", 92),))
    action_rects: list[tuple[str, Rect]] = [("close", close_rect)]
    commands.extend(_button(
        close_rect, "x", button_font,
        state="pressed" if pressed == "close" and hovered == "close" else "hover" if hovered == "close" else "normal",
        layer=layer,
    ))
    x = width - horizontal_margin
    for action, label, action_width in reversed(action_specs[1:]):
        x -= action_width
        rect = Rect(x, height - bottom_margin - button_height, action_width, button_height)
        action_rects.append((action, rect))
        state = "pressed" if pressed == action and hovered == action else "hover" if hovered == action else "normal"
        commands.extend(_button(rect, label, button_font, state=state, layer=layer))
        x -= button_gap
    return AnnouncementVisualDescription(
        ANNOUNCEMENT_SIZE,
        tuple(action_rects),
        page,
        page_count,
        DrawBatch(tuple(commands)),
    )


__all__ = [
    "ANNOUNCEMENT_DARK_COLORS",
    "ANNOUNCEMENT_LIGHT_COLORS",
    "get_announcement_colors",
    "ANNOUNCEMENT_LINES_PER_PAGE",
    "ANNOUNCEMENT_SIZE",
    "AnnouncementVisualDescription",
    "announcement_hit_test",
    "build_announcement_visual",
]
