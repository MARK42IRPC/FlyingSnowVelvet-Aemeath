"""Layout and DrawBatch construction for Dango Showdown (no GUI toolkit)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from config.font_config import get_ui_font_family
from lib.core.render.layers import Layer
from lib.core.render.visuals.commands import (
    ClipPop,
    ClipPush,
    TransformPop,
    TransformPush,
    DrawBatch,
    EllipseCommand,
    GradientStop,
    LinearGradientFill,
    LineCommand,
    PathCommand,
    PathSegment,
    RectCommand,
    SpriteCommand,
    StrokeJoin,
    TextAlignment,
    TextCommand,
)
from lib.core.render.visuals.resources import RasterFrame
from lib.core.render.visuals.types import Color, FontSpec, Point, Rect, Size

from . import constants as C
from . import roster as R
from .animator import selection_breath
from .model import MatchPhase, RoundResult
from .series import FORMATS, DEFAULT_FORMAT_LABEL

ALIGN_LEFT = int(TextAlignment.LEFT | TextAlignment.VCENTER)
ALIGN_RIGHT = int(TextAlignment.RIGHT | TextAlignment.VCENTER)
ALIGN_CENTER = int(TextAlignment.HCENTER | TextAlignment.VCENTER)
ALIGN_TOP_LEFT = int(TextAlignment.LEFT | TextAlignment.TOP)
ALIGN_WRAP_LEFT = int(TextAlignment.LEFT | TextAlignment.TOP | TextAlignment.WORD_WRAP)
ALIGN_WRAP_CENTER = int(TextAlignment.HCENTER | TextAlignment.TOP | TextAlignment.WORD_WRAP)

ACTION_FIGHT = "fight"
ACTION_NEXT = "next"
ACTION_CLOSE = "close"


@dataclass(frozen=True, slots=True)
class CharacterView:
    side: str
    character_id: str
    name: str
    hp: int = C.MAX_HP
    max_hp: int = C.MAX_HP
    meters: tuple = ()
    charges: dict[str, int] = field(default_factory=dict)
    sun_energy: tuple[str, ...] = ()
    sun_cooldowns: dict[str, float] = field(default_factory=dict)



    state: str = C.IDLE
    stretch: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)
    flash: float = 0.0
    x: float = 0.5
    y: float = 0.5
    radius: float = C.BASE_RADIUS
    flipped: bool = False
    hits_taken: int = 0
    hits_dealt: int = 0
    frost_energy: int = 0
    frost_elapsed: float = 0.0
    slow_seconds: float = 0.0
    slow_multiplier: float = 1.0

    @property
    def hp_ratio(self) -> float:
        return max(0.0, min(1.0, self.hp / float(max(1, self.max_hp))))


@dataclass(frozen=True, slots=True)
class ScreenSnapshot:
    phase: MatchPhase = MatchPhase.SELECT
    picked_side: str = ""
    bet_side: str = ""
    left_id: str = ""
    right_id: str = ""
    bet_amount: int = 0
    can_start: bool = False
    countdown: float = 0.0
    characters: tuple[CharacterView, ...] = ()
    record: dict[str, int] = field(default_factory=dict)
    round_index: int = 0
    result: RoundResult | None = None
    paused: bool = False
    round_duration: float = 0.0
    draw: bool = False
    sun_spirits: tuple = ()
    # Series bookkeeping for the header and the fighter-card win rows.
    series_left: int = 0
    series_right: int = 0
    series_label: str = ""
    series_round: int = 0
    series_over: bool = False
    series_winner: str = ""
    # Caster side of the last skill; its card glows while the window runs.
    skill_flash_side: str = ""
    auto_advance_seconds: float | None = None
    presentation_seconds: float = 0.0
    roster_page: int = 0


CANVAS_WIDTH = 1080.0
CANVAS_HEIGHT = 720.0


@dataclass(frozen=True, slots=True)
class CanvasTransform:
    """Maps the virtual 1080x720 canvas onto the real widget rect."""

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    @classmethod
    def fit(cls, width: float, height: float) -> "CanvasTransform":
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        scale = min(width / CANVAS_WIDTH, height / CANVAS_HEIGHT)
        return cls(scale, (width - CANVAS_WIDTH * scale) / 2.0, (height - CANVAS_HEIGHT * scale) / 2.0)

    def to_local(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.offset_x, y * self.scale + self.offset_y


@dataclass(frozen=True, slots=True)
class ArenaRect:
    left: float
    top: float
    side: float

    @property
    def rect(self) -> Rect:
        return Rect(self.left, self.top, self.side, self.side)


@dataclass(slots=True)
class ButtonHit:
    action: str
    rect: Rect
    value: str = ""

    def contains(self, x: float, y: float) -> bool:
        return (
            self.rect.x <= x <= self.rect.right
            and self.rect.y <= y <= self.rect.bottom
        )


@dataclass(slots=True)
class HitMap:
    buttons: list[ButtonHit] = field(default_factory=list)
    arena: ArenaRect = ArenaRect(0.0, 0.0, 1.0)

    def hit_test(self, x: float, y: float) -> ButtonHit | None:
        for button in self.buttons:
            if button.contains(x, y):
                return button
        return None

    def clear(self) -> None:
        self.buttons.clear()


@dataclass(frozen=True, slots=True)
class FrameSource:
    resource_id: str
    revision: int
    frame: RasterFrame
    frame_index: int = 0


def _font(size: float, bold: bool = False) -> FontSpec:
    """Project UI face; the Qt host registers the bundled HarmonyOS Sans SC."""
    return FontSpec(get_ui_font_family(), max(8, int(round(size))), bold)


def _digit_font(size: float, bold: bool = False) -> FontSpec:
    """Keep every in-game number on the same downloaded HarmonyOS family."""
    return FontSpec(get_ui_font_family(), max(8, int(round(size))), bold)


def _charge_bar(cmds: list, rect: Rect, value: int, slots: int, color: Color) -> None:
    """Discrete meter slots below HP; empty slots stay dark."""
    slots = max(1, int(slots))
    gap = 3.0
    width = max(1.0, (rect.width - gap * (slots - 1)) / slots)
    filled = max(0, min(slots, int(value)))
    for index in range(slots):
        slot = Rect(rect.x + index * (width + gap), rect.y, width, rect.height)
        cmds.append(_rounded(slot, color if index < filled else C.C_CHARGE_SLOT, 1.0, color.with_alpha(120), 1.0))


def _rounded(rect: Rect, fill: Color | None, radius: float, stroke: Color | None = None, width: float = 1.0) -> RectCommand:
    return RectCommand(rect=rect, fill=fill, radius=radius, stroke=stroke, stroke_width=width, antialias=True)


def _text(text: str, rect: Rect, font: FontSpec, color: Color, alignment: int = ALIGN_LEFT, alpha: float = 1.0) -> TextCommand:
    return TextCommand(text=text, font=font, color=color, rect=rect, alignment=alignment, alpha=alpha)


def plate_vertices(rect: Rect, cut: float) -> tuple[Point, ...]:
    """Six hard corners: top-left and bottom-right are chamfered."""
    c = max(0.0, min(float(cut), rect.width * 0.5, rect.height * 0.5))
    return (
        Point(rect.x + c, rect.y),
        Point(rect.right, rect.y),
        Point(rect.right, rect.bottom - c),
        Point(rect.right - c, rect.bottom),
        Point(rect.x, rect.bottom),
        Point(rect.x, rect.y + c),
    )


def plate_path(rect: Rect, cut: float, *, closed: bool = True) -> tuple[PathSegment, ...]:
    points = plate_vertices(rect, cut)
    pairs = list(zip(points, points[1:] + points[:1]))
    if not closed:
        pairs = pairs[:-1]
    return tuple(PathSegment(a, b) for a, b in pairs)


def shape_points(rect: Rect, cut: float, *, closed: bool = True) -> tuple[Point, ...]:
    """Vertices in draw order; ``closed`` only documents that the last closes back."""
    del closed
    return plate_vertices(rect, cut)


def plate(
    rect: Rect,
    fill: Color | LinearGradientFill | None,
    cut: float,
    stroke: Color | None = None,
    width: float = 1.5,
    *,
    z: int = 0,
    alpha: float = 1.0,
) -> PathCommand:
    """Chamfered sci-fi plate: the workhorse panel shape of this game."""
    return PathCommand(
        plate_path(rect, cut),
        fill=fill,
        stroke=stroke,
        stroke_width=width,
        stroke_join=int(StrokeJoin.MITER),
        antialias=True,
        alpha=alpha,
        layer=Layer.PANEL,
        z=z,
    )


def slanted(
    rect: Rect,
    skew: float,
    fill: Color | LinearGradientFill | None,
    cut: float = 0.0,
    stroke: Color | None = None,
    width: float = 1.5,
    *,
    z: int = 0,
) -> PathCommand:
    """Parallelogram control (tabs, chips, buttons) leaning like a HUD readout."""
    s = float(skew)
    c = max(0.0, min(float(cut), rect.height * 0.4))
    points = (
        Point(rect.x + s, rect.y),
        Point(rect.right + s, rect.y),
        Point(rect.right - s, rect.bottom),
        Point(rect.right - s - c, rect.bottom),
        Point(rect.x - s + c, rect.bottom),
        Point(rect.x - s, rect.bottom),
    )
    return PathCommand(
        tuple(PathSegment(a, b) for a, b in zip(points, points[1:] + points[:1])),
        fill=fill,
        stroke=stroke,
        stroke_width=width,
        stroke_join=int(StrokeJoin.MITER),
        antialias=True,
        layer=Layer.PANEL,
        z=z,
    )


def corner_marks(cmds: list, rect: Rect, color: Color, length: float = 14.0, width: float = 2.0, z: int = 3) -> None:
    """Four L-shaped targeting brackets, a sci-fi HUD staple."""
    length = min(length, rect.width * 0.3, rect.height * 0.3)
    corners = (
        (Point(rect.x, rect.y), Point(rect.x + length, rect.y), Point(rect.x, rect.y + length)),
        (Point(rect.right, rect.y), Point(rect.right - length, rect.y), Point(rect.right, rect.y + length)),
        (Point(rect.x, rect.bottom), Point(rect.x + length, rect.bottom), Point(rect.x, rect.bottom - length)),
        (Point(rect.right, rect.bottom), Point(rect.right - length, rect.bottom), Point(rect.right, rect.bottom - length)),
    )
    for corner, horizontal, vertical in corners:
        cmds.append(LineCommand(corner, horizontal, color, width=width, layer=Layer.PANEL, z=z))
        cmds.append(LineCommand(corner, vertical, color, width=width, layer=Layer.PANEL, z=z))


def segment_bar(cmds: list, rect: Rect, segments: int, color: Color, *, z: int = 3) -> None:
    """Parallel slashes: decorative telemetry, reads as geometry not data."""
    segments = max(1, int(segments))
    gap = 3.0
    width = max(1.0, (rect.width - gap * (segments - 1)) / segments)
    for index in range(segments):
        bar = Rect(rect.x + index * (width + gap), rect.y + index * 2.0, width, max(2.0, rect.height - index * 2.0))
        cmds.append(slanted(bar, 2.0, color, z=z))


def _section_label(cmds: list, text: str, rect: Rect, color: Color, *, size: float = 15.0, accent: Color | None = None) -> None:
    """Bold section heading with a short accent tick in front."""
    tick = Rect(rect.x, rect.y + rect.height * 0.22, 3.0, rect.height * 0.56)
    cmds.append(RectCommand(rect=tick, fill=accent or C.C_ACCENT, layer=Layer.PANEL, z=3))
    cmds.append(_text(text, Rect(rect.x + 10.0, rect.y, rect.width - 10.0, rect.height), _font(size, True), color, ALIGN_LEFT))


def _paint_arena(cmds: list, rect: Rect) -> None:
    """Duel chamber: a bare black plate; the crest alone marks the arena.

    Deliberately no rulers, reticles, diamonds or inner frames: they competed
    with the fight and made the chamber look busy.
    """
    cmds.append(plate(rect, C.C_ARENA, 14.0, C.C_ARENA_EDGE.with_alpha(170), 1.5))


def _paint_backdrop(cmds: list, width: float, height: float) -> None:
    cmds.extend(_backdrop_commands(float(width), float(height)))


@lru_cache(maxsize=4)
def _backdrop_commands(width: float, height: float) -> tuple:
    """Deep-metal gradient, crisp blueprint grid, angular hull light."""
    cmds: list = []
    layer = Layer.PANEL
    cmds.append(RectCommand(rect=Rect(0, 0, width, height), fill=LinearGradientFill(
        start=Point(0, 0),
        end=Point(width * 0.25, height),
        stops=(
            GradientStop(0.0, C.C_BG_TOP),
            GradientStop(0.55, C.C_BG_BOTTOM),
            GradientStop(1.0, C.C_BG_TOP),
        ),
    ), layer=layer))
    # Large slanted plates give depth without noise or soft glows.
    cmds.append(PathCommand(
        (
            PathSegment(Point(0.0, 0.0), Point(width * 0.42, 0.0)),
            PathSegment(Point(width * 0.42, 0.0), Point(width * 0.18, height)),
            PathSegment(Point(width * 0.18, height), Point(0.0, height)),
            PathSegment(Point(0.0, height), Point(0.0, 0.0)),
        ),
        fill=C.C_BG_GEO,
        layer=layer,
    ))
    cmds.append(PathCommand(
        (
            PathSegment(Point(width * 0.80, 0.0), Point(width, 0.0)),
            PathSegment(Point(width, 0.0), Point(width, height)),
            PathSegment(Point(width, height), Point(width * 0.62, height)),
            PathSegment(Point(width * 0.62, height), Point(width * 0.80, 0.0)),
        ),
        fill=C.C_BG_GEO.with_alpha(30),
        layer=layer,
    ))
    minor = 24.0
    major = minor * 5.0
    for x in range(1, int(width // minor) + 1):
        px = x * minor
        color = C.C_GRID_MAJOR if x % 5 == 0 else C.C_GRID
        cmds.append(RectCommand(rect=Rect(px, 0.0, 1.0, height), fill=color, layer=layer))
    for y in range(1, int(height // minor) + 1):
        py = y * minor
        color = C.C_GRID_MAJOR if y % 5 == 0 else C.C_GRID
        cmds.append(RectCommand(rect=Rect(0.0, py, width, 1.0), fill=color, layer=layer))
    for x, y in ((major * 2, major), (major * 5, major * 2), (major * 4, height - major), (width - major * 2, major * 3)):
        size = 9.0
        cmds.append(RectCommand(rect=Rect(x - 1.0, y - size, 2.0, size * 2.0), fill=C.C_GRID_MAJOR, layer=layer))
        cmds.append(RectCommand(rect=Rect(x - size, y - 1.0, size * 2.0, 2.0), fill=C.C_GRID_MAJOR, layer=layer))
    inset = 8.0
    corner_marks(cmds, Rect(inset, inset, width - inset * 2.0, height - inset * 2.0), C.C_PANEL_EDGE.with_alpha(110), 26.0, 2.0)
    return tuple(cmds)


def _paint_header(cmds: list, hit: HitMap, layout: Layout, snapshot: ScreenSnapshot) -> None:
    """Top command bar: title block, round plate, streak readout, close key."""
    header = layout.header_rect
    cmds.append(plate(
        header,
        LinearGradientFill(
            start=header.top_left,
            end=Point(header.right, header.y),
            stops=(
                GradientStop(0.0, C.C_PANEL_LIFTED),
                GradientStop(0.65, C.C_PANEL),
                GradientStop(1.0, C.C_PANEL_DEEP),
            ),
        ),
        12.0,
        C.C_PANEL_EDGE,
        1.4,
    ))
    cmds.append(RectCommand(rect=Rect(header.x + 12.0, header.y + 2.0, header.width - 24.0, 2.0), fill=C.C_ACCENT.with_alpha(180), layer=Layer.PANEL, z=2))
    mark = Rect(header.x + 18.0, header.y + 12.0, 4.0, header.height - 24.0)
    cmds.append(slanted(Rect(mark.x, mark.y, 22.0, mark.height), 4.0, C.C_ACCENT, z=2))
    cmds.append(_text("团子大决战", Rect(header.x + 46.0, header.y, header.width * 0.34, 34.0), _font(28.0, True), C.C_TEXT, ALIGN_LEFT))
    cmds.append(_text("DANGO SHOWDOWN", Rect(header.x + 48.0, header.y + 36.0, header.width * 0.34, 12.0), _font(9.0, True), C.C_TEXT_FAINT, ALIGN_LEFT))

    title_end = header.x + 46.0 + text_width_estimate("团子大决战", 28.0)
    close_rect = Rect(header.right - 46.0, header.y + 9.0, 34.0, header.height - 18.0)
    stats_w = 240.0
    round_rect = Rect(
        title_end + 14.0,
        header.y + 9.0,
        max(96.0, close_rect.x - 12.0 - stats_w - 8.0 - (title_end + 14.0)),
        header.height - 18.0,
    )
    if snapshot.phase is MatchPhase.SELECT:
        round_text = "选择出战团子"
    elif snapshot.series_label:
        round_text = f"第 {max(1, int(snapshot.round_index))} 轮 · {snapshot.series_label}"
    else:
        round_text = f"第 {max(1, int(snapshot.round_index))} 轮"
    cmds.append(slanted(round_rect, 4.0, C.C_BG_TOP.with_alpha(200), 4.0, C.C_PANEL_EDGE_BRIGHT.with_alpha(140), 1.0))
    cmds.append(_text(round_text, round_rect, _fit_text(round_text, round_rect, max_size=18.0, min_size=10.0), C.C_ACCENT, ALIGN_CENTER))

    record = snapshot.record or {}
    chips = int(record.get("chips", 0))
    streak = int(record.get("current_streak", 0))
    best = int(record.get("best_streak", 0))
    streak_rect = Rect(close_rect.x - 8.0 - stats_w, header.y + 9.0, stats_w, header.height - 18.0)
    cmds.append(slanted(streak_rect, 4.0, C.C_BG_TOP.with_alpha(200), 4.0, C.C_PANEL_EDGE_BRIGHT.with_alpha(140), 1.0))
    # Labels and values own separate rows, including long-lived save records.
    cell_w = streak_rect.width / 3.0
    cells = (
        ("连续押胜", str(streak), C.C_GOLD),
        ("最高", str(best), C.C_TEXT),
        ("筹码", str(chips), C.C_GREEN if chips >= 0 else C.C_RED),
    )
    for index, (label, value, value_color) in enumerate(cells):
        cell = Rect(streak_rect.x + index * cell_w + 10.0, streak_rect.y, cell_w - 20.0, streak_rect.height)
        label_rect = Rect(cell.x, cell.y + 1.0, cell.width, 13.0)
        value_rect = Rect(cell.x, label_rect.bottom, cell.width, cell.bottom - label_rect.bottom)
        cmds.append(_text(label, label_rect, _font(9.0, True), C.C_TEXT_DIM, ALIGN_CENTER))
        cmds.append(_text(value, value_rect, _fit_text(value, value_rect, max_size=14.0, min_size=8.0), value_color, ALIGN_CENTER))
        if index:
            cmds.append(RectCommand(rect=Rect(streak_rect.x + index * cell_w, streak_rect.y + 9.0, 1.5, streak_rect.height - 18.0), fill=C.C_PANEL_EDGE, layer=Layer.PANEL, z=2))

    cmds.append(plate(close_rect, C.C_RED.with_alpha(36), 8.0, C.C_RED.with_alpha(200), 1.5, z=2))
    cmds.append(_text("×", close_rect, _font(22.0, True), C.C_RED, ALIGN_CENTER))
    hit.buttons.append(ButtonHit(ACTION_CLOSE, close_rect))


class Layout:
    """Splits the 1080x720 canvas into header, three columns and footer."""

    def __init__(self, width: float = CANVAS_WIDTH, height: float = CANVAS_HEIGHT) -> None:
        self.width = float(width)
        self.height = float(height)
        self.pad = 20.0
        self.gap = 14.0
        self.header_rect = Rect(self.pad, self.pad, self.width - self.pad * 2.0, 52.0)
        self.footer_rect = Rect(self.pad, self.height - self.pad - 98.0, self.width - self.pad * 2.0, 98.0)
        content_top = self.header_rect.bottom + self.gap
        content_bottom = self.footer_rect.y - self.gap
        self.content_rect = Rect(self.pad, content_top, self.width - self.pad * 2.0, content_bottom - content_top)
        self.side_width = 236.0
        arena_width = self.content_rect.width - self.side_width * 2.0 - self.gap * 2.0
        side = max(80.0, min(arena_width, self.content_rect.height))
        arena_left = self.content_rect.x + self.side_width + self.gap + (arena_width - side) / 2.0
        arena_top = self.content_rect.y + (self.content_rect.height - side) / 2.0
        self.arena = ArenaRect(arena_left, arena_top, side)
        self.left_panel = Rect(self.content_rect.x, arena_top, self.side_width, side)
        self.right_panel = Rect(self.content_rect.right - self.side_width, arena_top, self.side_width, side)

    @property
    def center_x(self) -> float:
        return self.width / 2.0


HP_BAR_HEIGHT = 10.0


def _hp_bar(cmds: list, rect: Rect, ratio: float) -> None:
    """Hard HP strip: dark trough, one uniform bright signal-red drain."""
    cmds.append(_rounded(rect, C.C_HP_BACK, 2.0, C.C_PANEL_INNER, 1.0))
    inner = Rect(rect.x + 2.0, rect.y + 2.0, rect.width - 4.0, rect.height - 4.0)
    if ratio <= 0.0:
        return
    fill = C.C_HP_FILL_LOW if ratio <= 0.25 else C.C_HP_FILL
    cmds.append(_rounded(Rect(inner.x, inner.y, inner.width * max(0.0, min(1.0, ratio)), inner.height), fill, 1.0))


def blend_color(color: Color, base: Color, alpha: float) -> Color:
    """Tint ``base`` with ``color`` at ``alpha`` (0..1): no ad-hoc colours."""
    amount = max(0.0, min(1.0, float(alpha)))
    return Color(
        round(color.red * amount + base.red * (1.0 - amount)),
        round(color.green * amount + base.green * (1.0 - amount)),
        round(color.blue * amount + base.blue * (1.0 - amount)),
        base.alpha,
    )


def _side_badge(cmds: list, rect: Rect, label: str, color: Color, *, alpha: int = 150) -> None:
    cmds.append(slanted(rect, 3.0, color.with_alpha(alpha), 3.0, color, 1.2, z=3))
    cmds.append(_text(label, Rect(rect.x + 3.0, rect.y, rect.width - 3.0, rect.height), _font(min(15.0, rect.height * .68), True), C.C_TEXT, ALIGN_CENTER))


def _character_card(rect: Rect, view: CharacterView, *, picked: bool, accent: Color, layout: Layout, portrait: FrameSource | None = None, wins: int | None = None, flash: bool = False, series_label: str = "") -> list:
    """A compact fighter dossier: portrait, vitality, ability and series wins."""
    del layout
    ui = min(1.0, rect.height / 502.0)
    cmds: list = []
    entry = R.find_entry(view.character_id)
    identity = _accent(entry) if entry is not None else accent
    edge = C.C_CARD_FLASH if flash else (accent if picked else C.C_PANEL_EDGE)
    cmds.append(plate(rect, LinearGradientFill(
        start=rect.top_left, end=Point(rect.right, rect.bottom),
        stops=(GradientStop(0.0, blend_color(identity, C.C_PANEL_LIFTED, .14)),
               GradientStop(.48, C.C_PANEL), GradientStop(1.0, C.C_PANEL_DEEP))),
        18.0, edge, 2.6 if picked or flash else 1.2))
    if flash:
        cmds.append(RectCommand(rect=rect, fill=C.C_CARD_FLASH.with_alpha(46), layer=Layer.PANEL, z=1))
    inner = Rect(rect.x + 15.0, rect.y + 12.0 * ui, rect.width - 30.0, rect.height - 24.0 * ui)
    cmds.append(RectCommand(rect=Rect(inner.x, rect.y + 2.0, inner.width, 3.0), fill=identity.with_alpha(210), layer=Layer.PANEL, z=3))
    _side_badge(cmds, Rect(inner.x, rect.y + 9.0 * ui, 43.0 * ui, 23.0 * ui), "1P" if view.side == "left" else "2P", C.C_BADGE_A if view.side == "left" else C.C_BADGE_B)
    role_rect = Rect(inner.x + 48.0 * ui, rect.y + 9.0 * ui, inner.width - 48.0 * ui, 23.0 * ui)
    cmds.append(_text("押注方" if picked else "决斗者", role_rect, _font(max(8.0, 13.0 * ui), True), accent if picked else C.C_TEXT_DIM, ALIGN_RIGHT))

    head = min(inner.width * .64, 126.0 * ui)
    head_rect = Rect(rect.center.x - head / 2.0, rect.y + 41.0 * ui, head, head)
    cmds.append(plate(head_rect, identity.with_alpha(22), 16.0 * ui, identity.with_alpha(160), 1.1, z=2))
    corner_marks(cmds, head_rect, identity.with_alpha(155), 14.0 * ui, 1.4)
    if portrait is not None:
        cmds.append(_sprite(portrait, Rect(head_rect.x + 3, head_rect.y + 3, head - 6, head - 6), layer=Layer.PANEL, z=2))
    name_rect = Rect(inner.x, head_rect.bottom + 7.0 * ui, inner.width, 30.0 * ui)
    cmds.append(_text(view.name, name_rect, _fit_text(view.name, name_rect, max_size=25.0 * ui, min_size=12.0), C.C_TEXT, ALIGN_CENTER))
    tag_rect = Rect(inner.x, name_rect.bottom, inner.width, 18.0 * ui)
    if entry is not None:
        tag = entry.tag
        if view.slow_seconds > 0:
            tag = f"减速{1 - view.slow_multiplier:.0%} · {view.slow_seconds:.1f}秒"
        cmds.append(_text(tag, tag_rect, _fit_text(tag, tag_rect, max_size=12.0 * ui, min_size=8.0),
                          C.C_SUN_CYAN if view.slow_seconds > 0 else identity, ALIGN_CENTER))

    hp = Rect(inner.x, tag_rect.bottom + 9.0 * ui, inner.width, 32.0 * ui)
    label = Rect(hp.x, hp.y, hp.width * .25, hp.height)
    value = Rect(label.right, hp.y, hp.width * .48, hp.height)
    maximum = Rect(value.right, hp.y, hp.width * .27, hp.height)
    cmds.append(_text("生命", label, _font(max(8.0, 13.0 * ui), True), C.C_TEXT_DIM))
    cmds.append(_text(str(view.hp), value, _font(32.0 * ui, True), C.C_HP_FILL, ALIGN_RIGHT))
    cmds.append(_text(f"/{view.max_hp}", maximum, _font(max(8.0, 14.0 * ui), True), C.C_TEXT_DIM, ALIGN_RIGHT))
    bar = Rect(inner.x, hp.bottom + 3.0 * ui, inner.width, HP_BAR_HEIGHT)
    _hp_bar(cmds, bar, view.hp_ratio)

    ability_y = bar.bottom + 13.0 * ui
    step = 38.0 * ui
    if entry is not None and entry.sun_spirit is not None:
        ability_y = _paint_sun_meter(cmds, inner, ability_y, view, entry.sun_spirit, ui)
    if entry is not None and entry.frost_slash is not None:
        ability_y = _paint_frost_meter(cmds, inner, ability_y, view, entry.frost_slash, ui)
    for spec in view.meters:
        color = C.C_CHARGE_BLUE if spec.meter_id == "starlight" else C.C_CHARGE_PINK
        row = Rect(inner.x, ability_y, inner.width, step)
        label_rect = Rect(row.x, row.y, row.width * .65, step * .48)
        count_rect = Rect(label_rect.right, row.y, row.width * .35, step * .48)
        cmds.append(_text(spec.label, label_rect, _font(max(8.0, 14.0 * ui), True), color))
        cmds.append(_text(f"{view.charges.get(spec.meter_id, 0)}/{spec.slots}", count_rect, _font(max(8.0, 14.0 * ui), True), color, ALIGN_RIGHT))
        _charge_bar(cmds, Rect(row.x, row.y + step * .63, row.width, max(4.0, 7.0 * ui)), view.charges.get(spec.meter_id, 0), spec.slots, color)
        ability_y += step

    if not view.meters and entry is not None and entry.collision_rules:
        rule = entry.collision_rules[0]
        chance = getattr(rule, "chance", None)
        if chance is not None:
            title = {"void_hand": "虚空之手"}.get(str(getattr(rule, "skill", "")), "碰撞技能")
            cmds.append(_text(title, Rect(inner.x, ability_y, inner.width, 20.0 * ui), _font(max(9.0, 14.0 * ui), True), identity))
            ability_y += 25.0 * ui
            chance_label = f"{float(chance) * 100:.10f}".rstrip("0").rstrip(".") + "%"
            values = (("触发概率", chance_label),)
            spread = getattr(rule, "damage_range", None)
            damage = f"{spread[0]}~{spread[1]}" if spread else str(getattr(rule, "damage", 0))
            values += (("技能伤害", damage),)
            cell_w = (inner.width - 8.0 * ui) / 2.0
            for index, (caption, value) in enumerate(values):
                cell = Rect(inner.x + index * (cell_w + 8.0 * ui), ability_y, cell_w, 66.0 * ui)
                cmds.append(plate(cell, identity.with_alpha(9), 7.0 * ui, identity.with_alpha(70), .8))
                cmds.append(_text(caption, Rect(cell.x, cell.y + 6.0 * ui, cell.width, 16.0 * ui), _font(max(8.0, 11.0 * ui), True), C.C_TEXT_DIM, ALIGN_CENTER))
                cmds.append(_text(value, Rect(cell.x, cell.y + 25.0 * ui, cell.width, 30.0 * ui), _font(27.0 * ui, True), identity, ALIGN_CENTER))
            ability_y += 76.0 * ui

    tally = Rect(inner.x, rect.bottom - 58.0 * ui, inner.width, 24.0 * ui)
    details = Rect(inner.x, ability_y + 8.0 * ui, inner.width, max(0.0, tally.y - ability_y - 18.0 * ui))
    if entry is not None and details.height >= 18.0 and (view.meters or entry.collision_rules or entry.skill.strip()):
        cmds.append(plate(details, C.C_BG_TOP.with_alpha(135), 6.0, C.C_PANEL_INNER, .8, z=1))
        lines = [f"{spec.slots} {spec.label} → {'激光' if spec.fill_meter else '全场'} {spec.damage} 伤害" for spec in view.meters]
        if entry.sun_spirit is not None:
            sun = entry.sun_spirit
            charge_hint = f"同色×{sun.cost} · 撞墙{sun.wall_chance:.0%}充能"
            if len(view.sun_energy) >= sun.slots and sun.overflow_cooldown_reduction > 0:
                charge_hint = f"满槽再获能 · 双冷却−{sun.overflow_cooldown_reduction:g}秒"
            lines = [charge_hint,
                     f"{sun.delay:g}秒爆 · 黄{sun.damage} / 青{sun.damage}+{sun.second_damage}"]
        elif entry.frost_slash is not None:
            frost = entry.frost_slash
            lines = [f"{frost.interval:g}秒环斩{frost.damage} · 命中减速{1 - frost.slow_multiplier:.0%}",
                     f"落空+1能量 · 碰撞耗{frost.energy_per_contact}增伤{frost.contact_bonus * frost.energy_per_contact}"]
        elif not lines:
            lines = ["每次碰撞或撞墙独立判定"] if entry.collision_rules else [entry.skill]
        line_h = (details.height - 8.0 * ui) / max(1, len(lines))
        for index, line in enumerate(lines):
            line_rect = Rect(details.x + 8.0, details.y + 4.0 * ui + index * line_h, details.width - 16.0, line_h)
            cmds.append(_text(line, line_rect, _fit_text(line, line_rect, max_size=12.0 * ui, min_size=8.0), C.C_TEXT_DIM))
    _paint_win_tally(cmds, tally, wins, identity, series_label)
    hits = Rect(inner.x, rect.bottom - 27.0 * ui, inner.width, 17.0 * ui)
    cmds.append(RectCommand(rect=Rect(inner.x, hits.y - 5.0 * ui, inner.width, 1.0), fill=C.C_PANEL_INNER, layer=Layer.PANEL))
    summary = f"命中 {view.hits_dealt}    受击 {view.hits_taken}"
    cmds.append(_text(summary, hits, _fit_text(summary, hits, max_size=12.0 * ui, min_size=8.0), C.C_TEXT_DIM, ALIGN_CENTER))
    return cmds


def sun_color(color: str) -> Color:
    return C.C_SUN_YELLOW if color == "yellow" else C.C_SUN_CYAN


def _paint_frost_meter(cmds: list, inner: Rect, y: float, view: CharacterView, spec, ui: float) -> float:
    color = C.C_SUN_CYAN
    cmds.append(_text("霜渐能量", Rect(inner.x, y, inner.width * .7, 20 * ui),
                      _font(14 * ui, True), color))
    cmds.append(_text(f"{view.frost_energy}/{spec.energy_slots}",
                      Rect(inner.x + inner.width * .7, y, inner.width * .3, 20 * ui),
                      _font(14 * ui, True), color, ALIGN_RIGHT))
    _charge_bar(cmds, Rect(inner.x, y + 25 * ui, inner.width, 8 * ui),
                view.frost_energy, spec.energy_slots, color)
    y += 43 * ui
    cell = Rect(inner.x, y, inner.width, 43 * ui)
    cmds.append(plate(cell, color.with_alpha(9), 5 * ui, color.with_alpha(70), .8))
    label = Rect(cell.x + 7 * ui, cell.y + 5 * ui, cell.width * .58, 25 * ui)
    cmds.append(_text("环斩冷却", label, _font(12 * ui, True), color))
    remaining = max(0.0, spec.interval - view.frost_elapsed)
    value = "就绪" if remaining <= 0 else f"{remaining:.1f}秒"
    cmds.append(_text(value, Rect(label.right, label.y, cell.right - label.right - 7 * ui, label.height),
                      _font(16 * ui, True), C.C_TEXT, ALIGN_RIGHT))
    bar = Rect(cell.x + 7 * ui, cell.bottom - 6 * ui, cell.width - 14 * ui, 2 * ui)
    cmds.append(RectCommand(rect=bar, fill=C.C_PANEL_INNER, layer=Layer.PANEL))
    progress = max(0.0, min(1.0, view.frost_elapsed / max(.01, spec.interval)))
    if progress:
        cmds.append(RectCommand(rect=Rect(bar.x, bar.y, bar.width * progress, bar.height), fill=color, layer=Layer.PANEL))
    return y + 50 * ui


def _paint_sun_meter(cmds: list, inner: Rect, y: float, view: CharacterView, spec, ui: float) -> float:
    """One shared four-cell inventory followed by two independent clocks."""
    cmds.append(_text("日灵能量", Rect(inner.x, y, inner.width * .7, 20 * ui),
                      _font(14 * ui, True), C.C_CARD_FLASH))
    cmds.append(_text(f"{len(view.sun_energy)}/{spec.slots}",
                      Rect(inner.x + inner.width * .7, y, inner.width * .3, 20 * ui),
                      _font(14 * ui, True), C.C_TEXT_DIM, ALIGN_RIGHT))
    y += 25 * ui
    gap = 6 * ui
    width = (inner.width - (spec.slots - 1) * gap) / spec.slots
    for index in range(spec.slots):
        color = sun_color(view.sun_energy[index]) if index < len(view.sun_energy) else None
        cell = Rect(inner.x + index * (width + gap), y, width, 17 * ui)
        cmds.append(plate(cell, color.with_alpha(45) if color else C.C_CHARGE_SLOT,
                          4 * ui, color if color else C.C_PANEL_EDGE, 1.0))
        if color is not None:
            cmds.append(RectCommand(rect=Rect(cell.x + 3 * ui, cell.y + 3 * ui, cell.width - 6 * ui, 3 * ui),
                                    fill=color.with_alpha(210), layer=Layer.PANEL, z=3))
            r = 3 * ui
            cmds.append(EllipseCommand(rect=Rect(cell.center.x - r, cell.center.y - r, r * 2, r * 2),
                                       fill=color, antialias=True, layer=Layer.PANEL, z=3))
    y += 26 * ui
    half = (inner.width - gap) / 2
    for index, (key, name) in enumerate((("yellow", "黄日灵"), ("cyan", "青日灵"))):
        color = sun_color(key)
        cooldown = max(0.0, float(view.sun_cooldowns.get(key, 0.0)))
        cell = Rect(inner.x + index * (half + gap), y, half, 45 * ui)
        cmds.append(plate(cell, color.with_alpha(10), 4 * ui, color.with_alpha(70), .8))
        label = Rect(cell.x + 4 * ui, cell.y + 3 * ui, cell.width - 8 * ui, 15 * ui)
        cmds.append(_text(name, label, _font(11 * ui, True), color, ALIGN_CENTER))
        ready = "就绪" if cooldown <= 0 else f"{cooldown:.1f}秒"
        cmds.append(_text(ready, Rect(label.x, label.bottom, label.width, 18 * ui),
                          _font(13 * ui, True), C.C_TEXT if cooldown <= 0 else C.C_TEXT_DIM, ALIGN_CENTER))
        bar = Rect(cell.x + 4 * ui, cell.bottom - 5 * ui, cell.width - 8 * ui, 2 * ui)
        cmds.append(RectCommand(rect=bar, fill=C.C_PANEL_INNER, layer=Layer.PANEL, z=2))
        progress = max(0.0, min(1.0, 1.0 - cooldown / max(.01, spec.cooldown)))
        if progress:
            cmds.append(RectCommand(rect=Rect(bar.x, bar.y, bar.width * progress, bar.height),
                                    fill=color, layer=Layer.PANEL, z=3))
    return y + 52 * ui


def _paint_win_tally(cmds: list, row: Rect, wins: int | None, accent: Color, series_label: str) -> None:
    """Round wins under a fighter card: filled pips out of the format target."""
    if wins is None:
        return
    count = max(0, int(wins))
    if series_label == "五轮三胜":
        slots = 3
    elif series_label == "三局两胜":
        slots = 2
    else:
        slots = 1
    slots = max(slots, min(count, 5))
    label_rect = Rect(row.x, row.y, row.width * 0.52, row.height)
    label = "胜场" if not series_label else f"胜场 · {series_label}"
    cmds.append(_text(label, label_rect, _fit_text(label, label_rect, max_size=11.0, min_size=8.0), C.C_TEXT_FAINT, ALIGN_LEFT))
    dot = min(12.0, row.height * 0.52)
    step = dot + 6.0
    total = slots * dot + (slots - 1) * (step - dot)
    start = row.right - total
    for index in range(slots):
        pip = Rect(start + index * step, row.center.y - dot / 2.0, dot, dot)
        filled = index < count
        cmds.append(RectCommand(
            rect=pip,
            fill=(accent if filled else C.C_PANEL_EDGE_BRIGHT.with_alpha(60)),
            layer=Layer.PANEL,
            z=3,
        ))


def paint_snapshot(
    snapshot: ScreenSnapshot,
    width: float,
    height: float,
    *,
    sprites: dict[str, FrameSource] | None = None,
    flashes: dict[str, FrameSource] | None = None,
    flash: float = 0.0,
    zoom: float = 1.0,
    shake: tuple[float, float] = (0.0, 0.0),
    watermark: FrameSource | None = None,
    feedback: DrawBatch | None = None,
) -> tuple[DrawBatch, HitMap]:
    """Build one paint pass; mutates and returns the caller's ``HitMap`` owner."""
    sprites = sprites or {}
    flashes = flashes or {}
    layout = Layout(width, height)
    hit = HitMap(arena=layout.arena)
    hit.clear()
    layer = Layer.PANEL
    cmds = []

    _paint_backdrop(cmds, width, height)
    _paint_header(cmds, hit, layout, snapshot)

    left_view, right_view = _views(snapshot)
    if snapshot.phase is MatchPhase.SELECT:
        _paint_select_screen(cmds, hit, layout, snapshot, sprites)
        return DrawBatch(commands=tuple(cmds)), hit
    cmds.extend(_character_card(
        layout.left_panel, left_view, picked=snapshot.bet_side == "left", accent=C.C_SIDE_A, layout=layout,
        portrait=sprites.get(left_view.character_id), wins=snapshot.series_left if snapshot.series_label else None,
        flash=snapshot.skill_flash_side == "left" and snapshot.phase is MatchPhase.FIGHT,
        series_label=snapshot.series_label,
    ))
    cmds.extend(_character_card(
        layout.right_panel, right_view, picked=snapshot.bet_side == "right", accent=C.C_SIDE_B, layout=layout,
        portrait=sprites.get(right_view.character_id), wins=snapshot.series_right if snapshot.series_label else None,
        flash=snapshot.skill_flash_side == "right" and snapshot.phase is MatchPhase.FIGHT,
        series_label=snapshot.series_label,
    ))

    arena_rect = layout.arena.rect
    sx, sy = shake
    arena_rect = Rect(arena_rect.x + sx, arena_rect.y + sy, arena_rect.width, arena_rect.height)
    _paint_arena(cmds, arena_rect)
    inner = Rect(arena_rect.x + 2.0, arena_rect.y + 2.0, arena_rect.width - 4.0, arena_rect.height - 4.0)
    # The chamber is deliberately bare: only a faint crest behind the fight.
    _paint_watermark(cmds, inner, watermark)

    loser = ""
    if snapshot.phase is MatchPhase.RESULT and snapshot.result is not None and not snapshot.result.draw:
        loser = snapshot.result.stats.loser
    # Keep casts anchored on their victim even at a wall; clipping, rather
    # than moving the decal, protects the header and adjacent fighter cards.
    cmds.append(ClipPush(inner))
    for view in (left_view, right_view):
        px = inner.x + view.x * inner.width
        py = inner.y + view.y * inner.height
        diameter = max(18.0, view.radius * 2.0 * inner.width * C.SPRITE_DIAMETER_RATIO)
        source = sprites.get(view.character_id)
        if source is not None:
            m11, m12, m21, m22 = view.stretch
            stretch_scale_x = math.hypot(m11, m21)
            stretch_scale_y = math.hypot(m12, m22)
            # The loser stays pinned in its hurt pose; the arena hands it a
            # bit of extra room over the lower wall so a flattened sprite is
            # still fully visible in the chamber.
            flash_source = flashes.get(view.character_id) if view.flash > 0.0 else None
            anchor_y = py
            if view.side == loser:
                # The loser is a wide, flat pancake: squash harder than the
                # live hurt wobble and keep its centre above the chamber floor.
                stretch_scale_x, stretch_scale_y = stretch_scale_x * 1.14, stretch_scale_y * 0.74
                anchor_y = min(py, inner.bottom - max(18.0, diameter * stretch_scale_y) * 0.5 - 1.0)
            w = diameter * stretch_scale_x
            h = diameter * stretch_scale_y
            cmds.append(SpriteCommand(
                resource_id=source.resource_id,
                resource_revision=source.revision,
                frame_index=source.frame_index,
                frame=source.frame,
                position=Point(px - w / 2.0, anchor_y - h / 2.0),
                alpha=1.0,
                flipped=view.flipped,
                scale=stretch_scale_x,
                layer=layer,
                z=2,
                order=0,
                target_size=Size(w, h),
            ))
            if flash_source is not None:
                cmds.append(SpriteCommand(
                    resource_id=flash_source.resource_id,
                    resource_revision=flash_source.revision,
                    frame_index=flash_source.frame_index,
                    frame=flash_source.frame,
                    position=Point(px - w / 2.0, anchor_y - h / 2.0),
                    alpha=max(0.0, min(1.0, view.flash)),
                    flipped=view.flipped,
                    scale=stretch_scale_x,
                    layer=layer,
                    z=3,
                    order=0,
                    target_size=Size(w, h),
                ))
        else:
            cmds.append(EllipseCommand(rect=Rect(px - diameter / 2.0, py - diameter / 2.0, diameter, diameter), fill=C.C_SIDE_A if view.side == "left" else C.C_SIDE_B, antialias=True, layer=layer, z=2))

    _paint_sun_spirits(cmds, inner, snapshot.sun_spirits)
    try:
        flash_value = max(0.0, min(1.0, float(flash or 0.0)))
    except (TypeError, ValueError):
        flash_value = 0.0
    if flash_value > 0.0:
        _paint_impact_flash(cmds, inner, flash_value)
    if feedback is not None:
        cmds.extend(feedback.commands)
    cmds.append(ClipPop())
    if snapshot.phase is MatchPhase.FIGHT and snapshot.countdown > 0.0:
        _paint_banner(cmds, arena_rect, "准备…", f"{max(1, int(math.ceil(snapshot.countdown)))}")
    elif snapshot.phase is MatchPhase.RESULT:
        if snapshot.result is None:
            # Three-second beat between the KO and the card: the arena holds
            # its frozen pose so the loser's hurt picture is readable.
            _paint_result_hold(cmds, arena_rect)
        else:
            _paint_result(cmds, hit, layout, snapshot)
    if snapshot.paused:
        _paint_banner(cmds, arena_rect, "暂停", "P 继续")

    _paint_footer(cmds, layout, snapshot)
    return DrawBatch(commands=tuple(cmds), resource_revisions=feedback.resource_revisions if feedback else ()), hit



