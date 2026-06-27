"""ams 拉海洛方块等级提升语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsLahaiLevelUpSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '拉海洛方块等级提升时'),
            audio_class='voice',
            logger=_logger,
            log_name='AmsLahaiLevelUpSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
