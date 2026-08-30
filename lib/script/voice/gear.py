"""gear 音频类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class GearSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'SOUND', 'gear'),
            audio_type='world_sfx',
            logger=_logger,
            log_name='GearSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