def _paint_watermark(cmds: list, arena: Rect, source: FrameSource | None) -> None:
    """A large translucent crest, without any circular backing shadow."""
    if source is None:
        return
    size = min(arena.width, arena.height) * C.ARENA_WATERMARK_SIZE_RATIO
    rect = Rect(arena.center.x - size / 2.0, arena.center.y - size / 2.0, size, size)
    cmds.append(ClipPush(arena))
    cmds.append(_sprite(source, rect, layer=Layer.PANEL, z=1, alpha=C.ARENA_WATERMARK_ALPHA))
    cmds.append(ClipPop())


@dataclass(frozen=True, slots=True)
class SunSpiritVisual:
    """An event-driven decal: only an actual BURST changes its stage."""

    summon_id: str
    color: str
    point: tuple[float, float]
    radius: float = R.SunSpiritSpec().radius
    age: float = 0.0
    delay: float = R.SunSpiritSpec().delay
    second_delay: float = R.SunSpiritSpec().second_delay
    second_radius: float = R.SunSpiritSpec().second_radius
    stage: int = 0
    stage_age: float = 0.0
    cancelled: bool = False


def _sun_ring(cmds: list, center: Point, radius: float, color: Color, alpha: int,
              width: float = 1.0, *, fill: int = 0, z: int = 8) -> None:
    cmds.append(EllipseCommand(
        rect=Rect(center.x - radius, center.y - radius, radius * 2, radius * 2),
        fill=color.with_alpha(fill) if fill else None,
        stroke=color.with_alpha(alpha), stroke_width=width,
        antialias=True, layer=Layer.PANEL, z=z,
    ))


