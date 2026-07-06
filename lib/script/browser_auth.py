"""Shared browser-login helpers for music provider auth flows."""

from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import Any


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            cookies[key] = value
    return cookies


def parse_set_cookie_headers(headers: Any) -> dict[str, str]:
    cookie_map: dict[str, str] = {}
    if not headers:
        return cookie_map

    raw_items: list[tuple[str, str]] = []
    try:
        for item in headers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                raw_items.append((name, value))
    except Exception:
        raw_items = []

    for name, value in raw_items:
        if name.lower() != "set-cookie":
            continue
        first_segment = value.split(";", 1)[0].strip()
        if not first_segment or "=" not in first_segment:
            continue
        cookie_name, cookie_value = first_segment.split("=", 1)
        cookie_name = cookie_name.strip()
        cookie_value = cookie_value.strip()
        if cookie_name and cookie_value:
            cookie_map[cookie_name] = cookie_value
    return cookie_map


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _candidate_local_chromium_executables() -> list[Path]:
    root = _project_root()
    pattern = root / "resc" / "playwright" / "browsers" / "ms-playwright" / "chromium-*" / "chrome-win64" / "chrome.exe"
    try:
        return sorted((Path(item) for item in glob(str(pattern))), reverse=True)
    except Exception:
        return []


def find_local_playwright_chromium() -> Path | None:
    for candidate in _candidate_local_chromium_executables():
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def launch_playwright_chromium(playwright, *, headless: bool, allow_visible_fallback: bool = True):
    local_chromium = find_local_playwright_chromium()
    if local_chromium is None:
        raise RuntimeError(
            "未检测到内置 Chromium 运行时，请先执行安装脚本完成离线浏览器安装："
            " resc/playwright/browsers/ms-playwright/chromium-*/chrome-win64/chrome.exe"
        )
    launch_args = {"executable_path": str(local_chromium), "headless": headless}
    return playwright.chromium.launch(**launch_args)
