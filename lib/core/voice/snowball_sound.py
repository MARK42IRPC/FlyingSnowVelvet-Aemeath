"""雪球音频类（响度相对 SnowSound 降低 0.75 倍）。"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class SnowballSound(DirectoryRandomSound):
    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'SOUND', 'snow'),
            audio_class='snow',
            logger=_logger,
            log_name='SnowballSound',
            volume_range=(0.225, 0.375),  # SnowSound 的 0.75 倍衰减
            interruptible=interruptible,
        )