def _sun_arc(cmds: list, center: Point, radius: float, start: float, extent: float,
             color: Color, width: float = 2.0) -> None:
    count = max(2, int(abs(extent) * 13))
    points = tuple(Point(center.x + math.cos(start + extent * i / count) * radius,
                         center.y + math.sin(start + extent * i / count) * radius)
                   for i in range(count + 1))
    cmds.append(PathCommand(segments=tuple(PathSegment(a, b) for a, b in zip(points, points[1:])),
                            stroke=color, stroke_width=width, antialias=True,
                            layer=Layer.PANEL, z=9))


def _paint_sun_spirits(cmds: list, arena: Rect, spirits: tuple) -> None:
    """Show exact gameplay radii and fuse progress; shared scripts own VFX."""
    for spirit in spirits:
        color = sun_color(spirit.color)
        center = Point(arena.x + spirit.point[0] * arena.width, arena.y + spirit.point[1] * arena.height)
        radius = spirit.radius * arena.width
        waiting = spirit.stage == 0
        echo = spirit.color == "cyan" and spirit.stage == 1 and not spirit.cancelled
        if waiting or echo:
            phase = min(1.0, (spirit.age / max(.01, spirit.delay)) if waiting else
                        spirit.stage_age / max(.01, spirit.second_delay))
            warning = radius if waiting else spirit.second_radius * arena.width
            _sun_ring(cmds, center, warning, color, int(72 + 65 * phase),
                      1.0 + phase, fill=8, z=7)
            _sun_arc(cmds, center, warning - 3, -math.pi / 2,
                     math.tau * max(.015, 1.0 - phase), color.with_alpha(210), 2.4)
            # Static marks make the actual gameplay boundary easy to read.
            for index in range(6):
                theta = index * math.tau / 6
                start = Point(center.x + math.cos(theta) * (warning + 3), center.y + math.sin(theta) * (warning + 3))
                end = Point(center.x + math.cos(theta) * (warning + 9), center.y + math.sin(theta) * (warning + 9))
                cmds.append(LineCommand(start=start, end=end, color=color.with_alpha(130), width=1.4,
                                        layer=Layer.PANEL, z=8))
        if spirit.stage > 0 and spirit.stage_age < C.SUN_BURST_EFFECT_SECONDS:
            # Confirm the exact resolved radius briefly. Decorative explosions
            # are the desktop's burst_line/star_streak instances in FeedbackScene.
            progress = spirit.stage_age / C.SUN_BURST_EFFECT_SECONDS
            blast_radius = (spirit.second_radius if spirit.stage == 2 else spirit.radius) * arena.width
            _sun_ring(cmds, center, blast_radius, color, int(200 * (1 - progress)), 2.0, z=12)




