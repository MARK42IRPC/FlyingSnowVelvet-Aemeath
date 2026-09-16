"""留言墙违规提示语音类。

和 `ams_bug` / `ams_speaker_create` 走同一条触发型语音链路：本类只负责按违规类别挑一条音频
并发布 `VOICE_REQUEST`，由 `lib/script/voice/handler.py` 转成 `SOUND_REQUEST` 交给音频核心。

音频放在 `resc/SOUND/forum/违规时/` 下，按类别分子目录（`FORUM_VIOLATION_SOUND_DIRS`）：
链接、数字、辱骂、引流、广告各有一套话术，剩下的走「通用」。判定给出的类别（`forum_filter`
的 `FORUM_CATEGORY_*`）在这里翻成目录名，目录里没有音频就回落到「通用」——新增一类话术只要
往对应目录丢 wav，代码不用改。同目录的 `文案.txt` 记着每条音频对应的文字、语音包与生成参数，
替换音频时按它重制，别让文案只活在波形里。挑文件的规则来自 `DirectoryRandomSound`：同类里
随机选一条，且不连续重复上一条。
"""

import os

from lib.core.forum_filter import (
    FORUM_CATEGORY_ABUSE,
    FORUM_CATEGORY_LINK,
    FORUM_CATEGORY_NUMBER,
    FORUM_CATEGORY_PROMOTION,
    FORUM_CATEGORY_TRAFFIC,
)
from lib.core.logger import get_logger
from lib.core.voice.random_sound import DirectoryRandomSound

_logger = get_logger(__name__)

#: 违规音频的根目录：类别目录都长在它下面。
FORUM_VIOLATION_SOUND_ROOT = os.path.join('resc', 'SOUND', 'forum', '违规时')
#: 没有专属话术的类别（违法违规、兜底）走这个目录。
FORUM_VIOLATION_FALLBACK_DIR = '通用'
#: 判定类别 -> 音频目录。表中没有的类别一律走「通用」，所以新增话术不必改这里的结构。
FORUM_VIOLATION_SOUND_DIRS = {
    FORUM_CATEGORY_LINK: '链接',
    FORUM_CATEGORY_NUMBER: '数字',
    FORUM_CATEGORY_ABUSE: '辱骂',
    FORUM_CATEGORY_TRAFFIC: '引流',
    FORUM_CATEGORY_PROMOTION: '广告',
}


class ForumViolationSound:
    """留言被本地过滤拦下时的提示语音：按违规类别挑目录，同类里随机。"""

    def __init__(self, interruptible: bool = True):
        self._interruptible = bool(interruptible)
        self._sounds: dict[str, DirectoryRandomSound] = {}

    # ── 对外 ─────────────────────────────────────────────────────────

    def play(self, category: str = "") -> None:
        """播一条该类别的话术；类别没有专属音频时播「通用」。"""
        sound = self._sound_for(category)
        if sound is not None:
            sound.play()

    @property
    def file_count(self) -> int:
        """所有类别目录里的音频总数：为 0 表示这次连兜底话术都没有。"""
        total = 0
        for name in self._dir_names():
            sound = self._sound(name)
            if sound is not None:
                total += sound.file_count
        return total

    def has_category(self, category: str) -> bool:
        """该类别是否有自己的音频（没有就走通用）。"""
        name = FORUM_VIOLATION_SOUND_DIRS.get(str(category or ''))
        sound = self._sound(name) if name else None
        return bool(sound is not None and sound.file_count)

    # ── 内部 ─────────────────────────────────────────────────────────

    def _dir_names(self) -> tuple[str, ...]:
        names = [FORUM_VIOLATION_FALLBACK_DIR]
        names.extend(
            name for name in FORUM_VIOLATION_SOUND_DIRS.values()
            if name != FORUM_VIOLATION_FALLBACK_DIR
        )
        return tuple(dict.fromkeys(names))

    def _sound(self, dir_name: str) -> DirectoryRandomSound | None:
        """按目录名取（并缓存）一个随机播放器；目录不存在时返回 None。"""
        if not dir_name:
            return None
        sound = self._sounds.get(dir_name)
        if sound is None:
            path = os.path.join(FORUM_VIOLATION_SOUND_ROOT, dir_name)
            if not os.path.isdir(path):
                return None
            sound = DirectoryRandomSound(
                sound_dir=path,
                audio_type='voice',
                logger=_logger,
                log_name=f'ForumViolationSound[{dir_name}]',
                volume_range=(0.32, 0.52),
                interruptible=self._interruptible,
            )
            self._sounds[dir_name] = sound
        return sound

    def _sound_for(self, category: str) -> DirectoryRandomSound | None:
        """某类别该用的播放器：优先专属目录，没音频就回落到通用。"""
        name = FORUM_VIOLATION_SOUND_DIRS.get(str(category or ''))
        if name:
            sound = self._sound(name)
            if sound is not None and sound.file_count:
                return sound
        return self._sound(FORUM_VIOLATION_FALLBACK_DIR)


__all__ = [
    "FORUM_VIOLATION_FALLBACK_DIR",
    "FORUM_VIOLATION_SOUND_DIRS",
    "FORUM_VIOLATION_SOUND_ROOT",
    "ForumViolationSound",
]
