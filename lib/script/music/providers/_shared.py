"""Provider 之间共用的解析与格式化助手。

`_format_duration_text` 此前在 netease / qq / kugou provider、`cloudmusic._mixin_events`
与 `ui.speaker_search_dialog` 各写了一份，行为已经出现细微分叉（`_mixin_events` 多一个
`None` 早退）。统一收在这里，新增音源只引用不再抄写。

各平台返回的歌手字段名不统一，`extract_first_artist` 通过候选键参数化：调用方给出自己
平台的原始键序，命中的第一个非空名字即结果。
"""

from __future__ import annotations

import re
from typing import Sequence

_DURATION_TEXT_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")
_ARTIST_TEXT_SPLIT_RE = re.compile(
    r"\s*(?:、|，|,|&|＆|;|；|\bfeat\.?\b|\bft\.?\b)\s*",
    re.IGNORECASE,
)

UNKNOWN_TITLE = "未知歌曲"
UNKNOWN_ARTIST = "未知作者"

_ARTIST_NAME_KEYS = ("name", "title", "artist")


def format_duration_text(duration_ms) -> str:
    """把毫秒数、`mm:ss` 文本或带时长的字典统一成 `mm:ss`。

    无法解析时返回 `00:00`，不抛异常——列表展示不应该因为一首歌的脏数据中断。
    """
    if duration_ms is None:
        return "00:00"
    try:
        if isinstance(duration_ms, str):
            match = _DURATION_TEXT_RE.match(duration_ms)
            if match:
                total_sec = int(match.group(1)) * 60 + int(match.group(2))
            else:
                total_sec = max(0, int(float(duration_ms)) // 1000)
        elif isinstance(duration_ms, dict):
            raw = (
                duration_ms.get("duration_ms")
                or duration_ms.get("duration")
                or duration_ms.get("dt")
                or duration_ms.get("ms")
            )
            total_sec = max(0, int(raw) // 1000) if raw is not None else 0
        else:
            total_sec = max(0, int(duration_ms) // 1000)
    except (TypeError, ValueError):
        return "00:00"
    mins, secs = divmod(total_sec, 60)
    return f"{mins:02d}:{secs:02d}"


def looks_like_single_slash_name(left: str, right: str) -> bool:
    """判断 `A/B` 这种形态是不是“一个名字里的斜杠”，而不是“两个歌手的拼接”。"""
    return (
        left.isascii()
        and right.isascii()
        and left.upper() == left
        and right.upper() == right
        and len(left) <= 4
        and len(right) <= 4
    )


def split_first_artist_text(artist_text) -> str:
    """从合唱串里取第一位歌手，兼容 `A、B`、`A/B`、`A feat. B` 等写法。"""
    text = str(artist_text or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in _ARTIST_TEXT_SPLIT_RE.split(text) if part.strip()]
    text = parts[0] if parts else text
    if "/" not in text:
        return text
    left, right = [part.strip() for part in text.split("/", 1)]
    if not left or not right or looks_like_single_slash_name(left, right):
        return text
    return left


def extract_artist_name(raw_artist) -> str:
    """递归展开列表 / 字典形态的歌手字段，返回第一位歌手的名字。"""
    if isinstance(raw_artist, list):
        for item in raw_artist:
            name = extract_artist_name(item)
            if name:
                return name
        return ""
    if isinstance(raw_artist, dict):
        for key in _ARTIST_NAME_KEYS:
            name = split_first_artist_text(raw_artist.get(key))
            if name:
                return name
        return ""
    return split_first_artist_text(raw_artist)


def extract_first_artist(
    song: dict,
    *,
    raw_keys: Sequence[str],
    song_keys: Sequence[str],
    unknown: str = UNKNOWN_ARTIST,
) -> str:
    """按调用方给定的键序，从 `song["raw"]` 与 `song` 里找第一位歌手。

    各平台原始字段名不同（酷狗有 `SingerName`，QQ 用 `singer`/`artists`），所以键序由
    调用方传入；解析逻辑本身不重复。
    """
    candidates: list = []
    raw = song.get("raw")
    if isinstance(raw, dict):
        candidates.extend(raw.get(key) for key in raw_keys)
    candidates.extend(song.get(key) for key in song_keys)
    for candidate in candidates:
        artist = extract_artist_name(candidate)
        if artist:
            return artist
    return unknown


def first_artist_from_list(artists, unknown: str = UNKNOWN_ARTIST) -> str:
    """取歌手列表的第一位，不拆分合唱串。

    与 `extract_first_artist` 的区别：这条路径保留原始名字（`A & B` 仍是 `A & B`），
    用于网易云、云音乐管理器与音响搜索面板——它们历史上就是直接取首元素。
    """
    if not artists:
        return unknown
    first = artists[0]
    if isinstance(first, dict):
        name = str(first.get("name") or "").strip()
        return name or unknown
    name = str(first).strip()
    return name or unknown


__all__ = [
    "UNKNOWN_ARTIST",
    "UNKNOWN_TITLE",
    "extract_artist_name",
    "extract_first_artist",
    "first_artist_from_list",
    "format_duration_text",
    "looks_like_single_slash_name",
    "split_first_artist_text",
]