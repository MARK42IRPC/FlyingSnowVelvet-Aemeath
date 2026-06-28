"""Audio volume policy helpers."""

from __future__ import annotations

from config.config import SOUND
from config.config_voice import VOICE

_AUDIO_TYPE_VOLUME_KEYS = {
    "voice": ("VOICE", "voice_volume"),
    "pet_voice": ("SOUND", "main_pet_volume"),
    "world_sfx": ("SOUND", "game_object_volume"),
    "game_sfx": ("SOUND", "game_object_volume"),
}


def _clamp_01(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def get_master_volume() -> float:
    """Return global master volume."""
    return _clamp_01(SOUND.get("master_volume", 1.0))


def get_audio_type_volume(audio_type: str) -> float:
    """Return configured volume for a non-music audio type."""
    namespace, key = _AUDIO_TYPE_VOLUME_KEYS.get(
        str(audio_type or "").strip(),
        ("SOUND", "game_object_volume"),
    )
    if namespace == "VOICE":
        return _clamp_01(VOICE.get(key, 1.0))
    return _clamp_01(SOUND.get(key, 1.0))


def get_effective_sound_volume(audio_type: str, volume_gain: float = 1.0) -> float:
    """Return master * type * event gain for non-music audio."""
    return get_master_volume() * get_audio_type_volume(audio_type) * _clamp_01(volume_gain)


def get_effective_music_volume(music_volume: float) -> float:
    """Return master * music volume for long-form music playback."""
    return get_master_volume() * _clamp_01(music_volume)
