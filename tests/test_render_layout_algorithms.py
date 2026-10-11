"""渲染层排布解算的确定性守卫（《render 层边界契约》档位 0）。

`lib/core/render/visuals/` 已经是命令框、气泡、右键按钮族与二维码面板排布的唯一
事实源：`resolve_*_layout` / `resolve_*_geometry` 给出纯几何结果，Qt 与 DX 各自执行
同一份结果。本模块把这份契约钉在数值上，理由是：

- 没有这层守卫，"把 Qt 的逐控件锚点事件收敛到 render 层"只能靠肉眼验收——解算结果
  变化不会让任何断言失败；
- `resolve_command_action_panel_layout` 是一份手抄自 Qt 按钮族的布局，按钮名字与宽高
  （`COMMAND_ACTION_BUTTONS`）和 `lib/script/ui/*_button.py` 的 `WIDTH`/`HEIGHT` 是
  两份事实源，这里把两者对齐：任何一边单方面改动都会失败；
- 数值是迁移前的 Qt 基准逐项算出来的，不是"当前实现恰好如此"。

断言只依赖 `config` 里的尺寸（命令框宽高、偏移）与纯几何函数，不构造 Qt。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from config.config import COMMAND_DIALOG, UI
from lib.core.render.visuals.application_visuals import (
    COMMAND_ACTION_BUTTONS,
    qr_panel_size,
    resolve_bubble_geometry,
    resolve_command_action_panel_layout,
    resolve_qr_panel_layout,
)
from lib.core.render.visuals.anchor_graph import (
    COMMAND_ACTION_GRAPH,
    COMMAND_ACTION_UI_IDS,
    AnchorGraph,
    AnchorNode,
    command_action_node,
)
from lib.core.render.visuals.controls import RectActionButtonControl
from lib.core.render.visuals.layout import AnchorPlacement, PlacementSpec, resolve_placement
from lib.core.render.visuals.types import Point, Rect, Size
from lib.core.render.visuals.visuals import resolve_command_panel_geometry

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 按钮 ID -> 定义 `WIDTH`/`HEIGHT` 的 Qt 控件文件。
_QT_BUTTON_FILES = {
    "clickthrough": "clickthrough_button.py",
    "scale_up": "scale_button.py",
    "scale_down": "scale_button.py",
    "close": "close_button.py",
    "launch_wuwa": "launch_wuwa_button.py",
    "chat_mode": "chat_mode_button.py",
    "interaction_mode": "interaction_mode_button.py",
    "more_functions": "more_functions_button.py",
}


def _class_level_sizes(path: Path) -> list[tuple[int, int]]:
    """按类定义顺序取出每个类里 `WIDTH` / `HEIGHT` 的第一个常量字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    sizes: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        width = height = None
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                continue
            name = statement.targets[0].id
            value = statement.value
            if not isinstance(value, ast.Call) or not value.args:
                continue
            first = value.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, int):
                continue
            if name == "WIDTH":
                width = first.value
            elif name == "HEIGHT":
                height = first.value
        if width is not None and height is not None:
            sizes.append((width, height))
    return sizes


