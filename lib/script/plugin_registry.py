"""
插件注册中心 - 支持模块动态发现和注册

提供统一的插件注册机制，让各模块（管理器、粒子脚本等）
可以自注册，主程序通过注册表发现并初始化。

使用方式：
1. 模块定义时使用装饰器注册：
   @register_manager('snow_leopard')
   class SnowLeopardManager(BaseManager):
       ...

2. 或在模块末尾手动注册：
   manager_registry.register('snow_leopard', SnowLeopardManager)

3. 主程序启动时扫描并初始化：
   from lib.script.plugin_registry import discover_all, init_all_managers
   discover_all()
   init_all_managers(entity)
"""

import importlib
import os
from typing import Dict, List, Type, Callable, Any, Optional
from abc import ABC, abstractmethod

from lib.core.logger import get_logger
logger = get_logger(__name__)


# ======================================================================
# 基类定义
# ======================================================================

class BaseManager(ABC):
    """管理器基类 - 所有管理器都应继承此类"""

    # 管理器唯一标识符（子类必须定义）
    MANAGER_ID: str = None

    # 管理器显示名称（用于提示信息）
    DISPLAY_NAME: str = ""

    # 命令触发词（如 '雪豹'、'沙发'，可为空表示不响应命令）
    COMMAND_TRIGGER: str = ""

    # 命令帮助信息
    COMMAND_HELP: str = ""

    @classmethod
    @abstractmethod
    def create(cls, entity: Any = None, **kwargs) -> "BaseManager":
        """
        工厂方法：创建管理器实例

        Args:
            entity: 主实体引用（如 PetWindow）
            **kwargs: 其他初始化参数

        Returns:
            管理器实例
        """
        pass

    @abstractmethod
    def cleanup(self):
        """清理资源"""
        pass


# ======================================================================
# 通用注册表
# ======================================================================

class Registry:
    """通用注册表"""

    def __init__(self, name: str):
        self._name = name
        self._items: Dict[str, Type] = {}
        self._instances: Dict[str, Any] = {}

    def register(self, item_id: str, item_class: Type) -> None:
        """
        注册项目

        Args:
            item_id: 项目唯一标识符
            item_class: 项目类
        """
        if item_id in self._items:
            logger.warning("[%s] 警告：'%s' 已注册，将被覆盖", self._name, item_id)
        self._items[item_id] = item_class

    def unregister(self, item_id: str) -> None:
        """取消注册"""
        self._items.pop(item_id, None)
        self._instances.pop(item_id, None)

    def get_class(self, item_id: str) -> Optional[Type]:
        """获取项目类"""
        return self._items.get(item_id)

    def get_instance(self, item_id: str) -> Optional[Any]:
        """获取项目实例（单例）"""
        return self._instances.get(item_id)

    def set_instance(self, item_id: str, instance: Any) -> None:
        """设置项目实例"""
        self._instances[item_id] = instance

    def get_all_ids(self) -> List[str]:
        """获取所有已注册的ID"""
        return list(self._items.keys())

    def get_all_classes(self) -> Dict[str, Type]:
        """获取所有已注册的类"""
        return dict(self._items)

    def clear(self) -> None:
        """清空注册表"""
        self._items.clear()
        self._instances.clear()

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._items

    def __len__(self) -> int:
        return len(self._items)


# ======================================================================
# 具体注册表实例
# ======================================================================

# 管理器注册表
manager_registry = Registry("ManagerRegistry")

# 粒子脚本注册表
particle_registry = Registry("ParticleRegistry")

# 特效脚本注册表
effect_registry = Registry("EffectRegistry")

# 命令处理器注册表


# ======================================================================
# 装饰器
# ======================================================================

def register_manager(manager_id: str):
    """
    管理器注册装饰器

    Usage:
        @register_manager('snow_leopard')
        class SnowLeopardManager(BaseManager):
            MANAGER_ID = 'snow_leopard'
            ...
    """
    def decorator(cls):
        manager_registry.register(manager_id, cls)
        return cls
    return decorator


def register_particle(particle_id: str):
    """
    粒子脚本注册装饰器

    Usage:
        @register_particle('scatter_fall')
        class SnowParticleScript(BaseParticleScript):
            PARTICLE_ID = 'scatter_fall'
            ...
    """
    def decorator(cls):
        particle_registry.register(particle_id, cls)
        return cls
    return decorator


def register_effect(effect_id: str):
    """
    特效脚本注册装饰器

    Usage:
        @register_effect('smooth_image_show')
        class SmoothImageShowEffectScript(BaseEffectScript):
            EFFECT_ID = 'smooth_image_show'
            ...
    """
    def decorator(cls):
        effect_registry.register(effect_id, cls)
        return cls
    return decorator


