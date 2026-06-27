"""ams 拉海洛方块游戏结束语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsLahaiGameOverSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '拉海洛方块游戏结束时'),
            audio_class='voice',
            logger=_logger,
            log_name='AmsLahaiGameOverSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
