"""Backend-neutral font paths, sizes, and registered family names."""
from __future__ import annotations

import re as _re
from pathlib import Path as _Path

from config.scale import scale_px


_DEFAULT_FONT_PX = {
    "ui_size": 12,
    "cmd_size": 12,
}

FONT = dict(_DEFAULT_FONT_PX)

_FONT_DIR = _Path(__file__).parent.parent / "resc" / "FRONTS"
_HARMONY_PATH = str(_FONT_DIR / "HarmonyOS_Sans_SC_Bold.ttf")
_LAHAI_ROI_PATH = str(_FONT_DIR / "WuWa Lahai-Roi Regular.ttf")

_harmony_family: str | None = None
_lahai_roi_family: str | None = None

_DIGIT_RE = _re.compile(r"\d+")
_DIGIT_SPLIT_RE = _re.compile(r"(\d+)")


def _set_scaled_font_defaults() -> None:
    for key, design_px in _DEFAULT_FONT_PX.items():
        FONT[key] = max(9, scale_px(design_px, min_abs=1))


def init_font_config() -> None:
    """Refresh backend-neutral font sizes after draw scale changes."""
    _set_scaled_font_defaults()


def _set_registered_font_families(ui_family: str, digit_family: str) -> None:
    global _harmony_family, _lahai_roi_family
    _harmony_family = str(ui_family or "Microsoft YaHei")
    _lahai_roi_family = str(digit_family or _harmony_family)


def get_ui_font_family() -> str:
    return _harmony_family or "Microsoft YaHei"


def get_digit_font_family() -> str:
    return _lahai_roi_family or get_ui_font_family()


def _split_digit_segments(text: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    for segment in _DIGIT_SPLIT_RE.split(text):
        if segment:
            segments.append((segment, bool(_DIGIT_RE.fullmatch(segment))))
    return segments
