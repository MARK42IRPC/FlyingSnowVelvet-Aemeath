"""ams 鼠标穿透提醒语音类。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class AmsClickthroughReminderSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = False):
        super().__init__(
            sound_dir=os.path.join('resc', 'sound', 'ams', '鼠标穿透提醒'),
            audio_class='voice',
            logger=_logger,
            log_name='AmsClickthroughReminderSound',
            volume_range=(0.30, 0.50),
            interruptible=interruptible,
        )