def _string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class CommandActionPanelLayoutTests(unittest.TestCase):
    """右键按钮族三行布局：名称、逐按钮矩形、整体尺寸。"""

    def test_layout_matches_the_frozen_qt_baseline(self):
        layout = resolve_command_action_panel_layout(Rect(100, 200, 240, 36))

        self.assertEqual(
            tuple(name for name, _rect in layout.rects),
            (
                "clickthrough", "scale_up", "scale_down", "close",
                "launch_wuwa", "chat_mode", "interaction_mode", "more_functions",
            ),
        )
        self.assertEqual(layout.size, Size(240, 96))
        self.assertEqual(
            tuple((name, rect) for name, rect in layout.rects),
            (
                ("clickthrough", Rect(100, 166, 80, 32)),
                ("scale_up", Rect(180, 166, 40, 32)),
                ("scale_down", Rect(220, 166, 40, 32)),
                ("close", Rect(260, 166, 80, 32)),
                ("launch_wuwa", Rect(100, 134, 80, 32)),
                ("chat_mode", Rect(180, 134, 80, 32)),
                ("interaction_mode", Rect(260, 134, 80, 32)),
                ("more_functions", Rect(100, 102, 80, 32)),
            ),
        )

    def test_layout_is_anchored_to_the_command_box_corner(self):
        origin = dict(resolve_command_action_panel_layout(Rect(0, 0, 240, 36)).rects)
        shifted = dict(resolve_command_action_panel_layout(Rect(100, 200, 240, 36)).rects)

        self.assertEqual(origin["clickthrough"], Rect(0, -34, 80, 32))
        self.assertEqual(origin["more_functions"], Rect(0, -98, 80, 32))
        self.assertEqual(origin["close"].right, origin["clickthrough"].x + 240)
        for name, rect in origin.items():
            self.assertEqual(shifted[name], Rect(rect.x + 100, rect.y + 200, rect.width, rect.height))

    def test_action_button_names_and_sizes_match_the_qt_controls(self):
        control_sizes = {name: (width, height) for name, _text, width, height in COMMAND_ACTION_BUTTONS}
        sizes_by_file: dict[str, list[tuple[int, int]]] = {}
        for path in {value for value in _QT_BUTTON_FILES.values()}:
            sizes_by_file[path] = _class_level_sizes(_REPO_ROOT / "lib" / "script" / "ui" / path)

        expected_order = [name for name, _rect in resolve_command_action_panel_layout(Rect()).rects]
        self.assertEqual(expected_order, [name for name, _text, _w, _h in COMMAND_ACTION_BUTTONS])

        for name, (width, height) in control_sizes.items():
            with self.subTest(button=name):
                qt_sizes = sizes_by_file[_QT_BUTTON_FILES[name]]
                if name == "scale_down":
                    # `scale_button.py` 里放大/缩小是两个类，第二个才是缩小按钮。
                    self.assertEqual(qt_sizes[1], (width, height))
                else:
                    self.assertEqual(qt_sizes[0], (width, height))

    def test_action_button_labels_match_the_qt_controls(self):
        for name, text, _width, _height in COMMAND_ACTION_BUTTONS:
            if name in {"chat_mode", "interaction_mode"}:
                continue  # 这两个按钮的文字随模式变化，paint 时再解析。
            with self.subTest(button=name):
                literals = _string_literals(
                    _REPO_ROOT / "lib" / "script" / "ui" / _QT_BUTTON_FILES[name]
                )
                self.assertIn(text, literals)


class BubbleGeometryTests(unittest.TestCase):
    """气泡：下中锚点对齐主宠上锚点，偏移后再夹取回屏幕。"""

    def test_offsets_are_applied_before_clamping(self):
        screen = Rect(0, 0, 800, 600)
        self.assertEqual(
            resolve_bubble_geometry(Point(400, 300), Size(100, 40), screen, offset_x=5, offset_y=-7),
            Rect(355, 253, 100, 40),
        )

    def test_clamps_into_each_screen_corner(self):
        screen = Rect(0, 0, 800, 600)
        self.assertEqual(resolve_bubble_geometry(Point(0, 0), Size(100, 40), screen), Rect(0, 0, 100, 40))
        self.assertEqual(
            resolve_bubble_geometry(Point(800, 600), Size(100, 40), screen),
            Rect(700, 560, 100, 40),
        )

    def test_clamps_against_a_screen_with_negative_origin(self):
        screen = Rect(-100, -100, 800, 600)
        self.assertEqual(
            resolve_bubble_geometry(Point(750, 590), Size(100, 40), screen),
            Rect(600, 460, 100, 40),
        )


