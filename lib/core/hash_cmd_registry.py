"""# 命令注册中心 - 集中管理所有 # 前缀命令的元信息（用于提示框显示和补全）

各命令宿主模块在初始化时调用 get_hash_cmd_registry().register()，
提示框在用户输入时调用 filter() 进行实时过滤。
"""

from __future__ import annotations

from typing import List, Optional, Tuple


class HashCmdRegistry:
    """
    # 命令全局注册中心（单例）。

    保持注册顺序，支持按前缀过滤。
    """

    def __init__(self) -> None:
        # 保持注册顺序：name -> (usage, description)
        self._cmds: dict[str, tuple[str, str]] = {}

    def register(self, name: str, usage: str = '', description: str = '') -> None:
        """
        注册一条 # 命令。

        Args:
            name:        命令名（不含 #），如 "雪豹"
            usage:       用法简述，如 "[数量]"
            description: 简短说明，如 "在屏幕底部生成雪豹"
        """
        self._cmds[name] = (usage, description)

    def unregister(self, name: str) -> None:
        """移除一条 # 命令；不存在时静默忽略。"""
        self._cmds.pop(str(name), None)

    def get_all(self) -> List[Tuple[str, str, str]]:
        """返回全部命令列表：[(name, usage, description), ...]"""
        return [(name, usage, desc) for name, (usage, desc) in self._cmds.items()]

    def filter(self, query: str) -> List[Tuple[str, str, str]]:
        """
        按前缀匹配过滤命令。

        Args:
            query: 用户已输入的关键字（不含 #），空字符串返回全部

        Returns:
            匹配的命令列表：[(name, usage, description), ...]
        """
        q = query.strip()
        if not q:
            return self.get_all()
        return [
            (name, usage, desc)
            for name, usage, desc in self.get_all()
            if name.startswith(q)
        ]


# ── 全局单例 ──────────────────────────────────────────────────────────

_registry: Optional[HashCmdRegistry] = None


def get_hash_cmd_registry() -> HashCmdRegistry:
    """获取全局 # 命令注册中心（懒初始化单例）。"""
    global _registry
    if _registry is None:
        _registry = HashCmdRegistry()
    return _registry
