"""控件描述层：产品控件不再继承 QWidget 的守卫断言。

第五轮改建把气泡框从 `QWidget` 子类改成"描述 + 后端渲染"：状态、队列与绘制批次在
`lib/core/render/visuals/controls.py`，真实窗口由 `backends/qt/widgets/control_host.py`
持有。这些断言从外部钉住这个结构：

- 描述层不 import PyQt5，可在没有桌面后端的进程里完成排版、排队与点击判定；
- 控件本体（`lib/script/ui/bubble.py`）不再出现 PyQt5，也不再继承任何 Qt 基类；
- 后端宿主确实执行描述的绘制批次，且指针事件先翻译成中立事件再变成产品意图。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_DESCRIPTION_SCRIPT = textwrap.dedent(
    """
    import builtins
    import sys

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "PyQt5" or name.startswith("PyQt5."):
            raise AssertionError(f"control description imported Qt: {name}")
        return original_import(name, *args, **kwargs)

    builtins.__import__ = blocked_import

    from lib.core.render.visuals.application_visuals import (
        create_portable_bubble_text_metrics,
    )
    from lib.core.render.visuals.controls import (
        BUTTON_RIGHT,
        TICK_HIDE,
        TICK_NONE,
        TICK_REPLACE_NEXT,
        BubbleControl,
        BubbleInfo,
        PointerEvent,
    )

    control = BubbleControl(
        create_portable_bubble_text_metrics(),
        max_width=360,
        padding=12,
        border_width=2,
        fade_duration_ms=150,
        paint_layer=7,
        placeholder_size=(100, 40),
    )

    visual = control.set_message("服务已就绪")
    assert int(visual.size.width) > 0 and int(visual.size.height) > 0
    assert visual.batch.commands, "描述层没有产出绘制命令"
    assert control.anchor_local("bottom").y == float(visual.size.height)

    # 未达到最小显示时间：新消息排队而不是替换。
    control.current = BubbleInfo("第一条", min_ticks=5, max_ticks=10)
    assert control.add("第二条", 1, 2, "center", True, False,
                       source="", task_id="", kind="") == "enqueue"
    assert len(control.pending_queue) == 1
    assert control.on_tick() == TICK_NONE
    control.current.elapsed_ticks = 5
    assert control.on_tick() == TICK_REPLACE_NEXT

    # 右键复制并关闭；粒子 ID 由描述层给出而不是控件写死。
    control.current = BubbleInfo("可复制", 1, 2)
    intent = control.click_intent(PointerEvent(button=BUTTON_RIGHT))
    assert intent.copy_text == "可复制" and intent.hide

    # 命令式强制替换清空队列；超时 tick 直接判定隐藏。
    control.current = BubbleInfo("旧的", min_ticks=99, max_ticks=100)
    assert control.add("强插", 1, 2, "center", True, True,
                       source="", task_id="", kind="") == "replace"
    assert control.pending_queue == []
    control.current = BubbleInfo("超时", 0, 1)
    assert control.on_tick() == TICK_HIDE

    # 按元数据撤销只影响匹配项。
    control.current = BubbleInfo("甲", 1, 2, source="aaa")
    control.enqueue("乙", 1, 2, "center", True, "bbb", "", "")
    assert control.drop_matching(source="bbb") is False
    assert control.pending_queue == []

    # 说明书：悬停计数、排版与位置解算同样不碰 Qt。
    from lib.core.render.visuals.controls import (
        TOOLTIP_HIDE,
        TOOLTIP_IDLE,
        TOOLTIP_SHOW,
        TooltipControl,
    )
    from lib.core.render.visuals.screen import virtual_screen_rect

    screen = virtual_screen_rect([__import__(
        "lib.core.render.visuals.types", fromlist=["Rect"]
    ).Rect(0, 0, 1920, 1080)])

    tooltip = TooltipControl(
        create_portable_bubble_text_metrics(),
        max_text_width=220,
        padding_x=6,
        padding_y=3,
        border_width=1,
        cursor_gap=10,
        min_text_width=40,
        paint_layer=4,
        hover_ticks=20,
    )
    visual = tooltip.set_text("压成两行的说明文字 " * 8)
    assert int(visual.size.width) > 0 and visual.batch.commands
    assert len(tooltip.wrapped_lines()) > 1, "长文本没有被换行"

    # 静止满 hover_ticks 才提示；中途移动立刻请求隐藏。
    origin = (100, 100)
    assert tooltip.on_tick(origin) == TOOLTIP_IDLE
    for _ in range(19):
        assert tooltip.on_tick(origin) == TOOLTIP_IDLE
    assert tooltip.on_tick(origin) == TOOLTIP_SHOW
    # 面板显示之后：继续静止不再重复触发；一旦移动就要求隐藏。
    tooltip.visible = True
    assert tooltip.on_tick(origin) == TOOLTIP_IDLE
    assert tooltip.on_tick((140, 100)) == TOOLTIP_HIDE

    placement = tooltip.place((1800, 40), screen)
    assert placement.x + int(tooltip.logical_size().width) <= screen.x + screen.width
    assert placement.x < 1800, "越界时应镜像到光标左侧"

    # 面板上的文本是排版事实源，重复解算必须是同一份结果。
    assert tooltip.build_visual().lines == tooltip.build_visual().lines

    # 语音指示器：靠近才显示，离开超过延时才收起。
    from lib.core.render.visuals.controls import (
        HOVER_HIDE,
        HOVER_NONE,
        HOVER_SHOW,
        MicSttControl,
    )
    from lib.core.render.visuals.types import Point, Rect

    mic = MicSttControl(size=24, hover_radius=120, hide_delay=2.0, paint_layer=7)
    area = Rect(100, 100, 24, 24)
    assert mic.update_hover(Point(2000, 2000), area, now=0.0) == HOVER_NONE
    mic.listening = True
    assert mic.update_hover(Point(105, 105), area, now=0.0) == HOVER_SHOW
    mic.visible = True
    assert mic.update_hover(Point(2000, 2000), area, now=1.0) == HOVER_NONE
    assert mic.update_hover(Point(2000, 2000), area, now=5.0) == HOVER_HIDE
    assert mic.scaled_opacity(2.0) == 1.0

    # 进度条：拖动算术、剩余时间反推与 tick 节奏同样不碰 Qt。
    from lib.core.render.visuals.controls import MediaProgressControl
    from lib.core.render.visuals.types import Rect as _Rect

    prog = MediaProgressControl(
        create_portable_bubble_text_metrics(),
        width=240, height=20, gap=2, paint_layer=6,
    )
    prog.apply_progress(0.25, 120)
    assert prog.time_text() == "2:00"
    slider = prog.slider_rect()
    assert slider.width > 0
    prog.begin_drag(slider.x + slider.width)
    assert prog.dragging and prog.drag_progress == 1.0
    assert prog.x_to_progress(slider.x) == 0.0
    assert prog.end_drag() == 1.0 and prog.progress == 1.0
    placed = prog.placement(_Rect(400, 600, 300, 200), _Rect(0, 0, 1920, 1080))
    assert (placed.x, placed.y) == (400, 578)
    prog.visible = True
    assert [prog.advance_tick(20) for _ in range(20)] == [False] * 19 + [True]

    assert [name for name in sys.modules if name.startswith("PyQt5")] == []
    """
)


class ControlDescriptionLayerTests(unittest.TestCase):
    def test_description_layer_works_without_pyqt(self):
        """描述层必须能在 PyQt5 被屏蔽的进程里完成排版、排队与点击判定。"""
        result = subprocess.run(
            [sys.executable, "-c", _DESCRIPTION_SCRIPT],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_product_controls_do_not_import_or_subclass_qt(self):
        """已迁移的控件都不再是 QWidget：不得 import PyQt5，也不得继承 Qt 基类。

        每迁完一个控件就在这里加一行；`frozen_ui_qt_importers` 的收缩与这份名单
        是同一件事的两个视角。
        """
        migrated = (
            ("lib/script/ui/bubble.py", "Bubble"),
            ("lib/script/ui/tooltip_panel.py", "TooltipPanel"),
            ("lib/script/ui/mic_stt_indicator.py", "MicSttIndicator"),
            ("lib/script/ui/progress_panel.py", "ProgressPanel"),
        )
        for relative, class_name in migrated:
            with self.subTest(control=relative):
                tree = ast.parse(
                    (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")
                )

                imported = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    imported.extend(
                        name for name in names
                        if name == "PyQt5" or name.startswith("PyQt5.")
                    )
                self.assertEqual(imported, [])

                control_class = next(
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name
                )
                bases = [ast.unparse(base) for base in control_class.bases]
                self.assertEqual(
                    bases, [], f"{class_name} 仍继承 {bases}；控件层应只描述状态"
                )

    def test_description_layer_owns_no_window_facts(self):
        """窗口标志、透明度动画与光标属于后端宿主，不得出现在描述层代码里。"""
        tree = ast.parse(
            (_REPO_ROOT / "lib/core/render/visuals/controls.py").read_text(
                encoding="utf-8-sig"
            )
        )
        forbidden = {"QWidget", "setWindowFlags", "QPropertyAnimation", "QCursor"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in forbidden:
                    offenders.append(name)
            elif isinstance(node, ast.ImportFrom) and node.module == "PyQt5":
                offenders.append("PyQt5")
        self.assertEqual(offenders, [])

    def test_scaled_opacity_applies_global_setting(self):
        from lib.core.render.visuals.controls import BubbleControl

        def make(scale):
            return BubbleControl(
                None,
                max_width=10,
                padding=1,
                border_width=1,
                fade_duration_ms=1,
                paint_layer=0,
                opacity_scale=scale,
            )

        self.assertAlmostEqual(make(0.5).scaled_opacity(1.0), 0.5)
        self.assertAlmostEqual(make(0.5).scaled_opacity(2.0), 0.5)
        self.assertAlmostEqual(make(lambda: 0.25).scaled_opacity(1.0), 0.25)


class QtControlHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))

        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _control():
        from lib.core.render.visuals.controls import BubbleControl
        from lib.script.ui.render_bridge import digit_font, text_metrics, ui_font

        font = ui_font()
        font.setBold(True)
        return BubbleControl(
            text_metrics(font, digit_font()),
            max_width=360,
            padding=12,
            border_width=2,
            fade_duration_ms=150,
            paint_layer=0,
        )

    @staticmethod
    def _host(batch=None, **kwargs):
        from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend
        from lib.core.render.backends.qt.drawing.presentation import QtPresentationHost
        from lib.core.render.backends.qt.widgets.control_host import QtControlHost

        kwargs.setdefault("draw_backend", QtDrawBackend())
        kwargs.setdefault("presentation_host", QtPresentationHost())
        kwargs.setdefault("paint_batch", lambda: batch)
        return QtControlHost(**kwargs)

    @staticmethod
    def _bytes(image) -> bytes:
        bits = image.constBits()
        bits.setsize(image.byteCount())
        return bytes(bits)

    def test_host_paints_the_description_batch_pixel_for_pixel(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QImage, QPainter

        from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend
        from lib.core.render.visuals.types import Rect

        visual = self._control().set_message("测试123abc")
        width, height = int(visual.size.width), int(visual.size.height)

        host = self._host(batch=visual.batch)
        try:
            host.apply_size(width, height)
            # 宿主默认透明度为 0（等待注入的淡入）；像素比对前先拉到不透明。
            host.set_opacity(1.0)
            through_host = QImage(width, height, QImage.Format_RGBA8888)
            through_host.fill(Qt.transparent)
            host.render(through_host)

            direct = QImage(width, height, QImage.Format_RGBA8888)
            direct.fill(Qt.transparent)
            painter = QPainter(direct)
            painter.setRenderHint(QPainter.Antialiasing, False)
            QtDrawBackend().render(visual.batch, painter, Rect(0, 0, width, height))
            painter.end()

            self.assertEqual(self._bytes(through_host), self._bytes(direct))
        finally:
            host.cleanup()

    def test_pointer_press_copies_then_hides_through_injected_intent(self):
        from PyQt5.QtCore import QEvent, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent
        from PyQt5.QtWidgets import QApplication

        from lib.core.render.visuals.controls import BubbleInfo

        control = self._control()
        control.current = BubbleInfo("复制我", 1, 2)
        seen = []
        hidden = []

        def on_pointer(event):
            seen.append(event)
            return control.click_intent(event)

        host = self._host(
            on_pointer=on_pointer,
            on_hide_requested=lambda: hidden.append(1),
        )
        try:
            event = QMouseEvent(
                QEvent.MouseButtonPress,
                QPointF(3, 4),
                Qt.RightButton,
                Qt.RightButton,
                Qt.NoModifier,
            )
            host.mousePressEvent(event)
            self.assertEqual([point.button for point in seen], ["right"])
            self.assertEqual(QApplication.clipboard().text(), "复制我")
            self.assertEqual(hidden, [1])
        finally:
            host.cleanup()

    def test_bubble_hide_publishes_integer_particle_area(self):
        """淡出粒子请求的面积必须取自宿主几何，且是 4 个整数。

        这条断言守着一次真实回归：核心 `Rect` 用属性（`rect.x`）而不是方法
        （`rect.x()`），`geometry_rect()` 换成核心类型后旧写法会让 tick→隐藏
        在事件回调里抛 `TypeError`，而只看构造的测试抓不到。
        """
        from lib.core.event.center import (
            Event,
            EventType,
            cleanup_event_center,
            get_event_center,
        )
        from lib.script.ui.bubble import Bubble

        center = get_event_center()
        requests = []
        center.subscribe(EventType.PARTICLE_REQUEST, lambda event: requests.append(event.data))

        bubble = Bubble()
        try:
            center.publish(Event(EventType.INFORMATION, {"text": "关机前的气泡", "min": 1, "max": 2}))
            self.app.processEvents()
            self.assertTrue(bubble.isVisible())

            center.publish(Event(EventType.TICK, {}))
            center.publish(Event(EventType.TICK, {}))
            self.app.processEvents()
            bubble._host._on_animation_finished()
            self.app.processEvents()

            rects = [data for data in requests if data.get("area_type") == "rect"]
            self.assertTrue(rects, "淡出没有发布矩形粒子请求")
            for data in rects:
                area = data["area_data"]
                self.assertEqual(len(area), 4)
                self.assertTrue(
                    all(isinstance(value, int) for value in area),
                    f"粒子面积必须是整数元组：{area!r}",
                )
            self.assertFalse(bubble.isVisible())
        finally:
            bubble.close()
            cleanup_event_center()

    def test_tooltip_host_paints_the_description_batch_pixel_for_pixel(self):
        """说明书面板的宿主要逐像素画出共享批次，而不是自己重排一遍。

        版面事实源是 `build_tooltip_visual`；控件层只决定文本与位置。这条断言守住
        「迁移没有把绘制偷回控件」——换后端时两个后端画出的是同一份批次。
        """
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QImage, QPainter

        from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend
        from lib.core.render.visuals.controls import TooltipControl
        from lib.core.render.visuals.types import Rect
        from lib.script.ui.render_bridge import digit_font, text_metrics, ui_font

        ui = ui_font()
        ui.setBold(True)
        control = TooltipControl(
            text_metrics(ui, digit_font()),
            max_text_width=220,
            padding_x=6,
            padding_y=3,
            border_width=1,
            cursor_gap=10,
            paint_layer=4,
        )
        visual = control.set_text("说明文字 with ASCII 123")
        width, height = int(visual.size.width), int(visual.size.height)
        self.assertEqual(
            (control.logical_size().width, control.logical_size().height),
            (float(width), float(height)),
        )

        host = self._host(batch=visual.batch, auto_hide_ms=500, on_auto_hide=lambda: None)
        try:
            host.apply_size(width, height)
            host.set_opacity(1.0)
            through_host = QImage(width, height, QImage.Format_RGBA8888)
            through_host.fill(Qt.transparent)
            host.render(through_host)

            direct = QImage(width, height, QImage.Format_RGBA8888)
            direct.fill(Qt.transparent)
            painter = QPainter(direct)
            painter.setRenderHint(QPainter.Antialiasing, False)
            QtDrawBackend().render(visual.batch, painter, Rect(0, 0, width, height))
            painter.end()

            self.assertEqual(self._bytes(through_host), self._bytes(direct))
        finally:
            host.cleanup()

    def test_host_applies_size_geometry_and_clickthrough(self):
        from PyQt5.QtCore import Qt

        from lib.core.render.visuals.types import Rect

        host = self._host()
        try:
            host.apply_size(0, 0)
            self.assertEqual((host.width(), host.height()), (1, 1))
            host.apply_size(120, 40)
            self.assertEqual((host.width(), host.height()), (120, 40))

            host.move_to(50, 60)
            geometry = host.geometry_rect()
            self.assertIsInstance(geometry, Rect)
            self.assertEqual((int(geometry.width), int(geometry.height)), (120, 40))

            host.set_clickthrough(True)
            self.assertTrue(host.testAttribute(Qt.WA_TransparentForMouseEvents))
            host.set_clickthrough(False)
            self.assertFalse(host.testAttribute(Qt.WA_TransparentForMouseEvents))
        finally:
            host.cleanup()



class TooltipPanelBehaviorTests(unittest.TestCase):
    """说明书控件真的走"描述 → 宿主"那条路，而且会自己收尾。

    迁移过程中冒出过两个只在运行期暴露的问题，单元断言都看不见：
    ``initial_position`` 没喂进去会让"静止 20 tick"晚一拍；淡出没有标记
    ``fade_out=True`` 会让窗口淡到透明后仍留在屏幕上继续拦鼠标。这条端到端
    断言同时钉住两者，并盯住事件中心有没有吞掉回调异常。
    """

    @classmethod
    def setUpClass(cls):
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))

        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_hover_shows_then_moves_hide_the_panel(self):
        import logging

        from PyQt5.QtCore import QPoint
        from PyQt5.QtWidgets import QWidget

        from lib.core.event.center import (
            Event,
            EventType,
            cleanup_event_center,
            get_event_center,
        )
        import lib.script.ui.tooltip_panel as tooltip_panel

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        event_logger = logging.getLogger("lib.core.event.center")
        event_logger.addHandler(handler)

        target = QWidget()
        target.setGeometry(300, 200, 300, 200)
        target._description = "测试说明书"
        target.show()
        self.app.processEvents()

        original_cursor = tooltip_panel.pointer_cursor
        cursor = [QPoint(350, 250)]
        tooltip_panel.pointer_cursor = lambda: cursor[0]

        panel = tooltip_panel.TooltipPanel()
        center = get_event_center()
        try:
            for _ in range(19):
                center.publish(Event(EventType.TICK, {}))
                self.app.processEvents()
            self.assertFalse(panel.isVisible(), "静止未满 20 tick 就弹了说明书")

            center.publish(Event(EventType.TICK, {}))
            self.app.processEvents()
            self.assertTrue(panel.isVisible(), "静止满 20 tick 后说明书没有出现")
            self.assertEqual(panel._current_text, "测试说明书")
            self.assertGreater(panel.width(), 1)

            # 进入淡出：动画播完后窗口必须真的收起，而不是留在屏幕上拦鼠标。
            cursor[0] = QPoint(500, 250)
            center.publish(Event(EventType.TICK, {}))
            self.app.processEvents()
            self.assertEqual(panel._stationary_ticks, 0, "移动后静止计数没有归零")
            panel._host._on_animation_finished()
            self.app.processEvents()
            self.assertFalse(panel.isVisible(), "淡出结束后说明书窗口仍然可见")

            # 立即隐藏会同时重置悬停计数，下一次要重新静止满 20 tick。
            panel._show("再次", cursor[0])
            self.app.processEvents()
            panel.hide_now()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())
            self.assertEqual(panel._stationary_ticks, 0)

            # `shutdown.py` 的通用关机清理会对每个单例调 hide()/close()/update()；
            # 面板不再是 QWidget 之后，这条路径必须仍然可用。
            panel.update()
            panel.hide()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())
            self.assertTrue(callable(panel.close))

            self.assertEqual(
                [text for text in records if "Event handler error" in text],
                [],
            )
        finally:
            event_logger.removeHandler(handler)
            panel.close()
            tooltip_panel.pointer_cursor = original_cursor
            target.close()
            cleanup_event_center()


class MicSttIndicatorBehaviorTests(unittest.TestCase):
    """语音指示器迁移后仍按"靠近显示、离开超时才收起"工作。

    这条断言守着一个只有运行期才暴露的坑：`QWidget.setFixedSize` 换成宿主之后，
    窗口尺寸不会自己出现；没调 `apply_size` 时窗口是 640x480 的默认值，
    位置与命中判定会一起算错。
    """

    @classmethod
    def setUpClass(cls):
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))

        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_indicator_follows_state_hover_and_clickthrough(self):
        import logging

        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QWidget

        from lib.core.event.center import (
            Event,
            EventType,
            cleanup_event_center,
            get_event_center,
        )
        from lib.core.render.visuals.types import Point
        import lib.script.ui.mic_stt_indicator as indicator_module

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        event_logger = logging.getLogger("lib.core.event.center")
        event_logger.addHandler(handler)

        owner = QWidget()
        owner.setGeometry(500, 400, 200, 200)
        owner.show()
        self.app.processEvents()

        original_pointer = indicator_module.pointer_position
        cursor = [Point(505.0, 405.0)]
        indicator_module.pointer_position = lambda: cursor[0]

        center = get_event_center()
        indicator = indicator_module.MicSttIndicator(owner)
        try:
            # 固定边长必须在构造期就落到窗口上，否则位置与命中判定全会偏。
            self.assertEqual((indicator.width(), indicator.height()),
                             (indicator.SIZE, indicator.SIZE))

            center.publish(Event(EventType.MIC_STT_STATE_CHANGE, {
                "is_listening": True, "speech_active": True, "status": "识别中",
            }))
            self.app.processEvents()
            self.assertTrue(indicator._visible)
            self.assertIn("识别中", indicator._description)

            # 指针在半径内：持续保持可见。
            center.publish(Event(EventType.FRAME, {}))
            self.app.processEvents()
            self.assertTrue(indicator._visible)

            # 离开但未超过 hide_delay：仍可见。
            cursor[0] = Point(4000.0, 4000.0)
            center.publish(Event(EventType.FRAME, {}))
            self.app.processEvents()
            self.assertTrue(indicator._visible, "刚离开就收起了，未尊重 hide_delay")

            # 超过 hide_delay：收起。
            indicator._control.last_pointer_inside_ts -= 5.0
            center.publish(Event(EventType.FRAME, {}))
            self.app.processEvents()
            self.assertFalse(indicator._visible)
            indicator._host._on_animation_finished()
            self.app.processEvents()
            self.assertFalse(indicator._host.isVisible())

            # 停止监听：不再显示。
            center.publish(Event(EventType.MIC_STT_STATE_CHANGE, {"is_listening": False}))
            self.app.processEvents()
            self.assertFalse(indicator._visible)

            # 穿透开关落到真实窗口属性上。
            center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": True}))
            self.app.processEvents()
            self.assertTrue(indicator._host.testAttribute(Qt.WA_TransparentForMouseEvents))
            center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": False}))
            self.app.processEvents()
            self.assertFalse(indicator._host.testAttribute(Qt.WA_TransparentForMouseEvents))

            self.assertEqual(
                [text for text in records if "Event handler error" in text],
                [],
            )
        finally:
            event_logger.removeHandler(handler)
            indicator.close()
            indicator_module.pointer_position = original_pointer
            owner.close()
            cleanup_event_center()


class ProgressPanelBehaviorTests(unittest.TestCase):
    """进度条迁移后仍按"压在滑条上拖动 -> 松手发 seek"工作。

    这条断言守着拖动链路：`QWidget` 的 press/move/release 三个回调换成宿主之后，
    少了"松手"这一环时进度会一直停在拖动中、seek 永远发不出去，而构造期与静态
    断言都看不出来。
    """

    @classmethod
    def setUpClass(cls):
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))

        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_drag_press_release_publishes_seek(self):
        import logging

        from PyQt5.QtCore import QRect

        from lib.core.event.center import (
            Event,
            EventType,
            cleanup_event_center,
            get_event_center,
        )
        from lib.core.render.visuals.controls import BUTTON_LEFT, PointerEvent
        from lib.core.render.visuals.types import Point
        from lib.script.ui.progress_panel import ProgressPanel

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        event_logger = logging.getLogger("lib.core.event.center")
        event_logger.addHandler(handler)

        center = get_event_center()
        seeks = []
        center.subscribe(EventType.MUSIC_SEEK, lambda event: seeks.append(event.data))

        panel = ProgressPanel()
        try:
            # 固定尺寸必须在构造期就落到窗口上，位置算术才有意义。
            self.assertEqual((panel.width(), panel.height()), (240, 20))

            panel.set_position_below_playlist(QRect(400, 600, 300, 200))
            self.assertEqual((panel._host.x(), panel._host.y()), (400, 578))

            panel.show_panel()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())

            center.publish(Event(EventType.MUSIC_PROGRESS, {"progress": 0.25, "remaining": 120}))
            self.app.processEvents()
            self.assertEqual(panel._control.time_text(), "2:00")

            # 按住滑条 -> 拖动 -> 松手：必须发出一次 seek，并停在非拖动状态。
            panel._on_pointer(PointerEvent(button=BUTTON_LEFT, local=Point(100, 10)))
            self.assertTrue(panel._control.dragging)
            panel._on_pointer(PointerEvent(button=BUTTON_LEFT, local=Point(120, 10)))
            panel._on_pointer_release()
            self.assertFalse(panel._control.dragging, "松手后仍在拖动")
            self.assertEqual(len(seeks), 1)
            self.assertAlmostEqual(seeks[0]["progress"], panel._control.progress)

            # 歌曲结束重置，隐藏后窗口真的收起。
            center.publish(Event(EventType.MUSIC_SONG_END, {}))
            self.app.processEvents()
            self.assertEqual((panel._control.progress, panel._control.remaining), (0.0, 0))

            panel.hide_panel()
            self.app.processEvents()
            panel._host._on_animation_finished()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())

            self.assertEqual(
                [text for text in records if "Event handler error" in text],
                [],
            )
        finally:
            event_logger.removeHandler(handler)
            panel.close()
            cleanup_event_center()


#: 返回核心几何（`Rect` / `Point` / `Size`）的共享入口。这些返回值用**属性面试**
#: （`rect.x`），与 Qt 的**方法面试**（`QRect.x()`）不同名同形，混用只会在运行时炸。
_CORE_GEOMETRY_PRODUCERS = {
    "screen_rect_for_point",
    "widget_global_rect",
    "widget_global_point",
    "clamp_rect_position",
    "pointer_position",
    "clamp_core_rect_position",
    "get_screen_rect_for_point",
    "get_virtual_screen_rect",
    "virtual_screen_rect",
    "resolve_bubble_geometry",
    "coerce_rect",
    "coerce_point",
    "coerce_size",
    "Rect",
    "Point",
    "Size",
}

#: 迁移前 QRect/QPoint 上按方法用的名字。核心类型里它们是属性或不存在。
_QT_STYLE_GEOMETRY_METHODS = {
    "center", "x", "y", "width", "height", "right", "bottom", "left", "top",
    "topLeft", "top_left", "size", "isValid", "isEmpty", "adjusted",
    "getRect", "toRect",
}


class CoreGeometryCallStyleTests(unittest.TestCase):
    """核心几何用属性，Qt 几何用方法；跨层访问不得再退回 Qt 的写法。

    这组断言守着一次真实回归：`widget_global_rect()` 的返回值从 `QRect` 换成核心
    `Rect` 后，`command_dialog._is_mouse_far_from_family()` 里遗留的
    `widget_global_rect(widget).center()` 只有在该 TICK 分支真的跑到时才抛
    `TypeError: \'Point\' object is not callable`——构造期、导入期与既有单元测试都
    看不到它。静态扫描把整类写法挡在 CI 里，运行期断言再钉一次热点路径。
    """

    _SKIP_PARTS = ("/render/backends/qt/", "/render/backends/dx/")

    @classmethod
    def _scan(cls):
        findings = []
        roots = _REPO_ROOT / "lib" / "script", _REPO_ROOT / "lib" / "core", _REPO_ROOT / "scripts"
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                relative = path.relative_to(_REPO_ROOT).as_posix()
                if "/__pycache__/" in relative:
                    continue
                if any(part in relative for part in cls._SKIP_PARTS):
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
                except SyntaxError:
                    continue
                findings.extend(cls._core_typed_misuse(relative, tree))
        return sorted(set(findings))

    @staticmethod
    def _core_typed_misuse(relative, tree):
        """把「绑定了核心几何的名字」当成 Qt 几何调用时报告出来。

        追踪是传递的：`rect = widget_global_rect(w)` 与 `copy = rect` 都算核心几何。
        这正是这类回归的复发路径——先出现一个中转变量，再有人给它写 `()`。
        """
        core_names: set[str] = set()
        core_attrs: set[str] = set()
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                assignments.append((node.targets, node.value))
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                assignments.append(([node.target], node.value))

        def is_core_expression(value):
            """判断赋值右侧是否为「核心几何」。`a = producer(); b = a` 也算。"""
            if isinstance(value, ast.Call):
                name = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
                return name if name in _CORE_GEOMETRY_PRODUCERS else None
            if isinstance(value, ast.Name) and value.id in core_names:
                return "__alias__"
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
                and value.attr in core_attrs
            ):
                return "__alias__"
            return None

        # 迭代到不动点，让别名链（`a = producer(); b = a`）也被标记为核心几何。
        changed = True
        while changed:
            changed = False
            for targets, value in assignments:
                if is_core_expression(value) is None:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in core_names:
                        core_names.add(target.id)
                        changed = True
                    elif (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr not in core_attrs
                    ):
                        core_attrs.add(target.attr)
                        changed = True
                    elif isinstance(target, ast.Tuple):
                        for element in target.elts:
                            if isinstance(element, ast.Name) and element.id not in core_names:
                                core_names.add(element.id)
                                changed = True

        findings = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in _QT_STYLE_GEOMETRY_METHODS:
                continue
            receiver = node.func.value
            label = None
            if isinstance(receiver, ast.Name) and receiver.id in core_names:
                label = f"{receiver.id}.{method}()"
            elif (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "self"
                and receiver.attr in core_attrs
            ):
                label = f"self.{receiver.attr}.{method}()"
            elif isinstance(receiver, ast.Call):
                producer = (
                    getattr(receiver.func, "id", None)
                    or getattr(receiver.func, "attr", None)
                )
                if producer in _CORE_GEOMETRY_PRODUCERS:
                    label = f"{producer}(...).{method}()"
            if label:
                findings.append(f"{relative}:{node.lineno}:{label}")
        return findings

    def test_no_core_geometry_is_called_like_a_qt_type(self):
        """核心 `Rect`/`Point` 的返回值不得再按 `QRect` 的方法写法取用。"""
        findings = self._scan()
        self.assertEqual(
            findings,
            [],
            "核心几何是属性（rect.x）；这里出现了 Qt 风格的方法调用：\n"
            + "\n".join(findings),
        )

    def test_core_geometry_keeps_the_same_reader_names_as_qt(self):
        """属性名与 QRect 的方法名逐一对齐，迁移时只需去括号，不必改名字。"""
        from lib.core.render.visuals.types import Rect

        rect = Rect(10, 20, 30, 40)
        self.assertEqual(rect.x, 10)
        self.assertEqual(rect.y, 20)
        self.assertEqual(rect.width, 30)
        self.assertEqual(rect.height, 40)
        self.assertEqual(rect.right, 40)
        self.assertEqual(rect.bottom, 60)
        self.assertEqual((rect.center.x, rect.center.y), (25.0, 40.0))
        self.assertEqual((rect.top_left.x, rect.top_left.y), (10, 20))


class CommandDialogGeometryIntegrationTests(unittest.TestCase):
    """命令框的 TICK 分支真的跑到核心几何；它在构造期与导入期都不会被触发。"""

    @classmethod
    def setUpClass(cls):
        import PyQt5

        root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))

        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_mouse_distance_check_runs_on_core_geometry(self):
        """驱动 `_is_mouse_far_from_family()`：它读的是核心 `Rect` 的属性。"""
        from PyQt5.QtWidgets import QWidget

        from lib.core.event.center import Event, EventType, get_event_center
        from lib.core.render.visuals.types import Point
        from lib.script.ui import pet_window_ui

        class _PetStub:
            def __init__(self, x, y):
                self._position = Point(x, y)

            def get_core_position(self):
                return self._position

        owner = QWidget()
        owner.resize(200, 200)
        ui = pet_window_ui.create_pet_window_ui(owner, on_close=lambda: None)
        layer = ui["_right_click_ui_layer"]
        try:
            command = ui["_cmd"]
            command.toggle(_PetStub(600, 400))
            layer._on_frame()
            self.app.processEvents()

            # 直接命中报错行：`widget_global_rect(widget).center`
            self.assertIsInstance(command._is_mouse_far_from_family(), bool)

            # 再让 TICK 真的事件走一遍。事件中心吞掉回调异常并 `logger.exception`，
            # 所以断言必须盯住日志，否则「回调炸了」看起来跟「什么都没发生」一样。
            import logging

            records = []

            class _Capture(logging.Handler):
                def emit(self, record):
                    records.append(record.getMessage())

            handler = _Capture()
            event_logger = logging.getLogger("lib.core.event.center")
            event_logger.addHandler(handler)
            center = get_event_center()
            try:
                for _ in range(3):
                    center.publish(Event(EventType.TICK, {}))
                    self.app.processEvents()
            finally:
                event_logger.removeHandler(handler)

            self.assertEqual(
                [text for text in records if "Event handler error" in text],
                [],
            )
        finally:
            layer.close_layer()
            pet_window_ui.shutdown_pet_window_ui(owner)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
