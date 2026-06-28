"""ams 音响创建语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsSpeakerCreateSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '音响时'),
            audio_type='voice',
            logger=_logger,
            log_name='AmsSpeakerCreateSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
