"""Procedural 8-bit style sound effects for mini games."""

from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path

from lib.core.event.center import Event, EventType, get_event_center


class GameSfx:
    """Generate lightweight retro SFX and play them via shared voice core."""

    _RATE = 22050
    _AMP = 0.40

    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[4]
        self._dir = root / "resc" / "user" / "temp" / "game_sfx"
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
            self._square_tone(freq_start=1260, freq_end=560, duration=0.060, duty=0.26, volume=0.92),
            self._square_tone(freq_start=680, freq_end=280, duration=0.085, duty=0.42, volume=0.46),
            self._noise(duration=0.040, volume=0.72),
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
