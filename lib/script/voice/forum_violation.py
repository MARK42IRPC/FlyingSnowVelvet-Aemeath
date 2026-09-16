"""留言墙违规提示语音类。

和 `ams_bug` / `ams_speaker_create` 走同一条触发型语音链路：本类只负责挑一条音频并发布
`VOICE_REQUEST`，由 `lib/script/voice/handler.py` 转成 `SOUND_REQUEST` 交给音频核心。

音频放在 `resc/SOUND/forum/违规时/`，由语音模块用同一套人设音色事先合成，文案是爱弥斯口吻
的简短提醒（「等等，这条留言可不能发出去哦」这类）。同目录的 `文案.txt` 记着每条音频对应
的文字、语音包和生成参数，替换音频时按它重制，别让文案只活在波形里。挑文件的规则来自
`DirectoryRandomSound`：随机选一条，且不连续重复上一条。
"""

import os

from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)


class ForumViolationSound(DirectoryRandomSound):
    """留言被本地过滤拦下时的提示语音。"""

    def __init__(self, interruptible: bool = True):
        super().__init__(
            sound_dir=os.path.join('resc', 'SOUND', 'forum', '违规时'),
            audio_type='voice',
            logger=_logger,
            log_name='ForumViolationSound',
            volume_range=(0.32, 0.52),
            interruptible=interruptible,
        )


__all__ = ["ForumViolationSound"]
