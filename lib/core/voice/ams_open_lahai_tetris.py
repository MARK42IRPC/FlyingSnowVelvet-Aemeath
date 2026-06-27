"""ams 打开拉海洛方块语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsOpenLahaiTetrisSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '打开拉海洛方块'),
            audio_class='voice',
            logger=_logger,
            log_name='AmsOpenLahaiTetrisSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
