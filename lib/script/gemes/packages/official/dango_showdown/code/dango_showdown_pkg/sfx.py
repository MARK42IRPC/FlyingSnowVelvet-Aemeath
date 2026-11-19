"""Dango cue names mapped to the desktop pet's existing game sound catalog."""

from __future__ import annotations

from pathlib import Path

from lib.script.gemes.MAIN.game_sfx import GameSfx


CUE_MAP = {
    "sun_summon": "skill_cast",
    "sun_burst": "drop_impact",
    "sun_echo": "skill_release",
    "ui_click": "move",
    "ui_confirm": "skill_press",
    "ui_back": "fall",
    "hit_light": "drop_start",
    "hit_heavy": "drop_impact",
    "hurt": "lock",
    "countdown": "fall",
    "start": "rotate",
    "win": "clear",
    "lose": "game_over",
    "draw": "lock",
    "streak": "partner_burst",
    "starburst": "drop_impact",
    "void_hand": "lock",
}

VOLUMES = {
    "sun_summon": .28,
    "sun_burst": .42,
    "sun_echo": .36,
    "ui_click": 0.24,
    "ui_confirm": 0.30,
    "ui_back": 0.24,
    "hit_light": 0.34,
    "hit_heavy": 0.46,
    "hurt": 0.3,
    "countdown": 0.34,
    "start": 0.4,
    "win": 0.44,
    "lose": 0.36,
    "draw": 0.34,
    "streak": 0.38,
    "starburst": 0.58,
    "void_hand": 0.42,
}


class DangoSfx:
    """Keep gameplay cue names; let GameSfx own WAVs and sound requests."""

    def __init__(self, cache_dir: Path, emit) -> None:
        try:
            self._sfx = GameSfx(cache_dir=cache_dir, emit=emit)
        except OSError:
            self._sfx = None

    def play(self, key: str) -> None:
        cue = CUE_MAP.get(key)
        if cue is None or self._sfx is None:
            return
        try:
            self._sfx.play(cue, volume_gain=VOLUMES[key])
        except Exception:
            pass

    def hit(self, speed: float) -> None:
        self.play("hit_heavy" if speed >= 0.45 else "hit_light")
