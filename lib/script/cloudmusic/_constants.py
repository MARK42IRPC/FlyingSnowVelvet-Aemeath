"""网易云音乐管理器 - 常量与信号类定义

所有模块级常量和 Qt 信号载体类集中于此，供各 Mixin 文件导入，
避免循环依赖。
"""

from pathlib import Path

from config.config import CLOUD_MUSIC, ANIMATION
from config.user_storage_paths import (
    ensure_user_storage_layout as ensure_canonical_user_storage_layout,
    get_user_cache_dir,
    get_user_secrets_dir,
)

# ── 播放参数 ──────────────────────────────────────────────────────────────
_BITRATE_LADDER    = CLOUD_MUSIC.get('bitrate_ladder', (320000, 192000, 128000))
_DEFAULT_VOLUME    = CLOUD_MUSIC.get('default_volume', 0.8)
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


# ── 用户数据目录 ──────────────────────────────────────────────────────────
_PROJECT_ROOT             = Path(__file__).parent.parent.parent.parent
_USER_DATA_DIR            = get_user_secrets_dir('music')
_CACHE_DIR                = get_user_cache_dir('music')
_CACHE_PLATFORM_DIRS      = ("netease", "qq", "kugou", "local", "other")
_LOGIN_CACHE_FILE         = _USER_DATA_DIR / 'cloudmusic_login_cache.json'
_QQ_LOGIN_CACHE_FILE      = _USER_DATA_DIR / 'qqmusic_login_cache.json'
_KUGOU_LOGIN_CACHE_FILE   = _USER_DATA_DIR / 'kugou_login_cache.json'
_LEGACY_CACHE_DIR         = _PROJECT_ROOT / 'resc' / 'temp'
_LEGACY_LOGIN_CACHE_FILE  = _PROJECT_ROOT / 'cloudmusic_login_cache.json'
_LEGACY_USER_DATA_DIR     = _PROJECT_ROOT / 'resc' / 'user'
_LEGACY_USER_CACHE_DIR    = _PROJECT_ROOT / CLOUD_MUSIC.get('cache_dir', 'resc/user/temp')


def _move_children_missing(source_dir: Path, target_dir: Path) -> None:
    if not source_dir.exists() or not source_dir.is_dir():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for old_path in source_dir.iterdir():
        new_path = target_dir / old_path.name
        if new_path.exists():
            continue
        old_path.replace(new_path)
    try:
        source_dir.rmdir()
    except OSError:
        pass


def ensure_user_storage_layout() -> None:
    """确保 user 数据目录存在，并将旧路径缓存迁移到新路径。"""
    ensure_canonical_user_storage_layout()
    _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name in _CACHE_PLATFORM_DIRS:
        (_CACHE_DIR / name).mkdir(parents=True, exist_ok=True)

    if _LEGACY_LOGIN_CACHE_FILE.exists() and not _LOGIN_CACHE_FILE.exists():
        _LEGACY_LOGIN_CACHE_FILE.replace(_LOGIN_CACHE_FILE)

    for name, target in (
        ('cloudmusic_login_cache.json', _LOGIN_CACHE_FILE),
        ('qqmusic_login_cache.json', _QQ_LOGIN_CACHE_FILE),
        ('kugou_login_cache.json', _KUGOU_LOGIN_CACHE_FILE),
    ):
        old_path = _LEGACY_USER_DATA_DIR / name
        if old_path.exists() and not target.exists():
            old_path.replace(target)

    if _LEGACY_CACHE_DIR.exists() and _LEGACY_CACHE_DIR != _CACHE_DIR:
        for name in _CACHE_PLATFORM_DIRS:
            _move_children_missing(_LEGACY_CACHE_DIR / name, _CACHE_DIR / name)

    if _LEGACY_USER_CACHE_DIR.exists() and _LEGACY_USER_CACHE_DIR != _CACHE_DIR:
        for name in _CACHE_PLATFORM_DIRS:
            _move_children_missing(_LEGACY_USER_CACHE_DIR / name, _CACHE_DIR / name)

