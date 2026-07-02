"""特效脚本管理器 - 使用动态注册机制。"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from lib.core.plugin_registry import discover_effects, effect_registry
from lib.script.effects.base_effect import BaseEffectScript


class EffectScriptManager:
    """管理所有特效脚本。"""

    def __init__(self):
        self._scripts: Dict[str, Type[BaseEffectScript]] = {}
        self._instances: Dict[str, BaseEffectScript] = {}
        self._discover_and_register()

    def _discover_and_register(self):
        discover_effects()
        for effect_id, script_class in effect_registry.get_all_classes().items():
            self._scripts[effect_id] = script_class

    def register_script(self, script_class: Type[BaseEffectScript]):
        effect_id = script_class.EFFECT_ID
        if effect_id:
            self._scripts[effect_id] = script_class
            effect_registry.register(effect_id, script_class)

    def get_script(self, effect_id: str) -> Optional[BaseEffectScript]:
        if effect_id in self._instances:
            return self._instances[effect_id]

        script_class = self._scripts.get(effect_id)
        if script_class is None:
            return None

        instance = script_class()
        self._instances[effect_id] = instance
        return instance

    def get_all_effect_ids(self) -> List[str]:
        return list(self._scripts.keys())

    def has_effect(self, effect_id: str) -> bool:
        return effect_id in self._scripts

    def reload(self):
        self._scripts.clear()
        self._instances.clear()
        self._discover_and_register()


_effect_script_manager = None


def get_effect_script_manager() -> EffectScriptManager:
    global _effect_script_manager
    if _effect_script_manager is None:
        _effect_script_manager = EffectScriptManager()
    return _effect_script_manager


def cleanup_effect_script_manager():
    global _effect_script_manager
    if _effect_script_manager is not None:
        _effect_script_manager._scripts.clear()
        _effect_script_manager._instances.clear()
        _effect_script_manager = None
