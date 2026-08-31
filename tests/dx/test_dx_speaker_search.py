from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.dx_bridge.speaker_search import DxSpeakerSearchWindow
from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import Key, MouseButton
from lib.core.world_objects import (
    WorldObjectMotion,
    WorldObjectState,
    configure_world_object_backend,
    reset_world_object_backend,
)


class _Backend:
    backend_id = "directx"

    def get_state(self, instance_id):
        return WorldObjectState(True)

    def get_geometry(self, instance_id):
        return Rect(100, 200, 120, 120)

    def get_center(self, instance_id):
        return Point(160, 260)

    def get_motion(self, instance_id):
        return WorldObjectMotion(Point(100, 200), Point(), 60)

    def apply_motion_delta(self, *args, **kwargs): pass
    def set_gravity_enabled(self, *args, **kwargs): pass
    def start_fadeout(self, *args, **kwargs): pass
    def spawn_jump(self, *args, **kwargs): pass
    def close(self, *args, **kwargs): pass


class _Host:
    identity = 1

    def __init__(self, width, height, *, x=0, y=0, callbacks=None, **kwargs):
        self.callbacks = callbacks
        self.geometry = Rect(x, y, width, height)
        self.visible = False
        self.active = False
        self.alive = True
        self.clickthrough = bool(kwargs.get("clickthrough", False))
        self.repaint_count = 0

    @property
    def native_handle(self): return self.identity
    def is_alive(self): return self.alive
    def is_visible(self): return self.visible and self.alive
    def show(self): self.visible = True
    def hide(self): self.visible = False
    def activate(self): self.active = True
    def set_geometry(self, geometry): self.geometry = geometry
    def get_geometry(self): return self.geometry
    def set_clickthrough(self, enabled): self.clickthrough = bool(enabled)
    def set_ime_position(self, x, y): self.ime_position = (x, y)
    def request_repaint(self, viewport=None): self.repaint_count += 1
    def capture_mouse(self): pass
    def release_mouse(self): pass
    def poll_events(self): return ()
    def raise_window(self): pass
    def stack_window(self, insert_after): return self.identity
    def cleanup(self): self.alive = False; self.visible = False


class _LayerManager:
    def register(self, *args, **kwargs): pass
    def unregister(self, *args, **kwargs): pass
    def enforce_burst(self): pass


class _MusicService:
    provider_mode_label = "网易模式"

    def __init__(self):
        self.queue = [(f"track:{index}", f"歌曲 {index}") for index in range(9)]
        self.index = 1
        self.moved = []
        self.removed = []
        self.cleared = False

    def is_playing(self): return False
    def is_paused(self): return False
    def is_logged_in(self): return False
    def queue_snapshot(self): return list(self.queue)
    def current_index(self): return self.index
    def play_mode(self): return "list_loop"
    def get_volume_percent(self): return 80
    def move_queue_item(self, index, direction):
        target = index + direction
        if not (0 <= target < len(self.queue)):
            return -1
        self.queue[index], self.queue[target] = self.queue[target], self.queue[index]
        self.moved.append((index, direction))
        return target
    def remove_queue_item(self, index):
        self.removed.append(index)
        self.queue.pop(index)
        return True
    def remove_song_from_history(self, track_ref): return True
    def next_track(self): self.index = (self.index + 1) % len(self.queue)
    def clear_queue(self): self.queue.clear(); self.index = -1; self.cleared = True


