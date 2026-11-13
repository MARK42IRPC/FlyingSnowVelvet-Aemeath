from __future__ import annotations

from concurrent.futures import Future
from datetime import date
from pathlib import Path
import tempfile
import unittest

from lib.core.announcement import (
    AnnouncementPreferences,
    AnnouncementService,
    load_announcement_preferences,
    save_announcement_preferences,
)


def _immediate_submit(func, *args):
    future = Future()
    try:
        future.set_result(func(*args))
    except Exception as exc:
        future.set_exception(exc)
    return future


class _Response:
    def __init__(self, raw):
        self.raw = raw
        self.closed = False

    def raise_for_status(self): pass
    def iter_content(self, chunk_size): return [self.raw]
    def close(self): self.closed = True


class AnnouncementServiceTests(unittest.TestCase):
    def test_manual_download_uses_cache_and_shared_suppression(self):
        raw = 'title:"公告"\ntext:"内容"\n'.encode("utf-8")
        response = _Response(raw)
        events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = AnnouncementService(
                dispatch=lambda callback: callback(),
                on_loading=lambda: events.append("loading"),
                on_document=lambda document, manual: events.append((document.title, manual)),
                on_error=lambda manual: events.append(("error", manual)),
                on_hide=lambda: events.append("hide"),
                state_path=root / "announcement.json",
                cache_path=root / "announcement.txt",
                today_provider=lambda: date(2026, 8, 30),
                submit_io=_immediate_submit,
                request_get=lambda *args, **kwargs: response,
            )
            try:
                service.open_manual()
                self.assertEqual(events[:2], ["loading", ("公告", True)])
                self.assertTrue(response.closed)
                self.assertEqual((root / "announcement.txt").read_bytes(), raw)
                service.suppress_today()
                self.assertEqual(events[-1], "hide")
                self.assertEqual(
                    load_announcement_preferences(root / "announcement.json").suppress_date,
                    "2026-08-30",
                )
            finally:
                service.cleanup()

    def test_start_reloads_permanent_suppression_without_network(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "announcement.json"
            save_announcement_preferences(AnnouncementPreferences(True), path)
            calls = []
            service = AnnouncementService(
                dispatch=lambda callback: callback(),
                on_loading=lambda: None,
                on_document=lambda document, manual: None,
                on_error=lambda manual: None,
                on_hide=lambda: None,
                state_path=path,
                cache_path=Path(tmpdir) / "announcement.txt",
                submit_io=lambda *args: calls.append(args),
            )
            try:
                self.assertFalse(service.start())
                self.assertEqual(calls, [])
            finally:
                service.cleanup()


if __name__ == "__main__":
    unittest.main()
