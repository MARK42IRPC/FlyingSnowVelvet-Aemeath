"""Native DirectX host for the backend-neutral speaker playlist visual."""
from __future__ import annotations

from collections.abc import Callable

from config.config import UI
from config.tooltip_config import TOOLTIPS
from config.scale import scale_px
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.application_visuals import create_portable_command_hint_metrics
from lib.core.graphics.commands import DrawBatch, scale_batch_alpha
from lib.core.graphics.screen import clamp_rect_position
from lib.core.graphics.speaker_playlist_visuals import (
    PLAYLIST_PAGE_SIZE,
    SpeakerPlaylistVisualDescription,
    build_speaker_playlist_visual,
    speaker_playlist_hit_test,
)
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import Key, MouseButton, MouseButtons
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.world_objects import WorldObjectInstance

from .loop import DxLoopContext
from .opacity import DxOpacityAnimator
from .screen import DxScreenProvider, get_cursor_position
from .text_metrics import create_directwrite_text_metrics
from .window_host import DxWindowHost


_logger = get_logger(__name__)


_TOOLTIP_KEYS = {
    "row": "playlist_panel",
    "remove": "playlist_remove_song",
    "play_selected": "playlist_play_now",
    "play_pause": "speaker_play_pause",
    "next_track": "speaker_next",
    "history": "speaker_history_queue",
    "local": "speaker_local_queue",
    "liked": "speaker_like_queue",
    "clear": "speaker_clear_queue",
    "play_mode": "speaker_play_mode",
    "volume_up": "speaker_volume_up",
    "volume_down": "speaker_volume_down",
}


