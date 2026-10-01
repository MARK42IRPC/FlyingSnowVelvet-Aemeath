"""Command-to-QPainter execution.

只在 `lib/core/render/router.py` 的装配路径与后端自带窗口的绘制回调中被引用；
业务层与 `lib/script/ui` 不得导入本子包（见 `doc/render层边界契约.md` 第 3 节档位 A）。
"""
