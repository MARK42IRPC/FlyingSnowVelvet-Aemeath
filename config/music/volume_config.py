"""音乐音量配置模块

管理用户音量偏好，保存到本地 JSON 文件。
音量值范围：0.0 - 1.0
"""

import json
import threading
from pathlib import Path
from typing import Optional

from lib.core.logger import get_logger
from config.config_music import CLOUD_MUSIC
from config.shared_storage import ensure_shared_config_ready, get_project_root, get_shared_config_path
from config.user_settings import load_section, migrate_section_once, save_section
from config.user_storage_paths import get_user_settings_path

_logger = get_logger(__name__)

# 配置文件名
_VOLUME_CONFIG_FILE = "volume.json"

# 默认音量（唯一来源：内置 CLOUD_MUSIC 默认配置）
_DEFAULT_VOLUME = float(CLOUD_MUSIC.get("default_volume", 0.3))

# 单例实例
_instance: Optional["VolumeConfig"] = None
_lock = threading.Lock()


def get_default_volume() -> float:
    return _DEFAULT_VOLUME


def get_volume_config() -> "VolumeConfig":
    """获取音量配置单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = VolumeConfig()
    return _instance


def cleanup_volume_config():
    """清理音量配置单例"""
    global _instance
    with _lock:
        if _instance is not None:
            _instance.save()
            _instance = None


class VolumeConfig:
    """音乐音量配置管理器"""

    def __init__(self, config_dir: Path = None):
        """
        初始化音量配置管理器

        Args:
            config_dir: 配置目录路径，默认为 config/music
        """
        self._uses_unified_settings = config_dir is None
        if self._uses_unified_settings:
            ensure_shared_config_ready()
            self._config_dir = get_user_settings_path().parent
            self._config_file = get_user_settings_path()
        else:
            self._config_dir = Path(config_dir)
            self._config_file = self._config_dir / _VOLUME_CONFIG_FILE
        self._shared_legacy_config_file = get_shared_config_path("music", _VOLUME_CONFIG_FILE)
        self._legacy_config_file = get_project_root() / "config" / "music" / _VOLUME_CONFIG_FILE
        self._volume: float = _DEFAULT_VOLUME
        self._data_lock = threading.Lock()
        
        # 确保目录存在
        self._config_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载配置
        self._load()

    def _load(self):
        """从文件加载音量配置"""
        if self._uses_unified_settings:
            legacy_data = {}
            for source in (self._shared_legacy_config_file, self._legacy_config_file):
                try:
                    payload = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if isinstance(payload, dict):
                    legacy_data = {"volume": payload.get("volume", _DEFAULT_VOLUME)}
                    break
            migrate_section_once(
                "legacy_music_volume_v1",
                "audio",
                legacy_data,
                {"volume": _DEFAULT_VOLUME},
            )
            data = load_section("audio", {"volume": _DEFAULT_VOLUME})
            volume = max(0.0, min(1.0, float(data["volume"])))
            with self._data_lock:
                self._volume = volume
            _logger.info("[VolumeConfig] 已加载音量配置: %.2f (%.0f%%)", volume, volume * 100)
            return

        changed = False
        try:
            source = self._config_file
            if not source.exists() and self._legacy_config_file.exists():
                source = self._legacy_config_file
                changed = True
            if source.exists():
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
                    changed = True
                if "volume" not in data:
                    changed = True
                volume = data.get("volume", _DEFAULT_VOLUME)
                # 确保音量在有效范围内
                volume = max(0.0, min(1.0, float(volume)))
                with self._data_lock:
                    self._volume = volume
                _logger.info("[VolumeConfig] 已加载音量配置: %.2f (%.0f%%)", self._volume, self._volume * 100)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            _logger.warning("[VolumeConfig] 加载音量配置失败: %s，使用默认值", e)
            with self._data_lock:
                self._volume = _DEFAULT_VOLUME
            changed = True
        if changed:
            self.save()

    def save(self):
        """保存音量配置到文件"""
        try:
            with self._data_lock:
                volume = self._volume

            if self._uses_unified_settings:
                save_section("audio", {"volume": volume}, {"volume": _DEFAULT_VOLUME})
                _logger.debug("[VolumeConfig] 已保存稀疏音量配置: %.2f", volume)
                return

            data = {"volume": volume}
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _logger.debug("[VolumeConfig] 已保存音量配置: %.2f (%.0f%%)", volume, volume * 100)
        except OSError as e:
            _logger.error("[VolumeConfig] 保存音量配置失败: %s", e)

    def get_volume(self) -> float:
        """获取当前音量（0.0 - 1.0）"""
        with self._data_lock:
            return self._volume

    def set_volume(self, volume: float):
        """
        设置音量并保存到配置文件

        Args:
            volume: 音量值（0.0 - 1.0）
        """
        # 确保音量在有效范围内
        volume = max(0.0, min(1.0, float(volume)))
        
        with self._data_lock:
            self._volume = volume
        
        _logger.info("[VolumeConfig] 音量已更新: %.2f (%.0f%%)", volume, volume * 100)
        
        # 保存到文件
        self.save()