class CommandPanelGeometryTests(unittest.TestCase):
    """命令框：优先贴主宠右侧，放不下时翻到左侧，再夹取回屏幕。"""

    def setUp(self):
        self.screen = Rect(0, 0, 800, 600)
        self.size = (int(UI["cmd_window_width"]), int(UI["cmd_window_height"]))
        self.offset_x = float(COMMAND_DIALOG["offset_x"])

    def test_flips_left_when_the_right_side_does_not_fit(self):
        right = resolve_command_panel_geometry(Rect(100, 200, 150, 150), self.size, self.screen)
        self.assertEqual(right, Rect(250 + self.offset_x, 257, self.size[0], self.size[1]))

        left = resolve_command_panel_geometry(Rect(700, 200, 100, 150), self.size, self.screen)
        self.assertEqual(left, Rect(700 - self.size[0] - self.offset_x, 257, self.size[0], self.size[1]))

    def test_clamps_when_neither_side_fits(self):
        cramped = Rect(795, 10, 10, 150)
        self.assertEqual(
            resolve_command_panel_geometry(cramped, self.size, self.screen, offset_x=self.offset_x),
            Rect(795 - self.size[0] - self.offset_x, 67, self.size[0], self.size[1]),
        )

    def test_clamps_vertically_to_the_screen(self):
        self.assertEqual(
            resolve_command_panel_geometry(Rect(100, -120, 150, 150), self.size, self.screen).y,
            0,
        )
        self.assertEqual(
            resolve_command_panel_geometry(Rect(100, 590, 150, 150), self.size, self.screen).y,
            600 - self.size[1],
        )


class QrPanelLayoutTests(unittest.TestCase):
    """二维码面板：默认尺寸与内部五块矩形。"""

    def test_default_layout_matches_the_qt_baseline(self):
        self.assertEqual(qr_panel_size(), (320, 430))
        layout = resolve_qr_panel_layout()

        self.assertEqual(layout.size, Size(320, 430))
        self.assertEqual(layout.inner_rect, Rect(4, 4, 312, 422))
        self.assertEqual(layout.title_rect, Rect(4, 4, 312, 36))
        self.assertEqual(layout.qr_rect, Rect(40.0, 50, 240, 240))
        self.assertEqual(layout.status_rect, Rect(14, 300, 292, 76))
        self.assertEqual(layout.action_rect, Rect(94.0, 384, 132, 30))

    def test_larger_panel_widens_the_centred_blocks(self):
        layout = resolve_qr_panel_layout((420, 560))

        self.assertEqual(layout.size, Size(420, 560))
        self.assertEqual(layout.inner_rect, Rect(4, 4, 412, 552))
        self.assertEqual(layout.qr_rect.x, 90.0)
        self.assertEqual(layout.action_rect, Rect(144.0, 514, 132, 30))
        self.assertEqual(layout.status_rect, Rect(14, 300, 392, 80))

    def test_layout_keeps_the_qr_square_and_action_button_inside(self):
        for size in ((320, 430), (420, 560), (600, 800)):
            with self.subTest(size=size):
                layout = resolve_qr_panel_layout(size)
                self.assertEqual(layout.qr_rect.width, layout.qr_rect.height)
                self.assertLessEqual(layout.action_rect.bottom, layout.size.height)
                self.assertGreaterEqual(layout.action_rect.x, layout.inner_rect.x)


