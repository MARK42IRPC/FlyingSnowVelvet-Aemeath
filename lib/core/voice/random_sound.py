"""Shared random-directory sound requester."""

import os
import random
from typing import List, Tuple

from lib.core.event.center import Event, EventType, get_event_center


class DirectoryRandomSound:
    """Publish VOICE_REQUEST with random file and per-event gain from a directory."""

    AUDIO_EXT = ('.mp3', '.wav', '.ogg', '.flac', '.m4a')

    def __init__(
        self,
        *,
        sound_dir: str,
        audio_type: str,
        logger,
        log_name: str,
        volume_range: Tuple[float, float] = (0.30, 0.50),
        interruptible: bool = True,
    ):
        self._sound_dir = sound_dir
        self._audio_type = audio_type
        self._logger = logger
        self._log_name = log_name
        self._vol_min = volume_range[0]
        self._vol_max = volume_range[1]
        self._interruptible = interruptible
        self._ec = get_event_center()
        self._files: List[str] = self._scan_files()
        self._last_file_path: str | None = None

        if not self._files:
            self._logger.warning('[%s] 警告: 未在 %s 找到音频文件', self._log_name, self._sound_dir)

    def _scan_files(self) -> List[str]:
        if not os.path.isdir(self._sound_dir):
            return []
        return [
            os.path.abspath(os.path.join(self._sound_dir, f))
            for f in sorted(os.listdir(self._sound_dir))
            if f.lower().endswith(self.AUDIO_EXT)
        ]

    def _can_play(self) -> bool:
        return True

    def play(self):
        if not self._files or not self._can_play():
            return
        if len(self._files) > 1 and self._last_file_path in self._files:
            candidates = [p for p in self._files if p != self._last_file_path]
        else:
            candidates = self._files
        selected_file = random.choice(candidates)
        self._last_file_path = selected_file
        self._ec.publish(Event(EventType.VOICE_REQUEST, {
            'audio_type': self._audio_type,
            'source': selected_file,
            'volume_gain': random.uniform(self._vol_min, self._vol_max),
            'interruptible': self._interruptible,
        }))

    @property
    def file_count(self) -> int:
        return len(self._files)
