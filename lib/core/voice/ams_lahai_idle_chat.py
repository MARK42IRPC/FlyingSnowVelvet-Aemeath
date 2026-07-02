"""ams 拉海洛方块闲聊语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsLahaiIdleChatSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '拉海洛方块闲聊'),
            audio_type='voice',
            logger=_logger,
            log_name='AmsLahaiIdleChatSound',
            volume_range=(0.28, 0.45),
            interruptible=interruptible,
        )
