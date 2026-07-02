"""拉海洛方块技能释放语音类。"""

from __future__ import annotations

import os
import random

from config.config_voice import VOICE
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class _LahaiSkillWaveSound(DirectoryRandomSound):
    AUDIO_EXT = (".wav",)
    _DEFAULT_VOLUME = 0.7

    def __init__(self, character_name: str, *, sound_subdir: str, log_name: str):
        self._character_name = str(character_name or "").strip()
        self._log_name = str(log_name or self.__class__.__name__)
        self._ec = get_event_center()
        super().__init__(
            sound_dir=os.path.join("resc", "SOUND", str(sound_subdir or "").strip(), self._character_name),
            audio_type="priority_voice",
            logger=_logger,
            log_name=self._log_name,
            volume_range=(self._DEFAULT_VOLUME, self._DEFAULT_VOLUME),
            interruptible=False,
        )

    def _get_volume(self) -> float:
        try:
            return max(0.0, min(1.0, float(VOICE.get("lahai_skill_release_volume", self._DEFAULT_VOLUME))))
        except (TypeError, ValueError):
            return self._DEFAULT_VOLUME

    def play(self):
        if not self._files:
            return
        if len(self._files) > 1 and self._last_file_path in self._files:
            candidates = [p for p in self._files if p != self._last_file_path]
        else:
            candidates = self._files
        selected_file = random.choice(candidates)
        self._last_file_path = selected_file
        self._ec.publish(Event(EventType.VOICE_REQUEST, {
            "audio_type": "priority_voice",
            "source": selected_file,
            "volume_gain": self._get_volume(),
            "interruptible": False,
        }))


class LahaiSkillReleaseSound(_LahaiSkillWaveSound):
    def __init__(self, character_name: str):
        super().__init__(character_name, sound_subdir="技能释放", log_name="LahaiSkillReleaseSound")


class LahaiSkillReleaseFailedSound(_LahaiSkillWaveSound):
    def __init__(self, character_name: str):
        super().__init__(character_name, sound_subdir="技能释放失败", log_name="LahaiSkillReleaseFailedSound")
