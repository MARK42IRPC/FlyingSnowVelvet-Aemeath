"""Native DirectX host for the shared speaker search visual."""
from __future__ import annotations

from concurrent.futures import Future
from typing import Callable

from config.config import CLOUD_MUSIC, SPEAKER_SEARCH_UI, UI
from config.tooltip_config import TOOLTIPS
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.application_visuals import create_portable_command_hint_metrics
from lib.core.graphics.commands import DrawBatch, scale_batch_alpha
from lib.core.graphics.screen import clamp_rect_position
from lib.core.graphics.speaker_visuals import (
    SPEAKER_SEARCH_MODES,
    SpeakerSearchVisualDescription,
    build_speaker_search_visual,
    speaker_visual_hit_test,
)
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import Key, MouseButton
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.world_objects import WorldObjectInstance

from .loop import DxLoopContext
from .opacity import DxOpacityAnimator
from .screen import DxScreenProvider, get_cursor_position
from .speaker_playlist import DxSpeakerPlaylistWindow
from .text_metrics import create_directwrite_text_metrics
from .window_host import DxWindowHost


_logger = get_logger(__name__)


_TOOLTIP_KEYS = {
    "input": "speaker_search_dialog",
    "search": "speaker_search_dialog",
    "result": "speaker_search_result_box",
    "play_pause": "speaker_play_pause",
    "next_track": "speaker_next",
    "provider": "speaker_platform_mode",
    "priority": "speaker_search_priority",
    "login": "speaker_music_login",
    "playlist": "speaker_playlist_toggle",
}


