"""网易云音乐管理器 - 常量与信号类定义

所有模块级常量和 Qt 信号载体类集中于此，供各 Mixin 文件导入，
避免循环依赖。
"""

from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from config.config import CLOUD_MUSIC, ANIMATION

# ── 播放参数 ──────────────────────────────────────────────────────────────
_BITRATE_LADDER    = CLOUD_MUSIC.get('bitrate_ladder', (320000, 192000, 128000))
_DEFAULT_VOLUME    = CLOUD_MUSIC.get('default_volume', 0.8)
_PYGAME_INIT_WAIT  = CLOUD_MUSIC.get('pygame_init_wait', 5)
_FRAME_FPS         = max(1, int(ANIMATION.get('frame_fps', 60) or 60))
_TICK_FPS          = 20
_PARTICLE_INTERVAL = max(
    1,
    int(round(float(CLOUD_MUSIC.get('particle_interval', 60)) * _TICK_FPS / _FRAME_FPS)),
)

# ── 二维码登录参数 ────────────────────────────────────────────────────────
_QR_LOGIN_TIMEOUT    = CLOUD_MUSIC.get('qr_login_timeout', 180)
_QR_POLL_INTERVAL    = CLOUD_MUSIC.get('qr_poll_interval', 1.0)
_QR_REFRESH_INTERVAL = max(1.0, float(CLOUD_MUSIC.get('qr_refresh_interval', 30.0)))

# ── 音频缓存格式（按优先级排序）─────────────────────────────────────────
_AUDIO_EXT_CANDIDATES = (
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".webm",
)

_CONTENT_TYPE_EXT_MAP = {
    "audio/mpeg":        ".mp3",
    "audio/mp3":         ".mp3",
    "audio/flac":        ".flac",
    "audio/x-flac":      ".flac",
    "audio/wav":         ".wav",
    "audio/wave":        ".wav",
    "audio/x-wav":       ".wav",
    "audio/aac":         ".aac",
    "audio/x-aac":       ".aac",
    "audio/aacp":        ".aac",
    "audio/mp4":         ".m4a",
    "audio/x-m4a":       ".m4a",
    "audio/ogg":         ".ogg",
    "application/ogg":   ".ogg",
    "audio/opus":        ".opus",
    "audio/webm":        ".webm",
}

_LOCAL_TRACK_PREFIX = "local::"


def make_local_track_ref(file_path: str | Path) -> str:
    """将本地音乐路径编码为队列 track_ref。"""
    return f"{_LOCAL_TRACK_PREFIX}{Path(file_path).resolve()}"


def is_local_track_ref(track_ref) -> bool:
    return isinstance(track_ref, str) and track_ref.startswith(_LOCAL_TRACK_PREFIX)


def local_track_path_from_ref(track_ref) -> Path | None:
    if not is_local_track_ref(track_ref):
        return None
    raw = str(track_ref)[len(_LOCAL_TRACK_PREFIX):].strip()
    if not raw:
        return None
    return Path(raw)


# ── 用户数据目录：项目根 / resc / user ────────────────────────────────────
_PROJECT_ROOT             = Path(__file__).parent.parent.parent.parent
_USER_DATA_DIR            = _PROJECT_ROOT / 'resc' / 'user'
_CACHE_DIR                = _PROJECT_ROOT / CLOUD_MUSIC.get('cache_dir', 'resc/user/temp')
_CACHE_PLATFORM_DIRS      = ("netease", "qq", "kugou", "local", "other")
_LOGIN_CACHE_FILE         = _USER_DATA_DIR / 'cloudmusic_login_cache.json'
_QQ_LOGIN_CACHE_FILE      = _USER_DATA_DIR / 'qqmusic_login_cache.json'
_KUGOU_LOGIN_CACHE_FILE   = _USER_DATA_DIR / 'kugou_login_cache.json'
_LEGACY_CACHE_DIR         = _PROJECT_ROOT / 'resc' / 'temp'
_LEGACY_LOGIN_CACHE_FILE  = _PROJECT_ROOT / 'cloudmusic_login_cache.json'


def ensure_user_storage_layout() -> None:
    """确保 user 数据目录存在，并将旧路径缓存迁移到新路径。"""
    _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name in _CACHE_PLATFORM_DIRS:
        (_CACHE_DIR / name).mkdir(parents=True, exist_ok=True)

    if _LEGACY_LOGIN_CACHE_FILE.exists() and not _LOGIN_CACHE_FILE.exists():
        _LEGACY_LOGIN_CACHE_FILE.replace(_LOGIN_CACHE_FILE)

    if _LEGACY_CACHE_DIR.exists() and _LEGACY_CACHE_DIR != _CACHE_DIR:
        for old_path in _LEGACY_CACHE_DIR.iterdir():
            new_path = _CACHE_DIR / old_path.name
            if new_path.exists():
                continue
            old_path.replace(new_path)
        try:
            _LEGACY_CACHE_DIR.rmdir()
        except OSError:
            # 目录非空时保留，避免影响正在使用的文件。
            pass


# ── Qt 信号载体（用于在主线程中执行播放操作）────────────────────────────
class _PlaySignal(QObject):
    """用于在主线程中执行播放操作的信号载体。"""

    play_requested = pyqtSignal(str, str)  # path, display
