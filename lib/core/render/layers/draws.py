"""绘制层的层内 z 槽：一处画布内部的先后次序。

`DrawBatch` 的 `z` 原本由各 presenter 手写裸整数，同一个面板里 `z=1..7`
的含义只能靠上下文还原，层内次序也没有单一事实源。这里把这条阶梯登记成
具名常量：数值越大越靠上，presenter 只引用名字。

- 层与层之间的先后由 `spec.Layer` 决定；本模块只管同一 layer 内部。
- 数值刻意与迁移前的字面量逐项一致；改动会同时改变像素与
  `tests/test_unified_draw_order.py` 的排序基线，属于契约改动。

各构造使用阶梯的前若干档，含义随构造而定（例如面板的第 4 档是文本，
气泡的第 3 档才是文本），但「第 n 档」的先后恒定。
"""

#: 底衬 / 最底层。
BASE = 0
#: 外框 / 描边。
FRAME = 1
#: 内衬 / 内容底板。
INNER = 2
#: 中间面 / 正文底色。
MIDDLE = 3
#: 主内容 / 文本。
CONTENT = 4
#: 覆盖层第一档（面板之上的动作按钮等）。
OVERLAY = 5
#: 覆盖层第二档。
OVERLAY_SECOND = 6
#: 覆盖层第三档。
OVERLAY_THIRD = 7

__all__ = [
    'BASE',
    'FRAME',
    'INNER',
    'MIDDLE',
    'CONTENT',
    'OVERLAY',
    'OVERLAY_SECOND',
    'OVERLAY_THIRD',
]
