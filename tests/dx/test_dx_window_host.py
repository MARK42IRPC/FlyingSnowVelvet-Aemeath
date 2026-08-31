from __future__ import annotations

import ctypes
import os
import threading
import unittest
from unittest.mock import patch

from lib.core.dx_bridge.window_host import (
    FSDX_EVENT_FLAG_TEXT_FIRST,
    FSDX_EVENT_FLAG_TEXT_LAST,
    FSDX_EVENT_KEY_PRESS,
    FSDX_EVENT_IME_COMPOSITION,
    FSDX_EVENT_IME_END,
    FSDX_EVENT_DEVICE_RECOVERED,
    FSDX_EVENT_TEXT_INPUT,
    FSDX_EVENT_POINTER_MOVE,
    FSDX_EVENT_POINTER_PRESS,
    FSDX_EVENT_POINTER_RELEASE,
    _NativeEvent,
    _WindowDesc,
    _WindowState,
    DxWindowHost,
    DxHostEvent,
    create_dx_layer_window_host,
)
from lib.core.graphics.commands import DrawBatch, RectCommand
from lib.core.graphics.types import Color, Point, Rect
from lib.core.input.types import Key, MouseButton


@unittest.skipUnless(os.name == "nt", "DirectX window integration requires Windows")
class DxWindowHostTests(unittest.TestCase):
    def test_v7_struct_sizes_are_fixed(self):
        self.assertEqual(ctypes.sizeof(_WindowDesc), 32)
        self.assertEqual(ctypes.sizeof(_WindowState), 56)
        self.assertEqual(ctypes.sizeof(_NativeEvent), 88)

    def test_window_lifecycle_geometry_dpi_and_cleanup(self):
        host = DxWindowHost(12, 10, x=-40, y=25, warp=True, topmost=False)
        try:
            owner = type("Owner", (), {"window_host": host})()
            self.assertIs(create_dx_layer_window_host(host), host)
            self.assertIs(create_dx_layer_window_host(owner), host)
            self.assertIsInstance(host.native_handle, int)
            self.assertEqual(host.get_geometry(), Rect(-40, 25, 12, 10))
            self.assertGreaterEqual(host.get_dpi(), 1)
            self.assertIsNotNone(host.get_screen_geometry())

            host.set_geometry(Rect(-80, 30, 14, 11))
            self.assertEqual(host.get_geometry(), Rect(-80, 30, 14, 11))
            host.render_batch(DrawBatch((
                RectCommand(Rect(0, 0, 14, 11), fill=Color(40, 80, 120)),
            )))
            pixels = host.readback_rgba()
            self.assertEqual(len(pixels), 14 * 11 * 4)
            self.assertEqual(pixels[:4], bytes((40, 80, 120, 255)))
            host.show()
            self.assertTrue(host.is_visible())
            host.hide()
            self.assertFalse(host.is_visible())
        finally:
            host.cleanup()
            host.cleanup()
        self.assertFalse(host.is_alive())
        self.assertIsNone(host.native_handle)
        with self.assertRaises(TypeError):
            create_dx_layer_window_host(object())

    def test_clickthrough_capture_and_window_stack(self):
        host = DxWindowHost(8, 8, warp=True, topmost=False)
        try:
            host.set_ime_position(3, 4)
            host.set_clickthrough(True)
            self.assertTrue(host.is_clickthrough_enabled())
            host.set_clickthrough(False)
            self.assertFalse(host.is_clickthrough_enabled())
            host.capture_mouse()
            self.assertTrue(host.has_mouse_capture())
            host.release_mouse()
            self.assertFalse(host.has_mouse_capture())
            self.assertEqual(host.stack_window(None), host.native_handle)
        finally:
            host.cleanup()

    def test_logical_content_scales_once_at_125_and_150_percent(self):
        positions = []

        class Callbacks:
            def handle_pointer_press(self, event):
                positions.append(event.pos)

        host = DxWindowHost(
            8, 6, warp=True, topmost=False,
            callbacks=Callbacks(), logical_content=True,
        )
        try:
            for dpi, expected_size in ((120, (10, 8)), (144, (12, 9))):
                with patch.object(host, "get_dpi", return_value=dpi):
                    host.set_geometry(Rect(-20, 10, 8, 6))
                    self.assertEqual((host.width, host.height), expected_size)
                    host.render_batch(DrawBatch((
                        RectCommand(Rect(0, 0, 8, 6), fill=Color(20, 40, 60)),
                    )))
                    pixels = host.readback_rgba()
                    self.assertTrue(pixels[-1])
                    host._dispatch_event(DxHostEvent(
                        type=FSDX_EVENT_POINTER_PRESS,
                        timestamp_ms=0,
                        local_pos=Point(expected_size[0] / 2, expected_size[1] / 2),
                        screen_pos=Point(100, 100),
                        size=expected_size,
                        dpi=dpi,
                        key=0,
                        button=int(MouseButton.LEFT),
                        buttons=int(MouseButton.LEFT),
                        modifiers=0,
                        repeat_count=0,
                        codepoint=0,
                        flags=0,
                        pointer_id=0,
                    ))
                    self.assertAlmostEqual(positions[-1].x, expected_size[0] / 2 / (dpi / 96.0))
                    self.assertAlmostEqual(positions[-1].y, expected_size[1] / 2 / (dpi / 96.0))
        finally:
            host.cleanup()

    def test_device_recovery_preserves_hwnd_visibility_and_resources(self):
        host = DxWindowHost(6, 5, warp=True, topmost=False)
        try:
            batch = DrawBatch((
                RectCommand(Rect(0, 0, 6, 5), fill=Color(12, 34, 56)),
            ))
            host.show()
            host.render_batch(batch)
            hwnd = host.native_handle
            generation = host.device_generation

            host.recover_device()
            host.render_batch(batch)
            events = host.poll_events()

            self.assertEqual(host.native_handle, hwnd)
            self.assertTrue(host.is_visible())
            self.assertEqual(host.device_generation, generation + 1)
            self.assertEqual(host.readback_rgba()[:4], bytes((12, 34, 56, 255)))
            self.assertTrue(any(event.type == FSDX_EVENT_DEVICE_RECOVERED for event in events))
            self.assertEqual(host.last_device_recovery_generation, generation + 1)
        finally:
            host.cleanup()

    def test_device_recovery_requires_window_owner_thread(self):
        host = DxWindowHost(4, 4, warp=True, topmost=False)
        errors = []
        generation = host.device_generation

        def recover_from_worker():
            try:
                host.recover_device()
            except Exception as exc:
                errors.append(exc)

        try:
            worker = threading.Thread(target=recover_from_worker)
            worker.start()
            worker.join(timeout=10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIn("owner thread", str(errors[0]))
            self.assertEqual(host.device_generation, generation)
        finally:
            host.cleanup()

    def test_native_events_translate_to_pet_callbacks_and_repaint(self):
        class Callbacks:
            def __init__(self):
                self.events = []
                self.render_count = 0
                self.text = []
                self.composition = []

            def prepare_render(self):
                self.render_count += 1
                return DrawBatch((RectCommand(Rect(0, 0, 8, 8), fill=Color(10, 20, 30)),))

            def handle_pointer_enter(self): self.events.append("enter")
            def handle_pointer_leave(self): self.events.append("leave")
            def handle_pointer_press(self, event): self.events.append(("press", event.button, event.pos))
            def handle_pointer_move(self, event): self.events.append(("move", event.global_pos))
            def handle_pointer_release(self, button): self.events.append(("release", button))
            def handle_window_moved(self, position): self.events.append(("moved", position))
            def handle_key_press(self, event): self.events.append(("key", event.key, event.text))
            def handle_key_release(self, event): self.events.append(("keyup", event.key))
            def handle_host_close(self): self.events.append("close")
            def handle_text_input(self, text): self.text.append(text)
            def handle_ime_composition(self, text): self.composition.append(text)
            def handle_ime_end(self): self.events.append("ime-end")

        callbacks = Callbacks()
        host = DxWindowHost(8, 8, warp=True, topmost=False, callbacks=callbacks)
        try:
            host.show()
            host.poll_events()
            self.assertGreaterEqual(callbacks.render_count, 1)

            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(host.native_handle)
            post = user32.PostMessageW
            post.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            post.restype = ctypes.c_int
            self.assertTrue(post(hwnd, 0x0201, 1, (3 & 0xffff) | ((4 & 0xffff) << 16)))
            self.assertTrue(post(hwnd, 0x0202, 0, (3 & 0xffff) | ((4 & 0xffff) << 16)))
            self.assertTrue(post(hwnd, 0x0100, 0x41, 1))
            self.assertTrue(post(hwnd, 0x0101, 0x41, (1 << 30) | (1 << 31)))
            self.assertTrue(post(hwnd, 0x0102, ord("雪"), 1))
            self.assertTrue(post(hwnd, 0x0102, ord("x"), 3))
            send = user32.SendMessageW
            send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            send.restype = ctypes.c_ssize_t
            send(hwnd, 0x0246, 11, (2 & 0xffff) | ((3 & 0xffff) << 16))
            send(hwnd, 0x0245, 11, (4 & 0xffff) | ((5 & 0xffff) << 16))
            send(hwnd, 0x0247, 11, (4 & 0xffff) | ((5 & 0xffff) << 16))
            send(hwnd, 0x010D, 0, 0)
            send(hwnd, 0x010E, 0, 0)
            events = host.poll_events()

            self.assertTrue(any(event.type == FSDX_EVENT_POINTER_PRESS for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_POINTER_RELEASE for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_KEY_PRESS for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_TEXT_INPUT for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_POINTER_MOVE and event.pointer_id == 11 for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_IME_COMPOSITION for event in events))
            self.assertTrue(any(event.type == FSDX_EVENT_IME_END for event in events))
            self.assertTrue(any(item[0] == "press" and item[1] == MouseButton.LEFT for item in callbacks.events if isinstance(item, tuple)))
            self.assertTrue(any(item[0] == "key" and item[1] == Key.A for item in callbacks.events if isinstance(item, tuple)))
            self.assertEqual(callbacks.text.count("雪"), 1)
            self.assertEqual(callbacks.text.count("a"), 1)
            self.assertIn("xxx", callbacks.text)
            key_event = next(item for item in callbacks.events if isinstance(item, tuple) and item[0] == "key")
            self.assertEqual(key_event[2], "")

            event_defaults = {
                "timestamp_ms": 0,
                "local_pos": Point(),
                "screen_pos": Point(),
                "size": (0, 0),
                "dpi": 96,
                "key": 0,
                "button": 0,
                "buttons": 0,
                "modifiers": 0,
                "repeat_count": 0,
            }
            host._dispatch_event(DxHostEvent(
                type=FSDX_EVENT_IME_COMPOSITION,
                codepoint=ord("中"),
                flags=FSDX_EVENT_FLAG_TEXT_FIRST,
                **event_defaults,
            ))
            host._dispatch_event(DxHostEvent(
                type=FSDX_EVENT_IME_COMPOSITION,
                codepoint=ord("文"),
                flags=FSDX_EVENT_FLAG_TEXT_LAST,
                **event_defaults,
            ))
            host._dispatch_event(DxHostEvent(
                type=FSDX_EVENT_IME_END,
                codepoint=0,
                flags=0,
                **event_defaults,
            ))
            self.assertEqual(callbacks.composition[-1], "中文")
            self.assertIn("", callbacks.composition)
            self.assertIn("ime-end", callbacks.events)
        finally:
            host.cleanup()


if __name__ == "__main__":
    unittest.main()