class PlacementSpecTests(unittest.TestCase):
    """档位 1：叶控件窗口落位的共享解算。"""

    def test_defaults_match_the_action_button_family(self):
        spec = PlacementSpec()
        self.assertEqual(spec.target_anchor_id, "top_left")
        self.assertEqual(spec.self_anchor_id, "bottom_left")
        self.assertEqual(spec.offset_x, 0.0)
        self.assertEqual(spec.offset_y, 0.0)

    def test_resolve_placement_matches_hand_written_anchor_arithmetic(self):
        screen = Rect(0, 0, 800, 600)
        target = Rect(100, 200, 240, 36)

        self.assertEqual(
            PlacementSpec().resolve_placement((80, 32), target, screen),
            AnchorPlacement(100, 168, screen),
        )
        self.assertEqual(
            PlacementSpec(target_anchor_id="top_right", self_anchor_id="bottom_right").resolve_placement(
                (80, 32), target, screen
            ),
            AnchorPlacement(260, 168, screen),
        )
        self.assertEqual(
            PlacementSpec(target_anchor_id="bottom", self_anchor_id="top").resolve_placement(
                (80, 32), target, screen
            ),
            AnchorPlacement(180, 236, screen),
        )
        self.assertEqual(
            PlacementSpec(offset_x=5, offset_y=-7).resolve_placement((80, 32), target, screen),
            AnchorPlacement(105, 161, screen),
        )

    def test_resolve_point_skips_clamping(self):
        point = PlacementSpec().resolve_point((80, 32), Rect(0, 0, 240, 36))
        self.assertEqual(point, Point(0.0, -32.0))

    def test_resolve_from_point_treats_the_anchor_as_a_point(self):
        screen = Rect(0, 0, 800, 600)
        self.assertEqual(
            PlacementSpec().resolve_from_point((80, 32), Point(100, 200), screen),
            AnchorPlacement(100, 168, screen),
        )

    def test_resolve_centered_centres_then_clamps(self):
        self.assertEqual(
            PlacementSpec().resolve_centered((80, 32), Rect(0, 0, 800, 600)),
            AnchorPlacement(360, 284, Rect(0, 0, 800, 600)),
        )
        # 窗口比屏幕还大时夹到左上角，而不是产生负坐标。
        self.assertEqual(
            PlacementSpec().resolve_centered((900, 700), Rect(0, 0, 800, 600)),
            AnchorPlacement(0, 0, Rect(0, 0, 800, 600)),
        )

    def test_function_form_matches_the_spec(self):
        screen = Rect(0, 0, 800, 600)
        target = Rect(100, 200, 240, 36)
        self.assertEqual(
            resolve_placement((80, 32), target, screen, offset_y=-2),
            PlacementSpec(offset_y=-2).resolve_placement((80, 32), target, screen),
        )

    def test_size_and_target_accept_duck_typed_values(self):
        class _Size:
            width = 80
            height = 32

        class _Rect:
            x = 100
            y = 200
            width = 240
            height = 36

        self.assertEqual(
            PlacementSpec().resolve_placement(_Size(), _Rect(), Rect(0, 0, 800, 600)),
            AnchorPlacement(100, 168, Rect(0, 0, 800, 600)),
        )

    def test_invalid_inputs_raise_instead_of_silently_placing(self):
        with self.assertRaises(TypeError):
            PlacementSpec().resolve_placement("nonsense", Rect(0, 0, 10, 10), Rect(0, 0, 800, 600))
        with self.assertRaises(TypeError):
            PlacementSpec().resolve_placement((10, 10), None, Rect(0, 0, 800, 600))


