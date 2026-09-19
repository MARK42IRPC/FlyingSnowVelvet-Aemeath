"""锁定 `subprocess(shell=True)` 的唯一入口集合与来源。

命令框（`/` 前缀）的设计目标就是让用户执行任意本地命令，因此这两处 `shell=True`
是有意保留的：改为参数数组会改变引号与转义语义。这个用例把「只有这两处、且都由
用户键盘输入触达」写成回归锁，避免以后有人悄悄把网络/AI 输出接进命令框。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("lib", "config", "scripts")

# shell=True 的合法落点：文件 -> 承载的函数
_ALLOWED_SHELL_CALLS = {
    "lib/core/cmd_center.py": "_run_command",
    "lib/script/ui/cmd_window.py": "_stream_command",
}

# INPUT_COMMAND / UI_OPEN_CMD_WINDOW_WITH_COMMAND 的发布方必须都在这个白名单内，
# 它们全部由用户在命令框（Qt 或 DX 命令面板）敲键盘触发。
_ALLOWED_COMMAND_PUBLISHERS = {
    "lib/core/dx_bridge/application_ui.py",
    "lib/script/ui/command_dialog.py",
}


def _iter_python_files():
    for folder in _SCAN_DIRS:
        yield from sorted((_ROOT / folder).rglob("*.py"))


def _shell_true_owners(tree: ast.AST):
    """返回 (函数名, 行号) 列表，表示哪些函数里出现了 shell=True。"""
    owners = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            for kw in child.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    owners.append((node.name, child.lineno))
    return owners


_COMMAND_EVENT_ATTRS = {"INPUT_COMMAND", "UI_OPEN_CMD_WINDOW_WITH_COMMAND"}


def _mentions_command_event(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Attribute) and inner.attr in _COMMAND_EVENT_ATTRS
        for inner in ast.walk(node)
    )


def _command_publishers(tree: ast.AST) -> bool:
    """判断模块是否发布了命令框事件。

    兼容两种写法：`publish(Event(EventType.INPUT_COMMAND, ...))` 与先赋值给局部变量
    再 `publish(event)`（command_dialog 用的是后者）。
    """
    bound_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _mentions_command_event(node.value):
            bound_names.update(t.id for t in node.targets if isinstance(t, ast.Name))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "publish":
            continue
        for arg in node.args:
            if _mentions_command_event(arg):
                return True
            if isinstance(arg, ast.Name) and arg.id in bound_names:
                return True
    return False


class ShellExecutionAllowlistTests(unittest.TestCase):
    def test_only_allowlisted_functions_use_shell_execution(self):
        found = {}
        for path in _iter_python_files():
            source = path.read_text(encoding="utf-8")
            if "shell=True" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
            rel = path.relative_to(_ROOT).as_posix()
            for name, lineno in _shell_true_owners(tree):
                found.setdefault(rel, []).append((name, lineno))

        self.assertEqual(sorted(found), sorted(_ALLOWED_SHELL_CALLS))
        for rel, expected_owner in _ALLOWED_SHELL_CALLS.items():
            names = {name for name, _ in found[rel]}
            self.assertEqual(names, {expected_owner}, f"{rel} 的 shell=True 位置变了")

    def test_command_event_publishers_are_keyboard_entry_points_only(self):
        publishers = set()
        for path in _iter_python_files():
            source = path.read_text(encoding="utf-8")
            if "INPUT_COMMAND" not in source and "UI_OPEN_CMD_WINDOW_WITH_COMMAND" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
            if _command_publishers(tree):
                publishers.add(path.relative_to(_ROOT).as_posix())

        self.assertEqual(publishers, _ALLOWED_COMMAND_PUBLISHERS)


if __name__ == "__main__":
    unittest.main()
