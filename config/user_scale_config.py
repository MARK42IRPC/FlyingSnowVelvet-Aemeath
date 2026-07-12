"""用户缩放配置模块

管理用户自定义缩放偏好，保存到本地 JSON 文件。
缩放值范围：0.5 - 2.0
默认值：1.0
"""

import json
import threading
from pathlib import Path
from typing import Optional

from lib.core.logger import get_logger
from config.scale import set_user_scale, get_user_scale, adjust_user_scale
from config.shared_storage import ensure_shared_config_ready, get_project_root, get_shared_config_path
from config.user_settings import load_section, migrate_section_once, save_section
from config.user_storage_paths import get_user_settings_path

_logger = get_logger(__name__)

# 配置文件名
_USER_SCALE_CONFIG_FILE = "user_scale.json"

# 默认缩放
_DEFAULT_USER_SCALE = 1.0

# 单例实例
_instance: Optional["UserScaleConfig"] = None
_lock = threading.Lock()


def get_user_scale_config() -> "UserScaleConfig":
    """获取用户缩放配置单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = UserScaleConfig()
    return _instance


def cleanup_user_scale_config():
    """清理用户缩放配置单例"""
    global _instance
    with _lock:
        if _instance is not None:
            _instance.save()
            _instance = None


class UserScaleConfig:
    """用户缩放配置管理器"""

    def __init__(self, config_dir: Path = None):
        """
        初始化用户缩放配置管理器

        Args:
            config_dir: 配置目录路径，默认为 config
        """
        self._uses_unified_settings = config_dir is None
        if self._uses_unified_settings:
            ensure_shared_config_ready()
            self._config_dir = get_user_settings_path().parent
            self._config_file = get_user_settings_path()
        else:
            self._config_dir = Path(config_dir)
            self._config_file = self._config_dir / _USER_SCALE_CONFIG_FILE
        self._shared_legacy_config_file = get_shared_config_path(_USER_SCALE_CONFIG_FILE)
        self._legacy_config_file = get_project_root() / "config" / _USER_SCALE_CONFIG_FILE
        self._scale: float = _DEFAULT_USER_SCALE
        self._data_lock = threading.Lock()
        
        self._config_dir.mkdir(parents=True, exist_ok=True)
        # 加载配置
        self._load()
        # 同步到 scale.py 的全局变量
        set_user_scale(self._scale)

    def _load(self):
        """从文件加载用户缩放配置"""
        if self._uses_unified_settings:
            legacy_data = {}
            for source in (self._shared_legacy_config_file, self._legacy_config_file):
                try:
                    payload = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if isinstance(payload, dict):
                    legacy_data = {"scale": payload.get("user_scale", _DEFAULT_USER_SCALE)}
                    break
            migrate_section_once(
                "legacy_user_scale_v1",
                "ui",
                legacy_data,
                {"scale": _DEFAULT_USER_SCALE},
            )
            data = load_section("ui", {"scale": _DEFAULT_USER_SCALE})
            scale = max(0.5, min(2.0, float(data["scale"])))
            with self._data_lock:
                self._scale = scale
            _logger.info("[UserScaleConfig] 已加载用户缩放配置: %.1f", self._scale)
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
                if "user_scale" not in data:
                    changed = True
                scale = data.get("user_scale", _DEFAULT_USER_SCALE)
                # 确保缩放在有效范围内
                scale = max(0.5, min(2.0, float(scale)))
                with self._data_lock:
                    self._scale = scale
                _logger.info("[UserScaleConfig] 已加载用户缩放配置: %.1f", self._scale)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            _logger.warning("[UserScaleConfig] 加载用户缩放配置失败: %s，使用默认值", e)
            with self._data_lock:
                self._scale = _DEFAULT_USER_SCALE
            changed = True
        if changed:
            self.save()

    def save(self):
        """保存用户缩放配置到文件"""
        try:
            with self._data_lock:
                scale = self._scale

            if self._uses_unified_settings:
                save_section("ui", {"scale": scale}, {"scale": _DEFAULT_USER_SCALE})
                _logger.debug("[UserScaleConfig] 已保存稀疏用户缩放配置: %.1f", scale)
                return

            data = {"user_scale": scale}
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _logger.debug("[UserScaleConfig] 已保存用户缩放配置: %.1f", scale)
        except OSError as e:
            _logger.error("[UserScaleConfig] 保存用户缩放配置失败: %s", e)

    def get_scale(self) -> float:
        """获取当前用户缩放（0.5 - 2.0）"""
        with self._data_lock:
            return self._scale

    def set_scale(self, scale: float):
        """
        设置用户缩放并保存到配置文件

        Args:
            scale: 缩放值（0.5 - 2.0）
        """
        # 确保缩放在有效范围内
        scale = max(0.5, min(2.0, float(scale)))
        
        with self._data_lock:
            self._scale = scale
        
        # 同步到 scale.py 的全局变量
        set_user_scale(scale)
        
        _logger.info("[UserScaleConfig] 用户缩放已更新: %.1f", scale)
        
        # 保存到文件
        self.save()

    def adjust_scale(self, delta: float) -> float:
        """
        调整用户缩放并保存

        Args:
            delta: 调整量（如 0.1 或 -0.1）

        Returns:
            新的缩放值
        """
        with self._data_lock:
            new_scale = self._scale + delta
            new_scale = max(0.5, min(2.0, new_scale))
            self._scale = new_scale
        
        # 同步到 scale.py 的全局变量
        set_user_scale(new_scale)
        
        _logger.info("[UserScaleConfig] 用户缩放已调整: %.1f", new_scale)
        
        # 保存到文件
        self.save()
        
        return new_scale