class DxSpeakerSearchWindow:
    """Own input, search state and native lifetime for one shared presenter."""

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
        self._input = ""
        self._composition = ""
        self._items: list[tuple[object, str]] = []
        self._search_mode = "song"
        self._searching = False
        self._search_generation = 0
        self._future: Future | None = None
        self._page = 0
        self._selected = -1
        self._hovered = ""
        self._hovered_result = -1
        self._pressed = ""
        self._pressed_result = -1
        self._playing = False
        self._logged_in = False
        self._provider_label = "音乐模式"
        self._clickthrough = False
        self._visual = self._build_visual()
        self._playlist: DxSpeakerPlaylistWindow | None = None
        self._cleanup_done = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    @property
    def visual(self) -> SpeakerSearchVisualDescription:
        return self._visual

    def _music_service(self) -> object | None:
        if not callable(self._music_service_provider):
            return None
        try:
            return self._music_service_provider()
        except Exception as exc:
            _logger.warning("DX speaker music service unavailable: %s", exc)
            return None

    def _sync_music_state(self) -> None:
        service = self._music_service()
        if service is None:
            return
        try:
            self._playing = bool(service.is_playing() and not service.is_paused())
        except Exception:
            pass
        try:
            self._logged_in = bool(service.is_logged_in())
        except Exception:
            pass
        try:
            self._provider_label = str(service.provider_mode_label)
        except Exception:
            pass

    def _build_visual(self) -> SpeakerSearchVisualDescription:
        return build_speaker_search_visual(
            self._input,
            self._composition,
            tuple(display for _track_ref, display in self._items),
            self._metrics,
            searching=self._searching,
            page=self._page,
            selected=self._selected,
            search_mode=self._search_mode,
            playing=self._playing,
            logged_in=self._logged_in,
            provider_label=self._provider_label,
            hovered=(
                "result" if self._hovered_result >= 0 else self._hovered
            ),
            pressed=(
                "result" if self._pressed_result >= 0 else self._pressed
            ),
            layer=int(Layer.PET_UI),
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
        gap = int(SPEAKER_SEARCH_UI.get("gap", 6)) * scale
        search_top = (
            target.y + target.height / 2.0
            - int(SPEAKER_SEARCH_UI.get("height", 36)) * scale / 2.0
            - 30 * scale
        )
        proposed_x = target.x + target.width + gap
        proposed_y = search_top - 66 * scale
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
            raise RuntimeError("DX speaker search window has been cleaned")
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
            get_layer_manager().register(host, Layer.PET_UI, name="DxSpeakerSearch")
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
        previous_size = self._visual.size
        self._visual = self._build_visual()
        host = self._host
        if host is None:
            return
        if geometry or self._visual.size != previous_size:
            host.set_geometry(self._geometry())
        host.request_repaint()

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    def toggle(self, target: WorldObjectInstance) -> None:
        if target.backend_id != "directx" or target.object_type != "speaker":
            return
        if self.is_visible() and target == self._target:
            self.hide()
            return
        if self._playlist is not None:
            self._playlist.hide()
        self._target = target
        self._input = ""
        self._composition = ""
        self._items = []
        self._page = 0
        self._selected = -1
        self._hovered = ""
        self._hovered_result = -1
        self._sync_music_state()
        self._visual = self._build_visual()
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        host.set_clickthrough(self._clickthrough)
        host.show()
        self._visible = True
        self._opacity.fade_in()
        host.activate()
        set_ime_position = getattr(host, "set_ime_position", None)
        if callable(set_ime_position):
            rect = self._visual.input_rect
            set_ime_position(int(rect.x + 6), int(rect.y + rect.height - 4))
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
        if callable(self._tooltip_hider):
            self._tooltip_hider()
        self._visible = False
        self._composition = ""
        self._pressed = ""
        self._pressed_result = -1
        host = self._host
        if host is not None:
            self._opacity.fade_out(host.hide)

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            self._host.set_clickthrough(self._clickthrough)
        if self._playlist is not None:
            self._playlist.set_clickthrough(self._clickthrough)

    def update_music_state(self, data: dict) -> None:
        self._playing = bool(data.get("playing", self._playing))
        if data.get("play_mode") is not None:
            pass
        if self.is_visible():
            self._refresh()

    def update_login_state(self, data: dict) -> None:
        self._logged_in = bool(data.get("logged_in", self._logged_in))
        self._sync_music_state()
        if self.is_visible():
            self._refresh()

    def tick(self) -> None:
        if self._playlist is not None:
            self._playlist.tick()
        if not self.is_visible():
            return
        geometry = self._target_geometry()
        if geometry is None:
            self.hide()
            return
        cursor = self._cursor_position_provider()
        panel = self._geometry()
        if self._host is not None:
            self._host.set_geometry(panel)
            panel = self._host.get_geometry()
        nearest_x = max(panel.x, min(cursor.x, panel.x + panel.width))
        nearest_y = max(panel.y, min(cursor.y, panel.y + panel.height))
        dx = cursor.x - nearest_x
        dy = cursor.y - nearest_y
        limit = float(UI.get("auto_hide_mouse_distance", 300))
        if dx * dx + dy * dy > limit * limit:
            self.hide()
            return

    def _start_search(self) -> None:
        keyword = self._input.strip()
        if not keyword or self._searching:
            return
        service = self._music_service()
        if service is None:
            self._show_error("音乐服务不可用")
            return
        self._search_generation += 1
        generation = self._search_generation
        mode = self._search_mode
        self._searching = True
        self._items = []
        self._page = 0
        self._selected = -1
        self._refresh(geometry=True)

        def worker():
            return service.search(
                keyword,
                mode=mode,
                limit=int(CLOUD_MUSIC.get("search_result_limit", 128)),
                fallback_enabled=False,
            )

        future = get_compute_hub().submit_io(worker)
        self._future = future

        def complete(done: Future) -> None:
            try:
                tracks = done.result()
                items = []
                for track in tracks:
                    track_ref = getattr(track, "track_id", None)
                    display = str(getattr(track, "display", "") or "").strip()
                    if track_ref is not None and display:
                        items.append((track_ref, display))
                self._context.post(
                    lambda generation=generation, items=items: self._finish_search(
                        generation, items, "",
                    )
                )
            except Exception as exc:
                error = str(exc)
                self._context.post(
                    lambda generation=generation, error=error: self._finish_search(
                        generation, [], error,
                    )
                )

        future.add_done_callback(complete)

    def _finish_search(self, generation: int, items: list[tuple[object, str]], error: str) -> None:
        if self._cleanup_done or generation != self._search_generation:
            return
        self._future = None
        self._searching = False
        self._items = list(items)
        self._page = 0
        self._selected = 0 if items else -1
        self._refresh(geometry=True)
        if error:
            self._show_error(error)

    def _show_error(self, message: str) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": f"[搜索失败] {message}", "min": 1, "max": 180,
        }))

    def _page_items(self) -> list[tuple[object, str]]:
        start = self._page * 5
        return self._items[start:start + 5]

    def _turn_page(self, direction: int) -> None:
        if self._searching or not self._items:
            return
        max_page = max(0, (len(self._items) - 1) // 5)
        self._page = (self._page + int(direction)) % (max_page + 1)
        self._selected = 0
        self._refresh(geometry=True)

    def _cycle_priority(self) -> None:
        try:
            index = SPEAKER_SEARCH_MODES.index(self._search_mode)
        except ValueError:
            index = 0
        self._search_mode = SPEAKER_SEARCH_MODES[(index + 1) % len(SPEAKER_SEARCH_MODES)]
        self._refresh()

    def _playlist_window(self) -> DxSpeakerPlaylistWindow:
        window = self._playlist
        if window is None:
            window = DxSpeakerPlaylistWindow(
                self._context,
                self._screen_provider,
                music_service_provider=self._music_service_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                cursor_position_provider=self._cursor_position_provider,
                tooltip_requester=self._tooltip_requester,
                tooltip_hider=self._tooltip_hider,
            )
            window.set_clickthrough(self._clickthrough)
            self._playlist = window
        return window

    def _dispatch_action(self, action: str) -> None:
        if action == "search":
            self._start_search()
        elif action == "priority":
            self._cycle_priority()
        elif action == "play_pause":
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": not self._playing}))
        elif action == "next_track":
            self._event_center.publish(Event(EventType.MUSIC_NEXT_TRACK, {}))
        elif action == "login":
            if self._logged_in:
                self._event_center.publish(Event(EventType.INFORMATION, {
                    "text": "音乐平台账号已登录", "min": 0, "max": 60,
                }))
            else:
                self._event_center.publish(Event(EventType.MUSIC_LOGIN_REQUEST, {}))
        elif action == "provider":
            service = self._music_service()
            if service is not None:
                try:
                    service.cycle_provider(persist=True)
                except Exception as exc:
                    self._show_error(str(exc))
                self._sync_music_state()
                self._refresh()
        elif action == "playlist":
            target = self._target
            if target is not None:
                self.hide()
                self._playlist_window().show_for(target)

    def _activate_result(self, index: int, button: MouseButton) -> None:
        items = self._page_items()
        if not (0 <= index < len(items)):
            return
        track_ref, display = items[index]
        if button == MouseButton.LEFT:
            event_type = EventType.MUSIC_PLAY_TOP
        elif button == MouseButton.RIGHT:
            event_type = EventType.MUSIC_ENQUEUE
        else:
            return
        self._event_center.publish(Event(event_type, {
            "song_id": track_ref,
            "track_ref": track_ref,
            "display": display,
        }))

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._visual.batch, self._opacity.value)

    def handle_text_input(self, text: str) -> None:
        value = "".join(character for character in str(text or "") if character.isprintable())
        remaining = max(0, 512 - len(self._input))
        if value and remaining:
            self._input += value[:remaining]
            self._composition = ""
            self._refresh()

    def handle_ime_composition(self, text: str) -> None:
        self._composition = str(text or "")[:max(0, 512 - len(self._input))]
        self._refresh()

    def handle_ime_end(self) -> None:
        if self._composition:
            self._composition = ""
            self._refresh()

    def handle_key_press(self, event: object) -> None:
        key = getattr(event, "key", Key.UNKNOWN)
        if key == Key.ESCAPE:
            self.hide()
        elif key == Key.BACKSPACE and not self._composition:
            self._input = self._input[:-1]
            self._refresh()
        elif key in (Key.RETURN, Key.ENTER) and not self._composition:
            self._start_search()
        elif key == Key.UP and self._page_items():
            self._selected = max(0, self._selected - 1)
            self._refresh()
        elif key == Key.DOWN and self._page_items():
            self._selected = min(len(self._page_items()) - 1, self._selected + 1)
            self._refresh()
        elif key == Key.LEFT:
            self._turn_page(-1)
        elif key == Key.RIGHT:
            self._turn_page(1)

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_pointer_move(self, event: object) -> None:
        pos = getattr(event, "pos", Point())
        action, index = speaker_visual_hit_test(self._visual, pos.x, pos.y)
        hovered_result = index if action == "result" else -1
        if action != self._hovered or hovered_result != self._hovered_result:
            self._hovered = action
            self._hovered_result = hovered_result
            if hovered_result >= 0:
                self._selected = hovered_result
            self._refresh()
        tooltip_key = _TOOLTIP_KEYS.get("result" if index >= 0 else action, "")
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
        action, index = speaker_visual_hit_test(self._visual, pos.x, pos.y)
        if action:
            self._publish_click_particle(event)
        if action == "result":
            self._activate_result(index, button)
            return
        if button != MouseButton.LEFT:
            return
        if action == "page_prev":
            self._turn_page(-1)
            return
        if action == "page_next":
            self._turn_page(1)
            return
        if action and action != "input":
            self._pressed = action
            capture = getattr(self._host, "capture_mouse", None)
            if callable(capture):
                capture()
            self._refresh()
        if self._host is not None:
            self._host.activate()

    def handle_pointer_release(self, button: MouseButton) -> None:
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
        self._hovered = ""
        self._hovered_result = -1
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
        self._search_generation += 1
        future, self._future = self._future, None
        if future is not None:
            future.cancel()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()
        playlist, self._playlist = self._playlist, None
        if playlist is not None:
            playlist.cleanup()


__all__ = ["DxSpeakerSearchWindow"]
