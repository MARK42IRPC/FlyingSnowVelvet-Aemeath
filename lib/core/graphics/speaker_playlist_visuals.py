"""Backend-neutral visual description for the speaker playlist family."""
from __future__ import annotations

from dataclasses import dataclass

from config.config_ui import COLORS, UI_THEME
from config.font_config import FONT, get_ui_font_family
from config.scale import scale_px
from lib.core.layer import Layer

from .commands import DrawBatch, RectCommand, TextAlignment, TextCommand
from .speaker_visuals import SpeakerTextMetrics
from .types import FontSpec, Rect, Size


PLAYLIST_PAGE_SIZE = 7
_MAIN_WIDTH = scale_px(240, min_abs=1)
_ACTION_WIDTH = scale_px(20, min_abs=1)
_CONTROL_HEIGHT = scale_px(32, min_abs=1)
_ROW_HEIGHT = scale_px(20, min_abs=1)
_LAYER_WIDTH = scale_px(2, min_abs=1)
_BORDER = _LAYER_WIDTH * 2
_PROGRESS_GAP = scale_px(2, min_abs=1)
_PROGRESS_HEIGHT = scale_px(20, min_abs=1)
_CONTROLS_HEIGHT = _CONTROL_HEIGHT * 3
_PROGRESS_Y = _CONTROLS_HEIGHT + _PROGRESS_GAP
_PANEL_Y = _PROGRESS_Y + _PROGRESS_HEIGHT + _PROGRESS_GAP


@dataclass(frozen=True, slots=True)
class SpeakerPlaylistVisualDescription:
    size: Size
    control_rects: tuple[tuple[str, Rect], ...]
    progress_rect: Rect
    slider_rect: Rect
    row_rects: tuple[Rect, ...]
    remove_rect: Rect | None
    play_rect: Rect | None
    page_rect: Rect | None
    batch: DrawBatch


def _contains(rect: Rect, x: float, y: float) -> bool:
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def speaker_playlist_hit_test(
    visual: SpeakerPlaylistVisualDescription,
    x: float,
    y: float,
) -> tuple[str, int]:
    for name, rect in visual.control_rects:
        if _contains(rect, x, y):
            return name, -1
    if _contains(visual.progress_rect, x, y):
        return "progress", -1
    if visual.remove_rect is not None and _contains(visual.remove_rect, x, y):
        return "remove", -1
    if visual.play_rect is not None and _contains(visual.play_rect, x, y):
        return "play_selected", -1
    for index, rect in enumerate(visual.row_rects):
        if _contains(rect, x, y):
            return "row", index
    if visual.page_rect is not None and _contains(visual.page_rect, x, y):
        midpoint = visual.page_rect.x + visual.page_rect.width / 2.0
        return ("page_prev" if x < midpoint else "page_next"), -1
    return "", -1


def _panel_commands(rect: Rect, layer: int, z: int = 0) -> list[object]:
    return [
        RectCommand(rect, fill=COLORS["black"], layer=layer, z=z),
        RectCommand(
            Rect(
                rect.x + _LAYER_WIDTH,
                rect.y + _LAYER_WIDTH,
                rect.width - _LAYER_WIDTH * 2,
                rect.height - _LAYER_WIDTH * 2,
            ),
            fill=COLORS["cyan"], layer=layer, z=z + 1,
        ),
        RectCommand(
            Rect(
                rect.x + _BORDER,
                rect.y + _BORDER,
                rect.width - _BORDER * 2,
                rect.height - _BORDER * 2,
            ),
            fill=COLORS["pink"], layer=layer, z=z + 2,
        ),
    ]


def _button_commands(
    rect: Rect,
    label: str,
    font: FontSpec,
    *,
    state: str,
    layer: int,
) -> list[object]:
    commands = _panel_commands(rect, layer)
    content = Rect(
        rect.x + _BORDER,
        rect.y + _BORDER,
        rect.width - _BORDER * 2,
        rect.height - _BORDER * 2,
    )
    if state in {"hover", "pressed"}:
        commands.append(RectCommand(
            content,
            fill=UI_THEME["highlight"] if state == "pressed" else UI_THEME["deep_pink"],
            layer=layer,
            z=3,
        ))
    commands.append(TextCommand(
        label,
        font,
        COLORS["black"],
        content,
        alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
        layer=layer,
        z=4,
    ))
    return commands


