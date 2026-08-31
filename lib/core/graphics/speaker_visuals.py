"""Backend-neutral visual description for the speaker search family."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from config.config_music import SPEAKER_SEARCH_UI
from config.config_ui import COLORS, UI_THEME
from config.font_config import FONT, get_ui_font_family
from config.scale import scale_px
from lib.core.layer import Layer

from .commands import DrawBatch, RectCommand, TextAlignment, TextCommand
from .types import FontSpec, Rect, Size


SPEAKER_SEARCH_PAGE_SIZE = 5
SPEAKER_SEARCH_MODES = ("song", "artist", "album", "playlist")
SPEAKER_SEARCH_MODE_LABELS = {
    "song": "单曲优先",
    "artist": "歌手优先",
    "album": "专辑优先",
    "playlist": "歌单优先",
}


class SpeakerTextMetrics(Protocol):
    default_font: FontSpec

    def measure(self, text: str, *, digit: bool = False, side: bool = False) -> float: ...


@dataclass(frozen=True, slots=True)
class SpeakerSearchVisualDescription:
    size: Size
    input_rect: Rect
    search_rect: Rect
    control_rects: tuple[tuple[str, Rect], ...]
    result_rects: tuple[Rect, ...]
    page_rect: Rect | None
    batch: DrawBatch


def _contains(rect: Rect, x: float, y: float) -> bool:
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def speaker_visual_hit_test(
    visual: SpeakerSearchVisualDescription,
    x: float,
    y: float,
) -> tuple[str, int]:
    if _contains(visual.search_rect, x, y):
        return "search", -1
    if _contains(visual.input_rect, x, y):
        return "input", -1
    for name, rect in visual.control_rects:
        if _contains(rect, x, y):
            return name, -1
    for index, rect in enumerate(visual.result_rects):
        if _contains(rect, x, y):
            return "result", index
    if visual.page_rect is not None and _contains(visual.page_rect, x, y):
        midpoint = visual.page_rect.x + visual.page_rect.width / 2.0
        return ("page_prev" if x < midpoint else "page_next"), -1
    return "", -1


def _panel_commands(rect: Rect, *, layer: int, z: int) -> list[object]:
    inset = scale_px(2, min_abs=1)
    return [
        RectCommand(rect, fill=UI_THEME["border"], layer=layer, z=z),
        RectCommand(
            Rect(rect.x + inset, rect.y + inset, rect.width - inset * 2, rect.height - inset * 2),
            fill=UI_THEME["mid"], layer=layer, z=z + 1,
        ),
        RectCommand(
            Rect(rect.x + inset * 2, rect.y + inset * 2, rect.width - inset * 4, rect.height - inset * 4),
            fill=UI_THEME["bg"], layer=layer, z=z + 2,
        ),
    ]


def _button_commands(
    rect: Rect,
    label: str,
    font: FontSpec,
    *,
    state: str,
    layer: int,
    z: int,
) -> list[object]:
    inset = scale_px(2, min_abs=1)
    commands = [
        RectCommand(rect, fill=COLORS["black"], layer=layer, z=z),
        RectCommand(
            Rect(rect.x + inset, rect.y + inset, rect.width - inset * 2, rect.height - inset * 2),
            fill=COLORS["cyan"], layer=layer, z=z + 1,
        ),
    ]
    hovered = state in {"hover", "pressed"}
    if hovered:
        commands.append(RectCommand(
            Rect(rect.x + inset * 2, rect.y + inset * 2, rect.width - inset * 4, rect.height - inset * 4),
            fill=UI_THEME["deep_pink"], layer=layer, z=z + 2,
        ))
        content = Rect(
            rect.x + inset * 3, rect.y + inset * 3,
            rect.width - inset * 6, rect.height - inset * 6,
        )
        content_z = z + 3
    else:
        content = Rect(
            rect.x + inset * 2, rect.y + inset * 2,
            rect.width - inset * 4, rect.height - inset * 4,
        )
        content_z = z + 2
    commands.extend((
        RectCommand(
            content,
            fill=UI_THEME["highlight"] if state == "pressed" else COLORS["pink"],
            layer=layer,
            z=content_z,
        ),
        TextCommand(
            label,
            font,
            COLORS["black"],
            content,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            layer=layer,
            z=content_z + 1,
        ),
    ))
    return commands


def _elide(text: str, width: float, metrics: SpeakerTextMetrics) -> str:
    value = str(text or "")
    if metrics.measure(value) <= width:
        return value
    suffix = "..."
    available = max(0.0, float(width) - metrics.measure(suffix))
    kept = ""
    for character in value:
        candidate = kept + character
        if metrics.measure(candidate) > available:
            break
        kept = candidate
    return kept + suffix


def build_speaker_search_visual(
    input_text: str,
    composition: str,
    results: tuple[str, ...],
    metrics: SpeakerTextMetrics,
    *,
    searching: bool = False,
    page: int = 0,
    selected: int = -1,
    search_mode: str = "song",
    playing: bool = False,
    logged_in: bool = False,
    provider_label: str = "网易模式",
    hovered: str = "",
    pressed: str = "",
    layer: int = int(Layer.PET_UI),
) -> SpeakerSearchVisualDescription:
    input_width = int(SPEAKER_SEARCH_UI.get("input_width", scale_px(160, min_abs=1)))
    button_width = int(SPEAKER_SEARCH_UI.get("button_width", scale_px(80, min_abs=1)))
    search_height = int(SPEAKER_SEARCH_UI.get("height", scale_px(36, min_abs=1)))
    total_width = input_width + button_width
    control_height = scale_px(32, min_abs=1)
    control_gap = scale_px(2, min_abs=1)
    search_y = control_height * 2 + control_gap
    result_y = search_y + search_height + scale_px(2, min_abs=1)
    border = scale_px(4, min_abs=1)
    row_height = scale_px(20, min_abs=1)
    padding_x = scale_px(6, min_abs=1)
    page_size = SPEAKER_SEARCH_PAGE_SIZE
    max_page = max(0, (len(results) - 1) // page_size)
    page = max(0, min(int(page), max_page))
    page_items = results[page * page_size:(page + 1) * page_size]
    display_items = ("♪ 搜索中...",) if searching else (page_items or ("(无结果，请输入关键词后搜索)",))
    has_pages = len(results) > page_size
    measure_items = tuple(display_items) + ((f"{page + 1}/{max_page + 1}",) if has_pages else ())
    result_width = int(max(
        scale_px(240, min_abs=1),
        min(
            scale_px(360, min_abs=1),
            max((metrics.measure(text) for text in measure_items), default=60)
            + border * 2 + padding_x * 2,
        ),
    ))
    result_rows = len(display_items) + (1 if has_pages else 0)
    result_height = border * 2 + result_rows * row_height
    width = max(total_width, result_width)
    height = result_y + result_height
    font = FontSpec(get_ui_font_family(), max(1, int(FONT.get("ui_size", 12))), True)

    input_panel = Rect(0, search_y, input_width, search_height)
    search_rect = Rect(input_width, search_y, button_width, search_height)
    entry_rect = Rect(
        border, search_y + border,
        input_width - border * 2, search_height - border * 2,
    )
    small_button_width = scale_px(40, min_abs=1)
    wide_button_width = scale_px(80, min_abs=1)
    controls = (
        ("play_pause", Rect(0, 0, small_button_width, control_height)),
        ("next_track", Rect(small_button_width, 0, small_button_width, control_height)),
        ("provider", Rect(wide_button_width * 2, 0, wide_button_width, control_height)),
        ("priority", Rect(0, control_height, wide_button_width, control_height)),
        ("login", Rect(wide_button_width, control_height, wide_button_width, control_height)),
        ("playlist", Rect(wide_button_width * 2, control_height, wide_button_width, control_height)),
    )
    labels = {
        "play_pause": "Ⅱ" if playing else "▶",
        "next_track": "▶|",
        "provider": str(provider_label or "音乐模式"),
        "priority": SPEAKER_SEARCH_MODE_LABELS.get(search_mode, SPEAKER_SEARCH_MODE_LABELS["song"]),
        "login": "已登录" if logged_in else "登录音乐",
        "playlist": "播放列表",
        "search": "搜索中..." if searching else "搜索歌曲",
    }
    commands: list[object] = []
    for name, rect in controls:
        state = "pressed" if pressed == name and hovered == name else "hover" if hovered == name else "normal"
        commands.extend(_button_commands(rect, labels[name], font, state=state, layer=layer, z=0))
    commands.extend(_panel_commands(input_panel, layer=layer, z=0))
    commands.extend((
        RectCommand(entry_rect, fill=COLORS["pink"], layer=layer, z=3),
        RectCommand(
            Rect(entry_rect.x + 2, entry_rect.y + 2, entry_rect.width - 4, entry_rect.height - 4),
            fill=SPEAKER_SEARCH_UI.get("entry_bg_color", (255, 255, 255)), layer=layer, z=4,
        ),
        TextCommand(
            (str(input_text or "") + str(composition or "")) or "输入歌曲名搜索...",
            font,
            COLORS["black"],
            Rect(entry_rect.x + 6, entry_rect.y, entry_rect.width - 12, entry_rect.height),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            alpha=0.45 if not input_text and not composition else 1.0,
            layer=layer,
            z=5,
        ),
    ))
    search_state = "pressed" if pressed == "search" and hovered == "search" else "hover" if hovered == "search" else "normal"
    commands.extend(_button_commands(search_rect, labels["search"], font, state=search_state, layer=layer, z=0))

    result_panel = Rect(0, result_y, result_width, result_height)
    commands.extend(_panel_commands(result_panel, layer=layer, z=0))
    row_rects: list[Rect] = []
    y = result_y + border
    if searching or not page_items:
        text_rect = Rect(border + padding_x, y, result_width - border * 2 - padding_x * 2, row_height)
        commands.append(TextCommand(
            display_items[0], font, COLORS["black"], text_rect,
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), layer=layer, z=4,
        ))
    else:
        for index, text in enumerate(page_items):
            row_rect = Rect(border, y, result_width - border * 2, row_height)
            row_rects.append(row_rect)
            if index == selected:
                commands.append(RectCommand(row_rect, fill=UI_THEME["highlight"], layer=layer, z=3))
            text_rect = Rect(row_rect.x + padding_x, y, row_rect.width - padding_x * 2, row_height)
            commands.append(TextCommand(
                _elide(text, text_rect.width, metrics), font, COLORS["black"], text_rect,
                alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), layer=layer, z=4,
            ))
            y += row_height
    page_rect = None
    if has_pages:
        page_rect = Rect(border, y, result_width - border * 2, row_height)
        commands.append(TextCommand(
            f"◀ {page + 1}/{max_page + 1} ▶", font, COLORS["black"], page_rect,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER), layer=layer, z=4,
        ))
    return SpeakerSearchVisualDescription(
        Size(width, height), entry_rect, search_rect, controls,
        tuple(row_rects), page_rect, DrawBatch(tuple(commands)),
    )


__all__ = [
    "SPEAKER_SEARCH_MODE_LABELS",
    "SPEAKER_SEARCH_MODES",
    "SPEAKER_SEARCH_PAGE_SIZE",
    "SpeakerSearchVisualDescription",
    "build_speaker_search_visual",
    "speaker_visual_hit_test",
]
