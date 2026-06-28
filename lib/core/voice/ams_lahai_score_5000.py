"""ams 拉海洛方块分数达到5000语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsLahaiScore5000Sound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '分数达到5000时'),
            audio_type='voice',
            logger=_logger,
            log_name='AmsLahaiScore5000Sound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