def _paint_impact_flash(cmds: list, arena: Rect, strength: float) -> None:
    """Ordinary camera flash; particle/image scripts provide the impact VFX."""
    value = max(0.0, min(1.0, float(strength)))
    cmds.append(RectCommand(rect=arena, fill=Color(248, 228, 244, int(110 * value)),
                            layer=Layer.PANEL, z=14))


def _settlement_order(snapshot: ScreenSnapshot) -> tuple[CharacterView, CharacterView]:
    """Backed dango first: the settled card always shows it on the left."""
    left, right = _views(snapshot)
    if snapshot.bet_side == "right":
        return right, left
    return left, right


def _name_for_side(snapshot: ScreenSnapshot, side: str) -> str:
    for view in _views(snapshot):
        if view.side == side:
            return view.name
    return "团子"


def _views(snapshot: ScreenSnapshot) -> tuple[CharacterView, CharacterView]:
    if len(snapshot.characters) >= 2:
        return snapshot.characters[0], snapshot.characters[1]
    left = R.find_entry(snapshot.left_id) or R.ROSTER[0]
    right = R.find_entry(snapshot.right_id) or R.ROSTER[-1]
    return (
        CharacterView("left", left.character_id, left.name, hp=left.max_hp, max_hp=left.max_hp, meters=left.meters),
        CharacterView("right", right.character_id, right.name, hp=right.max_hp, max_hp=right.max_hp, meters=right.meters),
    )


