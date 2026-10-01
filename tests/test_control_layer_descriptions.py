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

    def test_product_control_does_not_import_or_subclass_qt(self):
        """气泡框已不是 QWidget：不得 import PyQt5，也不得继承任何 Qt 基类。"""
        path = _REPO_ROOT / "lib/script/ui/bubble.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))

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

        bubble_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Bubble"
        )
        bases = [ast.unparse(base) for base in bubble_class.bases]
        self.assertEqual(bases, [], f"Bubble 仍继承 {bases}；控件层应只描述状态")

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


if __name__ == "__main__":
    unittest.main()