class DxSpeakerPlaylistWindow:
    """Own the native playlist, progress and queue-control interaction."""

    def __init__(
        self,
        context: DxLoopContext,
        screen_provider: DxScreenProvider,
        *,
        music_service_provider: Callable[[], object] | None,
        window_host_factory: Callable[..., DxWindowHost],
        warp: bool,
        cursor_position_provider: Callable[[], Point] = get_cursor_position,
        tooltip_requester: Callable[[str, Point], None] | None = None,
        tooltip_hider: Callable[[], None] | None = None,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider
        self._music_service_provider = music_service_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._cursor_position_provider = cursor_position_provider
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._event_center = get_event_center()
        self._metrics = create_portable_command_hint_metrics()
        self._host: DxWindowHost | None = None
        self._visible = False
        self._target: WorldObjectInstance | None = None
        self._queue: list[tuple[object, str]] = []
        self._current_index = -1
        self._page = 0
        self._selected = -1
        self._playing = False
        self._play_mode = "list_loop"
        self._logged_in = False
        self._progress = 0.0
        self._remaining = 0
        self._dragging_progress = False
        self._drag_progress = 0.0
        self._progress_request_ticks = 0
        self._hovered = ""
        self._hovered_row = -1
        self._pressed = ""
        self._clickthrough = False
        self._cleanup_done = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)
        self._visual = self._build_visual()
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status)
        self._event_center.subscribe(EventType.MUSIC_PROGRESS, self._on_music_progress)
        self._event_center.subscribe(EventType.MUSIC_SONG_END, self._on_song_end)
        self._event_center.subscribe(EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status)

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    @property
    def visual(self) -> SpeakerPlaylistVisualDescription:
        return self._visual

    def _music_service(self) -> object | None:
        if not callable(self._music_service_provider):
            return None
        try:
            return self._music_service_provider()
        except Exception as exc:
            _logger.warning("DX speaker playlist music service unavailable: %s", exc)
            return None

    def _sync_content(self, *, reset_page: bool = False, preferred_index: int | None = None) -> None:
        old_selected = self._selected_absolute_index()
        service = self._music_service()
        if service is None:
            self._queue = []
            self._current_index = -1
            self._playing = False
            self._logged_in = False
        else:
            try:
                self._queue = list(service.queue_snapshot())
            except Exception:
                self._queue = []
            try:
                self._current_index = int(service.current_index())
            except Exception:
                self._current_index = -1
            try:
                self._playing = bool(service.is_playing() and not service.is_paused())
            except Exception:
                pass
            try:
                self._play_mode = str(service.play_mode())
            except Exception:
                pass
            try:
                self._logged_in = bool(service.is_logged_in())
            except Exception:
                pass
        if not self._queue:
            self._page = 0
            self._selected = -1
            return
        max_page = (len(self._queue) - 1) // PLAYLIST_PAGE_SIZE
        if preferred_index is not None:
            selected = max(0, min(int(preferred_index), len(self._queue) - 1))
            self._page = selected // PLAYLIST_PAGE_SIZE
            self._selected = selected % PLAYLIST_PAGE_SIZE
        elif reset_page:
            self._page = 0
            self._selected = self._default_selected_row()
        elif old_selected >= 0:
            selected = min(old_selected, len(self._queue) - 1)
            self._page = selected // PLAYLIST_PAGE_SIZE
            self._selected = selected % PLAYLIST_PAGE_SIZE
        else:
            self._page = max(0, min(self._page, max_page))
            self._selected = self._default_selected_row()

    def _page_items(self) -> list[tuple[object, str]]:
        start = self._page * PLAYLIST_PAGE_SIZE
        return self._queue[start:start + PLAYLIST_PAGE_SIZE]

    def _selected_absolute_index(self) -> int:
        if self._selected < 0:
            return -1
        index = self._page * PLAYLIST_PAGE_SIZE + self._selected
        return index if 0 <= index < len(self._queue) else -1

    def _default_selected_row(self) -> int:
        items = self._page_items()
        if not items:
            return -1
        offset = self._page * PLAYLIST_PAGE_SIZE
        for row in range(len(items)):
            if offset + row != self._current_index:
                return row
        return 0

    def _build_visual(self) -> SpeakerPlaylistVisualDescription:
        return build_speaker_playlist_visual(
            tuple(self._queue),
            self._metrics,
            current_index=self._current_index,
            page=self._page,
            selected=self._selected,
            playing=self._playing,
            play_mode=self._play_mode,
            logged_in=self._logged_in,
            progress=self._drag_progress if self._dragging_progress else self._progress,
            remaining=self._remaining,
            hovered=("row" if self._hovered_row >= 0 else self._hovered),
            pressed=self._pressed,
            layer=int(Layer.PANEL),
        )

    def _target_geometry(self) -> Rect | None:
        target = self._target
        if target is None:
            return None
        try:
            if not target.is_alive():
                return None
            return target.get_geometry()
        except (RuntimeError, TypeError, ValueError):
            return None

    def _geometry(self) -> Rect:
        target = self._target_geometry()
        screen = (
            self._screen_provider.get_screen_rect_for_point(Point(
                target.x + target.width / 2.0,
                target.y + target.height / 2.0,
            ))
            if target is not None
            else self._screen_provider.get_primary_screen_rect()
        )
        width = self._visual.size.width
        height = self._visual.size.height
        target_point = (
            Point(target.x + target.width / 2.0, target.y + target.height / 2.0)
            if target is not None else Point(
                screen.x + screen.width / 2.0,
                screen.y + screen.height / 2.0,
            )
        )
        scale = self._screen_provider.get_scale_for_point(target_point)
        physical_width = width * scale
        physical_height = height * scale
        if target is None:
            return Rect(
                screen.x + (screen.width - physical_width) / 2.0,
                screen.y + (screen.height - physical_height) / 2.0,
                width,
                height,
            )
        controls_and_progress = scale_px(120, min_abs=1)
        panel_height = (height - controls_and_progress) * scale
        gap = scale_px(6, min_abs=1) * scale
        proposed_x = target.x + target.width + gap
        proposed_y = (
            target.y + target.height / 2.0
            - panel_height / 2.0
            - controls_and_progress * scale
        )
        x, y, _ = clamp_rect_position(
            proposed_x, proposed_y, physical_width, physical_height, screen,
        )
        if x != proposed_x:
            left_x = target.x - physical_width - gap
            alt_x, alt_y, _ = clamp_rect_position(
                left_x, proposed_y, physical_width, physical_height, screen,
            )
            if alt_x == left_x or abs(alt_x - left_x) < abs(x - proposed_x):
                x, y = alt_x, alt_y
        return Rect(x, y, width, height)

    def _ensure_host(self) -> DxWindowHost:
        if self._cleanup_done:
            raise RuntimeError("DX speaker playlist window has been cleaned")
        if self._host is not None:
            return self._host
        geometry = self._geometry()
        host = self._window_host_factory(
            int(geometry.width), int(geometry.height),
            x=int(round(geometry.x)), y=int(round(geometry.y)),
            callbacks=self, warp=self._warp, topmost=True, tool_window=True,
            no_activate=False, clickthrough=self._clickthrough,
            logical_content=True,
        )
        try:
            self._context.register_poller(host)
            get_layer_manager().register(host, Layer.PANEL, name="DxSpeakerPlaylist")
        except Exception:
            self._context.unregister_poller(host)
            host.cleanup()
            raise
        self._host = host
        native_metrics = create_directwrite_text_metrics(host)
        if native_metrics is not None:
            self._metrics = native_metrics
            self._visual = self._build_visual()
            host.set_geometry(self._geometry())
        return host

    def _refresh(self, *, geometry: bool = False) -> None:
        old_size = self._visual.size
        self._visual = self._build_visual()
        host = self._host
        if host is None:
            return
        if geometry or self._visual.size != old_size:
            host.set_geometry(self._geometry())
        host.request_repaint()

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    def show_for(self, target: WorldObjectInstance) -> None:
        if target.backend_id != "directx" or target.object_type != "speaker":
            return
        self._target = target
        self._sync_content(reset_page=True)
        self._hovered = ""
        self._hovered_row = -1
        self._pressed = ""
        self._visual = self._build_visual()
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        host.set_clickthrough(self._clickthrough)
        host.show()
        self._visible = True
        self._opacity.fade_in()
        host.activate()
        get_layer_manager().enforce_burst()
        host.request_repaint()

    def is_visible(self) -> bool:
        return self._visible

    def hide(self) -> None:
        if not self.is_visible():
            return
        geometry = self._host.get_geometry() if self._host is not None else Rect()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "right_fade",
            "area_type": "rect",
            "area_data": (
                geometry.x, geometry.y,
                geometry.x + geometry.width, geometry.y + geometry.height,
            ),
        }))
        self._target = None
        self._visible = False
        self._dragging_progress = False
        self._pressed = ""
        host = self._host
        if host is not None:
            self._opacity.fade_out(host.hide)
        if callable(self._tooltip_hider):
            self._tooltip_hider()

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            self._host.set_clickthrough(self._clickthrough)

    def tick(self) -> None:
        if not self.is_visible():
            return
        if self._target_geometry() is None:
            self.hide()
            return
        self._progress_request_ticks += 1
        if self._progress_request_ticks >= 20 and not self._dragging_progress:
            self._progress_request_ticks = 0
            self._event_center.publish(Event(EventType.MUSIC_PROGRESS_REQUEST, {}))
        panel = self._geometry()
        if self._host is not None:
            self._host.set_geometry(panel)
            panel = self._host.get_geometry()
        cursor = self._cursor_position_provider()
        nearest_x = max(panel.x, min(cursor.x, panel.x + panel.width))
        nearest_y = max(panel.y, min(cursor.y, panel.y + panel.height))
        dx = cursor.x - nearest_x
        dy = cursor.y - nearest_y
        limit = float(UI.get("auto_hide_mouse_distance", 300))
        if dx * dx + dy * dy > limit * limit:
            self.hide()
            return

    def _on_music_status(self, event: Event) -> None:
        if not self.is_visible():
            return
        data = event.data or {}
        self._playing = bool(data.get("playing", self._playing))
        self._play_mode = str(data.get("play_mode", self._play_mode))
        self._sync_content()
        self._refresh(geometry=True)

    def _on_music_progress(self, event: Event) -> None:
        if not self.is_visible() or self._dragging_progress:
            return
        data = event.data or {}
        self._progress = max(0.0, min(1.0, float(data.get("progress", 0.0))))
        self._remaining = max(0, int(data.get("remaining", 0)))
        self._refresh()

    def _on_song_end(self, event: Event) -> None:
        if not self.is_visible():
            return
        self._progress = 0.0
        self._remaining = 0
        self._sync_content()
        self._refresh(geometry=True)

    def _on_login_status(self, event: Event) -> None:
        if not self.is_visible():
            return
        self._logged_in = bool((event.data or {}).get("logged_in", self._logged_in))
        self._sync_content()
        self._refresh()

    def _turn_page(self, direction: int) -> None:
        if len(self._queue) <= PLAYLIST_PAGE_SIZE:
            return
        max_page = (len(self._queue) - 1) // PLAYLIST_PAGE_SIZE
        self._page = (self._page + int(direction)) % (max_page + 1)
        self._selected = self._default_selected_row()
        self._refresh(geometry=True)

    def _move_selected(self, direction: int) -> None:
        index = self._selected_absolute_index()
        if index < 0:
            self._selected = self._default_selected_row()
            self._refresh()
            return
        target = index + int(direction)
        if not (0 <= target < len(self._queue)):
            return
        if index == self._current_index or target == self._current_index:
            self._event_center.publish(Event(EventType.INFORMATION, {
                "text": "当前播放歌曲不可移动", "min": 0, "max": 60,
            }))
            return
        service = self._music_service()
        if service is None:
            return
        try:
            new_index = int(service.move_queue_item(index, int(direction)))
        except Exception as exc:
            _logger.warning("DX playlist move failed: %s", exc)
            return
        if new_index >= 0:
            self._sync_content(preferred_index=new_index)
            self._refresh(geometry=True)

    def _remove_selected(self) -> None:
        index = self._selected_absolute_index()
        if not (0 <= index < len(self._queue)):
            return
        service = self._music_service()
        if service is None:
            return
        track_ref, _display = self._queue[index]
        try:
            if index == self._current_index:
                service.remove_song_from_history(track_ref)
                service.next_track()
                removed = True
            else:
                removed = bool(service.remove_queue_item(index))
                if removed:
                    service.remove_song_from_history(track_ref)
        except Exception as exc:
            _logger.warning("DX playlist remove failed: %s", exc)
            return
        if removed:
            self._sync_content(preferred_index=index)
            self._refresh(geometry=True)

    def _dispatch_action(self, action: str) -> None:
        if action == "play_pause":
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": not self._playing}))
        elif action == "next_track":
            self._event_center.publish(Event(EventType.MUSIC_NEXT_TRACK, {}))
        elif action == "history":
            self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_HISTORY, {}))
        elif action == "local":
            self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LOCAL, {}))
        elif action == "liked":
            if self._logged_in:
                self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LIKED, {}))
            else:
                self._event_center.publish(Event(EventType.INFORMATION, {
                    "text": "请先登录音乐平台账号", "min": 0, "max": 60,
                }))
        elif action == "clear":
            service = self._music_service()
            if service is not None:
                service.clear_queue()
                self._sync_content(reset_page=True)
                self._refresh(geometry=True)
        elif action == "play_mode":
            self._event_center.publish(Event(EventType.MUSIC_PLAY_MODE_TOGGLE, {}))
        elif action in {"volume_up", "volume_down"}:
            delta = 0.05 if action == "volume_up" else -0.05
            self._event_center.publish(Event(EventType.MUSIC_VOLUME, {"delta": delta}))
            service = self._music_service()
            try:
                percent = int(service.get_volume_percent()) if service is not None else 0
            except Exception:
                percent = 0
            self._event_center.publish(Event(EventType.INFORMATION, {
                "text": f"音量 {percent}%", "min": 0,
            }))
        elif action == "remove":
            self._remove_selected()
        elif action == "play_selected":
            index = self._selected_absolute_index()
            if index >= 0:
                self._event_center.publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {"index": index}))

    def _set_drag_progress(self, x: float) -> None:
        slider = self._visual.slider_rect
        clamped = max(slider.x, min(float(x), slider.x + slider.width))
        self._drag_progress = (clamped - slider.x) / slider.width if slider.width else 0.0
        self._refresh()

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._visual.batch, self._opacity.value)

    def handle_key_press(self, event: object) -> None:
        key = getattr(event, "key", Key.UNKNOWN)
        if key == Key.ESCAPE:
            self.hide()
        elif key == Key.LEFT:
            self._move_selected(-1)
        elif key == Key.RIGHT:
            self._move_selected(1)

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_pointer_move(self, event: object) -> None:
        pos = getattr(event, "pos", Point())
        if self._dragging_progress and getattr(event, "buttons", MouseButtons.NONE) & MouseButtons.LEFT:
            self._set_drag_progress(pos.x)
            return
        action, row = speaker_playlist_hit_test(self._visual, pos.x, pos.y)
        hovered_row = row if action == "row" else -1
        if action != self._hovered or hovered_row != self._hovered_row:
            self._hovered = action
            self._hovered_row = hovered_row
            if hovered_row >= 0:
                self._selected = hovered_row
            self._refresh()
        tooltip_key = _TOOLTIP_KEYS.get("row" if row >= 0 else action, "")
        if tooltip_key and callable(self._tooltip_requester):
            point = getattr(event, "screen_pos", getattr(event, "global_pos", Point()))
            self._tooltip_requester(TOOLTIPS[tooltip_key], point)
        elif callable(self._tooltip_hider):
            self._tooltip_hider()

    def _publish_click_particle(self, event: object) -> None:
        button = getattr(event, "button", MouseButton.NONE)
        if button not in {MouseButton.LEFT, MouseButton.RIGHT}:
            return
        point = getattr(event, "screen_pos", getattr(event, "global_pos", None))
        if not isinstance(point, Point):
            local = getattr(event, "pos", Point())
            geometry = self._host.get_geometry() if self._host is not None else Rect()
            point = Point(geometry.x + local.x, geometry.y + local.y)
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "click" if button == MouseButton.LEFT else "pink_click",
            "area_type": "point",
            "area_data": (point.x, point.y),
        }))

    def handle_pointer_press(self, event: object) -> None:
        pos = getattr(event, "pos", Point())
        button = getattr(event, "button", MouseButton.NONE)
        action, row = speaker_playlist_hit_test(self._visual, pos.x, pos.y)
        if action:
            self._publish_click_particle(event)
        if action == "progress" and button == MouseButton.LEFT:
            self._dragging_progress = True
            self._set_drag_progress(pos.x)
            capture = getattr(self._host, "capture_mouse", None)
            if callable(capture):
                capture()
            return
        if action == "row":
            self._selected = row
            if button == MouseButton.LEFT:
                self._move_selected(-1)
            elif button == MouseButton.RIGHT:
                self._move_selected(1)
            return
        if button != MouseButton.LEFT:
            return
        if action == "page_prev":
            self._turn_page(-1)
            return
        if action == "page_next":
            self._turn_page(1)
            return
        if action:
            self._pressed = action
            capture = getattr(self._host, "capture_mouse", None)
            if callable(capture):
                capture()
            self._refresh()
        if self._host is not None:
            self._host.activate()

    def handle_pointer_release(self, button: MouseButton) -> None:
        if self._dragging_progress and button == MouseButton.LEFT:
            self._dragging_progress = False
            self._progress = self._drag_progress
            self._event_center.publish(Event(EventType.MUSIC_SEEK, {"progress": self._progress}))
            release = getattr(self._host, "release_mouse", None)
            if callable(release):
                release()
            self._refresh()
            return
        action = self._pressed
        commit = bool(action and action == self._hovered and button == MouseButton.LEFT)
        self._pressed = ""
        release = getattr(self._host, "release_mouse", None)
        if callable(release):
            release()
        self._refresh()
        if commit:
            self._dispatch_action(action)

    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        if not self._dragging_progress:
            self._hovered = ""
            self._hovered_row = -1
            self._refresh()
        if callable(self._tooltip_hider):
            self._tooltip_hider()

    def handle_window_moved(self, position: Point) -> None:
        return None

    def handle_host_close(self) -> None:
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._visible = False
        self._opacity.cancel()
        if callable(self._tooltip_hider):
            self._tooltip_hider()
        self._event_center.unsubscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status)
        self._event_center.unsubscribe(EventType.MUSIC_PROGRESS, self._on_music_progress)
        self._event_center.unsubscribe(EventType.MUSIC_SONG_END, self._on_song_end)
        self._event_center.unsubscribe(EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status)
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


__all__ = ["DxSpeakerPlaylistWindow"]
