from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.core.announcement import AnnouncementBlock, AnnouncementDocument
from lib.core.dx_bridge.announcement import DxAnnouncementWindow
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.graphics.types import Point, Rect


def _immediate_submit(func, *args):
    future = Future()
    try:
        future.set_result(func(*args))
    except Exception as exc:
        future.set_exception(exc)
    return future


class _Response:
    def __init__(self, raw): self.raw = raw
    def raise_for_status(self): pass
    def iter_content(self, chunk_size): return [self.raw]
    def close(self): pass


class _Host:
    identity = 71

    def __init__(self, width, height, *, x=0, y=0, callbacks=None, **kwargs):
        self.callbacks = callbacks
        self.geometry = Rect(x, y, width, height)
        self.visible = False
        self.alive = True
        self.repaint_count = 0
    @property
    def native_handle(self): return self.identity
    def is_visible(self): return self.visible and self.alive
    def show(self): self.visible = True
    def hide(self): self.visible = False
    def activate(self): pass
    def get_geometry(self): return self.geometry
    def set_geometry(self, geometry): self.geometry = geometry
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


class DxAnnouncementTests(unittest.TestCase):
    def test_manual_request_renders_native_document_and_long_content_pages(self):
        raw = ('title:"原生公告"\ntext:"' + '\n'.join(f"第 {i} 行" for i in range(40)) + '\n"\n').encode("utf-8")
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "lib.core.dx_bridge.announcement.get_layer_manager",
            return_value=_LayerManager(),
        ):
            context = DxLoopContext()
            window = DxAnnouncementWindow(
                context,
                DxScreenProvider(monitor_loader=lambda: (), fallback=Rect(0, 0, 1200, 800)),
                window_host_factory=_Host,
                warp=True,
                state_path=Path(tmpdir) / "announcement.json",
                cache_path=Path(tmpdir) / "announcement.txt",
                submit_io=_immediate_submit,
                request_get=lambda *args, **kwargs: _Response(raw),
            )
            try:
                window.open_manual()
                self.assertTrue(window.is_visible())
                self.assertEqual(window._mode, "loading")
                context.run_once()
                self.assertEqual(window._mode, "document")
                self.assertEqual(window._document.title, "原生公告")
                self.assertGreater(window.visual.page_count, 1)
                first_page = window.visual.page
                window._dispatch_action("page_next")
                self.assertNotEqual(window.visual.page, first_page)
                self.assertTrue(window.prepare_render().commands)
            finally:
                window.cleanup()

    def test_visual_paginates_document_without_toolkit_objects(self):
        document = AnnouncementDocument(
            "公告",
            (AnnouncementBlock("text", "\n".join(str(i) for i in range(40))),),
        )
        with patch(
            "lib.core.dx_bridge.announcement.get_layer_manager",
            return_value=_LayerManager(),
        ):
            window = DxAnnouncementWindow(
                DxLoopContext(),
                DxScreenProvider(monitor_loader=lambda: (), fallback=Rect(0, 0, 1200, 800)),
                window_host_factory=_Host,
                warp=True,
                submit_io=_immediate_submit,
                request_get=lambda *args, **kwargs: _Response(b"text:\"x\""),
            )
            try:
                window.show_document(document, False)
                self.assertGreater(window.visual.page_count, 1)
            finally:
                window.cleanup()


if __name__ == "__main__":
    unittest.main()