def _sprite(source: FrameSource, rect: Rect, *, layer: int = Layer.PANEL, z: int = 2, alpha: float = 1.0) -> SpriteCommand:
    return SpriteCommand(
        resource_id=source.resource_id,
        resource_revision=source.revision,
        frame_index=source.frame_index,
        frame=source.frame,
        position=Point(rect.x, rect.y),
        alpha=alpha,
        flipped=False,
        scale=rect.width / max(1.0, float(source.frame.width)),
        layer=layer,
        z=z,
        order=0,
        target_size=Size(rect.width, rect.height),
    )


def _accent(entry: R.DangoEntry) -> Color:
    return Color(*entry.accent)


@dataclass(frozen=True, slots=True)
class RosterGridLayout:
    """Selection geometry shared by drawing, pagination and pointer handling."""
    top: Rect
    bay: Rect
    header: Rect
    grid: Rect
    footer: Rect
    tile: float
    gap: float
    columns: int
    rows: int

    @property
    def capacity(self) -> int:
        return self.columns * self.rows


def roster_grid_layout(width: float = CANVAS_WIDTH, height: float = CANVAS_HEIGHT) -> RosterGridLayout:
    layout = Layout(width, height)
    ui = min(layout.width / CANVAS_WIDTH, layout.height / CANVAS_HEIGHT)
    available = Rect(layout.pad, layout.header_rect.bottom + layout.gap,
                     layout.width - layout.pad * 2.0,
                     layout.height - layout.pad - layout.header_rect.bottom - layout.gap)
    gap = 8.0 * ui
    inner_w = max(1.0, available.width - 36.0 * ui)
    columns = max(1, min(12, int((inner_w + gap) / max(1.0, 56.0 * ui + gap))))
    tile = max(16.0, min(76.0 * ui, (inner_w - gap * (columns - 1)) / columns))
    header_h, footer_h = 50.0 * ui, 22.0 * ui
    rows = 3
    grid_h = rows * tile + (rows - 1) * gap
    bay_h = header_h + grid_h + footer_h + 24.0 * ui
    bay = Rect(available.x, available.bottom - bay_h, available.width, bay_h)
    top = Rect(available.x, available.y, available.width, max(1.0, bay.y - available.y - 14.0 * ui))
    occupied_w = columns * tile + (columns - 1) * gap
    grid = Rect(bay.center.x - occupied_w / 2.0, bay.y + header_h + 12.0 * ui, occupied_w, grid_h)
    header = Rect(bay.x + 18.0 * ui, bay.y + 5.0 * ui, bay.width - 36.0 * ui, header_h)
    footer = Rect(header.x, grid.bottom + 6.0 * ui, header.width, footer_h)
    return RosterGridLayout(top, bay, header, grid, footer, tile, gap, columns, rows)


