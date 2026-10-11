"""Workbench 主题色的后端中立事实源（token -> 属性对象）。

`lib/script/workbench/theme.py` 的 `get_workbench_colors()` 返回一个 `WorkbenchColors`
数据类实例，但档位 D 宿主不得 import 产品包。本模块把同一份 token 包成同形属性对象，
供宿主（`workbench_chrome`、`ai_settings_about` 等）取色，取值逐字段与产品侧同源。
"""

from __future__ import annotations

from types import SimpleNamespace

from lib.core.render.visuals.workbench_tokens import get_workbench_tokens


def get_workbench_colors(mode: str | None = None) -> SimpleNamespace:
    """工作台主题色（`canvas` / `surface_raised` / `cyan` / `text_dim` …）。"""
    return SimpleNamespace(**get_workbench_tokens(mode))


__all__ = ["get_workbench_colors"]
