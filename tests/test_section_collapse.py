"""设置页卡片联动：滑入滑出必须真的动，且不能把卡片内容压扁。

设置页里「开关一改，某张卡片跟着出现/消失」是高频交互（回复模式切换 AI 页四张模式卡片、
`LIVE_STREAM.enabled` 控住「弹幕点歌」、`GIFT_ALERT.enabled` 控住「礼物提醒」、办公页
「独立 api」控住那一块）。此前都是裸 `setVisible()`：卡片一帧内凭空出现，下方内容瞬间跳位。

两条实现约束在这里钉死：

- 动画必须作用在**裁切容器**上。直接对卡片做 `maximumHeight` 动画会把内部的输入框
  挤成 1px 高——看起来是「压扁」而不是「滑出」；
- 卡片要保持自己的自然高度并留在原布局位置，否则包裹本身就会让卡片跳到页面末尾。
"""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QPropertyAnimation
from PyQt5.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from lib.core.render.backends.qt.widgets import section_collapse as collapse
from lib.core.render.backends.qt.widgets.workbench_settings_layout import SettingsSection
from lib.core.render.visuals import motion


class _Fixture:
    """一张中间位置的卡片 + 它下面的参照卡片。"""

    def __init__(self, app):
        self.app = app
        self.host = QWidget()
        self.host.resize(420, 700)
        self.layout = QVBoxLayout(self.host)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(12)

        self.top = SettingsSection("上", parent=self.host)
        self.layout.addWidget(self.top)
        self.section = SettingsSection("中", parent=self.host)
        self.section.body_layout.addWidget(QLineEdit(self.section))
        self.layout.addWidget(self.section)
        self.bottom = SettingsSection("下", parent=self.host)
        self.layout.addWidget(self.bottom)
        self.layout.addStretch(1)
        self.host.show()
        app.processEvents()

    def dispose(self) -> None:
        """销毁前先让动画落位。

        页面真正销毁时会走 `deleteLater()` 并继续跑事件循环；如果在动画中途直接销毁，
        收尾回调会碰到已释放的容器。生产代码已经用 `destroyed` 连接兜住这种情况，
        测试这里主动收尾，避免在离屏环境下依赖那条兜底路径的行为细节。
        """
        state = getattr(self.section, collapse._STATE_KEY, None)
        if state is not None:
            for attribute in ("height_anim", "fade_anim"):
                animation = getattr(state, attribute, None)
                if animation is not None:
                    try:
                        animation.stop()
                    except RuntimeError:
                        pass
        self.app.processEvents()
        self.host.deleteLater()
        self.app.processEvents()

    def pump(self, until_idle: bool = True, budget: float = 0.8):
        """跑事件循环直到动画结束（离屏下不要用 QTest.qWait，会触发自绘 grab）。"""
        samples = []
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            self.app.processEvents()
            state = getattr(self.section, collapse._STATE_KEY, None)
            samples.append(
                (
                    state.outer.height() if state is not None else self.section.height(),
                    self.section.height(),
                    self.bottom.y(),
                )
            )
            if not until_idle:
                break
            if state is None or state.height_anim is None:
                break
            if state.height_anim.state() != QPropertyAnimation.Running:
                break
            time.sleep(0.008)
        return samples


class SectionCollapseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hide_then_show_animates_the_clip_container_not_the_card(self):
        fixture = _Fixture(self.app)
        try:
            collapse.hide_section(fixture.section, host=fixture.host)
            # 动画会把卡片固定成它在裁切容器里的自然高度；以这一份为准，
            # 而不是包裹前后各量一次的 sizeHint（两者可能相差一个 layout 的余量）。
            samples = fixture.pump()
            pinned = fixture.section.height()
            self.assertGreater(pinned, 0)
            heights = {outer for outer, _card, _bottom in samples if outer > 0}
            self.assertGreater(len(heights), 1, f"收起时容器高度没有变化：{samples}")
            # 卡片自身在收起过程中保持固定高度：它是被裁切，不是被压扁。
            self.assertTrue(
                all(card in (pinned, 0) for _outer, card, _bottom in samples),
                f"卡片高度在收起过程中被压缩：{samples}",
            )
            self.assertTrue(fixture.section.isHidden())

            collapse.show_section(fixture.section, host=fixture.host)
            samples = fixture.pump()
            heights = {outer for outer, _card, _bottom in samples if outer > 0}
            self.assertGreater(len(heights), 1, f"展开时容器高度没有变化：{samples}")
            self.assertFalse(fixture.section.isHidden())
            # 落位后解除高度限制，卡片重新跟随内容。
            self.assertEqual(fixture.section.minimumHeight(), 0)
            self.assertEqual(fixture.section.maximumHeight(), 16777215)
        finally:
            fixture.dispose()

    def test_bottom_card_follows_the_animation_instead_of_jumping(self):
        """下方卡片必须逐帧跟随，而不是最后一帧跳位。"""
        fixture = _Fixture(self.app)
        try:
            collapse.hide_section(fixture.section, host=fixture.host)
            samples = fixture.pump()
            positions = {bottom for _outer, _card, bottom in samples}
            self.assertGreater(
                len(positions),
                1,
                f"下方卡片位置没有逐帧变化，说明是硬切：{samples}",
            )
        finally:
            fixture.dispose()

    def test_section_keeps_its_place_in_the_page_layout(self):
        """包裹后卡片仍在原位，不能跳到页面末尾。"""
        fixture = _Fixture(self.app)
        try:
            index_before = fixture.layout.indexOf(fixture.section)
            collapse.hide_section(fixture.section, host=fixture.host)
            state = getattr(fixture.section, collapse._STATE_KEY, None)
            self.assertIsNotNone(state, "隐藏后应当建出裁切容器")
            self.assertEqual(
                fixture.layout.indexOf(state.outer),
                index_before,
                "裁切容器没有接管卡片原来的布局位置",
            )
        finally:
            fixture.dispose()

    def test_repeated_toggle_does_not_double_wrap(self):
        fixture = _Fixture(self.app)
        try:
            collapse.hide_section(fixture.section, host=fixture.host)
            fixture.pump()
            state = fixture.section._section_collapse_state
            collapse.show_section(fixture.section, host=fixture.host)
            fixture.pump()
            collapse.hide_section(fixture.section, host=fixture.host)
            fixture.pump()
            self.assertIs(fixture.section._section_collapse_state, state, "重复联动不应重复包裹")
        finally:
            fixture.dispose()

    def test_animate_false_applies_the_end_state_immediately(self):
        fixture = _Fixture(self.app)
        try:
            collapse.set_section_visible(fixture.section, False, host=fixture.host, animate=False)
            self.assertTrue(fixture.section.isHidden())
            collapse.set_section_visible(fixture.section, True, host=fixture.host, animate=False)
            self.assertFalse(fixture.section.isHidden())
        finally:
            fixture.dispose()


class SectionRevealTimingTests(unittest.TestCase):
    def test_reveal_duration_is_derived_from_the_shared_fade_setting(self):
        for fade in (180, 200, 260, 0, 5000):
            with self.subTest(fade=fade):
                duration = motion.section_reveal_ms(fade)
                self.assertGreaterEqual(duration, motion.SECTION_REVEAL_RANGE[0])
                self.assertLessEqual(duration, motion.SECTION_REVEAL_RANGE[1])

    def test_reveal_is_faster_than_a_full_page_transition(self):
        # 卡片联动比切页频繁得多，用切页那一档会显得拖沓。
        self.assertLess(
            motion.SECTION_REVEAL_RANGE[1],
            motion.PAGE_TRANSITION_RANGE[1] + 1,
        )
        self.assertLessEqual(motion.section_reveal_ms(200), motion.page_transition_ms(200))


if __name__ == "__main__":
    unittest.main()
