"""Multi-source search router for music providers."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from lib.core.logger import get_logger

from .provider import MusicProvider
from .types import MusicTrack

logger = get_logger(__name__)

_SPACE_RE = re.compile(r"\s+")
_TRIM_PUNCT_RE = re.compile(r"[\\s\\-_/()\\[\\]{}]+")


@dataclass
class ProviderRouteStat:
    provider: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    last_attempt_at: float = 0.0
    last_success_at: float = 0.0
    last_error: str = ""

    @property
    def average_latency_ms(self) -> float:
        return self.total_latency_ms / self.attempts if self.attempts > 0 else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_latency_ms": round(self.last_latency_ms, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }


class SourceRouter:
    """Route search requests across providers with fallback and dedupe."""

    def __init__(self) -> None:
        self._stats: dict[str, ProviderRouteStat] = {}

    def provider_stats(self) -> dict[str, dict[str, Any]]:
        return {name: stat.snapshot() for name, stat in self._stats.items()}

    def search(
        self,
        *,
        providers: dict[str, MusicProvider],
        primary_provider: str,
        keyword: str,
        mode: str = "song",
        limit: int = 25,
        fallback_enabled: bool = True,
        fallback_order: list[str] | tuple[str, ...] | None = None,
    ) -> list[MusicTrack]:
        query = str(keyword or "").strip()
        if not query:
            return []
        normalized_mode = str(mode or "song").strip().lower() or "song"
        max_items = max(1, int(limit or 25))

        provider_names = self._build_provider_order(
            providers=providers,
            primary_provider=primary_provider,
            fallback_enabled=fallback_enabled,
            fallback_order=fallback_order,
        )
        results: list[MusicTrack] = []
        seen_keys: set[tuple[str, str, int | None]] = set()

        for index, provider_name in enumerate(provider_names):
            provider = providers.get(provider_name)
            if provider is None or not provider.supports_mode(normalized_mode):
                continue
            remaining = max_items - len(results)
            if remaining <= 0:
                break
            request_limit = remaining if index == 0 else min(max_items, max(remaining * 2, 8))

            started = time.perf_counter()
            try:
                tracks = provider.search(query, mode=normalized_mode, limit=request_limit)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self._record_attempt(provider_name, elapsed_ms=elapsed_ms, error=exc)
                logger.warning(
                    "[SourceRouter] provider=%s search failed mode=%s keyword=%s: %s",
                    provider_name,
                    normalized_mode,
                    query,
                    exc,
                )
                continue

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._record_attempt(provider_name, elapsed_ms=elapsed_ms, error=None)
            if not tracks:
                continue

            for track in tracks:
                dedupe_key = self._track_dedupe_key(track)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                results.append(track)
                if len(results) >= max_items:
                    break

        return results[:max_items]

    def _build_provider_order(
        self,
        *,
        providers: dict[str, MusicProvider],
        primary_provider: str,
        fallback_enabled: bool,
        fallback_order: list[str] | tuple[str, ...] | None,
    ) -> list[str]:
        normalized_primary = str(primary_provider or "").strip().lower()
        available = [name for name in providers.keys() if str(name).strip()]
        if normalized_primary not in providers:
            normalized_primary = available[0] if available else ""

        ordered: list[str] = []
        if normalized_primary:
            ordered.append(normalized_primary)
        if not fallback_enabled:
            return ordered

        configured = [
            str(name or "").strip().lower()
            for name in (fallback_order or ())
            if str(name or "").strip().lower() in providers
        ]
        if not configured:
            configured = [name for name in available if name != normalized_primary]

        rank_map = {name: idx for idx, name in enumerate(configured)}
        rest = [name for name in available if name != normalized_primary]
        rest.sort(key=lambda name: self._provider_sort_key(name, rank_map.get(name, 10_000)))
        for name in rest:
            if name not in ordered:
                ordered.append(name)
        return ordered

    def _provider_sort_key(self, provider_name: str, configured_rank: int) -> tuple[int, int, float, int]:
        stat = self._stats.get(provider_name)
        if stat is None:
            return (0, 0, 0.0, configured_rank)
        return (
            stat.consecutive_failures,
            stat.failures,
            int(round(stat.average_latency_ms)),
            configured_rank,
        )

    def _record_attempt(self, provider_name: str, *, elapsed_ms: float, error: Exception | None) -> None:
        name = str(provider_name or "").strip().lower() or "unknown"
        stat = self._stats.setdefault(name, ProviderRouteStat(provider=name))
        stat.attempts += 1
        stat.last_attempt_at = time.time()
        stat.last_latency_ms = max(0.0, float(elapsed_ms))
        stat.total_latency_ms += stat.last_latency_ms
        if error is None:
            stat.successes += 1
            stat.consecutive_failures = 0
            stat.last_success_at = stat.last_attempt_at
            stat.last_error = ""
            return
        stat.failures += 1
        stat.consecutive_failures += 1
        stat.last_error = str(error or "")

    @staticmethod
    def _normalize_match_text(raw: Any) -> str:
        text = str(raw or "").strip().lower()
        if not text:
            return ""
        text = _SPACE_RE.sub(" ", text)
        text = _TRIM_PUNCT_RE.sub("", text)
        return text.strip()

    @classmethod
    def _track_dedupe_key(cls, track: MusicTrack) -> tuple[str, str, int | None]:
        duration_bucket: int | None = None
        try:
            if track.duration_ms is not None:
                duration_bucket = int(track.duration_ms) // 2000
        except Exception:
            duration_bucket = None
        return (
            cls._normalize_match_text(track.title),
            cls._normalize_match_text(track.artist),
            duration_bucket,
        )
