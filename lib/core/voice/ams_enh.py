"""ams-enh 音频类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.core import get_voice_core
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsEnhSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', 'enh'),
            audio_type='pet_voice',
            logger=_logger,
            log_name='AmsEnhSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )

    def _can_play(self) -> bool:
        return not get_voice_core().is_type_playing('pet_voice')
