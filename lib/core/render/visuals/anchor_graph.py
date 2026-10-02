"""声明式锚点图：一次解算一批窗口的落位。

右键按钮族此前把同一组几何关系写成两条互不相干的事实：

- ``resolve_command_action_panel_layout()`` 用一排手抄的绝对偏移（0/80/120/160、-34/-66/-98）
  描述三行按钮的最终矩形；
- 八个 Qt 按钮各自订阅锚点事件、各自 ``place_at_point()``，用「贴到上游按钮的哪个锚点」
  的链路描述同一组关系。

两条事实必须永远一致，却没有任何东西保证它们一致。本模块把「链路」提升为唯一声明：
``AnchorNode`` 描述「本节点贴到哪个上游节点的哪个锚点、自身用哪个锚点、偏移多少、尺寸
多大」，``AnchorGraph.resolve()`` 按声明顺序做一次拓扑解算，返回每个节点的屏幕矩形。

属于《render 层边界契约》档位 2 的共享事实源（与 ``layout.py`` 同级）：只依赖后端中立
几何，不 import PyQt5、任一 ``backends/*``、``lib.script`` 或 ``config.config_ui``；偏移量
是逻辑像素，调用方按需传入 ``scale``。
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.core.render.visuals.layout import PlacementSpec
from lib.core.render.visuals.screen import clamp_rect_position
from lib.core.render.visuals.types import Rect, coerce_rect

#: 命令框在锚点图里的节点名；族内其余节点的上游最终都追溯到它。
COMMAND_ACTION_ROOT = "command_dialog"

#: 逻辑节点名 -> Qt 控件 ``_ui_id``。两者是同一份事实的两个落点，由守卫对齐。
COMMAND_ACTION_UI_IDS = {
    "clickthrough": "clickthrough_button",
    "scale_up": "scale_up_button",
    "scale_down": "scale_down_button",
    "close": "close_button",
    "launch_wuwa": "launch_wuwa_button",
    "chat_mode": "chat_mode_button",
    "interaction_mode": "interaction_mode_button",
    "more_functions": "more_functions_button",
}

_UI_ID_TO_NODE = {ui_id: node_id for node_id, ui_id in COMMAND_ACTION_UI_IDS.items()}


@dataclass(frozen=True, slots=True)
class AnchorNode:
    """锚点图里的一个节点：贴到 ``target_id`` 的 ``target_anchor_id`` 上。"""

    node_id: str
    target_id: str
    target_anchor_id: str = "top_left"
    self_anchor_id: str = "bottom_left"
    size: tuple[float, float] = (0.0, 0.0)
    offset_x: float = 0.0
    offset_y: float = 0.0

    def placement_spec(self, *, scale: float = 1.0) -> PlacementSpec:
        return PlacementSpec(
            target_anchor_id=self.target_anchor_id,
            self_anchor_id=self.self_anchor_id,
            offset_x=self.offset_x * float(scale),
            offset_y=self.offset_y * float(scale),
        )


@dataclass(frozen=True, slots=True)
class AnchorGraph:
    """一组按上游顺序排列的锚点节点。

    ``nodes`` 必须拓扑有序：每个节点的 ``target_id`` 要么是 ``root_id``，要么是它前面
    已经出现过的节点。``resolve()`` 不会为了乱序声明做二次排序——声明顺序就是解算顺序，
    乱序会让下游节点拿不到上游矩形并静默跳过，属于声明错误。
    """

    nodes: tuple[AnchorNode, ...]
    root_id: str = COMMAND_ACTION_ROOT

    def node(self, node_id: str) -> AnchorNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def resolve(
        self,
        root_rect: Rect,
        *,
        sizes: dict[str, tuple[float, float]] | None = None,
        scale: float = 1.0,
        screen: Rect | None = None,
    ) -> dict[str, Rect]:
        """解算整张图，返回 ``node_id -> 屏幕矩形``（不含根节点）。

        ``sizes`` 允许调用方用真实窗口尺寸覆盖声明里的逻辑尺寸（Qt 控件宽高已随绘制缩放），
        覆盖只影响该节点自身矩形与它的下游链路，不改变锚点语义；未覆盖的节点按 ``scale``
        放大声明尺寸。偏移量同样按 ``scale`` 放大。给 ``screen`` 时逐个节点夹取回屏幕。
        """
        root = coerce_rect(root_rect)
        if root is None:
            raise TypeError(f"anchor graph requires a rect-like root, got {root_rect!r}")
        factor = max(0.0, float(scale))
        override = sizes or {}
        resolved: dict[str, Rect] = {self.root_id: root}
        for node in self.nodes:
            target = resolved.get(node.target_id)
            if target is None:
                continue
            size = override.get(node.node_id)
            if size is None:
                size = (node.size[0] * factor, node.size[1] * factor)
            point = node.placement_spec(scale=factor).resolve_point(size, target)
            x, y = point.x, point.y
            if screen is not None:
                x, y, _ = clamp_rect_position(
                    int(round(x)), int(round(y)),
                    max(1, int(round(size[0]))), max(1, int(round(size[1]))),
                    screen,
                )
            resolved[node.node_id] = Rect(x, y, size[0], size[1])
        return {node.node_id: resolved[node.node_id] for node in self.nodes if node.node_id in resolved}


#: 右键按钮族的唯一链路声明（逻辑像素，命令框左上角为原点）。
#: 与 ``COMMAND_ACTION_BUTTONS`` 的名称和宽高同源，由 `tests/test_render_layout_algorithms.py`
#: 与 `tests/test_right_click_ui_layer.py` 两处守卫交叉钉死。
COMMAND_ACTION_GRAPH = AnchorGraph(nodes=(
    AnchorNode("clickthrough", COMMAND_ACTION_ROOT,
               "top_left", "bottom_left", (80, 32), 0, -2),
    AnchorNode("scale_up", "clickthrough",
               "right", "left", (40, 32)),
    AnchorNode("scale_down", "scale_up",
               "right", "left", (40, 32)),
    AnchorNode("close", COMMAND_ACTION_ROOT,
               "top_right", "bottom_right", (80, 32), 0, -2),
    AnchorNode("launch_wuwa", "clickthrough",
               "top_left", "bottom_left", (80, 32)),
    AnchorNode("chat_mode", "launch_wuwa",
               "top_right", "top_left", (80, 32)),
    AnchorNode("interaction_mode", "chat_mode",
               "right", "left", (80, 32)),
    AnchorNode("more_functions", "launch_wuwa",
               "top_left", "bottom_left", (80, 32)),
))


def command_action_node(ui_id: str) -> AnchorNode | None:
    """按 Qt 控件 ``_ui_id`` 取该控件在族里的链路声明。"""
    node_id = _UI_ID_TO_NODE.get(str(ui_id or ""))
    return None if node_id is None else COMMAND_ACTION_GRAPH.node(node_id)


__all__ = [
    "AnchorGraph",
    "AnchorNode",
    "COMMAND_ACTION_GRAPH",
    "COMMAND_ACTION_ROOT",
    "COMMAND_ACTION_UI_IDS",
    "command_action_node",
]
