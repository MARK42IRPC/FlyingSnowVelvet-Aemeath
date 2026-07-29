"""Resolve the welfare API configuration from the fastest release source."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import requests

from lib.core.compute_hub import get_compute_hub
from .network_policy import API_RETRY_COUNT, API_TIMEOUT_SECS, API_TOTAL_ATTEMPTS


WELFARE_CONFIG_URLS = (
    "https://github.com/MARK42IRPC/FlyingSnowVelvet-Aemeath/releases/download/RESC/free-token.ini",
    "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/releases/download/RESC/free-token.ini",
)
_CACHE_LOCK = threading.Lock()
_CACHED_CONFIG: dict[str, object] | None = None
WELFARE_STANDARD_MODEL = "agnes-2.0-flash"
WELFARE_BOOST_MODEL = "agnes-2.5-flash"
WELFARE_MODELS = (WELFARE_STANDARD_MODEL, WELFARE_BOOST_MODEL)


def _parse_welfare_config(text: str) -> dict[str, object]:
    lines = [line.strip().lstrip("\ufeff") for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("福利 API 配置至少需要密钥和接口地址")
    api_key, base_url = lines[:2]
    if not api_key or not base_url.startswith(("http://", "https://")):
        raise ValueError("福利 API 配置格式无效")
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
    }


def _download_source(url: str) -> tuple[float, dict[str, object]]:
    started_at = time.monotonic()
    with requests.get(url, timeout=API_TIMEOUT_SECS) as response:
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        config = _parse_welfare_config(response.text)
    return time.monotonic() - started_at, config


def select_welfare_model(models: tuple[str, ...] | list[str], intelligence_boost: bool) -> str:
    preferred = WELFARE_BOOST_MODEL if intelligence_boost else WELFARE_STANDARD_MODEL
    available = {str(model or "").strip() for model in models}
    if preferred not in available:
        raise RuntimeError(f"福利 API 当前未提供模型 {preferred}")
    return preferred


def _resolve_once() -> dict[str, object]:
    futures: dict[Future, str] = {
        get_compute_hub().submit_io(_download_source, url): url
        for url in WELFARE_CONFIG_URLS
    }
    candidates: list[tuple[float, str, dict[str, object]]] = []
    errors: list[str] = []
    for future, url in futures.items():
        try:
            elapsed, config = future.result()
            candidates.append((elapsed, url, config))
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if not candidates:
        raise RuntimeError("；".join(errors) or "福利 API 配置源均不可用")
    elapsed, source_url, config = min(candidates, key=lambda item: item[0])
    return {
        **config,
        "models": WELFARE_MODELS,
        "source_url": source_url,
        "latency_ms": int(round(elapsed * 1000)),
    }


def resolve_welfare_api_config(*, force_refresh: bool = False) -> dict[str, object]:
    """Race both sources and retry three times before reporting failure."""
    global _CACHED_CONFIG

    with _CACHE_LOCK:
        if _CACHED_CONFIG is not None and not force_refresh:
            return dict(_CACHED_CONFIG)

        last_error: Exception | None = None
        for _attempt in range(API_TOTAL_ATTEMPTS):
            try:
                resolved = _resolve_once()
                _CACHED_CONFIG = dict(resolved)
                return dict(resolved)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"福利 API 配置获取失败，已重试 {API_RETRY_COUNT} 次：{last_error}") from last_error


def clear_welfare_api_config_cache() -> None:
    global _CACHED_CONFIG
    with _CACHE_LOCK:
        _CACHED_CONFIG = None
