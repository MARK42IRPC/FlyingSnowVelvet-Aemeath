"""Backend-neutral announcement parsing, persistence and download service."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import threading
import time

import requests

from config.shared_storage_io import write_bytes_atomic
from config.user_storage_paths import get_user_cache_dir, get_user_state_dir
from lib.core.compute_hub import get_compute_hub
from lib.core.logger import get_logger


ANNOUNCEMENT_URL = (
    "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/releases/download/RESC/"
    "%E5%85%AC%E5%91%8A.txt"
)
ANNOUNCEMENT_REQUEST_TIMEOUT = (5.0, 12.0)
ANNOUNCEMENT_MAX_BYTES = 1024 * 1024

_FIELD_START_RE = re.compile(
    r'^\s*(title|subtitle|text)\s*:\s*"(.*)$',
    re.IGNORECASE,
)
_logger = get_logger(__name__)


@dataclass(frozen=True)
class AnnouncementBlock:
    kind: str
    text: str


@dataclass(frozen=True)
class AnnouncementDocument:
    title: str
    blocks: tuple[AnnouncementBlock, ...]


@dataclass(frozen=True)
class AnnouncementPreferences:
    suppress_forever: bool = False
    suppress_date: str = ""


def parse_announcement(raw_text: str) -> AnnouncementDocument:
    raw = str(raw_text or "").lstrip("\ufeff")
    lines = raw.splitlines()
    fields: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        match = _FIELD_START_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        key = match.group(1).lower()
        remainder = match.group(2)
        inline = remainder.rstrip()
        if inline.endswith('"'):
            fields.append((key, inline[:-1].strip()))
            index += 1
            continue
        value_lines: list[str] = []
        if remainder:
            value_lines.append(remainder)
        index += 1
        while index < len(lines) and lines[index].strip() != '"':
            value_lines.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        fields.append((key, "\n".join(value_lines).strip()))
    title = next((value for key, value in fields if key == "title" and value), "")
    blocks = tuple(
        AnnouncementBlock(key, value)
        for key, value in fields
        if key in {"subtitle", "text"} and value
    )
    if not fields and raw.strip():
        blocks = (AnnouncementBlock("text", raw.strip()),)
    if not title:
        title = "桌宠公告"
    if not blocks and not raw.strip():
        raise ValueError("公告内容为空")
    return AnnouncementDocument(title=title, blocks=blocks)


def load_announcement_preferences(path: Path | None = None) -> AnnouncementPreferences:
    state_path = Path(path) if path is not None else get_user_state_dir("announcement.json")
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return AnnouncementPreferences()
    if not isinstance(payload, dict):
        return AnnouncementPreferences()
    suppress_forever = payload.get("suppress_forever", False)
    suppress_date = payload.get("suppress_date", "")
    return AnnouncementPreferences(
        suppress_forever=suppress_forever if isinstance(suppress_forever, bool) else False,
        suppress_date=suppress_date if isinstance(suppress_date, str) else "",
    )


def save_announcement_preferences(
    preferences: AnnouncementPreferences,
    path: Path | None = None,
) -> None:
    state_path = Path(path) if path is not None else get_user_state_dir("announcement.json")
    payload = {
        "schema_version": 1,
        "suppress_forever": bool(preferences.suppress_forever),
        "suppress_date": str(preferences.suppress_date or ""),
    }
    write_bytes_atomic(
        state_path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def set_announcement_forever_suppressed(
    suppressed: bool,
    path: Path | None = None,
) -> AnnouncementPreferences:
    current = load_announcement_preferences(path)
    updated = AnnouncementPreferences(bool(suppressed), current.suppress_date)
    save_announcement_preferences(updated, path)
    return updated


def is_announcement_suppressed(
    preferences: AnnouncementPreferences,
    current_date: date | None = None,
) -> bool:
    today = current_date or date.today()
    return bool(
        preferences.suppress_forever
        or preferences.suppress_date == today.isoformat()
    )


def decode_announcement_payload(payload: bytes) -> str:
    if not payload:
        raise ValueError("公告内容为空")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


class AnnouncementService:
    """Coordinate latest-only announcement requests without a UI toolkit."""

    def __init__(
        self,
        *,
        dispatch: Callable[[Callable[[], None]], None],
        on_loading: Callable[[], None],
        on_document: Callable[[AnnouncementDocument, bool], None],
        on_error: Callable[[bool], None],
        on_hide: Callable[[], None],
        state_path: Path | None = None,
        cache_path: Path | None = None,
        today_provider: Callable[[], date] = date.today,
        submit_io: Callable[..., Future] | None = None,
        request_get: Callable[..., object] | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._on_loading = on_loading
        self._on_document = on_document
        self._on_error = on_error
        self._on_hide = on_hide
        self._state_path = Path(state_path) if state_path is not None else get_user_state_dir(
            "announcement.json"
        )
        self._cache_path = Path(cache_path) if cache_path is not None else get_user_cache_dir(
            "announcement.txt"
        )
        self._today_provider = today_provider
        self._submit_io = submit_io or get_compute_hub().submit_io
        self._request_get = request_get or requests.get
        self._preferences = load_announcement_preferences(self._state_path)
        self._current_document: AnnouncementDocument | None = None
        self._manual_waiting = False
        self._request_id = 0
        self._active_request_id = 0
        self._futures: dict[int, Future] = {}
        self._responses: dict[int, object] = {}
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self._closed or self.is_suppressed():
            return False
        return self._request_download(manual=False)

    def open_manual(self) -> None:
        if self._closed:
            return
        self._on_loading()
        self._manual_waiting = True
        self._request_download(manual=True)

    def retry(self) -> None:
        self.open_manual()

    def dismiss(self) -> None:
        self._manual_waiting = False

    def suppress_today(self) -> None:
        self._preferences = AnnouncementPreferences(
            suppress_forever=False,
            suppress_date=self._today_provider().isoformat(),
        )
        self._save_preferences()
        self._on_hide()

    def suppress_forever(self) -> None:
        self._preferences = AnnouncementPreferences(
            suppress_forever=True,
            suppress_date=self._preferences.suppress_date,
        )
        self._save_preferences()
        self._on_hide()

    def is_suppressed(self) -> bool:
        self._preferences = load_announcement_preferences(self._state_path)
        return is_announcement_suppressed(self._preferences, self._today_provider())

    def _request_download(self, *, manual: bool) -> bool:
        if self._closed:
            return False
        self._request_id += 1
        request_id = self._request_id
        with self._lock:
            self._active_request_id = request_id
            stale_futures = tuple(self._futures.values())
            stale_responses = tuple(self._responses.values())
            self._responses.clear()
        for future in stale_futures:
            future.cancel()
        for response in stale_responses:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        try:
            future = self._submit_io(self._download_worker, request_id)
        except Exception as exc:
            self._complete_failure(request_id, manual, str(exc))
            return False
        with self._lock:
            if not self._closed:
                self._futures[request_id] = future

        def complete(done: Future) -> None:
            try:
                raw_text = done.result()
            except Exception as exc:
                self._dispatch(
                    lambda request_id=request_id, manual=manual, message=str(exc):
                    self._complete_failure(request_id, manual, message)
                )
            else:
                self._dispatch(
                    lambda request_id=request_id, manual=manual, raw_text=raw_text:
                    self._complete_success(request_id, manual, raw_text)
                )
            finally:
                with self._lock:
                    if self._futures.get(request_id) is done:
                        self._futures.pop(request_id, None)

        future.add_done_callback(complete)
        return True

    def _download_worker(self, request_id: int) -> str:
        response = None
        try:
            if not self._is_current(request_id):
                raise RuntimeError("公告请求已取消")
            response = self._request_get(
                ANNOUNCEMENT_URL,
                params={"_": str(time.time_ns())},
                headers={
                    "User-Agent": "FlyingSnowVelvet-Announcement/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                timeout=ANNOUNCEMENT_REQUEST_TIMEOUT,
                stream=True,
            )
            with self._lock:
                if not self._is_current_unlocked(request_id):
                    raise RuntimeError("公告请求已取消")
                self._responses[request_id] = response
            response.raise_for_status()
            payload = bytearray()
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if not self._is_current(request_id):
                    raise RuntimeError("公告请求已取消")
                if not chunk:
                    continue
                payload.extend(chunk)
                if len(payload) > ANNOUNCEMENT_MAX_BYTES:
                    raise ValueError("公告文件超过 1 MiB 限制")
            raw_text = decode_announcement_payload(bytes(payload))
            parse_announcement(raw_text)
            return raw_text
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            with self._lock:
                if self._responses.get(request_id) is response:
                    self._responses.pop(request_id, None)

    def _complete_success(self, request_id: int, manual: bool, raw_text: str) -> None:
        if not self._is_current(request_id):
            return
        try:
            document = parse_announcement(raw_text)
        except ValueError as exc:
            self._complete_failure(request_id, manual, str(exc))
            return
        self._current_document = document
        try:
            write_bytes_atomic(self._cache_path, raw_text.encode("utf-8"))
        except OSError as exc:
            _logger.warning("缓存桌宠公告失败: %s", exc)
        manual_display = bool(manual or self._manual_waiting)
        self._manual_waiting = False
        if manual_display or not self.is_suppressed():
            self._on_document(document, manual_display)

    def _complete_failure(self, request_id: int, manual: bool, message: str) -> None:
        if not self._is_current(request_id):
            return
        _logger.warning("下载桌宠公告失败: %s", message)
        document = self._current_document or self._load_cached_document()
        if document is not None:
            self._current_document = document
        manual_display = bool(manual or self._manual_waiting)
        self._manual_waiting = False
        if document is not None and (manual_display or not self.is_suppressed()):
            self._on_document(document, manual_display)
        elif manual_display:
            self._on_error(True)

    def _load_cached_document(self) -> AnnouncementDocument | None:
        try:
            return parse_announcement(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None

    def _save_preferences(self) -> None:
        try:
            save_announcement_preferences(self._preferences, self._state_path)
        except OSError as exc:
            _logger.warning("保存桌宠公告显示偏好失败: %s", exc)

    def _is_current_unlocked(self, request_id: int) -> bool:
        return not self._closed and request_id == self._active_request_id

    def _is_current(self, request_id: int) -> bool:
        with self._lock:
            return self._is_current_unlocked(request_id)

    def cleanup(self) -> None:
        if self._closed:
            return
        self._manual_waiting = False
        with self._lock:
            self._closed = True
            futures = tuple(self._futures.values())
            responses = tuple(self._responses.values())
            self._futures.clear()
            self._responses.clear()
        for future in futures:
            future.cancel()
        for response in responses:
            close = getattr(response, "close", None)
            if callable(close):
                close()


__all__ = [
    "ANNOUNCEMENT_MAX_BYTES",
    "ANNOUNCEMENT_REQUEST_TIMEOUT",
    "ANNOUNCEMENT_URL",
    "AnnouncementBlock",
    "AnnouncementDocument",
    "AnnouncementPreferences",
    "AnnouncementService",
    "decode_announcement_payload",
    "is_announcement_suppressed",
    "load_announcement_preferences",
    "parse_announcement",
    "save_announcement_preferences",
    "set_announcement_forever_suppressed",
]