def discover_managers() -> None:
    """
    自动发现并注册所有管理器模块

    扫描 lib/script/obj-* 目录下的 manager.py 文件
    """
    script_path = os.path.join(os.path.dirname(__file__), '..', 'script')
    script_path = os.path.abspath(script_path)

    if not os.path.exists(script_path):
        return

    for item in os.listdir(script_path):
        item_path = os.path.join(script_path, item)
        if os.path.isdir(item_path) and item.startswith('obj-'):
            manager_file = os.path.join(item_path, 'manager.py')
            if os.path.exists(manager_file):
                # 构建模块路径
                module_path = f"lib.script.{item}.manager"
                try:
                    importlib.import_module(module_path)
                except Exception as e:
                    logger.warning('[PluginRegistry] 警告：无法加载模块 %s: %s', module_path, e)


def discover_particles() -> None:
    """
    自动发现并注册所有粒子脚本

    扫描 lib/script/practical/ 目录下的 *_particle.py 文件
    """
    practical_path = os.path.join(
        os.path.dirname(__file__), '..', 'script', 'practical'
    )
    practical_path = os.path.abspath(practical_path)

    if not os.path.exists(practical_path):
        return

    for file in os.listdir(practical_path):
        if file.endswith('_particle.py'):
            module_path = f"lib.script.practical.{file[:-3]}"
            try:
                importlib.import_module(module_path)
            except Exception as e:
                logger.warning('[PluginRegistry] 警告：无法加载模块 %s: %s', module_path, e)


def discover_effects() -> None:
    """
    自动发现并注册所有特效脚本

    扫描 lib/script/effects/ 目录下的 *_effect.py 文件
    """
    effects_path = os.path.join(
        os.path.dirname(__file__), '..', 'script', 'effects'
    )
    effects_path = os.path.abspath(effects_path)

    if not os.path.exists(effects_path):
        return

    for file in os.listdir(effects_path):
        if file.endswith('_effect.py'):
            module_path = f"lib.script.effects.{file[:-3]}"
            try:
                importlib.import_module(module_path)
            except Exception as e:
                logger.warning('[PluginRegistry] 警告：无法加载模块 %s: %s', module_path, e)


def discover_all() -> None:
    """发现所有可注册的模块"""
    discover_managers()
    discover_particles()
    discover_effects()


# ======================================================================
# 初始化函数
# ======================================================================

def init_manager(manager_id: str, entity: Any = None, **kwargs) -> Optional[BaseManager]:
    """
    初始化指定管理器

    Args:
        manager_id: 管理器ID
        entity: 主实体引用
        **kwargs: 其他初始化参数

    Returns:
        管理器实例，失败返回 None
    """
    # 检查是否已有实例
    instance = manager_registry.get_instance(manager_id)
    if instance is not None:
        return instance

    # 获取类并创建实例
    cls = manager_registry.get_class(manager_id)
    if cls is None:
        logger.error("[PluginRegistry] 错误：未找到管理器 '%s'", manager_id)
        return None

    try:
        instance = cls.create(entity, **kwargs)
        manager_registry.set_instance(manager_id, instance)
        return instance
    except Exception as e:
        logger.error("[PluginRegistry] 错误：初始化管理器 '%s' 失败: %s", manager_id, e)
        return None


def init_all_managers(entity: Any = None, **kwargs) -> Dict[str, BaseManager]:
    """
    初始化所有已注册的管理器

    Args:
        entity: 主实体引用
        **kwargs: 其他初始化参数

    Returns:
        管理器实例字典 {manager_id: instance}
    """
    instances = {}
    for manager_id in manager_registry.get_all_ids():
        instance = init_manager(manager_id, entity, **kwargs)
        if instance is not None:
            instances[manager_id] = instance
    return instances


def cleanup_all_managers() -> None:
    """清理所有管理器"""
    for manager_id in manager_registry.get_all_ids():
        instance = manager_registry.get_instance(manager_id)
        if instance is not None:
            try:
                instance.cleanup()
            except Exception as e:
                logger.warning("[PluginRegistry] 警告：清理管理器 '%s' 失败: %s", manager_id, e)
    manager_registry._instances.clear()


def get_manager(manager_id: str) -> Optional[BaseManager]:
    """获取已初始化的管理器实例"""
    return manager_registry.get_instance(manager_id)


# ======================================================================
# 粒子脚本便捷函数
# ======================================================================

def get_particle_class(particle_id: str) -> Optional[Type]:
    """获取粒子脚本类"""
    return particle_registry.get_class(particle_id)


def get_all_particle_ids() -> List[str]:
    """获取所有已注册的粒子ID"""
    return particle_registry.get_all_ids()


def get_effect_class(effect_id: str) -> Optional[Type]:
    """获取特效脚本类"""
    return effect_registry.get_class(effect_id)


def get_all_effect_ids() -> List[str]:
    """获取所有已注册的特效ID"""
    return effect_registry.get_all_ids()