class AnchorGraphTests(unittest.TestCase):
    """档位 2：声明式锚点图是按钮族唯一链路，且与共享布局逐格一致。"""

    COMMAND = Rect(100, 200, 240, 36)
    SCREEN = Rect(0, 0, 1920, 1080)

    def test_graph_reproduces_the_shared_action_panel_layout(self):
        resolved = COMMAND_ACTION_GRAPH.resolve(self.COMMAND)
        shared = dict(resolve_command_action_panel_layout(self.COMMAND).rects)

        self.assertEqual(set(resolved), set(shared))
        for node_id, want in shared.items():
            got = resolved[node_id]
            self.assertEqual(
                (got.x, got.y, got.width, got.height),
                (want.x, want.y, want.width, want.height),
                msg=node_id,
            )

    def test_root_is_excluded_from_the_result(self):
        resolved = COMMAND_ACTION_GRAPH.resolve(self.COMMAND)
        self.assertNotIn(COMMAND_ACTION_GRAPH.root_id, resolved)

    def test_scale_grows_sizes_offsets_and_spacing(self):
        resolved = COMMAND_ACTION_GRAPH.resolve(self.COMMAND, scale=2.0)
        self.assertEqual(
            (resolved["clickthrough"].x, resolved["clickthrough"].y),
            (100.0, 200.0 - 68.0),  # 偏移 -2 与行高 34 都翻倍
        )
        self.assertEqual(
            (resolved["clickthrough"].width, resolved["clickthrough"].height),
            (160.0, 64.0),
        )
        self.assertEqual(resolved["scale_up"].x, 100.0 + 160.0)

    def test_size_override_propagates_downstream(self):
        resolved = COMMAND_ACTION_GRAPH.resolve(
            self.COMMAND, sizes={"clickthrough": (100, 40)}
        )
        self.assertEqual(resolved["scale_up"].x, self.COMMAND.x + 100)
        # launch_wuwa 贴 clickthrough 顶部（自身高 32），上游加高只抬高它的起点。
        self.assertEqual(resolved["launch_wuwa"].y, self.COMMAND.y - 40 - 2 - 32)

    def test_screen_is_clamped_when_requested(self):
        screen = Rect(0, 0, 200, 120)
        resolved = COMMAND_ACTION_GRAPH.resolve(self.COMMAND, screen=screen)
        for rect in resolved.values():
            self.assertGreaterEqual(rect.x, screen.x)
            self.assertGreaterEqual(rect.y, screen.y)

    def test_out_of_order_nodes_are_skipped_not_guessed(self):
        graph = AnchorGraph(nodes=(
            AnchorNode("b", "a", "right", "left", (10, 10)),
            AnchorNode("a", "root", "top_left", "top_left", (10, 10)),
        ), root_id="root")
        resolved = graph.resolve(Rect(0, 0, 10, 10))
        self.assertNotIn("b", resolved)  # 上游还没出现，静默跳过
        self.assertEqual((resolved["a"].x, resolved["a"].y), (0.0, 0.0))

    def test_root_rect_must_be_rect_like(self):
        with self.assertRaises(TypeError):
            COMMAND_ACTION_GRAPH.resolve(None)

    def test_toolkit_ui_ids_and_node_names_are_declared_together(self):
        graph_ids = {node.node_id for node in COMMAND_ACTION_GRAPH.nodes}
        self.assertEqual(graph_ids, set(COMMAND_ACTION_UI_IDS))
        for node_id, ui_id in COMMAND_ACTION_UI_IDS.items():
            node = command_action_node(ui_id)
            self.assertIsNotNone(node, ui_id)
            self.assertEqual(node.node_id, node_id)
        self.assertIsNone(command_action_node("no_such_ui"))

    def test_every_node_ui_id_is_the_literal_in_its_qt_button_file(self):
        for node_id, ui_id in COMMAND_ACTION_UI_IDS.items():
            with self.subTest(node=node_id):
                source = (
                    _REPO_ROOT / "lib" / "script" / "ui" / _QT_BUTTON_FILES[node_id]
                ).read_text(encoding="utf-8-sig")
                self.assertIn(f"_ui_id = '{ui_id}'", source)


