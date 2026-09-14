"""Backend-neutral visuals for the pet's music media panels.

The progress bar and the playlist window share the layered panel shell and the
mixed digit/UI text placement that used to be recomputed inside the Qt widgets.
The presenters here own that geometry so the hosts only execute a batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from config.scale import scale_px
from lib.core.layer import Layer

from .commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    RectCommand,
    TextAlignment,
    TextCommand,
)
from .palette import COLORS, UI_THEME
from .panel_visuals import (
    inset_rect,
    panel_frame_commands,
    panel_inset,
    panel_shell_commands,
    slider_handle_commands,
)
from .types import FontSpec, Rect, Size


class MediaTextMetrics(Protocol):
    """Low-level metrics supplied by the rendering adapter."""

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

    def ascent_for(self, text: str, *, digit: bool = False) -> float:
        """Line ascent the rasterizer will use for ``text`` (fallback aware)."""
        ...


PROGRESS_PANEL_WIDTH = scale_px(240, min_abs=1)
PROGRESS_PANEL_HEIGHT = scale_px(20, min_abs=1)
#: Snap positions / small tick marks drawn by the shared horizontal slider.
SLIDER_TICK_COUNT = 20
PLAYLIST_PAGE_SIZE = 7
PLAYLIST_ROW_HEIGHT = scale_px(20, min_abs=1)

#: Search result box: five rows per page, auto width between a floor and a cap.
SEARCH_RESULT_PAGE_SIZE = 5
SEARCH_RESULT_ROW_HEIGHT = scale_px(20, min_abs=1)
SEARCH_RESULT_MIN_WIDTH = scale_px(240, min_abs=1)
SEARCH_RESULT_MAX_WIDTH = scale_px(360, min_abs=1)
SEARCH_RESULT_MIN_TEXT_WIDTH = scale_px(60, min_abs=1)
SEARCH_RESULT_SEARCHING_TEXT = "♪ 搜索中..."
SEARCH_RESULT_EMPTY_TEXT = "(无结果，请输入关键词后搜索)"


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


def mixed_text_commands(
    rect: Rect,
    text: str,
    metrics: MediaTextMetrics,
    *,
    alignment: int = int(TextAlignment.LEFT | TextAlignment.VCENTER),
    color=COLORS["text"],
    layer: int = int(Layer.PANEL),
    z: int = 4,
    alpha: float = 1.0,
) -> list[object]:
    """Split mixed text into per-segment commands matching ``draw_mixed_text``.

    The Qt baseline measures each digit/UI run with its own font metrics and
    shares one vertical baseline; each command keeps that baseline so the
    adapters never re-derive the placement.
    """
    segments = _split_digit_segments(text)
    widths = [metrics.measure(segment, digit=is_digit) for segment, is_digit in segments]
    total = sum(widths)
    if alignment & int(TextAlignment.HCENTER):
        x = rect.x + (rect.width - total) / 2.0
    elif alignment & int(TextAlignment.RIGHT):
        x = rect.x + rect.width - total
    else:
        x = rect.x

    ascent = max(float(metrics.default_ascent), float(metrics.digit_ascent))
    descent = max(float(metrics.default_descent), float(metrics.digit_descent))
    baseline = float(int(round(rect.y + (rect.height + ascent - descent) / 2.0)))
    right_edge = rect.x + rect.width

    # The Qt baseline clips the run to its layout rectangle, so overflow from a
    # glyph with a negative side bearing must be clipped here as well.
    commands: list[object] = [ClipPush(rect)]
    for (segment, is_digit), width in zip(segments, widths):
        font = metrics.digit_font if is_digit else metrics.default_font
        segment_ascent = float(metrics.ascent_for(segment, digit=is_digit))
        x_position = int(round(x))
        top = int(round(baseline - segment_ascent))
        commands.append(TextCommand(
            segment,
            font,
            color,
            Rect(x_position, top, right_edge - x_position, rect.y + rect.height - top),
            alignment=int(TextAlignment.LEFT | TextAlignment.TOP),
            alpha=alpha,
            layer=layer,
            z=z,
        ))
        x += width
    commands.append(ClipPop())
    return commands


def elide_mixed_text(text: str, max_width: float, metrics: MediaTextMetrics) -> str:
    """Mirror ``qt_bridge.font.elide_mixed_text`` without importing Qt."""
    value = str(text or "")
    if max_width <= 0:
        return ""
    if metrics.measure(value) <= max_width:
        return value
    ellipsis = "..."
    if metrics.measure(ellipsis) > max_width:
        return ""
    kept = ""
    for char in value:
        if metrics.measure(kept + char + ellipsis) > max_width:
            break
        kept += char
    return kept + ellipsis


def mixed_text_width(text: str, metrics: MediaTextMetrics) -> float:
    """Measured advance of mixed digit/UI text, matching ``draw_mixed_text``."""
    return sum(
        metrics.measure(segment, digit=is_digit)
        for segment, is_digit in _split_digit_segments(text)
    )


@dataclass(frozen=True, slots=True)
class ProgressPanelVisual:
    size: Size
    slider_rect: Rect
    handle_rect: Rect
    separator_rect: Rect
    time_rect: Rect
    batch: DrawBatch


def build_progress_panel_visual(
    *,
    progress: float,
    time_text: str,
    metrics: MediaTextMetrics,
    width: int | None = None,
    height: int | None = None,
    layer: int = int(Layer.PANEL),
    alpha: float = 1.0,
) -> ProgressPanelVisual:
    """Build the playback progress bar shown under the playlist window."""
    width = PROGRESS_PANEL_WIDTH if width is None else max(1, int(width))
    height = PROGRESS_PANEL_HEIGHT if height is None else max(1, int(height))
    inset = panel_inset()
    border = inset * 2
    separator_width = scale_px(5, min_abs=1)
    separator_line_width = scale_px(1, min_abs=1)
    time_width = scale_px(57, min_abs=1)
    slider_width = width - border * 2 - separator_width - time_width
    outer = Rect(0, 0, width, height)
    # The Qt baseline paints the outer black frame last, so a handle parked on
    # the first pixel stays covered by the frame instead of bleeding into it.
    commands: list[object] = [
        RectCommand(
            inset_rect(outer, inset), fill=COLORS["cyan"], layer=layer, z=1, alpha=alpha,
        ),
        RectCommand(
            inset_rect(outer, border), fill=COLORS["pink"], layer=layer, z=2, alpha=alpha,
        ),
    ]

    slider = Rect(border, border, slider_width, height - border * 2)
    ratio = max(0.0, min(1.0, float(progress)))
    fill_width = int(ratio * slider.width)
    if fill_width > 0:
        commands.append(RectCommand(
            Rect(slider.x, slider.y, fill_width, slider.height),
            fill=UI_THEME["deep_cyan"],
            alpha=alpha,
            layer=layer,
            z=3,
        ))

    handle_commands, handle_rect = slider_handle_commands(
        slider.x + fill_width,
        slider,
        UI_THEME["deep_pink"],
        layer=layer,
        z=4,
        alpha=alpha,
    )
    commands.extend(handle_commands)

    separator = Rect(border + slider_width, inset, separator_width, height - inset * 2)
    commands.append(RectCommand(
        separator, fill=COLORS["cyan"], alpha=alpha, layer=layer, z=3,
    ))
    line_x = separator.x + (separator.width - separator_line_width) // 2
    commands.append(RectCommand(
        Rect(line_x, border, separator_line_width, height - border * 2),
        fill=COLORS["black"], alpha=alpha, layer=layer, z=3,
    ))

    time_rect = Rect(
        border + slider_width + separator_width,
        border,
        time_width,
        height - border * 2,
    )
    commands.append(TextCommand(
        str(time_text or ""),
        metrics.digit_font,
        COLORS["text"],
        time_rect,
        alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
        alpha=alpha,
        layer=layer,
        z=3,
    ))
    commands.extend(panel_frame_commands(outer, inset=inset, layer=layer, z=5, alpha=alpha))

    return ProgressPanelVisual(
        Size(width, height),
        slider,
        handle_rect,
        separator,
        time_rect,
        DrawBatch(tuple(commands)),
    )


@dataclass(frozen=True, slots=True)
class SliderVisual:
    """Resolved geometry and draw commands for one horizontal slider."""

    size: Size
    track_rect: Rect
    handle_rect: Rect
    tick_rects: tuple[Rect, ...]
    batch: DrawBatch


def slider_track_rect(
    *,
    x: int = 0,
    y: int = 0,
    width: int | None = None,
    height: int | None = None,
) -> Rect:
    """Return the inner track rectangle of a shared horizontal slider."""
    width = PROGRESS_PANEL_WIDTH if width is None else max(1, int(width))
    height = PROGRESS_PANEL_HEIGHT if height is None else max(1, int(height))
    border = panel_inset() * 2
    return Rect(
        int(x) + border,
        int(y) + border,
        width - border * 2,
        height - border * 2,
    )


def slider_ratio_at(track_rect: Rect, x: float) -> float:
    """Map a pointer x inside ``track_rect`` to a 0.0-1.0 slider ratio."""
    width = float(track_rect.width)
    if width <= 0:
        return 0.0
    return max(0.0, min(1.0, (float(x) - float(track_rect.x)) / width))


def snap_slider_ratio(ratio: float, steps: int = SLIDER_TICK_COUNT) -> float:
    """Snap a ratio to the nearest tick so dragging feels grainy."""
    count = max(1, int(steps))
    value = max(0.0, min(1.0, float(ratio)))
    return round(value * count) / count


def build_slider_visual(
    *,
    ratio: float,
    x: int = 0,
    y: int = 0,
    width: int | None = None,
    height: int | None = None,
    ticks: int = SLIDER_TICK_COUNT,
    layer: int = int(Layer.PANEL),
    z: int = 0,
    alpha: float = 1.0,
) -> SliderVisual:
    """Build the shared horizontal slider used by volume-style controls.

    The shell, fill, ``ticks`` small marks and the portrait handle all come from
    the same tokens as the playback progress bar, so the speaker volume slider
    and the media panels cannot drift apart. Hosts that want the notched
    "grainy" drag feel snap the ratio to ``1 / ticks``.
    """
    width = PROGRESS_PANEL_WIDTH if width is None else max(1, int(width))
    height = PROGRESS_PANEL_HEIGHT if height is None else max(1, int(height))
    origin_x = int(x)
    origin_y = int(y)
    inset = panel_inset()
    border = inset * 2
    outer = Rect(origin_x, origin_y, width, height)
    commands: list[object] = [
        RectCommand(outer, fill=COLORS["black"], alpha=alpha, layer=layer, z=z),
        RectCommand(
            inset_rect(outer, inset), fill=COLORS["cyan"], alpha=alpha, layer=layer, z=z + 1,
        ),
        RectCommand(
            inset_rect(outer, border), fill=COLORS["pink"], alpha=alpha, layer=layer, z=z + 2,
        ),
    ]

    track = slider_track_rect(x=origin_x, y=origin_y, width=width, height=height)
    value = max(0.0, min(1.0, float(ratio)))
    fill_width = int(round(value * track.width))
    if fill_width > 0:
        commands.append(RectCommand(
            Rect(track.x, track.y, fill_width, track.height),
            fill=UI_THEME["deep_cyan"],
            alpha=alpha,
            layer=layer,
            z=z + 3,
        ))

    tick_rects: list[Rect] = []
    count = max(0, int(ticks))
    if count:
        tick_width = scale_px(1, min_abs=1)
        tick_height = max(2, int(round(track.height * 0.4)))
        tick_y = track.y + (track.height - tick_height) // 2
        for index in range(1, count + 1):
            tick_x = track.x + int(round(index * track.width / count))
            tick_x = min(tick_x, track.x + track.width - tick_width)
            rect = Rect(tick_x, tick_y, tick_width, tick_height)
            tick_rects.append(rect)
            commands.append(RectCommand(
                rect,
                fill=COLORS["black"],
                alpha=0.55 * alpha,
                layer=layer,
                z=z + 3,
            ))

    handle_commands, handle_rect = slider_handle_commands(
        track.x + fill_width,
        track,
        UI_THEME["deep_pink"],
        layer=layer,
        z=z + 4,
        alpha=alpha,
    )
    commands.extend(handle_commands)
    commands.extend(panel_frame_commands(outer, inset=inset, layer=layer, z=z + 5, alpha=alpha))
    return SliderVisual(
        Size(width, height),
        track,
        handle_rect,
        tuple(tick_rects),
        DrawBatch(tuple(commands)),
    )


@dataclass(frozen=True, slots=True)
class PlaylistPanelVisual:
    size: Size
    row_rects: tuple[Rect, ...]
    page_rect: Rect | None
    batch: DrawBatch


def build_playlist_panel_visual(
    queue: tuple[tuple[object, str], ...],
    metrics: MediaTextMetrics,
    *,
    page: int = 0,
    selected: int = -1,
    current_index: int = -1,
    width: int | None = None,
    page_size: int = PLAYLIST_PAGE_SIZE,
    layer: int = int(Layer.PANEL),
    alpha: float = 1.0,
) -> PlaylistPanelVisual:
    """Build the queue window shown next to the anchored speaker."""
    queue = tuple(queue)
    width = scale_px(240, min_abs=1) if width is None else max(1, int(width))
    row_height = PLAYLIST_ROW_HEIGHT
    inset = panel_inset()
    border = inset * 2
    padding = scale_px(6, min_abs=1)

    max_page = max(0, (len(queue) - 1) // page_size)
    page = max(0, min(int(page), max_page))
    page_items = queue[page * page_size: page * page_size + page_size]
    rows = max(1, len(page_items))
    has_pages = len(queue) > page_size
    if has_pages:
        rows += 1
    height = border * 2 + rows * row_height

    outer = Rect(0, 0, width, height)
    commands, _content = panel_shell_commands(outer, layer=layer, alpha=alpha)
    content_x = border
    content_width = width - border * 2

    row_rects: list[Rect] = []
    page_rect: Rect | None = None
    y = border
    if not queue:
        text_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
        commands.append(TextCommand(
            "（队列为空）",
            metrics.default_font,
            COLORS["text"],
            text_rect,
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            alpha=alpha,
            layer=layer,
            z=4,
        ))
    else:
        page_offset = page * page_size
        for row, (_track, display) in enumerate(page_items):
            row_rect = Rect(content_x, y, content_width, row_height)
            row_rects.append(row_rect)
            text_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
            is_current = page_offset + row == current_index
            if row == selected and not is_current:
                commands.append(RectCommand(
                    row_rect, fill=UI_THEME["highlight"], alpha=alpha, layer=layer, z=3,
                ))
            if is_current:
                commands.append(RectCommand(
                    row_rect, fill=COLORS["cyan"], alpha=alpha, layer=layer, z=3,
                ))
            prefix = "> " if row == selected else ""
            label = prefix + ("♪ " + display if is_current else display)
            commands.extend(mixed_text_commands(
                text_rect,
                elide_mixed_text(label, text_rect.width, metrics),
                metrics,
                color=COLORS["text"],
                layer=layer,
                z=4,
                alpha=alpha,
            ))
            y += row_height
        if has_pages:
            page_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
            commands.extend(mixed_text_commands(
                page_rect,
                f"{page + 1}/{max_page + 1}",
                metrics,
                alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                color=COLORS["text"],
                layer=layer,
                z=4,
                alpha=alpha,
            ))

    return PlaylistPanelVisual(
        Size(width, height),
        tuple(row_rects),
        page_rect,
        DrawBatch(tuple(commands)),
    )


@dataclass(frozen=True, slots=True)
class SearchResultPanelVisual:
    """Resolved geometry and draw commands for the song search result box."""

    size: Size
    row_rects: tuple[Rect, ...]
    page_rect: Rect | None
    batch: DrawBatch


def _search_result_page_items(
    items: tuple[tuple[object, str], ...],
    page: int,
    page_size: int,
) -> tuple[tuple[object, str], ...]:
    start = max(0, int(page)) * page_size
    return items[start: start + page_size]


def search_result_panel_size(
    items: tuple[tuple[object, str], ...],
    metrics: MediaTextMetrics,
    *,
    page: int = 0,
    page_size: int = SEARCH_RESULT_PAGE_SIZE,
    searching: bool = False,
) -> Size:
    """Resolve the auto-fitted size of the search result box.

    The Qt host needs the size before it can position the window, so the width
    (driven by the widest mixed-font row) is resolved here instead of in the
    widget.
    """
    items = tuple(items)
    if searching:
        texts: list[str] = [SEARCH_RESULT_SEARCHING_TEXT]
        rows = 1
    elif not items:
        texts = [SEARCH_RESULT_EMPTY_TEXT]
        rows = 1
    else:
        page_items = _search_result_page_items(items, page, page_size)
        texts = [str(display) for _ref, display in page_items]
        rows = len(page_items)
        if len(items) > page_size:
            max_page = (len(items) - 1) // page_size
            texts.append(f"{int(page) + 1}/{max_page + 1}")
            rows += 1

    max_text_width = max(
        (mixed_text_width(text, metrics) for text in texts),
        default=float(SEARCH_RESULT_MIN_TEXT_WIDTH),
    )
    border = panel_inset() * 2
    padding = scale_px(6, min_abs=1)
    width = int(max(
        float(SEARCH_RESULT_MIN_WIDTH),
        min(float(SEARCH_RESULT_MAX_WIDTH), max_text_width + border * 2 + padding * 2),
    ))
    height = int(border * 2 + rows * SEARCH_RESULT_ROW_HEIGHT)
    return Size(width, height)


def build_search_result_panel_visual(
    size: Size,
    items: tuple[tuple[object, str], ...],
    metrics: MediaTextMetrics,
    *,
    page: int = 0,
    page_size: int = SEARCH_RESULT_PAGE_SIZE,
    selected: int = -1,
    searching: bool = False,
    layer: int = int(Layer.PANEL),
    alpha: float = 1.0,
) -> SearchResultPanelVisual:
    """Build the search result list shell, rows and page indicator."""
    items = tuple(items)
    width = float(size.width)
    height = float(size.height)
    inset = panel_inset()
    border = inset * 2
    padding = scale_px(6, min_abs=1)
    row_height = SEARCH_RESULT_ROW_HEIGHT

    outer = Rect(0, 0, width, height)
    commands, _content = panel_shell_commands(outer, layer=layer, alpha=alpha)
    content_x = border
    content_width = width - border * 2

    row_rects: list[Rect] = []
    page_rect: Rect | None = None
    y = border
    if searching:
        text_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
        # The status lines are single-run rect text: the Qt baseline lets the
        # rasterizer centre the layout (including the ``♪`` fallback ascent),
        # which a shared baseline computed from the digit/UI fonts cannot.
        commands.append(TextCommand(
            SEARCH_RESULT_SEARCHING_TEXT,
            metrics.default_font,
            UI_THEME["text"],
            text_rect,
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            alpha=alpha,
            layer=layer,
            z=4,
        ))
    elif not items:
        text_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
        commands.append(TextCommand(
            SEARCH_RESULT_EMPTY_TEXT,
            metrics.default_font,
            UI_THEME["text"],
            text_rect,
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            alpha=alpha,
            layer=layer,
            z=4,
        ))
    else:
        for row, (_ref, display) in enumerate(_search_result_page_items(items, page, page_size)):
            row_rect = Rect(content_x, y, content_width, row_height)
            row_rects.append(row_rect)
            text_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
            if row == selected:
                commands.append(RectCommand(
                    row_rect, fill=UI_THEME["highlight"], alpha=alpha, layer=layer, z=3,
                ))
            commands.extend(mixed_text_commands(
                text_rect,
                elide_mixed_text(str(display), text_rect.width, metrics),
                metrics,
                color=UI_THEME["text"],
                layer=layer,
                z=4,
                alpha=alpha,
            ))
            y += row_height
        if len(items) > page_size:
            max_page = (len(items) - 1) // page_size
            page_rect = Rect(content_x + padding, y, content_width - padding * 2, row_height)
            commands.extend(mixed_text_commands(
                page_rect,
                f"{int(page) + 1}/{max_page + 1}",
                metrics,
                alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                color=UI_THEME["text"],
                layer=layer,
                z=4,
                alpha=alpha,
            ))

    return SearchResultPanelVisual(
        Size(width, height),
        tuple(row_rects),
        page_rect,
        DrawBatch(tuple(commands)),
    )


__all__ = [
    "MediaTextMetrics",
    "PLAYLIST_PAGE_SIZE",
    "PLAYLIST_ROW_HEIGHT",
    "PROGRESS_PANEL_HEIGHT",
    "PROGRESS_PANEL_WIDTH",
    "PlaylistPanelVisual",
    "ProgressPanelVisual",
    "SEARCH_RESULT_EMPTY_TEXT",
    "SEARCH_RESULT_MAX_WIDTH",
    "SEARCH_RESULT_MIN_TEXT_WIDTH",
    "SEARCH_RESULT_MIN_WIDTH",
    "SEARCH_RESULT_PAGE_SIZE",
    "SEARCH_RESULT_ROW_HEIGHT",
    "SEARCH_RESULT_SEARCHING_TEXT",
    "SLIDER_TICK_COUNT",
    "SearchResultPanelVisual",
    "SliderVisual",
    "build_playlist_panel_visual",
    "build_progress_panel_visual",
    "build_search_result_panel_visual",
    "build_slider_visual",
    "elide_mixed_text",
    "mixed_text_commands",
    "mixed_text_width",
    "search_result_panel_size",
    "slider_ratio_at",
    "slider_track_rect",
    "snap_slider_ratio",
]
