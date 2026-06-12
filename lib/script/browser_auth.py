"""Shared browser-login helpers for music provider auth flows."""

from __future__ import annotations

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


def _preferred_chromium_channels() -> tuple[str, ...]:
    return ("msedge", "chrome")


def launch_playwright_chromium(playwright, *, headless: bool, allow_visible_fallback: bool = True):
    launch_errors: list[str] = []
    browser = None
    for channel in _preferred_chromium_channels():
        try:
            browser = playwright.chromium.launch(channel=channel, headless=headless)
            break
        except Exception as exc:
            launch_errors.append(f"{channel}(headless={headless}): {exc}")

    if browser is None and headless and allow_visible_fallback:
        for channel in _preferred_chromium_channels():
            try:
                browser = playwright.chromium.launch(channel=channel, headless=False)
                break
            except Exception as exc:
                launch_errors.append(f"{channel}(headless=False): {exc}")

    if browser is None:
        raise RuntimeError(
            "未检测到可用的系统浏览器内核，请确认已安装桌面版 Microsoft Edge 或 Google Chrome: "
            + " | ".join(launch_errors)
        )
    return browser