class ControlPlacementDelegationTests(unittest.TestCase):
    """档位 1：已收敛的控件与按钮族必须走共享解算。"""

    def test_rect_action_button_control_delegates_to_the_shared_spec(self):
        button = RectActionButtonControl(width=80, height=32)
        target = Rect(100, 200, 240, 36)
        screen = Rect(0, 0, 800, 600)

        self.assertEqual(
            button.placement(target, screen),
            PlacementSpec().resolve_placement(button.logical_size(), target, screen),
        )
        self.assertEqual(
            button.anchored_top_left(target),
            PlacementSpec().resolve_point(button.logical_size(), target),
        )
        self.assertIsInstance(button.placement_spec(), PlacementSpec)

    def test_media_progress_control_delegates_to_the_shared_spec(self):
        from lib.core.render.visuals.application_visuals import create_portable_bubble_text_metrics
        from lib.core.render.visuals.controls import MediaProgressControl

        progress = MediaProgressControl(
            create_portable_bubble_text_metrics(), width=240, height=20, gap=2, paint_layer=6,
        )
        playlist = Rect(400, 600, 300, 200)
        screen = Rect(0, 0, 1920, 1080)

        self.assertEqual(
            progress.placement(playlist, screen),
            PlacementSpec(offset_y=-2.0).resolve_placement((240, 20), playlist, screen),
        )

    #: 已把窗口落位收进共享解算的控件文件。落位算术（目标锚点、自身锚点、偏移、
    #: 屏幕夹取）现在只有 `visuals/layout.py` 一份；这里逐个登记，任何一边回退都会红。
    MIGRATED_LEAF_CONTROLS = (
        "clickthrough_button.py",
        "close_button.py",
        "restore_button.py",
        "launch_wuwa_button.py",
        "more_functions_button.py",
        "chat_mode_button.py",
        "interaction_mode_button.py",
        "scale_button.py",
        "mic_stt_indicator.py",
        "command_hint_box.py",
        "page_turn_buttons.py",
        "qr_dialog_base.py",
        "speaker_search_dialog.py",
        "speaker_search_result_box.py",
        "speaker_control_buttons.py",
        "playlist_panel.py",
        "voice_package_installer.py",
    )

    def test_no_ui_control_clamps_its_own_window_position(self):
        """`lib/script/ui` 里不得再出现 `clamp_rect_position` 调用。

        该函数只剩 `render_bridge` 自身作为转发层保留；任何控件重新自己夹取屏幕，
        都意味着档位 1 的算术又被抄回业务层。
        """
        offenders = []
        ui_root = _REPO_ROOT / "lib" / "script" / "ui"
        for path in sorted(ui_root.rglob("*.py")):
            if path.name == "render_bridge.py":
                continue
            source = path.read_text(encoding="utf-8-sig")
            if "clamp_rect_position" in source:
                offenders.append(path.relative_to(_REPO_ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_migrated_leaf_controls_use_the_render_bridge_placement_seam(self):
        #: 档位 1 的单窗口解算（``place_at_point`` 等）与档位 2 的整族解算
        #: （``family_placement``）都是允许的 render_bridge 落位入口。
        seam_calls = ("place_at_point(", "centered_placement(", "resolve_placement(", "family_placement(")
        for name in self.MIGRATED_LEAF_CONTROLS:
            with self.subTest(control=name):
                source = (_REPO_ROOT / "lib" / "script" / "ui" / name).read_text(encoding="utf-8-sig")
                self.assertTrue(any(seam in source for seam in seam_calls), name)


class QtActionButtonChainTests(unittest.TestCase):
    """档位 3 的迁移 oracle：Qt 按钮链的锚点语义必须收敛到同一份共享布局。

    `lib/script/ui` 的 8 个右键按钮通过锚点事件彼此串联落位（穿透 → 缩放/启动 →
    聊天/更多 → 交互），而 `resolve_command_action_panel_layout()` 是同一组按钮的
    第二份事实源。这里把那条**事件链的锚点语义**用共享解算独立重放一遍，再断言它与
    共享布局逐格相等：两者一旦分歧，Qt 改为直接消费共享布局就不再是等价重构。

    重放不构造任何 Qt 控件，`target_anchor_id` / `self_anchor_id` / 偏移逐项抄自
    各按钮的 `_target_anchor_id` / `_self_anchor_id` / `_offset_*` 与它们的
    `_update_position()`；改这些值时本测试会先红。
    """

    COMMAND = Rect(100, 200, 240, 36)
    SCREEN = Rect(0, 0, 1920, 1080)

    def _place(self, size, target_rect, *, target_anchor, self_anchor, off_x=0.0, off_y=0.0):
        return PlacementSpec(
            target_anchor_id=target_anchor,
            self_anchor_id=self_anchor,
            offset_x=off_x,
            offset_y=off_y,
        ).resolve_placement(size, target_rect, self.SCREEN)

    def _as_lists(self, layout):
        return {name: [int(r.x), int(r.y), int(r.width), int(r.height)] for name, r in layout.rects}

    def test_the_anchor_event_chain_reproduces_the_shared_panel_layout(self):
        shared = self._as_lists(resolve_command_action_panel_layout(self.COMMAND))

        # 1. 穿透按钮：bottom_left 对齐命令框 top_left（偏移 -2px）。
        clickthrough = self._place(
            (80, 32), self.COMMAND,
            target_anchor="top_left", self_anchor="bottom_left", off_y=-2,
        )
        # 2/3. 放大、缩小：各自 left 贴上游 right（同一行）。
        scale_up = self._place(
            (40, 32), Rect(clickthrough.x, clickthrough.y, 80, 32),
            target_anchor="right", self_anchor="left",
        )
        scale_down = self._place(
            (40, 32), Rect(scale_up.x, scale_up.y, 40, 32),
            target_anchor="right", self_anchor="left",
        )
        # 4. 关闭按钮：bottom_right 对齐命令框 top_right（偏移 -2px）。
        close = self._place(
            (80, 32), self.COMMAND,
            target_anchor="top_right", self_anchor="bottom_right", off_y=-2,
        )
        # 5. 启动鸣潮：bottom_left 对齐穿透按钮 top_left。
        launch = self._place(
            (80, 32), Rect(clickthrough.x, clickthrough.y, 80, 32),
            target_anchor="top_left", self_anchor="bottom_left",
        )
        # 6. 聊天模式：top_left 对齐启动鸣潮 top_right。
        chat = self._place(
            (80, 32), Rect(launch.x, launch.y, 80, 32),
            target_anchor="top_right", self_anchor="top_left",
        )
        # 7. 交互模式：left 对齐聊天模式 right。
        interaction = self._place(
            (80, 32), Rect(chat.x, chat.y, 80, 32),
            target_anchor="right", self_anchor="left",
        )
        # 8. 更多功能：bottom_left 对齐启动鸣潮 top_left。
        more = self._place(
            (80, 32), Rect(launch.x, launch.y, 80, 32),
            target_anchor="top_left", self_anchor="bottom_left",
        )

        replayed = {
            "clickthrough": [clickthrough.x, clickthrough.y, 80, 32],
            "scale_up": [scale_up.x, scale_up.y, 40, 32],
            "scale_down": [scale_down.x, scale_down.y, 40, 32],
            "close": [close.x, close.y, 80, 32],
            "launch_wuwa": [launch.x, launch.y, 80, 32],
            "chat_mode": [chat.x, chat.y, 80, 32],
            "interaction_mode": [interaction.x, interaction.y, 80, 32],
            "more_functions": [more.x, more.y, 80, 32],
        }
        self.assertEqual(replayed, shared)

    def test_the_chain_is_stable_after_an_edge_flip(self):
        """命令框翻到左侧后，按钮链仍与共享布局一致（布局随 command_rect 平移）。"""
        flipped_command = Rect(600, 200, 240, 36)
        shared = self._as_lists(resolve_command_action_panel_layout(flipped_command))

        clickthrough = self._place(
            (80, 32), flipped_command,
            target_anchor="top_left", self_anchor="bottom_left", off_y=-2,
        )
        launch = self._place(
            (80, 32), Rect(clickthrough.x, clickthrough.y, 80, 32),
            target_anchor="top_left", self_anchor="bottom_left",
        )
        self.assertEqual([clickthrough.x, clickthrough.y, 80, 32], shared["clickthrough"])
        self.assertEqual([launch.x, launch.y, 80, 32], shared["launch_wuwa"])


if __name__ == "__main__":
    unittest.main()
