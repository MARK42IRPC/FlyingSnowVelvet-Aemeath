"""Qt 渲染后端。

分两档：`drawing/`（命令到 QPainter 的执行，只允许路由层与后端窗口回调引用）与
`runtime/`（窗口、输入、调度、字体、屏幕、托盘、播放器等平台能力）。

本包故意不做 `from .runtime import *` 这类重新导出：档位规则的断言要能按
子包前缀直接判定，聚合导出会让「业务层不得 import drawing」失效。
装配入口是 `lib/core/render/router.py`。
"""