def _elide(text: str, width: float, metrics: SpeakerTextMetrics) -> str:
    value = str(text or "")
    if metrics.measure(value) <= width:
        return value
    suffix = "..."
    available = max(0.0, width - metrics.measure(suffix))
    kept = ""
    for character in value:
        if metrics.measure(kept + character) > available:
            break
        kept += character
    return kept + suffix


def build_speaker_playlist_visual(
    queue: tuple[tuple[object, str], ...],
    metrics: SpeakerTextMetrics,
    *,
    current_index: int = -1,
    page: int = 0,
    selected: int = -1,
    playing: bool = False,
    play_mode: str = "list_loop",
    logged_in: bool = False,
    progress: float = 0.0,
    remaining: int = 0,
    hovered: str = "",
    pressed: str = "",
    layer: int = int(Layer.PANEL),
) -> SpeakerPlaylistVisualDescription:
    queue = tuple(queue)
    max_page = max(0, (len(queue) - 1) // PLAYLIST_PAGE_SIZE)
    page = max(0, min(int(page), max_page))
    page_items = queue[page * PLAYLIST_PAGE_SIZE:(page + 1) * PLAYLIST_PAGE_SIZE]
    has_pages = len(queue) > PLAYLIST_PAGE_SIZE
    row_count = max(1, len(page_items)) + (1 if has_pages else 0)
    panel_height = _BORDER * 2 + row_count * _ROW_HEIGHT
    width = _MAIN_WIDTH + _ACTION_WIDTH * 2
    height = _PANEL_Y + panel_height
    font = FontSpec(get_ui_font_family(), max(1, int(FONT.get("ui_size", 12))), True)
    small = scale_px(40, min_abs=1)
    wide = scale_px(80, min_abs=1)
    controls = (
        ("liked", Rect(wide, 0, wide, _CONTROL_HEIGHT)),
        ("clear", Rect(0, _CONTROL_HEIGHT, wide, _CONTROL_HEIGHT)),
        ("local", Rect(wide, _CONTROL_HEIGHT, wide, _CONTROL_HEIGHT)),
        ("play_mode", Rect(wide * 2, _CONTROL_HEIGHT, wide, _CONTROL_HEIGHT)),
        ("play_pause", Rect(0, _CONTROL_HEIGHT * 2, small, _CONTROL_HEIGHT)),
        ("next_track", Rect(small, _CONTROL_HEIGHT * 2, small, _CONTROL_HEIGHT)),
        ("history", Rect(wide, _CONTROL_HEIGHT * 2, wide, _CONTROL_HEIGHT)),
        ("volume_up", Rect(wide * 2, _CONTROL_HEIGHT * 2, small, _CONTROL_HEIGHT)),
        ("volume_down", Rect(wide * 2 + small, _CONTROL_HEIGHT * 2, small, _CONTROL_HEIGHT)),
    )
    mode_labels = {
        "single_loop": "单曲循环",
        "list_loop": "列表循环",
        "random": "随机播放",
    }
    labels = {
        "liked": "一键喜欢" if logged_in else "请先登录",
        "clear": "清空列表",
        "local": "一键本地",
        "play_mode": mode_labels.get(play_mode, "列表循环"),
        "play_pause": "Ⅱ" if playing else "▶",
        "next_track": "▶|",
        "history": "一键历史",
        "volume_up": "+",
        "volume_down": "-",
    }
    commands: list[object] = []
    for name, rect in controls:
        state = "pressed" if pressed == name and hovered == name else "hover" if hovered == name else "normal"
        commands.extend(_button_commands(rect, labels[name], font, state=state, layer=layer))

    progress_rect = Rect(0, _PROGRESS_Y, _MAIN_WIDTH, _PROGRESS_HEIGHT)
    commands.extend(_panel_commands(progress_rect, layer))
    time_width = scale_px(57, min_abs=1)
    separator_width = scale_px(5, min_abs=1)
    slider_width = _MAIN_WIDTH - _BORDER * 2 - separator_width - time_width
    slider_rect = Rect(_BORDER, _PROGRESS_Y + _BORDER, slider_width, _PROGRESS_HEIGHT - _BORDER * 2)
    progress = max(0.0, min(1.0, float(progress)))
    fill_width = slider_rect.width * progress
    if fill_width > 0:
        commands.append(RectCommand(
            Rect(slider_rect.x, slider_rect.y, fill_width, slider_rect.height),
            fill=COLORS["cyan"], layer=layer, z=3,
        ))
    handle_size = max(2, slider_rect.height - 2)
    handle_x = max(slider_rect.x, min(slider_rect.x + slider_rect.width, slider_rect.x + fill_width))
    commands.append(RectCommand(
        Rect(handle_x - handle_size / 2.0, slider_rect.y + 1, handle_size, handle_size),
        fill=UI_THEME["deep_pink"], layer=layer, z=4,
    ))
    time_rect = Rect(
        _BORDER + slider_width + separator_width,
        _PROGRESS_Y + _BORDER,
        time_width,
        _PROGRESS_HEIGHT - _BORDER * 2,
    )
    commands.append(TextCommand(
        f"{max(0, int(remaining)) // 60}:{max(0, int(remaining)) % 60:02d}",
        font,
        COLORS["black"],
        time_rect,
        alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
        layer=layer,
        z=4,
    ))

    panel_rect = Rect(0, _PANEL_Y, _MAIN_WIDTH, panel_height)
    commands.extend(_panel_commands(panel_rect, layer))
    row_rects: list[Rect] = []
    y = _PANEL_Y + _BORDER
    padding = scale_px(6, min_abs=1)
    if not page_items:
        commands.append(TextCommand(
            "（队列为空）", font, COLORS["black"],
            Rect(_BORDER + padding, y, _MAIN_WIDTH - _BORDER * 2 - padding * 2, _ROW_HEIGHT),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), layer=layer, z=4,
        ))
    else:
        for row, (_track_ref, display) in enumerate(page_items):
            absolute_index = page * PLAYLIST_PAGE_SIZE + row
            rect = Rect(_BORDER, y, _MAIN_WIDTH - _BORDER * 2, _ROW_HEIGHT)
            row_rects.append(rect)
            is_current = absolute_index == current_index
            if is_current:
                commands.append(RectCommand(rect, fill=COLORS["cyan"], layer=layer, z=3))
            elif row == selected:
                commands.append(RectCommand(rect, fill=UI_THEME["highlight"], layer=layer, z=3))
            prefix = "> " if row == selected else ""
            label = prefix + (("♪ " + display) if is_current else display)
            text_rect = Rect(rect.x + padding, rect.y, rect.width - padding * 2, rect.height)
            commands.append(TextCommand(
                _elide(label, text_rect.width, metrics), font, COLORS["black"], text_rect,
                alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER), layer=layer, z=4,
            ))
            y += _ROW_HEIGHT
    page_rect = None
    if has_pages:
        page_rect = Rect(_BORDER, y, _MAIN_WIDTH - _BORDER * 2, _ROW_HEIGHT)
        commands.append(TextCommand(
            f"◀ {page + 1}/{max_page + 1} ▶", font, COLORS["black"], page_rect,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER), layer=layer, z=4,
        ))

    remove_rect = play_rect = None
    if 0 <= selected < len(page_items):
        action_y = _PANEL_Y + _BORDER + selected * _ROW_HEIGHT
        remove_rect = Rect(_MAIN_WIDTH, action_y, _ACTION_WIDTH, _ACTION_WIDTH)
        play_rect = Rect(_MAIN_WIDTH + _ACTION_WIDTH, action_y, _ACTION_WIDTH, _ACTION_WIDTH)
        for name, rect, label in (
            ("remove", remove_rect, "x"),
            ("play_selected", play_rect, "▶"),
        ):
            state = "pressed" if pressed == name and hovered == name else "hover" if hovered == name else "normal"
            commands.extend(_button_commands(rect, label, font, state=state, layer=layer))

    return SpeakerPlaylistVisualDescription(
        Size(width, height), controls, progress_rect, slider_rect,
        tuple(row_rects), remove_rect, play_rect, page_rect,
        DrawBatch(tuple(commands)),
    )


__all__ = [
    "PLAYLIST_PAGE_SIZE",
    "SpeakerPlaylistVisualDescription",
    "build_speaker_playlist_visual",
    "speaker_playlist_hit_test",
]
