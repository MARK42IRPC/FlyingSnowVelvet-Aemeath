"""Procedural 8-bit style sound effects for mini games."""

from __future__ import annotations

import math
import os
import shutil
import struct
import wave
from pathlib import Path

from config.user_storage_paths import get_user_cache_dir
from lib.core.event.center import Event, EventType, get_event_center


class GameSfx:
    """Generate lightweight retro SFX and play them via shared voice core."""

    _RATE = 22050
    _AMP = 0.40

    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[4]
        self._dir = get_user_cache_dir("game_sfx")
        if not self._dir.exists():
            for legacy_dir in (
                get_user_cache_dir("music", "game_sfx"),
                root / "resc" / "user" / "temp" / "game_sfx",
            ):
                if not legacy_dir.exists():
                    continue
                try:
                    shutil.copytree(legacy_dir, self._dir, dirs_exist_ok=True)
                    break
                except OSError:
                    continue
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ec = get_event_center()
        self._files = {
            "move": self._ensure_wave("lahai_move.wav", self._build_move),
            "fall": self._ensure_wave("lahai_fall.wav", self._build_fall),
            "rotate": self._ensure_wave("lahai_rotate.wav", self._build_rotate),
            "lock": self._ensure_wave("lahai_lock.wav", self._build_lock),
            "drop_start": self._ensure_wave("lahai_drop_start.wav", self._build_drop_start),
            "drop_impact": self._ensure_wave("lahai_drop_impact.wav", self._build_drop_impact),
            "clear": self._ensure_wave("lahai_clear.wav", self._build_clear),
            "skill_press": self._ensure_wave("lahai_skill_press.wav", self._build_skill_press),
            "skill_cast": self._ensure_wave("lahai_skill_cast.wav", self._build_skill_cast),
            "skill_release": self._ensure_wave("lahai_skill_release.wav", self._build_skill_release),
            "partner_burst": self._ensure_wave("lahai_partner_burst.wav", self._build_partner_burst),
            "game_over": self._ensure_wave("lahai_game_over.wav", self._build_game_over),
        }

    def _ensure_wave(self, name: str, builder) -> str:
        path = self._dir / name
        if not path.exists():
            frames = builder()
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self._RATE)
                wav.writeframes(frames)
        return str(path)

    def _emit(self, key: str, volume: float) -> None:
        path = self._files.get(key)
        if not path:
            return
        self._ec.publish(Event(EventType.SOUND_REQUEST, {
            "audio_type": "game_sfx",
            "source": path,
            "volume_gain": volume,
            "interruptible": True,
        }))

    @classmethod
    def _pack(cls, samples: list[float]) -> bytes:
        out = bytearray()
        for sample in samples:
            value = max(-1.0, min(1.0, sample))
            out.extend(struct.pack("<h", int(value * 32767)))
        return bytes(out)

    @classmethod
    def _square_tone(
        cls,
        *,
        freq_start: float,
        freq_end: float,
        duration: float,
        duty: float = 0.5,
        volume: float = 1.0,
        vibrato_hz: float = 0.0,
        vibrato_depth: float = 0.0,
    ) -> list[float]:
        count = max(1, int(cls._RATE * duration))
        samples: list[float] = []
        phase = 0.0
        for i in range(count):
            t = i / cls._RATE
            progress = i / max(1, count - 1)
            freq = freq_start + (freq_end - freq_start) * progress
            if vibrato_hz > 0.0 and vibrato_depth > 0.0:
                freq *= 1.0 + math.sin(t * math.tau * vibrato_hz) * vibrato_depth
            phase += freq / cls._RATE
            square = 1.0 if (phase % 1.0) < duty else -1.0
            envelope = min(1.0, t * 60.0) * max(0.0, 1.0 - progress) ** 1.6
            samples.append(square * envelope * volume * cls._AMP)
        return samples

    @classmethod
    def _noise(cls, *, duration: float, volume: float = 1.0) -> list[float]:
        count = max(1, int(cls._RATE * duration))
        state = 0x13579B
        samples: list[float] = []
        for i in range(count):
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= (state >> 17)
            state ^= (state << 5) & 0xFFFFFFFF
            rand = ((state & 0xFFFF) / 32767.5) - 1.0
            progress = i / max(1, count - 1)
            envelope = max(0.0, 1.0 - progress) ** 2.4
            samples.append(rand * envelope * volume * cls._AMP * 0.45)
        return samples

    @classmethod
    def _mix(cls, *tracks: list[float]) -> bytes:
        length = max((len(track) for track in tracks), default=0)
        samples = [0.0] * length
        for track in tracks:
            for i, sample in enumerate(track):
                samples[i] += sample
        return cls._pack(samples)

    def _build_move(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=820, freq_end=980, duration=0.050, duty=0.33, volume=0.90),
        )

    def _build_fall(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=560, freq_end=620, duration=0.035, duty=0.30, volume=0.46),
        )

    def _build_rotate(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=620, freq_end=1120, duration=0.085, duty=0.40, volume=1.00, vibrato_hz=18.0, vibrato_depth=0.03),
        )

    def _build_lock(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=380, freq_end=210, duration=0.10, duty=0.48, volume=1.00),
            self._noise(duration=0.045, volume=0.60),
        )

    def _build_drop_start(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=980, freq_end=520, duration=0.070, duty=0.28, volume=0.82),
            self._square_tone(freq_start=640, freq_end=360, duration=0.085, duty=0.50, volume=0.38),
        )

    def _build_drop_impact(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=360, freq_end=82, duration=0.160, duty=0.50, volume=1.00),
            self._square_tone(freq_start=720, freq_end=180, duration=0.115, duty=0.40, volume=0.52),
            self._noise(duration=0.075, volume=0.88),
        )

    def _build_clear(self) -> bytes:
        notes = [
            self._square_tone(freq_start=660, freq_end=760, duration=0.055, duty=0.34, volume=0.78),
            self._square_tone(freq_start=880, freq_end=1040, duration=0.060, duty=0.34, volume=0.82),
            self._square_tone(freq_start=1180, freq_end=1480, duration=0.090, duty=0.30, volume=0.92),
        ]
        gap = [0.0] * int(self._RATE * 0.020)
        sequence = notes[0] + gap + notes[1] + gap + notes[2]
        sparkle = self._noise(duration=len(sequence) / self._RATE, volume=0.20)
        return self._mix(sequence, sparkle)

    def _build_skill_press(self) -> bytes:
        return self._mix(
            self._square_tone(freq_start=920, freq_end=1180, duration=0.045, duty=0.30, volume=0.62),
            self._square_tone(freq_start=560, freq_end=720, duration=0.050, duty=0.50, volume=0.22),
        )

    def _build_skill_cast(self) -> bytes:
        first = self._square_tone(freq_start=520, freq_end=760, duration=0.095, duty=0.34, volume=0.68)
        second = self._square_tone(freq_start=760, freq_end=1040, duration=0.105, duty=0.30, volume=0.74)
        third = self._square_tone(freq_start=1040, freq_end=880, duration=0.090, duty=0.40, volume=0.60)
        tail = self._square_tone(freq_start=660, freq_end=520, duration=0.085, duty=0.48, volume=0.38)
        gap = [0.0] * int(self._RATE * 0.018)
        sparkle = self._noise(duration=(len(first) + len(second) + len(third) + len(tail) + len(gap) * 3) / self._RATE, volume=0.12)
        sequence = first + gap + second + gap + third + gap + tail
        return self._mix(sequence, sparkle)

    def _build_skill_release(self) -> bytes:
        hit = self._square_tone(freq_start=420, freq_end=220, duration=0.14, duty=0.46, volume=0.92)
        top = self._square_tone(freq_start=980, freq_end=620, duration=0.11, duty=0.26, volume=0.58)
        body = self._square_tone(freq_start=240, freq_end=180, duration=0.18, duty=0.50, volume=0.64)
        impact = self._noise(duration=0.060, volume=0.62)
        return self._mix(hit, top, body, impact)

    def _build_partner_burst(self) -> bytes:
        click = self._square_tone(freq_start=1320, freq_end=980, duration=0.028, duty=0.22, volume=1.00)
        chirp = self._square_tone(freq_start=920, freq_end=1460, duration=0.040, duty=0.30, volume=0.86)
        snap = self._square_tone(freq_start=560, freq_end=420, duration=0.050, duty=0.18, volume=0.40)
        sparkle = self._noise(duration=0.024, volume=0.34)
        return self._mix(click, chirp, snap, sparkle)

    def _build_game_over(self) -> bytes:
        note1 = self._square_tone(freq_start=560, freq_end=500, duration=0.20, duty=0.46, volume=0.62)
        note2 = self._square_tone(freq_start=430, freq_end=380, duration=0.22, duty=0.48, volume=0.68)
        note3 = self._square_tone(freq_start=320, freq_end=260, duration=0.26, duty=0.50, volume=0.74)
        bass = self._square_tone(freq_start=150, freq_end=92, duration=0.84, duty=0.52, volume=0.38)
        undertone = self._square_tone(freq_start=240, freq_end=180, duration=0.52, duty=0.50, volume=0.24)
        tail_noise = self._noise(duration=0.30, volume=0.22)
        gap1 = [0.0] * int(self._RATE * 0.030)
        gap2 = [0.0] * int(self._RATE * 0.040)
        gap3 = [0.0] * int(self._RATE * 0.020)
        lead = note1 + gap1 + note2 + gap2 + note3 + gap3
        return self._mix(lead, bass, undertone, ([0.0] * (len(lead) - len(tail_noise)) + tail_noise) if len(lead) >= len(tail_noise) else tail_noise)

    def play_move(self) -> None:
        self._emit("move", 0.28)

    def play_fall(self) -> None:
        self._emit("fall", 0.18)

    def play_rotate(self) -> None:
        self._emit("rotate", 0.34)

    def play_lock(self) -> None:
        self._emit("lock", 0.36)

    def play_drop_start(self) -> None:
        self._emit("drop_start", 0.34)

    def play_drop_impact(self) -> None:
        self._emit("drop_impact", 0.48)

    def play_clear(self) -> None:
        self._emit("clear", 0.46)

    def play_skill_press(self) -> None:
        self._emit("skill_press", 0.22)

    def play_skill_cast(self) -> None:
        self._emit("skill_cast", 0.30)

    def play_skill_release(self) -> None:
        self._emit("skill_release", 0.54)

    def play_partner_burst(self) -> None:
        self._emit("partner_burst", 0.40)

    def play_game_over(self) -> None:
        self._emit("game_over", 0.46)