class DxSpeakerSearchTests(unittest.TestCase):
    def setUp(self):
        cleanup_event_center()
        configure_world_object_backend(_Backend())
        self.context = DxLoopContext()
        self.provider = DxScreenProvider(
            monitor_loader=lambda: (), fallback=Rect(0, 0, 900, 700),
        )
        self.layer_patch = patch(
            "lib.core.dx_bridge.speaker_search.get_layer_manager",
            return_value=_LayerManager(),
        )
        self.layer_patch.start()
        self.music = _MusicService()
        self.tooltips = []
        self.tooltip_hide_count = 0
        self.window = DxSpeakerSearchWindow(
            self.context,
            self.provider,
            music_service_provider=lambda: self.music,
            window_host_factory=_Host,
            warp=True,
            cursor_position_provider=lambda: Point(300, 260),
            tooltip_requester=lambda text, point: self.tooltips.append((text, point)),
            tooltip_hider=self._hide_tooltip,
        )

    def _hide_tooltip(self):
        self.tooltip_hide_count += 1

    def tearDown(self):
        self.window.cleanup()
        self.layer_patch.stop()
        reset_world_object_backend()
        cleanup_event_center()

    def test_input_results_and_music_events_are_backend_neutral(self):
        from lib.core.world_objects import WorldObjectInstance

        self.window.toggle(WorldObjectInstance("directx", 1, "speaker"))
        self.assertTrue(self.window.host.visible)
        self.assertTrue(self.window.host.active)
        self.window.handle_text_input("雪绒")
        self.assertEqual(self.window._input, "雪绒")
        self.window.handle_key_press(SimpleNamespace(key=Key.BACKSPACE))
        self.assertEqual(self.window._input, "雪")

        received = []
        center = get_event_center()
        center.subscribe(EventType.MUSIC_PLAY_TOP, lambda event: received.append((event.type, event.data)))
        center.subscribe(EventType.MUSIC_ENQUEUE, lambda event: received.append((event.type, event.data)))
        self.window._finish_search(0, [("track:1", "03:20 测试歌曲")], "")
        row = self.window.visual.result_rects[0]
        point = Point(row.x + 2, row.y + 2)
        self.window.handle_pointer_press(SimpleNamespace(pos=point, button=MouseButton.LEFT))
        self.window.handle_pointer_press(SimpleNamespace(pos=point, button=MouseButton.RIGHT))

        self.assertEqual([item[0] for item in received], [
            EventType.MUSIC_PLAY_TOP,
            EventType.MUSIC_ENQUEUE,
        ])
        self.assertEqual(received[0][1]["track_ref"], "track:1")

    def test_playlist_replaces_placeholder_and_supports_queue_and_progress_actions(self):
        from lib.core.world_objects import WorldObjectInstance

        center = get_event_center()
        received = []
        center.subscribe(EventType.MUSIC_PLAY_QUEUE_INDEX, lambda event: received.append(event))
        center.subscribe(EventType.MUSIC_SEEK, lambda event: received.append(event))

        self.window.toggle(WorldObjectInstance("directx", 1, "speaker"))
        self.window._dispatch_action("playlist")
        playlist = self.window._playlist
        self.assertIsNotNone(playlist)
        self.assertFalse(self.window.is_visible())
        self.assertTrue(playlist.is_visible())
        self.assertEqual(len(playlist.visual.row_rects), 7)
        self.assertIsNotNone(playlist.visual.page_rect)

        row = playlist.visual.row_rects[2]
        playlist.handle_pointer_move(SimpleNamespace(
            pos=Point(row.x + 2, row.y + 2), buttons=0,
        ))
        play = playlist.visual.play_rect
        play_point = Point(play.x + 2, play.y + 2)
        playlist.handle_pointer_move(SimpleNamespace(pos=play_point, buttons=0))
        playlist.handle_pointer_press(SimpleNamespace(pos=play_point, button=MouseButton.LEFT))
        playlist.handle_pointer_release(MouseButton.LEFT)
        self.assertEqual(received[-1].type, EventType.MUSIC_PLAY_QUEUE_INDEX)
        self.assertEqual(received[-1].data["index"], 2)

        slider = playlist.visual.slider_rect
        progress_point = Point(slider.x + slider.width / 2.0, slider.y + 1)
        playlist.handle_pointer_press(SimpleNamespace(
            pos=progress_point, button=MouseButton.LEFT,
        ))
        playlist.handle_pointer_release(MouseButton.LEFT)
        self.assertEqual(received[-1].type, EventType.MUSIC_SEEK)
        self.assertAlmostEqual(received[-1].data["progress"], 0.5)

        playlist._dispatch_action("clear")
        self.assertTrue(self.music.cleared)
        self.assertEqual(playlist._queue, [])

    def test_search_and_playlist_publish_particles_and_tooltips(self):
        from config.tooltip_config import TOOLTIPS
        from lib.core.world_objects import WorldObjectInstance

        particles = []
        get_event_center().subscribe(
            EventType.PARTICLE_REQUEST,
            lambda event: particles.append(event.data),
        )
        self.window.toggle(WorldObjectInstance("directx", 1, "speaker"))
        search = self.window.visual.search_rect
        event = SimpleNamespace(
            pos=Point(search.x + 2, search.y + 2),
            screen_pos=Point(321, 222),
            button=MouseButton.LEFT,
        )
        self.window.handle_pointer_move(event)
        self.window.handle_pointer_press(event)
        self.assertEqual(self.tooltips[-1], (
            TOOLTIPS["speaker_search_dialog"], Point(321, 222),
        ))
        self.assertEqual(particles[-1]["particle_id"], "click")

        self.window.hide()
        self.assertEqual(particles[-1]["particle_id"], "right_fade")
        self.assertGreater(self.tooltip_hide_count, 0)

        self.window.toggle(WorldObjectInstance("directx", 1, "speaker"))
        self.window._dispatch_action("playlist")
        playlist = self.window._playlist
        row = playlist.visual.row_rects[0]
        row_event = SimpleNamespace(
            pos=Point(row.x + 2, row.y + 2),
            screen_pos=Point(400, 300),
            button=MouseButton.RIGHT,
            buttons=0,
        )
        playlist.handle_pointer_move(row_event)
        playlist.handle_pointer_press(row_event)
        self.assertEqual(self.tooltips[-1][0], TOOLTIPS["playlist_panel"])
        self.assertEqual(particles[-1]["particle_id"], "pink_click")


if __name__ == "__main__":
    unittest.main()
