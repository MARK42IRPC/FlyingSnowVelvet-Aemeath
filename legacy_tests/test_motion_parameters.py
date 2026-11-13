"""动效参数表：后端中立、可从共享时长派生、且被夹在有界区间内。

《视觉表现契约》第 3 节把「动画时序、缓动、插值」列为必须位于后端之外的视觉逻辑。
这份参数表是工作台切页、侧边栏抽屉与设置页分区错峰的唯一时长来源——此前它们各自在
Qt 宿主里写死 ``UI.get("ui_fade_duration", 180)``，与共享的
``ui_presentation.ui_fade_duration_ms()``（回退 200）分歧，DirectX 端也无从复用。
"""

from __future__ import annotations

import unittest

from lib.core.render.visuals import motion
from lib.core.services.ui_presentation import ui_fade_duration_ms


class MotionParametersTests(unittest.TestCase):
    def test_durations_follow_the_shared_fade_setting(self):
        for fade in (180, 200, 260):
            with self.subTest(fade=fade):
                self.assertEqual(
                    motion.page_transition_ms(fade),
                    max(
                        motion.PAGE_TRANSITION_RANGE[0],
                        min(motion.PAGE_TRANSITION_RANGE[1], fade),
                    ),
                )
                self.assertEqual(
                    motion.group_collapse_ms(fade),
                    max(
                        motion.GROUP_COLLAPSE_RANGE[0],
                        min(motion.GROUP_COLLAPSE_RANGE[1], round(fade * 0.9)),
                    ),
                )

    def test_extreme_user_settings_stay_inside_the_declared_ranges(self):
        # 用户把 ui_fade_duration 调到 5000 时，切页不该变成 5 秒；调到 0 时也不该出现
        # 负时长或「零秒动画」以外的怪值。
        for fade in (0, 1, 5000):
            with self.subTest(fade=fade):
                page = motion.page_transition_ms(fade)
                group = motion.group_collapse_ms(fade)
                self.assertGreaterEqual(page, motion.PAGE_TRANSITION_RANGE[0])
                self.assertLessEqual(page, motion.PAGE_TRANSITION_RANGE[1])
                self.assertGreaterEqual(group, motion.GROUP_COLLAPSE_RANGE[0])
                self.assertLessEqual(group, motion.GROUP_COLLAPSE_RANGE[1])

    def test_none_defers_to_the_single_shared_fallback(self):
        # 不传值时必须走 ui_presentation 的唯一事实源，不能自带一个 180 的私有回退。
        self.assertEqual(motion.page_transition_ms(), motion.page_transition_ms(ui_fade_duration_ms()))

    def test_section_stagger_increases_with_order_and_respects_the_limit(self):
        durations = [motion.section_enter_ms(order, 200) for order in range(4)]
        self.assertEqual(durations, sorted(durations))
        self.assertEqual(
            durations[1] - durations[0],
            motion.SECTION_STAGGER_MS,
        )
        self.assertEqual(motion.section_enter_ms(-3, 200), motion.section_enter_ms(0, 200))
        self.assertEqual(motion.SECTION_STAGGER_LIMIT, 8)

    def test_group_content_starts_partially_transparent(self):
        # 抽屉展开的内容淡入必须从一个可见但未满的起点开始，避免「啪」地出现。
        self.assertGreater(motion.GROUP_CONTENT_MIN_OPACITY, 0.0)
        self.assertLess(motion.GROUP_CONTENT_MIN_OPACITY, 1.0)


if __name__ == "__main__":
    unittest.main()