def roster_page_count(count: int | None = None, width: float = CANVAS_WIDTH, height: float = CANVAS_HEIGHT) -> int:
    total = len(R.ROSTER) if count is None else max(0, int(count))
    capacity = roster_grid_layout(width, height).capacity
    return max(1, (total + capacity - 1) // capacity)


def _paint_select_screen(cmds: list, hit: HitMap, layout: Layout, snapshot: ScreenSnapshot, sprites: dict[str, FrameSource]) -> None:
    """Compact fighter previews above a full-width, paginated character matrix."""
    left_entry = R.find_entry(snapshot.left_id) or R.ROSTER[0]
    right_entry = R.find_entry(snapshot.right_id) or R.ROSTER[-1]
    ui = min(layout.width / CANVAS_WIDTH, layout.height / CANVAS_HEIGHT)
    roster = roster_grid_layout(layout.width, layout.height)
    top = roster.top
    channel = 16.0 * ui
    card_w = min(312.0 * ui, top.width * .30)
    for side, label, entry, color, x in (
        ("left", "1P", left_entry, C.C_SIDE_A, top.x),
        ("right", "2P", right_entry, C.C_SIDE_B, top.right - card_w),
    ):
        _paint_fighter_plate(cmds, hit, Rect(x, top.y, card_w, top.height), side, label,
            entry, color, snapshot.bet_side == side, sprites,
            wins=snapshot.series_left if side == "left" else snapshot.series_right,
            series_label=snapshot.series_label, clock=snapshot.presentation_seconds)
    console = Rect(top.x + card_w + channel, top.y, top.width - card_w * 2.0 - channel * 2.0, top.height)
    _paint_console(cmds, hit, console, snapshot, (left_entry.name, right_entry.name), ui)
    _paint_roster_bay(cmds, hit, roster, snapshot, sprites, left_entry, right_entry)


def _fighter_plate_metrics(card: Rect) -> dict:
    """Compact preview grid; the matrix, rather than the posters, owns the screen."""
    ui = min(card.height / 260.0, card.width / 312.0)
    pad = 16.0 * ui
    head = 112.0 * ui
    portrait = Rect(card.x + pad, card.y + 44.0 * ui, head, head)
    info_x = portrait.right + 12.0 * ui
    width = max(12.0, card.right - pad - info_x)
    name = Rect(info_x, card.y + 49.0 * ui, width, 30.0 * ui)
    tag = Rect(info_x, name.bottom + 2.0 * ui, width, 18.0 * ui)
    hp = Rect(info_x, tag.bottom + 10.0 * ui, width, 29.0 * ui)
    tally_h = 17.0 * ui
    tally_y = card.bottom - 27.0 * ui
    skill_y = portrait.bottom + 13.0 * ui
    skill = Rect(card.x + pad, skill_y, card.width - pad * 2.0, max(16.0, tally_y - skill_y - 8.0 * ui))
    return {"head": portrait, "name": name, "tag": tag, "hp": hp, "skill": skill,
            "pad_x": pad, "ui": ui, "tally_y": tally_y, "tally_h": tally_h}


def text_width_estimate(text: str, size: float) -> float:
    """Worst-case glyph advance (wide CJK ~1.0em, Latin ~0.62em) without a toolkit."""
    total = 0.0
    for ch in str(text):
        total += 1.0 if ord(ch) > 0x2E7F else 0.62
    return total * float(size) * 1.04


def _fit_text(text: str, rect: Rect, *, max_size: float, bold: bool = True, min_size: float = 10.0) -> FontSpec:
    """Shrink a single line until it fits ``rect`` (never paints past its edge)."""
    size = max(min_size, max_size)
    while size > min_size and text_width_estimate(text, size) > rect.width:
        size -= 1.0
    return _font(size, bold)


def wrap_lines(text: str, width: float, size: float) -> list[str]:
    """Greedy CJK/Latin wrap; the renderer draws each line explicitly, so a
    backend without word wrap still cannot spill text outside the card."""
    lines: list[str] = []
    current = ""
    for ch in str(text):
        candidate = current + ch
        if current and text_width_estimate(candidate, size) > width:
            lines.append(current)
            current = ch.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def wrapped_text(cmds: list, text: str, rect: Rect, color: Color, *, max_size: float, min_size: float = 9.0, bold: bool = True) -> None:
    """Largest bold size whose explicit line block fits ``rect`` exactly."""
    size = max(min_size, max_size)
    while True:
        lines = wrap_lines(text, rect.width, size)
        line_h = size * 1.3
        if len(lines) * line_h <= rect.height or size <= min_size:
            break
        size -= 1.0
    line_h = min(size * 1.3, rect.height / max(1, len(lines)))
    for index, line in enumerate(lines):
        y = rect.y + index * line_h
        if y + line_h > rect.bottom + 0.5:
            break
        cmds.append(_text(line, Rect(rect.x, y, rect.width, line_h), _font(size, bold), color, ALIGN_TOP_LEFT))


def _paint_fighter_plate(cmds: list, hit: HitMap, card: Rect, side: str, label: str,
                         entry: R.DangoEntry, color: Color, active: bool,
                         sprites: dict[str, FrameSource], *, wins: int = 0,
                         series_label: str = "", clock: float = 0.0) -> None:
    """A character poster with enough scale for the artwork to carry the screen."""
    accent = _accent(entry)
    metrics = _fighter_plate_metrics(card)
    ui = metrics["ui"]
    cmds.append(plate(card, LinearGradientFill(
        start=card.top_left, end=Point(card.right, card.bottom),
        stops=(GradientStop(0.0, blend_color(accent, C.C_SELECT_CARD, .26 if active else .15)),
               GradientStop(.58, C.C_PANEL), GradientStop(1.0, C.C_PANEL_DEEP))),
        18.0 * ui, accent if active else C.C_PANEL_EDGE, 2.4 if active else 1.2))
    hit.buttons.append(ButtonHit(ACTION_FIGHT, card, side))
    pad = metrics["pad_x"]
    cmds.append(RectCommand(rect=Rect(card.x + pad, card.y + 2.0, card.width - pad * 2.0, 3.0), fill=accent.with_alpha(210), layer=Layer.PANEL, z=3))
    _side_badge(cmds, Rect(card.x + pad, card.y + 12.0 * ui, 48.0 * ui, 24.0 * ui), label, C.C_BADGE_A if side == "left" else C.C_BADGE_B)
    role = "已选为押注方" if active else "点击卡片押注"
    role_rect = Rect(card.x + pad + 58.0 * ui, card.y + 12.0 * ui, card.width - pad * 2.0 - 58.0 * ui, 24.0 * ui)
    cmds.append(_text(role, role_rect, _fit_text(role, role_rect, max_size=12.0 * ui, min_size=9.0), accent if active else C.C_TEXT_DIM, ALIGN_RIGHT))
    head = metrics["head"]
    # A quiet geometric halo lives on the card, never in the duel chamber.
    phase = float(clock) * 1.7 + (0.0 if side == "left" else .7)
    pulse = .5 + .5 * math.sin(phase)
    cmds.append(EllipseCommand(rect=head, fill=accent.with_alpha(round(10 + 5 * pulse)), stroke=accent.with_alpha(round(55 + 25 * pulse)), stroke_width=1.0, antialias=True, layer=Layer.PANEL, z=1))
    corner_marks(cmds, Rect(head.x - 5.0, head.y + 5.0, head.width + 10.0, head.height - 10.0), accent.with_alpha(150), 18.0 * ui, 1.6)
    portrait = sprites.get(entry.character_id)
    if portrait is not None:
        # Sprite geometry stays integral and cacheable. The transform carries
        # fractional movement and breathing through Qt's integer pixmap rect.
        scale_x, scale_y, lift = selection_breath(clock, 0.0 if side == "left" else .7)
        local_w, local_h = max(1, round(head.width)), max(1, round(head.height))
        cmds.append(TransformPush((scale_x, 0.0, 0.0, scale_y,
            head.center.x - local_w * scale_x * .5,
            head.center.y - local_h * scale_y * .5 + lift * ui)))
        cmds.append(_sprite(portrait, Rect(0.0, 0.0, local_w, local_h), layer=Layer.PANEL, z=2))
        cmds.append(TransformPop())
    name = metrics["name"]
    cmds.append(_text(entry.name, name, _fit_text(entry.name, name, max_size=28.0 * ui, min_size=15.0), C.C_TEXT, ALIGN_CENTER))
    tag = metrics["tag"]
    cmds.append(_text(entry.tag or "战术单位", tag, _font(max(9.0, 12.0 * ui), True), accent, ALIGN_CENTER))
    hp = metrics["hp"]
    label_rect = Rect(hp.x, hp.y, hp.width * .5, hp.height)
    value_rect = Rect(label_rect.right, hp.y, hp.width * .5, hp.height)
    cmds.append(_text("初始生命", label_rect, _font(max(9.0, 12.0 * ui), True), C.C_TEXT_DIM))
    cmds.append(_text(str(entry.max_hp), value_rect, _font(24.0 * ui, True), C.C_HP_FILL, ALIGN_RIGHT))
    _hp_bar(cmds, Rect(hp.x, hp.bottom + 3.0 * ui, hp.width, 7.0 * ui), 1.0)
    if entry.skill.strip():
        skill = metrics["skill"]
        cmds.append(RectCommand(rect=Rect(skill.x, skill.y + 1.0, 2.0, max(8.0, skill.height - 2.0)), fill=accent.with_alpha(170), layer=Layer.PANEL, z=3))
        text_rect = Rect(skill.x + 10.0 * ui, skill.y, skill.width - 10.0 * ui, skill.height)
        wrapped_text(cmds, entry.skill, text_rect, C.C_TEXT_DIM, max_size=13.0 * ui, min_size=9.0)
    _paint_win_tally(cmds, Rect(card.x + pad, metrics["tally_y"], card.width - pad * 2.0, metrics["tally_h"]), wins, accent, series_label)


def owner_size(rect: Rect) -> float:
    """Name text may use the row height, but never more than ~30 px."""
    return max(14.0, min(30.0, rect.height * 0.95))


def _paint_console(cmds: list, hit: HitMap, col: Rect, snapshot: ScreenSnapshot, names: tuple[str, str], ui: float = 1.0) -> None:
    """Keep betting in a compact stack so the roster can occupy three rows."""
    unit = min(ui, col.height / 260.0)
    pad = 18.0 * unit
    inner = Rect(col.x + pad, col.y, col.width - pad * 2.0, col.height)
    cmds.append(plate(col, LinearGradientFill(start=col.top_left, end=Point(col.x, col.bottom),
        stops=(GradientStop(0.0, C.C_PANEL_LIFTED), GradientStop(.5, C.C_BG_TOP), GradientStop(1.0, C.C_PANEL_DEEP))),
        14.0, C.C_PANEL_EDGE, 1.2))
    cmds.append(_text("对 决", Rect(inner.x, col.y + 9.0 * unit, inner.width, 32.0 * unit), _font(26.0 * unit, True), C.C_TEXT, ALIGN_CENTER))
    label = Rect(inner.x, col.y + 45.0 * unit, inner.width * .30, 26.0 * unit)
    target_rect = Rect(label.right, label.y, inner.width * .70, label.height)
    target = (names[0] if snapshot.picked_side == "left" else names[1]) if snapshot.picked_side else "未选择"
    cmds.append(_text("押注给", label, _font(max(9.0, 12.0 * unit), True), C.C_TEXT_DIM))
    cmds.append(_text(target, target_rect, _fit_text(target, target_rect, max_size=22.0 * unit, min_size=12.0), C.C_ACCENT if snapshot.picked_side else C.C_TEXT_FAINT, ALIGN_RIGHT))
    chips_y = col.y + 87.0 * unit
    gap = 9.0 * unit
    chip_w = (inner.width - gap * 2.0) / 3.0
    for index, amount in enumerate(C.BET_AMOUNTS):
        chip = Rect(inner.x + index * (chip_w + gap), chips_y, chip_w, 41.0 * unit)
        active = snapshot.bet_amount == amount
        cmds.append(slanted(chip, 6.0 * unit, C.C_GOLD.with_alpha(62 if active else 9), 6.0 * unit,
                            C.C_GOLD if active else C.C_PANEL_EDGE, 2.0 if active else 1.0, z=3))
        cmds.append(_text(str(amount), chip, _font(23.0 * unit, True), C.C_GOLD if active else C.C_TEXT_DIM, ALIGN_CENTER))
        hit.buttons.append(ButtonHit(ACTION_FIGHT, chip, f"amount:{amount}"))
    wallet = Rect(inner.x, col.y + 141.0 * unit, inner.width, 22.0 * unit)
    chips = int((snapshot.record or {}).get("chips", 0))
    label_rect = Rect(wallet.x, wallet.y, wallet.width * .5, wallet.height)
    value_rect = Rect(label_rect.right, wallet.y, wallet.width * .5, wallet.height)
    cmds.append(_text("当前筹码", label_rect, _font(max(9.0, 12.0 * unit), True), C.C_TEXT_DIM))
    cmds.append(_text(str(chips), value_rect, _fit_text(str(chips), value_rect, max_size=18.0 * unit, min_size=9.0), C.C_GREEN if chips >= 0 else C.C_RED, ALIGN_RIGHT))
    rules = Rect(inner.x, col.y + 169.0 * unit, inner.width, 24.0 * unit)
    cmds.append(_text("押中 +1倍 · 押错 −1倍 · 平局不变", rules, _fit_text("押中 +1倍 · 押错 −1倍 · 平局不变", rules, max_size=10.0 * unit, min_size=8.0), C.C_TEXT_DIM, ALIGN_CENTER))
    start = Rect(inner.x, col.bottom - 57.0 * unit, inner.width, 37.0 * unit)
    _paint_start(cmds, hit, start, snapshot)
    hint = Rect(inner.x, start.bottom + 4.0 * unit, inner.width, 12.0 * unit)
    cmds.append(_text("回车 开赛 · R 随机 · 系列赛自动续局", hint, _fit_text("回车 开赛 · R 随机 · 系列赛自动续局", hint, max_size=9.0 * unit, min_size=8.0), C.C_TEXT_FAINT, ALIGN_CENTER))


def _paint_format_picker(cmds: list, hit: HitMap, rect: Rect, snapshot: ScreenSnapshot, ui: float = 1.0) -> None:
    """Series selector: three options in one band; locked while a series runs."""
    locked = bool(snapshot.series_label) and bool(snapshot.series_round) and not snapshot.series_over
    active_label = snapshot.series_label or _series_label()
    formats = [fmt for fmt in FORMATS if fmt.format_id in ("bo5", "bo3", "bo1")]
    gap = round(8.0 * ui, 1)
    button_w = (rect.width - gap * (len(formats) - 1)) / len(formats)
    for index, fmt in enumerate(formats):
        button = Rect(rect.x + index * (button_w + gap), rect.y, button_w, rect.height)
        active = fmt.label == active_label
        if locked:
            edge = C.C_ACCENT if active else C.C_PANEL_EDGE.with_alpha(70)
            fill_alpha = 112 if active else 18
        else:
            edge = C.C_ACCENT if active else C.C_PANEL_EDGE_BRIGHT.with_alpha(140)
            fill_alpha = 112 if active else 26
        cmds.append(slanted(button, 6.0, C.C_ACCENT.with_alpha(fill_alpha), 6.0, edge, 1.8 if active else 1.0, z=3))
        color = C.C_TEXT if active else (C.C_TEXT_FAINT if locked else C.C_TEXT_DIM)
        cmds.append(_text(fmt.label, button, _fit_text(fmt.label, button, max_size=min(15.0 * ui, button.height * 0.62), min_size=9.0), color, ALIGN_CENTER))
        if not locked:
            hit.buttons.append(ButtonHit(ACTION_FIGHT, button, "format:" + fmt.format_id))


def _active_format_id(label: str) -> str:
    for fmt in FORMATS:
        if fmt.label == label:
            return fmt.format_id
    return FORMATS[0].format_id


def _series_label() -> str:
    return DEFAULT_FORMAT_LABEL


def _paint_console_rules(cmds: list, rect: Rect, snapshot: ScreenSnapshot, ui: float = 1.0) -> None:
    """Rules and payout in two stacked boxes that exactly fill ``rect``."""
    cmds.append(plate(rect, C.C_PANEL_DEEP.with_alpha(190), 8.0, C.C_PANEL_EDGE.with_alpha(120), 1.0, z=2))
    header_h = round(19.0 * ui, 1)
    payout_w = max(96.0, round(150.0 * ui, 1))
    text_w = rect.width - payout_w - 26.0
    _section_label(cmds, "对战协议", Rect(rect.x + 12.0, rect.y + 4.0, rect.width - 24.0, header_h), C.C_TEXT, size=13.0 * ui)
    lines = tuple(wrap_lines("团子自动碰撞，摸到即扣血；生命归零的一方落败", max(60.0, text_w), min(12.0 * ui, 12.0)))
    line_h = min(round(20.0 * ui, 1), (rect.height - header_h - 10.0) / len(lines))
    for index, line in enumerate(lines):
        y = rect.y + header_h + 6.0 + index * line_h
        if y + line_h > rect.bottom - 2.0:
            break
        cmds.append(_text(line, Rect(rect.x + 12.0, y, text_w, line_h), _font(min(12.0 * ui, line_h * 0.8), True), C.C_TEXT_DIM, ALIGN_LEFT))
    payout = Rect(rect.right - payout_w, rect.y, payout_w, rect.height)
    mid = rect.y + rect.height * 0.5
    cmds.append(RectCommand(rect=Rect(payout.x - 6.0, rect.y + 6.0, 1.0, rect.height - 12.0), fill=C.C_PANEL_INNER, layer=Layer.PANEL, z=3))
    _section_label(cmds, "筹码倍率", Rect(payout.x, rect.y + 4.0, payout.width - 8.0, header_h), C.C_TEXT, size=13.0 * ui, accent=C.C_GOLD)
    rows = (("押中", "+1×", C.C_GREEN), ("押错", "-1×", C.C_RED))
    for index, (label, value, color) in enumerate(rows):
        y = rect.y + header_h + 6.0 + index * ((rect.height - header_h - 10.0) / 2.0)
        text = f"{label} {value}"
        cmds.append(_text(text, Rect(payout.x, y, payout.width - 10.0, round(18.0 * ui, 1)), _fit_text(text, Rect(payout.x, y, payout.width - 10.0, 18.0), max_size=14.0 * ui, min_size=10.0), color, ALIGN_LEFT))
    _ = snapshot, mid


def _paint_roster_header(cmds: list, hit: HitMap, geometry: RosterGridLayout, snapshot: ScreenSnapshot, page: int, pages: int) -> None:
    header = geometry.header
    ui = geometry.tile / 76.0
    title = Rect(header.x, header.y + 2.0 * ui, 145.0 * ui, 25.0 * ui)
    _section_label(cmds, "团子列表", title, C.C_TEXT, size=20.0 * ui)
    hint = Rect(title.x, title.bottom + 1.0 * ui, 155.0 * ui, 14.0 * ui)
    cmds.append(_text("点击头像 · 更换出战团子", hint, _font(max(8.0, 10.0 * ui), True), C.C_TEXT_DIM))
    nav_w = 230.0 * ui
    nav_x = header.right - nav_w
    picker = Rect(header.x + 165.0 * ui, header.y + 5.0 * ui, max(60.0, nav_x - header.x - 185.0 * ui), 33.0 * ui)
    _paint_format_picker(cmds, hit, picker, snapshot, ui)
    button_w = 57.0 * ui
    for caption, target, x, enabled in (("上一页", page - 1, nav_x, page > 0),
                                        ("下一页", page + 1, header.right - button_w, page + 1 < pages)):
        button = Rect(x, picker.y, button_w, picker.height)
        cmds.append(plate(button, C.C_ACCENT.with_alpha(18 if enabled else 3), 6.0 * ui,
                          C.C_PANEL_EDGE if enabled else C.C_PANEL_INNER, 1.0))
        cmds.append(_text(caption, button, _font(max(8.0, 11.0 * ui), True), C.C_TEXT if enabled else C.C_TEXT_FAINT, ALIGN_CENTER))
        if enabled:
            hit.buttons.append(ButtonHit(ACTION_FIGHT, button, f"roster_page:{target}"))
    page_rect = Rect(nav_x + button_w + 3.0 * ui, picker.y, nav_w - button_w * 2.0 - 6.0 * ui, picker.height)
    cmds.append(_text(f"{page + 1} / {pages}", page_rect, _font(max(9.0, 15.0 * ui), True), C.C_ACCENT, ALIGN_CENTER))


@lru_cache(maxsize=256)
def _empty_roster_tile(cell: Rect, ui: float) -> tuple:
    """Static empty slots share immutable commands across presentation frames."""
    color = C.C_PANEL_EDGE.with_alpha(80)
    return (
        plate(cell, C.C_BG_TOP.with_alpha(70), 6.0 * ui, C.C_PANEL_EDGE.with_alpha(45), .8),
        RectCommand(rect=Rect(cell.center.x - 6.0 * ui, cell.center.y - .5, 12.0 * ui, 1.0), fill=color, layer=Layer.PANEL, z=2),
        RectCommand(rect=Rect(cell.center.x - .5, cell.center.y - 6.0 * ui, 1.0, 12.0 * ui), fill=color, layer=Layer.PANEL, z=2),
    )


def _paint_roster_bay(cmds: list, hit: HitMap, geometry: RosterGridLayout,
                      snapshot: ScreenSnapshot, sprites: dict[str, FrameSource],
                      left_entry: R.DangoEntry, right_entry: R.DangoEntry) -> None:
    """A stable 12 by 3 matrix; unfilled cells reserve room for future fighters."""
    bay, grid = geometry.bay, geometry.grid
    tile, gap = geometry.tile, geometry.gap
    ui = tile / 76.0
    # The geometry is the capacity source even when callers render a small canvas.
    pages = max(1, (len(R.ROSTER) + geometry.capacity - 1) // geometry.capacity)
    page = max(0, min(pages - 1, int(snapshot.roster_page)))
    cmds.append(plate(bay, C.C_PANEL.with_alpha(222), 14.0, C.C_PANEL_EDGE, 1.2))
    _paint_roster_header(cmds, hit, geometry, snapshot, page, pages)
    start = page * geometry.capacity
    for offset in range(geometry.capacity):
        row, column = divmod(offset, geometry.columns)
        cell = Rect(grid.x + column * (tile + gap), grid.y + row * (tile + gap), tile, tile)
        index = start + offset
        if index >= len(R.ROSTER):
            cmds.extend(_empty_roster_tile(cell, ui))
            continue
        entry = R.ROSTER[index]
        accent = _accent(entry)
        in_left, in_right = entry.character_id == left_entry.character_id, entry.character_id == right_entry.character_id
        selected = in_left or in_right
        cmds.append(plate(cell, accent.with_alpha(42 if selected else 11), 7.0 * ui,
                          accent if selected else C.C_PANEL_EDGE.with_alpha(145), 2.0 if selected else 1.0))
        portrait = sprites.get(entry.character_id)
        if portrait is not None:
            cmds.append(_sprite(portrait, Rect(cell.x + 3.0 * ui, cell.y + 3.0 * ui, cell.width - 6.0 * ui, cell.height - 6.0 * ui), layer=Layer.PANEL, z=2))
        name_rect = Rect(cell.x + 3.0 * ui, cell.bottom - 20.0 * ui, cell.width - 6.0 * ui, 17.0 * ui)
        cmds.append(RectCommand(rect=name_rect, fill=C.C_SELECT_DIM.with_alpha(232), layer=Layer.PANEL, z=3))
        cmds.append(_text(entry.name, name_rect, _fit_text(entry.name, name_rect, max_size=12.0 * ui, min_size=8.0), C.C_TEXT, ALIGN_CENTER))
        if selected:
            badge_w = min(28.0 * ui, cell.width * .4)
            badge = Rect(cell.right - badge_w - 3.0 * ui, cell.y + 3.0 * ui, badge_w, 17.0 * ui)
            color = C.C_SIDE_A if in_left else C.C_SIDE_B
            cmds.append(slanted(badge, 2.0 * ui, color.with_alpha(145), 2.0 * ui, color, 1.0, z=3))
            cmds.append(_text("1P" if in_left else "2P", badge, _font(max(8.0, 11.0 * ui), True), C.C_TEXT, ALIGN_CENTER))
        target_side = snapshot.picked_side or "left"
        hit.buttons.append(ButtonHit(ACTION_FIGHT, cell, f"pick:{target_side}:{entry.character_id}"))
    footer = geometry.footer
    editing = "1P" if snapshot.picked_side != "right" else "2P"
    summary = f"已收录 {len(R.ROSTER)} 名团子 · 每页 {geometry.capacity} 位 · 空位供后续角色扩展"
    cmds.append(_text(summary, Rect(footer.x, footer.y, footer.width * .72, footer.height), _font(max(8.0, 10.0 * ui), True), C.C_TEXT_DIM))
    cmds.append(_text(f"当前编辑 {editing} · 点击上方卡片切换", Rect(footer.x + footer.width * .72, footer.y, footer.width * .28, footer.height),
                      _font(max(8.0, 10.0 * ui), True), C.C_ACCENT, ALIGN_RIGHT))


def _paint_start(cmds: list, hit: HitMap, rect: Rect, snapshot: ScreenSnapshot) -> None:
    """Primary action: bold slanted block, lit when the bet is complete."""
    ready = snapshot.can_start
    if ready:
        cmds.append(slanted(
            rect,
            8.0,
            LinearGradientFill(
                start=rect.top_left,
                end=Point(rect.right, rect.bottom),
                stops=(GradientStop(0.0, C.C_BUTTON_ACTIVE.lighter(125)), GradientStop(1.0, C.C_ACCENT_DEEP)),
            ),
            8.0,
            C.C_BUTTON_EDGE,
            2.0,
            z=4,
        ))
    else:
        cmds.append(slanted(rect, 8.0, C.C_BUTTON.with_alpha(190), 8.0, C.C_PANEL_EDGE, 1.2, z=4))
    text = "开始对决" if ready else ("先选押注方" if not snapshot.picked_side else ("再选筹码" if snapshot.bet_amount == 0 else "开始对决"))
    cmds.append(_text(text, rect, _fit_text(text, rect, max_size=19.0 if rect.height >= 38.0 else 15.0, min_size=11.0), C.C_TEXT if ready else C.C_TEXT_DIM, ALIGN_CENTER))
    if ready:
        hit.buttons.append(ButtonHit(ACTION_FIGHT, rect))


def _side_label(side: str) -> str:
    return "左" if side == "left" else "右"


def _paint_banner(cmds: list, arena: Rect, title: str, subtitle: str) -> None:
    w = min(arena.width * 0.62, 300.0)
    h = min(arena.height * 0.26, 150.0)
    box = Rect(arena.center.x - w / 2.0, arena.center.y - h / 2.0, w, h)
    cmds.append(plate(box, C.C_BG_TOP.with_alpha(236), 16.0, C.C_ACCENT, 2.0, z=18))
    cmds.append(RectCommand(rect=Rect(box.x + 30.0, box.y + 2.0, box.width - 60.0, 3.0), fill=C.C_ACCENT, layer=Layer.PANEL, z=19))
    corner_marks(cmds, Rect(box.x + 7.0, box.y + 7.0, box.width - 14.0, box.height - 14.0), C.C_ACCENT.with_alpha(140), 14.0, 2.0, z=19)
    cmds.append(_text(title, Rect(box.x, box.y + 12.0, box.width, h * 0.50), _font(34.0, True), C.C_ACCENT, ALIGN_CENTER))
    cmds.append(_text(subtitle, Rect(box.x, box.y + h * 0.54, box.width, h * 0.36), _digit_font(30.0, True), C.C_TEXT, ALIGN_CENTER))
    if title == "暂停":
        cmds.append(_text("按 P 继续", Rect(box.x + 12.0, box.bottom + 8.0, box.width - 24.0, 22.0), _font(14.0, True), C.C_TEXT_DIM, ALIGN_CENTER))


def _paint_result_hold(cmds: list, arena: Rect) -> None:
    """KO banner with no settlement card (used during the reveal delay)."""
    width = min(arena.width * 0.62, 330.0)
    height = 96.0
    box = Rect(arena.center.x - width / 2.0, arena.y + arena.height * 0.13, width, height)
    cmds.append(plate(box, C.C_BG_TOP.with_alpha(238), 16.0, C.C_GOLD, 2.0, z=18))
    cmds.append(RectCommand(rect=Rect(box.x + 30.0, box.y + 2.0, box.width - 60.0, 3.0), fill=C.C_GOLD, layer=Layer.PANEL, z=19))
    corner_marks(cmds, Rect(box.x + 7.0, box.y + 7.0, box.width - 14.0, box.height - 14.0), C.C_GOLD.with_alpha(150), 14.0, 2.0, z=19)
    cmds.append(_text("对战结束", Rect(box.x, box.y + 10.0, box.width, 40.0), _font(32.0, True), C.C_GOLD, ALIGN_CENTER))
    cmds.append(_text("结算生成中…", Rect(box.x, box.y + 52.0, box.width, 30.0), _font(18.0, True), C.C_TEXT_DIM, ALIGN_CENTER))


def _paint_result(cmds: list, hit: HitMap, layout: Layout, snapshot: ScreenSnapshot) -> None:
    """A legible result hierarchy with a live, honest next-round countdown."""
    result = snapshot.result
    if result is None:
        return
    arena = layout.arena.rect
    w, h = min(arena.width * .94, 454.0), min(arena.height * .94, 466.0)
    box = Rect(arena.center.x - w / 2.0, arena.center.y - h / 2.0, w, h)
    ui = min(w / 454.0, h / 466.0)
    outcome_color = C.C_ACCENT if result.draw else (C.C_GOLD if result.bet_won else C.C_RED)
    series_winner = snapshot.series_winner
    if snapshot.series_over:
        if not series_winner and snapshot.series_left != snapshot.series_right:
            series_winner = "left" if snapshot.series_left > snapshot.series_right else "right"
        outcome_color = C.C_GOLD if series_winner else C.C_ACCENT
    cmds.append(RectCommand(rect=arena, fill=C.C_BG_TOP.with_alpha(218), layer=Layer.PANEL, z=20))
    cmds.append(plate(box, LinearGradientFill(start=box.top_left, end=Point(box.right, box.bottom),
        stops=(GradientStop(0.0, C.C_PANEL_LIFTED), GradientStop(1.0, C.C_PANEL_DEEP))),
        20.0, outcome_color.with_alpha(220), 2.0, z=21))
    cmds.append(RectCommand(rect=Rect(box.x + 22.0 * ui, box.y + 2.0, box.width - 44.0 * ui, 3.0), fill=outcome_color, layer=Layer.PANEL, z=22))
    corner_marks(cmds, Rect(box.x + 8.0, box.y + 8.0, box.width - 16.0, box.height - 16.0), outcome_color.with_alpha(120), 16.0 * ui, 1.5, z=22)
    first, second = _settlement_order(snapshot)
    backed = _name_for_side(snapshot, result.bet_side) if result.bet_side else ""
    winner = _name_for_side(snapshot, result.winner) if result.winner else ""
    pad = 24.0 * ui
    row_w = box.width - pad * 2.0
    y = box.y + 13.0 * ui
    head = Rect(box.x + pad, y, row_w, 18.0 * ui)
    cmds.append(_text("系列赛结束" if snapshot.series_over else "本轮结束", head, _font(12.0 * ui, True), C.C_TEXT_DIM, ALIGN_CENTER))
    title = "平局 · 双方力竭" if result.draw else f"{winner} 获胜"
    round_caption = snapshot.series_label or "系列比分"
    if snapshot.series_over:
        title = f"{_name_for_side(snapshot, series_winner)} 赢得系列赛" if series_winner else "系列赛平局"
        round_caption += " · 本轮平局" if result.draw else f" · 本轮 {winner} 获胜"
    title_rect = Rect(head.x, head.bottom + 2.0 * ui, row_w, 43.0 * ui)
    cmds.append(_text(title, title_rect, _fit_text(title, title_rect, max_size=33.0 * ui, min_size=15.0), outcome_color, ALIGN_CENTER))
    score_box = Rect(head.x, title_rect.bottom + 8.0 * ui, row_w, 62.0 * ui)
    cmds.append(plate(score_box, outcome_color.with_alpha(8), 9.0, outcome_color.with_alpha(65), .8, z=21))
    round_rect = Rect(score_box.x + 6.0 * ui, score_box.y + 4.0 * ui, score_box.width - 12.0 * ui, 17.0 * ui)
    cmds.append(_text(round_caption, round_rect, _fit_text(round_caption, round_rect, max_size=11.0 * ui, min_size=8.0), C.C_TEXT_DIM, ALIGN_CENTER))
    first_wins = snapshot.series_left if first.side == "left" else snapshot.series_right
    second_wins = snapshot.series_left if second.side == "left" else snapshot.series_right
    score = f"{first.name} {first_wins} : {second_wins} {second.name}"
    score_rect = Rect(score_box.x + 8.0 * ui, score_box.y + 24.0 * ui, score_box.width - 16.0 * ui, 30.0 * ui)
    cmds.append(_text(score, score_rect, _fit_text(score, score_rect, max_size=23.0 * ui, min_size=11.0), C.C_ACCENT, ALIGN_CENTER))
    change = 0 if result.draw else result.payout
    change_text = f"+{change}" if change > 0 else str(change)
    if result.draw:
        bet_text, bet_color = "平局 · 连押保持不变", C.C_TEXT_DIM
    elif result.bet_won:
        bet_text, bet_color = f"押中 {backed} · 筹码 {change_text} · 连押 {result.streak}", C.C_GREEN
    elif result.bet_side:
        bet_text, bet_color = f"押错 {backed} · 筹码 {change_text} · 连押清零", C.C_RED
    else:
        bet_text, bet_color = "未押注", C.C_TEXT_DIM
    bet_rect = Rect(head.x, score_box.bottom + 8.0 * ui, row_w, 24.0 * ui)
    cmds.append(_text(bet_text, bet_rect, _fit_text(bet_text, bet_rect, max_size=15.0 * ui, min_size=9.0), bet_color, ALIGN_CENTER))
    stats = result.stats
    rows = ((f"本局撞击 · {first.name} : {second.name}", str(stats.total_hits), C.C_TEXT),
            (f"剩余生命 · {first.name} : {second.name}", _hp_pair(first, second, stats), C.C_TEXT),
            ("本轮用时", f"{stats.duration_seconds:.1f}s", C.C_TEXT),
            ("连续押胜", f"{result.streak}（最高 {result.best_streak}）", C.C_GOLD),
            ("筹码变化", change_text, bet_color),
            ("当前筹码", f"{int(result.chips) - change} → {int(result.chips)}", C.C_GREEN if result.chips >= 0 else C.C_RED))
    y = bet_rect.bottom + 9.0 * ui
    row_h = 27.0 * ui
    for index, (caption, value, color) in enumerate(rows):
        row = Rect(head.x, y + index * row_h, row_w, row_h)
        if index % 2 == 0:
            cmds.append(RectCommand(rect=row, fill=C.C_ACCENT.with_alpha(5), layer=Layer.PANEL, z=21))
        label_rect = Rect(row.x + 8.0 * ui, row.y, row.width * .55 - 8.0 * ui, row.height)
        value_rect = Rect(row.x + row.width * .55, row.y, row.width * .45 - 8.0 * ui, row.height)
        cmds.append(_text(caption, label_rect, _fit_text(caption, label_rect, max_size=12.0 * ui, min_size=8.0), C.C_TEXT_DIM))
        cmds.append(_text(value, value_rect, _fit_text(value, value_rect, max_size=16.0 * ui, min_size=8.0), color, ALIGN_RIGHT))
    remaining = C.AUTO_ADVANCE_SECONDS if snapshot.auto_advance_seconds is None else max(0.0, snapshot.auto_advance_seconds)
    seconds = max(1, int(math.ceil(remaining)))
    progress_y = y + len(rows) * row_h + 15.0 * ui
    track = Rect(head.x, progress_y, row_w, 4.0 * ui)
    cmds.append(RectCommand(rect=track, fill=C.C_PANEL_INNER, layer=Layer.PANEL, z=22))
    if not snapshot.series_over:
        ratio = min(1.0, remaining / max(.01, C.AUTO_ADVANCE_SECONDS))
        cmds.append(RectCommand(rect=Rect(track.x, track.y, track.width * ratio, track.height), fill=C.C_ACCENT, layer=Layer.PANEL, z=22))
    button = Rect(head.x, box.bottom - 68.0 * ui, row_w, 40.0 * ui)
    cmds.append(slanted(button, 8.0 * ui, LinearGradientFill(start=button.top_left, end=Point(button.right, button.bottom),
        stops=(GradientStop(0.0, C.C_BUTTON_ACTIVE.lighter(120)), GradientStop(1.0, C.C_ACCENT_DEEP))),
        8.0 * ui, C.C_BUTTON_EDGE, 1.5))
    action = "返回选人" if snapshot.series_over else f"下一轮 {seconds} 秒"
    cmds.append(_text(action, button, _font(19.0 * ui, True), C.C_TEXT, ALIGN_CENTER))
    hint = Rect(head.x, button.bottom + 5.0 * ui, row_w, 15.0 * ui)
    hint_text = "系列赛结束 · 点击返回选人界面" if snapshot.series_over else "沿用双方与押注 · R 立即开始下一轮"
    cmds.append(_text(hint_text, hint, _fit_text(hint_text, hint, max_size=10.0 * ui, min_size=8.0), C.C_TEXT_DIM, ALIGN_CENTER))
    hit.buttons.append(ButtonHit(ACTION_NEXT, button))


def _hp_pair(first: CharacterView, second: CharacterView, stats) -> str:
    """Remaining HP in the same order as the names on the card."""
    hp = {"left": int(stats.left_hp), "right": int(stats.right_hp)}
    return f"{hp.get(first.side, 0)} : {hp.get(second.side, 0)}"


def _paint_footer(cmds: list, layout: Layout, snapshot: ScreenSnapshot) -> None:
    footer = layout.footer_rect
    cmds.append(plate(
        footer,
        LinearGradientFill(
            start=Point(footer.x, footer.bottom),
            end=footer.top_left,
            stops=(GradientStop(0.0, C.C_PANEL_DEEP), GradientStop(1.0, C.C_PANEL)),
        ),
        12.0,
        C.C_PANEL_EDGE,
        1.2,
    ))
    record = snapshot.record or {}
    left, right = _views(snapshot)
    top_row = Rect(footer.x + 16.0, footer.y + 8.0, footer.width - 32.0, 30.0)
    phase = snapshot.phase
    if phase is MatchPhase.FIGHT and snapshot.countdown > 0.0:
        status = f"开赛倒计时 {max(1, int(math.ceil(snapshot.countdown)))}"
    elif phase is MatchPhase.FIGHT:
        status = "决斗中" + ("（已暂停）" if snapshot.paused else "")
    elif phase is MatchPhase.RESULT and snapshot.result is None:
        status = "对战结束 · 正在生成结算…"
    elif phase is MatchPhase.RESULT and snapshot.series_over:
        status = f"{snapshot.series_label} 系列赛结束 · 点击返回选人"
    elif phase is MatchPhase.RESULT:
        remaining = C.AUTO_ADVANCE_SECONDS if snapshot.auto_advance_seconds is None else snapshot.auto_advance_seconds
        status = f"{snapshot.series_label or '系列赛'} 进行中 · {max(1, int(math.ceil(remaining)))} 秒后自动进入下一轮"
    else:
        status = "选择押注方与筹码，然后开赛"
    cmds.append(_text(status, Rect(top_row.x, top_row.y, top_row.width * 0.55, top_row.height), _font(16.0, True), C.C_ACCENT, ALIGN_LEFT))
    cmds.append(_text(
        f"总轮次 {int(record.get('total_rounds', 0))}   押中 {int(record.get('total_bet_wins', 0))} / 押错 {int(record.get('total_bet_losses', 0))} / 平局 {int(record.get('total_draws', 0))}",
        top_row,
        _font(13.0, True),
        C.C_TEXT_DIM,
        ALIGN_RIGHT,
    ))
    cmds.append(RectCommand(rect=Rect(footer.x + 16.0, top_row.bottom + 3.0, footer.width - 32.0, 1.0), fill=C.C_PANEL_INNER, layer=Layer.PANEL))
    cards = [
        (left, C.C_SIDE_A, "左"),
        (right, C.C_SIDE_B, "右"),
    ]
    card_w = (footer.width - 32.0) / 2.0
    for index, (view, color, prefix) in enumerate(cards):
        key = "押注" if view.side == snapshot.bet_side else ("1P" if view.side == "left" else "2P")
        card = Rect(footer.x + 16.0 + index * card_w + 4.0, top_row.bottom + 9.0, card_w - 8.0, footer.bottom - top_row.bottom - 19.0)
        cmds.append(plate(card, C.C_BG_TOP.with_alpha(170), 6.0, C.C_PANEL_EDGE.with_alpha(120), 1.0))
        cmds.append(RectCommand(rect=Rect(card.x, card.y + 5.0, 3.0, card.height - 10.0), fill=color, layer=Layer.PANEL, z=2))
        cmds.append(_text(f"{key} · {view.name}", Rect(card.x + 14.0, card.y, card.width * 0.45, card.height), _fit_text(f"{key} · {view.name}", Rect(card.x + 14.0, card.y, card.width * 0.45 - 8.0, card.height), max_size=16.0, min_size=12.0), color, ALIGN_LEFT))
        cmds.append(_text(
            f"生命 {view.hp}/{view.max_hp}   命中 {view.hits_dealt}   受击 {view.hits_taken}",
            Rect(card.x + card.width * 0.46, card.y, card.width * 0.54 - 16.0, card.height),
            _font(14.0, True),
            C.C_TEXT_DIM,
            ALIGN_RIGHT,
        ))

