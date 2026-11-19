"""QWidget glue for Dango Showdown.

This is the only module in the package that imports a GUI toolkit. It keeps the
window alive, forwards frames and pointer input, and hands the neutral
``DrawBatch`` built by ``presenter.py`` to the render layer for painting.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import replace

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.qt_bridge import create_painter_host
from lib.core.services.feedback_scene import FeedbackScene
from lib.script.practical.manager import get_particle_script_manager
from lib.script.effects.manager import get_effect_script_manager
from lib.core.render.visuals.resources import ImageResource
from lib.script.gemes.MAIN.game_packages import GameContext

from . import constants as C
from . import events as ev
from .animator import deform, hit_flash_alpha, mirrored
from .assets import DangoAssetLoader, laser_palette
from .engine import DangoMatch
from .model import MatchPhase
from lib.core.render.visuals.types import Rect

from .presenter import (
    ACTION_CLOSE,
    ACTION_FIGHT,
    ACTION_NEXT,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CanvasTransform,
    CharacterView,
    FrameSource,
    HitMap,
    Layout,
    ScreenSnapshot,
    SunSpiritVisual,
    paint_snapshot,
    roster_page_count,
)
from .sfx import DangoSfx
from .stats import DangoStats

PHASE_SELECT = "select"
PHASE_FIGHT = "fight"
PHASE_RESULT = "result"
COUNTDOWN_TICKS = 4


def _ray_limit(x: float, y: float, dx: float, dy: float, arena: Rect, sign: float) -> float:
    """Distance from ``(x, y)`` along ``sign * (dx, dy)`` until the arena wall."""
    limit = float("inf")
    vx = dx * sign
    vy = dy * sign
    if abs(vx) > 1e-9:
        limit = min(limit, ((arena.right if vx > 0.0 else arena.x) - x) / vx)
    if abs(vy) > 1e-9:
        limit = min(limit, ((arena.bottom if vy > 0.0 else arena.y) - y) / vy)
    return max(0.0, min(limit, arena.width * 2.0))


class DangoShowdownWidget(QWidget):
    """Dango Showdown canvas: selection, betting, auto battle and results."""

    def __init__(self, context: GameContext, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._close_callback = None
        self._fullscreen_callback = None
        self._bgm_callback = None
        self._bgm_request: tuple[str, str, str] | None = None
        self._event_center = get_event_center()
        self._painter_host = create_painter_host()
        self._hit_map = HitMap()
        self._sprites: dict[str, FrameSource] = {}
        self._state_sprites: dict[tuple[str, str], FrameSource] = {}
        self._flash_sprites: dict[str, FrameSource] = {}
        self._last_tick = time.perf_counter()
        self._clock = 0.0
        self._roster_page = 0
        self._visual_rng = random.Random()
        self._paused = False
        self._shake = 0.0
        self._last_hp: tuple[int, int] = (C.MAX_HP, C.MAX_HP)
        self._countdown_ticks = 0
        self._visual_clock = 0.0
        self._feedback = FeedbackScene(particle_manager=get_particle_script_manager(), effect_manager=get_effect_script_manager())
        self._starburst = None
        self._starburst_elapsed = 0.0
        self._starburst_impact_fired = False
        self._laser_asset = None
        self._blade_asset = None
        self._slash_trail_asset = None
        self._slash_trail_reverse_asset = None
        self._slash_flash_asset = None
        self._blade_pivot = (.5, .5)
        self._petal_assets = ()
        self._frost_blades: list[dict] = []
        self._starburst_asset: ImageResource | bool | None = None
        self._void_hand_asset: ImageResource | bool | None = None
        self._watermark_asset: ImageResource | bool | None = None
        # Every cast keeps its own hand until its 2 s fade completes.
        # (target normalised point, clockwise degrees, born at clock time)
        self._sun_spirit_fx: dict[str, dict] = {}
        self._sun_spirit_frames: dict[str, FrameSource] = {}
        self._starburst_payload: dict | None = None
        self._deferred_numbers: list[dict] = []
        self._flash = 0.0
        self._hit_stop = 0.0
        self._last_views: tuple = ()
        # Caster side + clock deadline of the lit skill card.
        self._card_flash: dict[str, object] = {}
        self._auto_advance: float | None = None
        # Side that clinched the current series ("" while undecided).
        self._series_winner = ""

        stats = DangoStats(self._context.data_root / "state.json")
        self._sfx = DangoSfx(self._context.cache_root, self._emit_sfx)
        bus = ev.ExtensionBus(publish=self._publish_event)
        self._match = DangoMatch(stats, bus=bus, on_finish=self._on_finish)
        self._match.prepare_round()
        self._load_sprites()

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ── host hooks ───────────────────────────────────────────────
    def set_close_callback(self, callback) -> None:
        self._close_callback = callback

    def set_fullscreen_callback(self, callback) -> None:
        self._fullscreen_callback = callback

    def set_bgm_callback(self, callback) -> None:
        """Request phase music through the host's shared music queue."""
        self._bgm_callback = callback if callable(callback) else None
        self._bgm_request = None
        self._sync_bgm()

    def _sync_bgm(self) -> None:
        if self._bgm_callback is None:
            return
        selecting = self._match.phase is MatchPhase.SELECT
        title = "团子准备中" if selecting else "团子出击"
        track_ref = "netease:3442263555" if selecting else "netease:3442262625"
        request = (title, "鸣潮先约电台", track_ref)
        if request == self._bgm_request:
            return
        self._bgm_request = request
        try:
            self._bgm_callback(title, request[1], track_ref=track_ref)
        except Exception:
            # A host music failure must not interrupt a match or Qt callback.
            pass

    def on_runtime_activated(self) -> None:
        self._last_tick = time.perf_counter()
        self._timer.start()

    def reset_game(self, start_running: bool = True) -> None:
        self._match.prepare_round()
        self._reset_presentation()
        self._last_tick = time.perf_counter()
        # The host resets with start_running=False after showing the child.
        # Selection and asset delivery still need the presentation timer.
        if start_running or self.isVisible():
            self._timer.start()
        self.update()

    def _reset_presentation(self) -> None:
        """A new bout never inherits the previous bout's cinematic effects."""
        self._last_hp = (self._match.left.hp, self._match.right.hp)
        self._paused = False
        self._shake = 0.0
        self._countdown_ticks = 0
        self._last_views = ()
        self._roster_page = 0
        self._feedback.clear()
        self._sun_spirit_fx.clear()
        self._frost_blades.clear()
        self._starburst = None
        self._starburst_payload = None
        self._deferred_numbers.clear()
        self._card_flash.clear()
        self._auto_advance = None
        self._series_winner = ""
        self._flash = 0.0
        self._hit_stop = 0.0
        self._sync_bgm()

    def deactivate(self) -> None:
        self._timer.stop()
        self._feedback.clear()
        self._frost_blades.clear()
        self.set_bgm_callback(None)

    def shutdown(self) -> None:
        """Stop the frame timer; the asset worker is a daemon and exits alone."""
        self._timer.stop()
        self._feedback.clear()
        self._frost_blades.clear()
        self.set_bgm_callback(None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last_tick = time.perf_counter()
        self._timer.start()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._feedback.clear()
        self._frost_blades.clear()
        self.set_bgm_callback(None)
        super().closeEvent(event)

    # ── assets ───────────────────────────────────────────────────
    def _load_sprites(self) -> None:
        """Start decoding off the GUI thread; frames land on the next tick."""
        self._asset_loader = DangoAssetLoader(self._context.assets_root)
        self._assets_applied = False
        self._starburst_asset = None
        self._void_hand_asset = None
        self._watermark_asset = None
        self._sun_spirit_frames.clear()

    def _apply_assets(self, *, blocking: bool) -> bool:
        """Move decoded resources into paint-ready sprites once."""
        loader = getattr(self, "_asset_loader", None)
        if self._assets_applied or loader is None:
            return self._assets_applied
        if not blocking and not loader.ready:
            return False
        if blocking:
            loader.wait(5.0)
        payload = loader.assets()
        if payload is None:
            return False
        for (character_id, state), entry in payload["states"].items():
            resource = entry["resource"]
            source = FrameSource(resource.resource_id, 1, resource.frames[0])
            self._state_sprites[(character_id, state)] = source
            if state == "idle":
                self._sprites[character_id] = source
            elif state == "hurt" and entry["hurt_tint"] is not None:
                tinted = entry["hurt_tint"]
                self._flash_sprites[character_id] = FrameSource(tinted.resource_id, 1, tinted.frames[0])
        self._laser_asset = payload["laser"]
        self._blade_asset = payload.get("feixue_blade")
        self._slash_trail_asset = payload.get("feixue_slash_trail")
        self._slash_trail_reverse_asset = payload.get("feixue_slash_trail_reverse")
        self._slash_flash_asset = payload.get("feixue_slash_flash")
        self._blade_pivot = payload.get("feixue_blade_pivot", (.5, .5))
        self._petal_assets = tuple(payload[key] for key in ("feixue_petal", "feixue_petal_red") if payload.get(key))
        self._starburst_asset = payload["starburst"] or False
        self._void_hand_asset = payload.get("void_hand") or False
        self._watermark_asset = payload.get("watermark") or False
        self._sun_spirit_frames = {
            color: FrameSource(resource.resource_id, 1, resource.frames[0])
            for color, resource in payload.get("sun_spirits", {}).items() if resource is not None
        }
        self._assets_applied = True
        return True

    def _starburst_resource(self) -> ImageResource | None:
        if not self._assets_applied:
            self._apply_assets(blocking=True)
        if self._starburst_asset is None:
            self._starburst_asset = False
        return self._starburst_asset or None

    def _void_hand_resource(self) -> ImageResource | None:
        if not self._assets_applied:
            self._apply_assets(blocking=False)
        return self._void_hand_asset or None

    def _watermark_resource(self) -> ImageResource | None:
        if not self._assets_applied:
            self._apply_assets(blocking=False)
        return self._watermark_asset or None

    def _sprite_for(self, view: CharacterView) -> FrameSource | None:
        if view.state == "hurt":
            return self._state_sprites.get((view.character_id, "hurt")) or self._sprites.get(view.character_id)
        if view.state == "attack":
            return self._state_sprites.get((view.character_id, "attack")) or self._sprites.get(view.character_id)
        return self._sprites.get(view.character_id)

    def _flash_sprite_for(self, view: CharacterView) -> FrameSource | None:
        if view.state != "hurt" or view.flash <= 0.0:
            return None
        return self._flash_sprites.get(view.character_id)

    # ── lifecycle ────────────────────────────────────────────────
    def _on_tick(self) -> None:
        now = time.perf_counter()
        delta = max(0.0, min(0.1, now - self._last_tick))
        self._last_tick = now
        # Pick up background-decoded frames as soon as they are ready.
        self._apply_assets(blocking=False)
        if not self._paused:
            phase_before = self._match.phase.value
            reveal_before = float(self._match.result_reveal or 0.0) if phase_before == PHASE_RESULT else 0.0
            # Hit-stop: physics freezes for a beat on cinematic impact while
            # presentation clocks keep running so FX never stall.
            if self._hit_stop > 0.0:
                self._hit_stop = max(0.0, self._hit_stop - delta)
            else:
                self._match.update(delta)
            self._clock += delta
            self._advance_visuals(delta)
            # Only time actually spent showing the card belongs to its
            # countdown; a reveal that ends partway through a frame cannot
            # consume that entire frame a second time.
            card_delta = max(0.0, delta - reveal_before) if reveal_before > 0.0 else delta
            self._auto_advance_tick(card_delta)
            self._shake = max(0.0, self._shake - delta * 3.2)
            self._flash = max(0.0, self._flash - delta * 3.4)
            self._track_fight_audio(phase_before)
        self.update()

    def _track_fight_audio(self, phase_before: str) -> None:
        match = self._match
        if match.phase.value != PHASE_FIGHT:
            self._countdown_ticks = 0
            return
        if match.countdown <= 0.0:
            if phase_before == PHASE_FIGHT and self._countdown_ticks > 0:
                self._countdown_ticks = 0
                self._sfx.play("start")
            return
        tick = max(1, int(math.ceil(match.countdown / (C.COUNTDOWN_SECONDS / COUNTDOWN_TICKS))))
        if tick != self._countdown_ticks:
            self._countdown_ticks = tick
            self._sfx.play("countdown")

    def _on_finish(self, result) -> None:
        self._shake = 1.0
        self._series_winner = str(getattr(result, "series_winner", "") or "")
        if result.draw:
            self._sfx.play("draw")
        elif result.bet_won:
            self._sfx.play("win")
            if result.streak >= 2:
                self._sfx.play("streak")
        elif result.bet_side:
            self._sfx.play("lose")

    def _auto_advance_tick(self, delta: float) -> None:
        """After the settlement card, the next round starts on its own.

        Only once the card has been on screen for AUTO_ADVANCE_SECONDS; a
        finished series keeps its final card until the player returns to
        selection. Manual R or the next-round button skips the card timer.
        """
        match = self._match
        if match.phase is not MatchPhase.RESULT or match.result_reveal not in (None, 0.0):
            self._auto_advance = None
            return
        if match.series_over:
            self._auto_advance = None
            return
        if self._auto_advance is None:
            self._auto_advance = C.AUTO_ADVANCE_SECONDS
        self._auto_advance = max(0.0, self._auto_advance - max(0.0, float(delta)))
        if self._auto_advance <= 1e-9:
            self._auto_advance = None
            self._next_round()

    def _next_round(self, *, manual: bool = False) -> None:
        # The engine owns the shared automatic/button/keyboard transition;
        # no input can skip the mandatory reveal or reopen a finished series.
        returning = self._match.series_over
        if not self._match.advance_round():
            return
        self._reset_presentation()
        if manual:
            self._sfx.play("ui_back" if returning else "ui_confirm")
        self.update()

    # ── sound / events ───────────────────────────────────────────
    def _emit_sfx(self, _key: str, path: str, volume: float) -> None:
        self._event_center.publish(Event(EventType.SOUND_REQUEST, {
            "audio_type": "game_sfx",
            "source": path,
            "volume_gain": volume,
            "interruptible": True,
        }))

    def _publish_event(self, name: str, payload: dict) -> None:
        if name == ev.MATCH_START:
            self._sync_bgm()
        try:
            self._event_center.publish(Event(name, dict(payload)))
        except Exception:
            pass
        self._handle_skill_event(name, payload)
        if name == ev.HIT:
            self._sfx.hit(float(payload.get("speed", 0.0) or 0.0))
            self._shake = min(1.0, self._shake + 0.30)
            self._show_collision_damage(payload)
            hp = (int(payload.get("hp_left", 0) or 0), int(payload.get("hp_right", 0) or 0))
            if hp != self._last_hp:
                self._last_hp = hp
                if min(hp) > 0:
                    self._sfx.play("hurt")

    # ── skill visuals / particles ────────────────────────────────
    def _advance_visuals(self, seconds: float) -> None:
        self._visual_clock += seconds
        for cast in self._frost_blades:
            owner = self._current_ball(cast["side"])
            if owner is not None and self._visual_clock - cast["born"] <= .06:
                cast["center"] = self._canvas_point((owner.x, owner.y))
            image = cast["image"]
            image.intro_start_pos = image.display_pos = image.outro_end_pos = cast["center"]
        self._feedback.advance(seconds)
        self._advance_frost_blades()
        self._advance_starburst(seconds)

    def _advance_frost_blades(self) -> None:
        """Follow the caster; the desktop effect owns the orbit and rotation."""
        for cast in list(self._frost_blades):
            image = cast["image"]
            if image not in self._feedback.effects:
                self._frost_blades.remove(cast)
                continue
            center = cast["center"]
            age = max(0.0, self._visual_clock - cast["born"])
            if cast.get("tail", True) and age >= cast["next_tail"] and age <= .08:
                cast["next_tail"] = age + .025
                angle = math.radians(image._render_rotation)
                point = (center[0] + math.cos(angle) * cast["radius"],
                         center[1] + math.sin(angle) * cast["radius"])
                for color in ((235, 253, 255), (105, 220, 255)):
                    self._spawn_particle("star_streak", "point", point, {
                        "color": color, "count_range": (1, 1), "direction": (-math.sin(angle), math.cos(angle)),
                        "speed_range": (25, 65), "bloom_range": (.8, 1.7), "spread": 6,
                        "duration_ticks": 5, "fade_ticks": 4,
                    })

    def _advance_starburst(self, seconds: float) -> None:
        if self._starburst is None:
            return
        self._starburst_elapsed += max(0.0, seconds)
        self._starburst.animation_seconds = self._starburst_elapsed
        payload = self._starburst_payload or {}
        impact = float(payload.get("impact_ms", 0) or 0) / 1000.0 or C.STARBURST_IMPACT_SECONDS
        if not self._starburst_impact_fired and self._starburst_elapsed >= impact:
            self._starburst_impact_fired = True
            self._starburst_impact(payload)
        if self._starburst_elapsed >= C.STARBURST_EFFECT_SECONDS:
            self._feedback.remove(self._starburst)
            self._starburst = None
            self._starburst_payload = None

    def _starburst_impact(self, payload: dict) -> None:
        """Land the mech: heavy shake, flash, full-screen particles, number."""
        region = self._rect_points(self._arena_rect())
        centre = self._canvas_point((0.5, 0.5))
        self._spawn_particle("cyan_pink_scatter_fall", "rect", region)
        self._spawn_particle("cyan_pink_scatter_fall", "rect", region)
        for rgb, count in (((255, 120, 200), (10, 14)), ((120, 220, 255), (8, 12)), ((255, 232, 180), (6, 10))):
            self._spawn_particle("burst_line", "point", centre, {"rgb": rgb, "count_range": count})
        for side in ("left", "right"):
            self._spawn_particle("white_pink_collision", "point", self._canvas_point((0.28 if side == "left" else 0.72, 0.5)))
        self._flash = 1.0
        self._hit_stop = 0.07
        self._shake = max(self._shake, C.STARBURST_SHAKE)
        self._sfx.play("starburst")
        # Amis's finale: a torrent of blue/cyan/white shards on the landing
        # spot (the enemy was just parked at the bottom centre) plus a crown
        # of the same palette at the chamber centre.
        landing = self._canvas_point((0.5, 1.0 - C.BASE_RADIUS - 0.004))
        for rgb, count in (
            ((63, 184, 255), (18, 26)),
            ((126, 233, 255), (16, 22)),
            ((236, 250, 255), (12, 18)),
        ):
            self._spawn_particle("burst_line", "point", landing, {"rgb": rgb, "count_range": count})
            self._spawn_particle("burst_line", "point", centre, {"rgb": rgb, "count_range": (8, 12)})
        self._spawn_particle("white_pink_collision", "point", landing)
        if self._deferred_numbers:
            self._flush_deferred_numbers()
        else:
            self._show_damage_number(payload, C.damage_rgb())

    def _arena_rect(self) -> Rect:
        return Layout(CANVAS_WIDTH, CANVAS_HEIGHT).arena.rect

    @staticmethod
    def _rect_points(rect: Rect) -> tuple[float, float, float, float]:
        return rect.x, rect.y, rect.right, rect.bottom

    def _canvas_point(self, point: tuple[float, float]) -> tuple[float, float]:
        """Normalised arena point -> virtual canvas point."""
        arena = self._arena_rect()
        return (
            arena.x + float(point[0]) * arena.width,
            arena.y + float(point[1]) * arena.height,
        )

    def _current_ball(self, side: str):
        for dango, ball in zip(self._match.dango, self._match.balls):
            if dango.side == side:
                return ball
        return None

    def _hit_canvas(self, payload: dict) -> tuple[float, float]:
        """Virtual-canvas point of the damaged dango's current body."""
        ball = self._current_ball(str(payload.get("target", "")))
        if ball is not None:
            return self._canvas_point((ball.x, ball.y))
        return self._canvas_point(payload.get("end") or payload.get("point") or (0.5, 0.5))

    def _beam_span(self, payload: dict) -> tuple[tuple[float, float], tuple[float, float]]:
        """Arena-space endpoints of the laser: through the hit dango, along ``angle``."""
        arena = self._arena_rect()
        hit = payload.get("_hit_canvas") or self._hit_canvas(payload)
        angle = float(payload.get("angle", 0.0) or 0.0)
        dx, dy = math.cos(angle), math.sin(angle)
        forward = _ray_limit(hit[0], hit[1], dx, dy, arena, 1.0)
        backward = _ray_limit(hit[0], hit[1], dx, dy, arena, -1.0)
        return (
            (hit[0] - dx * backward, hit[1] - dy * backward),
            (hit[0] + dx * forward, hit[1] + dy * forward),
        )

    def _spawn_particle(self, particle_id: str, area_type: str, area_data, options: dict | None = None) -> list:
        """Use the desktop's registered particle script in this clipped scene."""
        return self._feedback.spawn_particle(particle_id, area_type, area_data, options)

    def _spawn_image(self, resource, point, *, intro=0.0, display=0.0, outro=0.0, scale=1.0, z=8, **options):
        if resource is None:
            return []
        return self._feedback.spawn_effect("smooth_image_show", "point", point, {
            "resource_path": resource.resource_id,
            "intro_start_pos": point, "display_pos": point, "outro_end_pos": point,
            "intro_duration": intro, "display_duration": display, "outro_duration": outro,
            "scale": scale, "z": z, **options,
        }, resource=resource)

    def _spawn_skill_feedback(self, payload: dict, color: tuple[int, int, int]) -> None:
        """Impact burst on the damaged dango (never a fixed screen centre)."""
        point = self._target_point(payload)
        self._spawn_particle("burst_line", "point", point, {"rgb": color})
        self._show_damage_number(payload, color)

    def _target_point(self, payload: dict) -> tuple[int, int]:
        """Canvas point of the damaged dango.

        Order: an explicit ``_point``, the event's ``point`` (collision/void-hand
        anchor), the live ball position of ``target``, and only then the target
        dango's last rendered position. There is deliberately no "screen centre"
        fallback: that made every unanchored effect pile up in the middle.
        """
        explicit = payload.get("_point")
        if explicit:
            return float(explicit[0]), float(explicit[1])
        hit = payload.get("_hit_canvas")
        if hit:
            return float(hit[0]), float(hit[1])
        point = payload.get("point")
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            return self._canvas_point((float(point[0]), float(point[1])))
        target = str(payload.get("target", ""))
        for dango, ball in zip(self._match.dango, self._match.balls):
            if dango.side == target:
                return self._canvas_point((ball.x, ball.y))
        for view in self._last_views:
            if view.side == target:
                return self._canvas_point((view.x, view.y))
        for dango, ball in zip(self._match.dango, self._match.balls):
            if dango.side != str(payload.get("side", "")):
                return self._canvas_point((ball.x, ball.y))
        ball = self._match.balls[0]
        return self._canvas_point((ball.x, ball.y))

    def _show_collision_damage(self, payload: dict) -> None:
        """Float each side's actual HP loss, including charged body attacks."""
        point = payload.get("point", (0.5, 0.5))
        screen = self._canvas_point(point)
        colour = C.damage_color()
        for side, dx, dy in (("left", -34.0, -6.0), ("right", 34.0, -26.0)):
            damage = {"target": side, "damage": payload.get("damage_" + side, C.DAMAGE_PER_IMPACT)}
            self._float_number_at(damage, screen, (dx, dy), (colour.red, colour.green, colour.blue))

    def _float_number_at(self, payload: dict, point: tuple[int, int], offset: tuple[float, float], color: tuple[int, int, int]) -> None:
        anchored = dict(payload)
        anchored["_point"] = (point[0] + offset[0], point[1] + offset[1])
        self._show_damage_number(anchored, color)

    def _show_damage_number(self, payload: dict, color: tuple[int, int, int]) -> None:
        """Float the damage with the existing ``floating_text`` particle.

        The font grows with the number: every extra point of damage adds
        ``DAMAGE_NUMBER_SIZE_STEP`` px on top of ``DAMAGE_NUMBER_MIN_SIZE`` and
        a little more drift, so a laser (8) reads far heavier than a bounce (1).
        """
        point = payload.get("_point") or self._target_point(payload)
        amount = max(0, int(payload.get("damage", 0) or 0))
        if amount <= 0:
            return
        size = C.damage_number_size(amount)
        # ``floating_text`` adds drift every tick, so keep it sub-pixel per frame.
        drift = 1.5 + min(4.5, amount * 0.12)
        rise = 34.0 + size * 1.4
        self._spawn_particle("floating_text", "point", point, {
            "text": f"-{amount}",
            "rgb": color,
            "size": size,
            "font_type": "ui",
            "font_bold": True,
            "bloom": 0.6 + min(1.4, amount * 0.1),
            "drift_amplitude": drift,
            "drift_speed": 7.0,
            "target_x": point[0],
            "target_y": point[1] - rise,
        })

    def _flush_deferred_numbers(self) -> None:
        pending = self._deferred_numbers
        if not pending:
            return
        self._deferred_numbers = []
        for payload in pending:
            self._show_damage_number(payload, C.damage_rgb())

    def _light_card(self, payload: dict) -> None:
        """Light the caster's fighter card for a beat on every skill."""
        side = str(payload.get("side", "") or "")
        if side in ("left", "right"):
            self._card_flash = {"side": side, "until": self._clock + C.CARD_FLASH_SECONDS}

    def _handle_skill_event(self, name: str, payload: dict) -> None:
        if name in (ev.LASER_FIRE, ev.STARBURST_FIRE):
            self._light_card(payload)
        if name == ev.FROST_SLASH:
            self._spawn_frost_slash(payload)
        elif name == ev.SUN_SPIRIT_SUMMON:
            self._light_card(payload)
            self._sfx.play("sun_summon")
            visual = SunSpiritVisual(
                summon_id=str(payload["summon_id"]), color=str(payload["color"]),
                point=tuple(payload["point"]), radius=float(payload["radius"]),
                delay=float(payload["delay"]), second_delay=float(payload["second_delay"]),
                second_radius=float(payload["second_radius"]),
            )
            self._apply_assets(blocking=False)
            source = self._sun_spirit_frames.get(visual.color)
            resource = ImageResource(source.resource_id, (source.frame,)) if source is not None else None
            point = self._canvas_point(visual.point)
            size = self._arena_rect().width * C.SUN_SPIRIT_SIZE_RATIO
            images = self._spawn_image(resource, (point[0], point[1] - 5), intro=.2, display=600,
                                       scale=size / max(1, source.frame.width) if source else 1,
                                       z=10, hover_amplitude=3, pulse_scale=.035, intro_scale=True)
            self._sun_spirit_fx[visual.summon_id] = {
                "visual": visual, "born": self._clock, "stage_born": self._clock, "images": images,
            }
            rgb = (C.C_SUN_YELLOW if visual.color == "yellow" else C.C_SUN_CYAN)
            self._spawn_particle("star_streak", "circle", (*point, size * .43), {
                "color": (rgb.red, rgb.green, rgb.blue), "count_range": (8, 10),
                "speed_range": (8, 16), "direction": (0, -1), "brownian": 0,
                "bloom_range": (2, 4), "duration_ticks": 24, "fade_ticks": 6,
            })
        elif name == ev.SUN_SPIRIT_BURST:
            identity = str(payload["summon_id"])
            effect = self._sun_spirit_fx.get(identity)
            if effect is not None:
                effect["visual"] = replace(effect["visual"], stage=int(payload["stage"]))
                effect["stage_born"] = self._clock
                for image in effect.get("images", ()):
                    self._feedback.remove(image)
                effect["images"] = []
            point = self._canvas_point(tuple(payload["point"]))
            color = C.C_SUN_YELLOW if payload["color"] == "yellow" else C.C_SUN_CYAN
            for rgb, count in (((color.red, color.green, color.blue), (20, 28)), ((244, 250, 255), (8, 12))):
                self._spawn_particle("burst_line", "point", point, {"rgb": rgb, "count_range": count})
            self._spawn_particle("star_streak", "circle", (*point, float(payload["radius"]) * self._arena_rect().width * .5), {
                "color": (color.red, color.green, color.blue), "count_range": (10, 16),
                "speed_range": (12, 36), "direction": (0, -1), "bloom_range": (3, 7),
                "duration_ticks": 11, "fade_ticks": 8,
            })
            self._sfx.play("sun_echo" if int(payload["stage"]) == 2 else "sun_burst")
            self._shake = min(.8, self._shake + (.26 if payload.get("hit") else .12))
        elif name == ev.LASER_FIRE:
            anchored = dict(payload)
            anchored["_hit_canvas"] = self._hit_canvas(payload)
            start, end = self._beam_span(anchored)
            self._apply_assets(blocking=False)
            direction = (end[0] - start[0], end[1] - start[1])
            resource = self._laser_asset
            center = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            images = self._spawn_image(resource, center, display=C.LASER_EFFECT_SECONDS,
                                       outro=C.LASER_FADE_SECONDS,
                                       scale=math.hypot(*direction) / resource.size[0] if resource else 1,
                                       z=7)
            for image in images:
                image.rotation = math.degrees(math.atan2(direction[1], direction[0]))
                image._render_rotation = image.rotation
            for index in range(14):
                ratio = (index + .5) / 14
                point = (start[0] + direction[0] * ratio, start[1] + direction[1] * ratio)
                self._spawn_particle("star_streak", "point", point, {
                    "count_range": (1, 2), "color": laser_palette(ratio), "direction": direction,
                    "speed_range": (160, 280), "bloom_range": (3, 7), "spread": 8,
                    "duration_ticks": 22, "fade_ticks": 4,
                })
            self._spawn_skill_feedback(anchored, C.damage_rgb())
            # The shared image effect and registered particles are composed
            # inside this game's arena clip, with no duplicate desktop decal.
        elif name == ev.STARBURST_FIRE:
            resource = self._starburst_resource()
            if resource is not None:
                if self._starburst is not None:
                    self._feedback.remove(self._starburst)
                arena = self._arena_rect()
                images = self._spawn_image(
                    resource, (arena.center.x, arena.center.y),
                    display=C.STARBURST_EFFECT_SECONDS - C.STARBURST_FADE_SECONDS,
                    outro=C.STARBURST_FADE_SECONDS, scale=arena.width / resource.size[0],
                    animate=True, playback_rate=2, animation_loop=False, z=6,
                )
                self._starburst = images[0] if images else None
                self._starburst_elapsed = 0.0
                self._starburst_impact_fired = False
                self._starburst_payload = dict(payload)
                self._deferred_numbers = [p for p in self._deferred_numbers if p.get("defer_visual")]
            else:
                self._starburst_impact(payload)
        elif name == ev.VOID_HAND:
            # Resolve this cue through the desktop's shared sound catalogue.
            self._sfx.play("void_hand")
            self._light_card(payload)
            anchor = payload.get("point", (0.5, 0.5))
            landed = dict(payload)
            landed["_hit_canvas"] = self._hit_canvas(payload)
            target = self._current_ball(str(payload.get("target", "")))
            spot = (float(target.x), float(target.y)) if target is not None else (float(anchor[0]), float(anchor[1]))
            screen = self._canvas_point(spot)
            resource = self._void_hand_resource()
            images = self._spawn_image(resource, screen, outro=C.VOID_HAND_EFFECT_SECONDS,
                                       scale=self._arena_rect().width * .46 / resource.size[0] if resource else 1,
                                       z=8)
            for image in images:
                image.rotation = self._visual_rng.uniform(0.0, 360.0)
                image._render_rotation = image.rotation
            self._spawn_particle("white_pink_collision", "point", screen)
            for rgb in ((190, 90, 255), (255, 80, 110)):
                self._spawn_particle("burst_line", "point", screen, {"rgb": rgb, "count_range": (10, 14)})
            # Daniya's skill number belongs on the damaged dango's body, not on
            # the hand contact point.
            self._spawn_skill_feedback(landed, C.damage_rgb())
        elif name == ev.SKILL_DAMAGE:
            hp = (int(payload.get("hp_left", 0) or 0), int(payload.get("hp_right", 0) or 0))
            self._last_hp = hp
            if str(payload.get("skill", "")).startswith("sun_spirit_") or payload.get("skill") == "frost_slash":
                self._show_damage_number(payload, C.damage_rgb())
            if payload.get("defer_visual"):
                # Starburst: the number waits for the mech landing.
                self._deferred_numbers.append(dict(payload))
                return
            self._shake = min(1.0, self._shake + 0.18)

    def _spawn_frost_slash(self, payload: dict) -> None:
        """Compose the weapon, petals and registered desktop sparks."""
        self._light_card(payload)
        self._sfx.play("sun_echo")
        self._apply_assets(blocking=False)
        center = self._canvas_point(payload["point"])
        radius = float(payload["radius"]) * self._arena_rect().width
        angle = -140 if payload["side"] == "left" else 40
        speed = 6000 if payload["side"] == "left" else -6000
        resource = self._blade_asset
        if resource is not None:
            width, height = resource.size
            pivot_x, pivot_y = self._blade_pivot
            scale = radius / max(1, width * (1 - pivot_x))
            images = self._spawn_image(resource, center, outro=.12, scale=scale, z=11,
                rotation=angle, angular_velocity=speed, angular_duration=.06,
                orbit_offset=((width * .5 - width * pivot_x) * scale,
                              (height * .5 - height * pivot_y) * scale), orbit_start_angle=angle)
            for image in images:
                self._frost_blades.append({"image": image, "side": payload["side"], "center": center,
                                          "radius": radius, "born": self._visual_clock, "next_tail": 0.0})
        trail = self._slash_trail_asset if payload["side"] == "left" else self._slash_trail_reverse_asset
        if trail is not None:
            for image in self._spawn_image(trail, center, outro=.52,
                                          scale=radius / 112, z=10, rotation=angle,
                                          angular_velocity=speed, angular_duration=.06):
                self._frost_blades.append({"image": image, "side": payload["side"], "center": center,
                                          "radius": radius, "born": self._visual_clock, "next_tail": 0.0, "tail": False})
        if self._slash_flash_asset is not None:
            self._spawn_image(self._slash_flash_asset, center, outro=.12,
                              scale=radius / 112, z=10)
        self._advance_frost_blades()
        self._spawn_particle("frost_snowflake", "circle", (*center, radius * .7), {
            "count": 14, "life": (.55, .85), "speed": (35, 90), "size": (8, 14),
            "seed": self._visual_rng.getrandbits(32),
        })
        if self._petal_assets:
            for index in range(8):
                angle = self._visual_rng.uniform(0, math.tau)
                distance = radius * self._visual_rng.uniform(.4, 1)
                point = (center[0] + math.cos(angle) * distance, center[1] + math.sin(angle) * distance)
                end = (point[0] + math.cos(angle) * 26, point[1] + 22 + self._visual_rng.uniform(0, 30))
                self._spawn_image(self._petal_assets[index % len(self._petal_assets)], point,
                    display=.08, outro=.72, scale=self._visual_rng.uniform(.24, .38), z=9,
                    rotation=self._visual_rng.uniform(0, 360), angular_velocity=self._visual_rng.uniform(-180, 180),
                    outro_end_pos=end)
        if payload.get("hit"):
            ball = self._current_ball(str(payload.get("target", "")))
            point = self._canvas_point((ball.x, ball.y)) if ball is not None else center
            for rgb in ((225, 250, 255), (112, 215, 255)):
                self._spawn_particle("burst_line", "point", point, {"rgb": rgb, "count_range": (8, 12)})
            self._shake = min(1.0, self._shake + .16)

    # ── snapshot / paint ─────────────────────────────────────────
    def _snapshot(self) -> ScreenSnapshot:
        match = self._match
        snapshot = self._build_snapshot(match)
        self._last_views = snapshot.characters
        return snapshot

    def _build_snapshot(self, match) -> ScreenSnapshot:
        if self._starburst is None:
            self._flush_deferred_numbers()
        views = []
        for dango, ball in zip(match.dango, match.balls):
            dango.x, dango.y = ball.x, ball.y
            if dango.state == "attack":
                duration = C.ATTACK_SECONDS
            elif dango.state == "hurt":
                duration = C.HURT_SECONDS
            else:
                duration = 0.0
            scale_x, scale_y = deform(dango.state, dango.state_seconds, dango.stretch_phase, duration)
            stretch = mirrored(scale_x, scale_y, ball.vx, ball.vy)
            views.append(CharacterView(
                side=dango.side,
                character_id=dango.character_id,
                name=dango.name,
                hp=dango.hp,
                max_hp=dango.max_hp,
                meters=dango.meters,
                charges=dict(dango.charges),
                sun_energy=tuple(dango.sun_energy),
                sun_cooldowns=dict(dango.sun_cooldowns),
                frost_energy=dango.frost_energy,
                frost_elapsed=dango.frost_elapsed,
                slow_seconds=dango.slow_seconds,
                slow_multiplier=dango.slow_multiplier,
                state=dango.state,
                stretch=stretch,
                flipped=ball.vx < 0.0,
                flash=hit_flash_alpha(dango),
                x=ball.x,
                y=ball.y,
                radius=dango.radius,
                hits_taken=dango.hits_taken,
                hits_dealt=dango.hits_dealt,
            ))
        reveal_pending = match.phase.value == PHASE_RESULT and bool(match.result_reveal)
        spirits = []
        for identity, effect in list(self._sun_spirit_fx.items()):
            visual = effect["visual"]
            age = max(0.0, self._clock - effect["born"])
            stage_age = max(0.0, self._clock - effect["stage_born"])
            final_stage = visual.stage == 2 or (visual.color == "yellow" and visual.stage == 1)
            # A KO cancels remaining logical blasts. Let an already-fired
            # afterimage finish, and remove any untriggered warning now.
            cancelled = match.phase is not MatchPhase.FIGHT
            if cancelled:
                for image in effect.get("images", ()):
                    self._feedback.remove(image)
                effect["images"] = []
            if (final_stage or cancelled) and (visual.stage == 0 or stage_age >= C.SUN_BURST_EFFECT_SECONDS):
                del self._sun_spirit_fx[identity]
                continue
            spirits.append(replace(visual, age=age, stage_age=stage_age, cancelled=cancelled))
        series = match.series
        flash_side = ""
        if self._card_flash and self._clock < float(self._card_flash.get("until", 0.0)):
            flash_side = str(self._card_flash.get("side", ""))
        self._roster_page = max(0, min(self._roster_page, roster_page_count() - 1))
        return ScreenSnapshot(
            series_left=int(series.left),
            series_right=int(series.right),
            series_label=series.format.label,
            series_round=int(series.played),
            series_over=bool(series.over),
            series_winner=str(getattr(self, "_series_winner", "") or ""),
            skill_flash_side=flash_side,
            phase=match.phase,
            picked_side=match.picked_side,
            left_id=match.left.character_id,
            right_id=match.right.character_id,
            roster_page=self._roster_page,
            bet_side=match.bet.side,
            bet_amount=match.bet.amount,
            can_start=match.can_start,
            countdown=match.countdown,
            auto_advance_seconds=(C.AUTO_ADVANCE_SECONDS if self._auto_advance is None else self._auto_advance)
            if match.phase is MatchPhase.RESULT and not reveal_pending and not match.series_over else None,
            presentation_seconds=self._clock,
            characters=tuple(views),
            record=match.record,
            round_index=match.round_index,
            result=match.result if match.phase.value == PHASE_RESULT and not reveal_pending else None,
            paused=self._paused,
            round_duration=match.tracker.elapsed,
            draw=match.result.draw if match.phase.value == PHASE_RESULT else False,
            sun_spirits=tuple(spirits),
        )

    def _transform(self) -> CanvasTransform:
        return CanvasTransform.fit(float(self.width()), float(self.height()))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            transform = self._transform()
            painter.translate(transform.offset_x, transform.offset_y)
            painter.scale(transform.scale, transform.scale)
            snapshot = self._snapshot()
            sprites = {}
            flashes = {}
            for view in snapshot.characters:
                source = self._sprite_for(view)
                if source is not None:
                    sprites[view.character_id] = source
                flash_source = self._flash_sprite_for(view)
                if flash_source is not None:
                    flashes[view.character_id] = flash_source
            if snapshot.phase is MatchPhase.SELECT:
                sprites.update(self._sprites)
            shake_x = shake_y = 0.0
            if self._shake > 0.0:
                amplitude = self._shake * max(2.0, CANVAS_WIDTH * 0.004)
                shake_x = math.sin(self._clock * 63.0) * amplitude
                shake_y = math.cos(self._clock * 57.0) * amplitude
            batch, self._hit_map = paint_snapshot(
                snapshot,
                CANVAS_WIDTH,
                CANVAS_HEIGHT,
                sprites=sprites,
                flashes=flashes,
                flash=self._flash,
                shake=(shake_x, shake_y),
                watermark=self._watermark_frame(snapshot.phase),
                feedback=self._feedback.batch(),
            )
            self._painter_host.render(batch, painter)
        finally:
            painter.end()

    def _watermark_frame(self, phase: MatchPhase) -> FrameSource | None:
        if phase is MatchPhase.SELECT:
            return None
        resource = self._watermark_resource()
        if resource is None:
            return None
        return FrameSource(resource.resource_id, 1, resource.frames[0])

    # ── input ────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        transform = self._transform()
        x, y = transform.to_local(float(event.pos().x()), float(event.pos().y()))
        hit = self._hit_map.hit_test(x, y)
        if hit is None:
            return
        if hit.action == ACTION_CLOSE:
            self._request_close()
        elif self._match.phase.value == PHASE_FIGHT:
            return
        elif hit.action == ACTION_NEXT:
            if self._match.result_reveal in (None, 0.0):
                self._next_round(manual=True)
        elif hit.action == ACTION_FIGHT:
            self._handle_select_hit(hit.value)

    def _handle_select_hit(self, value: str) -> None:
        match = self._match
        if match.phase is not MatchPhase.SELECT:
            return
        accepted = False
        if value.startswith("amount:"):
            accepted = match.choose_bet_amount(int(value.split(":", 1)[1]))
        elif value.startswith("roster_page:"):
            self._set_roster_page(int(value.split(":", 1)[1]))
        elif value in ("left", "right"):
            accepted = match.choose_dango(value)
        elif value.startswith("pick:"):
            _, side, character_id = value.split(":", 2)
            accepted = match.choose_dango(side, character_id)
        elif value.startswith("format:"):
            accepted = match.choose_format(value.split(":", 1)[1])
        elif not value and match.can_start:
            self._start_match()
        if accepted:
            self._sfx.play("ui_click")
        self.update()

    def _set_roster_page(self, page: int) -> None:
        """The shared grid is the source of truth for roster page bounds."""
        if self._match.phase is MatchPhase.SELECT:
            next_page = max(0, min(int(page), roster_page_count() - 1))
            if next_page != self._roster_page:
                self._roster_page = next_page
                self._sfx.play("ui_click")
            self.update()

    def _start_match(self) -> None:
        if self._match.start_match():
            self._countdown_ticks = 0
            self._sfx.play("ui_confirm")

    def keyPressEvent(self, event) -> None:
        if event.isAutoRepeat():
            return
        key = event.key()
        phase = self._match.phase.value
        if key == Qt.Key_F11:
            if callable(self._fullscreen_callback):
                self._fullscreen_callback()
                self._sfx.play("ui_click")
            return
        if key == Qt.Key_Escape:
            self._request_close()
            return
        if phase == PHASE_RESULT:
            if key in (Qt.Key_R, Qt.Key_Return, Qt.Key_Enter):
                self._next_round(manual=True)
            return
        if phase == PHASE_FIGHT:
            if key == Qt.Key_P:
                self._paused = not self._paused
                if not self._paused:
                    self._last_tick = time.perf_counter()
                self._sfx.play("ui_click")
                self.update()
            return
        match = self._match
        if key in (Qt.Key_F1, Qt.Key_F2, Qt.Key_F3):
            self._handle_select_hit("format:" + ("bo5", "bo3", "bo1")[key - Qt.Key_F1])
        elif key == Qt.Key_PageUp:
            self._set_roster_page(self._roster_page - 1)
        elif key == Qt.Key_PageDown:
            self._set_roster_page(self._roster_page + 1)
        elif key == Qt.Key_1:
            self._handle_select_hit("left")
        elif key == Qt.Key_2:
            self._handle_select_hit("right")
        elif key == Qt.Key_A:
            self._handle_select_hit("left")
        elif key == Qt.Key_B:
            self._handle_select_hit("right")
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._start_match()
        elif key in (Qt.Key_3, Qt.Key_4, Qt.Key_5):
            # Qt uses the same key code for the keypad, with KeypadModifier.
            index = key - Qt.Key_3
            self._handle_select_hit(f"amount:{C.BET_AMOUNTS[index]}")
        elif key == Qt.Key_R and not event.modifiers():
            match.randomize()
            self._sfx.play("ui_click")
        self.update()

    def _request_close(self) -> None:
        if callable(self._close_callback):
            self._sfx.play("ui_back")
            self.set_bgm_callback(None)
            self._close_callback()
