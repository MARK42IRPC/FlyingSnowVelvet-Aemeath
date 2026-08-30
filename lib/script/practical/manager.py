"""粒子脚本管理器 - 使用动态注册机制"""
from typing import Dict, Type, List, Optional

from lib.script.practical.base_particle import BaseParticleScript
from lib.script.plugin_registry import particle_registry, discover_particles


class ParticleScriptManager:
    """粒子脚本管理器 - 管理所有粒子效果脚本（动态注册）"""

    def __init__(self):
        self._scripts: Dict[str, Type[BaseParticleScript]] = {}
        self._instances: Dict[str, BaseParticleScript] = {}
        # 自动发现并注册粒子脚本
        self._discover_and_register()

    def _discover_and_register(self):
        """自动发现并注册粒子脚本"""
        # 触发动态发现（扫描 lib/script/practical/ 目录）
        discover_particles()

        # 从全局注册表同步已注册的粒子脚本
        for particle_id, script_class in particle_registry.get_all_classes().items():
            self._scripts[particle_id] = script_class

    def register_script(self, script_class: Type[BaseParticleScript]):
        """
        手动注册粒子脚本

        Args:
            script_class: 粒子脚本类
        """
        particle_id = script_class.PARTICLE_ID
        if particle_id:
            self._scripts[particle_id] = script_class
            # 同时注册到全局注册表
            particle_registry.register(particle_id, script_class)

    def get_script(self, particle_id: str) -> Optional[BaseParticleScript]:
        """
        获取粒子脚本实例（缓存）

        Args:
            particle_id: 粒子ID

        Returns:
            粒子脚本实例，如果不存在则返回 None
        """
        # 优先返回缓存实例
        if particle_id in self._instances:
            return self._instances[particle_id]

        # 创建新实例
        script_class = self._scripts.get(particle_id)
        if script_class:
            instance = script_class()
            self._instances[particle_id] = instance
            return instance
        return None

    def get_all_particle_ids(self) -> List[str]:
        """获取所有已注册的粒子ID"""
        return list(self._scripts.keys())

    def has_particle(self, particle_id: str) -> bool:
        """检查粒子是否已注册"""
        return particle_id in self._scripts

    def reload(self):
        """重新发现并注册粒子脚本（热重载支持）"""
        self._scripts.clear()
        self._instances.clear()
        self._discover_and_register()


# 全局粒子脚本管理器实例
_particle_script_manager = None


def get_particle_script_manager() -> ParticleScriptManager:
    """获取全局粒子脚本管理器实例（单例模式）"""
    global _particle_script_manager
    if _particle_script_manager is None:
        _particle_script_manager = ParticleScriptManager()
    return _particle_script_manager


def cleanup_particle_script_manager():
    """清理全局粒子脚本管理器实例"""
    global _particle_script_manager
    if _particle_script_manager is not None:
        _particle_script_manager._scripts.clear()
        _particle_script_manager._instances.clear()
        _particle_script_manager = None
