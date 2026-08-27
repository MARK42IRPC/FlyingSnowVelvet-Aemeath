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


def launch_playwright_edge(playwright, *, headless: bool):
    try:
        return playwright.chromium.launch(channel="msedge", headless=headless)
    except Exception as exc:
        raise RuntimeError(
            f"无法启动系统 Microsoft Edge，请确认 Edge 已安装且可正常运行：{exc}"
        ) from exc
