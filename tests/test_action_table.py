"""动作表与状态机迁移矩阵的回归测试。"""
import unittest

from config.actions import (
    ACTION,
    ACTIONS,
    BASE,
    INTERRUPT_ANY,
    INTERRUPT_EVENT,
    INTERRUPT_NONE,
    TRANSIENT,
    action_spec,
    actions_in_group,
    gif_files,
    validate,
)


class ActionTableTests(unittest.TestCase):
    def test_table_has_no_structural_problems(self):
        self.assertEqual(validate(), [])

    def test_base_layer_contains_idle_and_moving_only(self):
        base = [aid for aid, spec in ACTIONS.items() if spec.layer == BASE]
        self.assertEqual(sorted(base), ["idle", "moving"])

    def test_moving_loops_forever_but_idle_repeats_finitely(self):
        # moving 是持续播放的移动动画；idle 是 idle 池成员，播几轮后重抽。
        self.assertEqual(ACTIONS["moving"].loops, -1)
        self.assertGreater(ACTIONS["idle"].loops, 0)

    def test_every_action_has_a_readable_gif(self):
        for path in gif_files():
            self.assertTrue(path.endswith(".gif"), path)
            self.assertIn("/MainAemeath/", path)

    def test_groups_are_known_and_non_empty(self):
        for group in ("click", "idle", "wake"):
            self.assertTrue(actions_in_group(group), group)

    def test_unknown_action_returns_none(self):
        self.assertIsNone(action_spec("does_not_exist"))
        self.assertIsNone(action_spec(""))
        self.assertIsNone(action_spec(None))

    def test_resource_ids_are_unique(self):
        ids = [spec.resource_id for spec in ACTIONS.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_moving_can_only_enter_from_idle(self):
        self.assertEqual(ACTIONS["moving"].enter_from, ("idle",))


class IdleWeightTests(unittest.TestCase):
    """idle 池：普通待机为主体，特殊小概率穿插且不连续。"""

    def test_idle_pool_contains_idle_and_every_interaction_action(self):
        from config.actions import actions_in_group

        pool = set(actions_in_group("idle"))
        self.assertEqual(
            pool,
            {"idle", "wave", "happy", "jumping", "boring", "play", "tape",
             "spinning", "singing1", "singing2"},
        )

    def test_ordinary_and_special_partition(self):
        from config.actions import actions_in_group

        self.assertEqual(
            sorted(actions_in_group("ordinary_idle")), ["boring", "idle", "play"]
        )
        self.assertEqual(
            sorted(actions_in_group("special_idle")),
            ["happy", "jumping", "spinning", "tape", "wave"],
        )

    def test_hello_has_been_removed(self):
        from config.actions import ACTIONS

        self.assertNotIn("hello", ACTIONS)

    def test_base_idle_is_the_heaviest_single_action(self):
        from config.actions import ACTIONS, actions_in_group

        self.assertEqual(ACTIONS["idle"].weight, 60)
        for action_id in actions_in_group("idle"):
            if action_id != "idle":
                self.assertEqual(ACTIONS[action_id].weight, 5, action_id)

    def test_special_actions_are_rarer_than_ordinary(self):
        from config.actions import ACTIONS, actions_in_group

        ordinary = sum(ACTIONS[a].weight for a in actions_in_group("ordinary_idle"))
        special = sum(ACTIONS[a].weight for a in actions_in_group("special_idle"))
        self.assertGreater(ordinary, special)

    def test_base_idle_dominates_the_distribution(self):
        """基础 idle 是唯一的高权重动作，应占多数播放时间。"""
        import random

        from config.actions import pick_idle_action

        rng = random.Random(20261109)
        picks = [pick_idle_action(rng=rng) for _ in range(4000)]
        ratio = sum(1 for p in picks if p == "idle") / len(picks)
        self.assertGreater(ratio, 0.6)

    def test_special_idle_never_repeats_consecutively(self):
        import random

        from config.actions import is_special_idle, pick_idle_action

        rng = random.Random(7)
        last_special = False
        for _ in range(2000):
            current = pick_idle_action(last_was_special=last_special, rng=rng)
            if last_special:
                self.assertFalse(is_special_idle(current), "连续两次特殊待机")
            last_special = is_special_idle(current)

    def test_singing_actions_are_excluded_while_music_is_not_playing(self):
        import random

        from config.actions import actions_in_group, pick_idle_action

        singing = set(actions_in_group("singing"))
        rng = random.Random(11)
        for _ in range(600):
            self.assertNotIn(pick_idle_action(music_playing=False, rng=rng), singing)

    def test_singing_actions_enter_the_pool_while_music_plays(self):
        import random

        from config.actions import actions_in_group, pick_idle_action

        singing = set(actions_in_group("singing"))
        rng = random.Random(11)
        samples = [pick_idle_action(music_playing=True, rng=rng) for _ in range(6000)]
        self.assertTrue(any(s in singing for s in samples))

    def test_idle_can_repeat_itself(self):
        """抽取完全由权重决定，允许连续抽到同一个动作（基础 idle 可循环）。"""
        import random

        from config.actions import pick_idle_action

        rng = random.Random(5)
        picks = [pick_idle_action(rng=rng) for _ in range(400)]
        repeats = sum(1 for a, b in zip(picks, picks[1:]) if a == b)
        self.assertGreater(repeats, 0, "允许抽到同一动作，否则基础 idle 无法循环")

    def test_pick_idle_action_only_returns_known_actions(self):
        import random

        from config.actions import ACTIONS, pick_idle_action

        rng = random.Random(7)
        for _ in range(500):
            self.assertIn(pick_idle_action(rng=rng), ACTIONS)


class TransitionMatrixTests(unittest.TestCase):
    def setUp(self):
        from lib.script.mainpet.state import TRANSITIONS

        self.transitions = TRANSITIONS

    def test_matrix_covers_every_layer_pair(self):
        layers = (BASE, ACTION, TRANSIENT)
        for source in layers:
            for target in layers:
                self.assertIn((source, target), self.transitions)

    def test_transient_cannot_drive_into_actions(self):
        self.assertEqual(self.transitions[(TRANSIENT, ACTION)], "never")
        self.assertEqual(self.transitions[(TRANSIENT, TRANSIENT)], "never")

    def test_base_can_become_action_by_its_own_interrupt_rule(self):
        """离开 base 层要按当前动作的 interrupt 判定，不能无条件放行。"""
        self.assertEqual(self.transitions[(BASE, ACTION)], "if_interruptible")
        self.assertEqual(self.transitions[(BASE, TRANSIENT)], "if_interruptible")

    def test_action_to_base_is_always_allowed(self):
        self.assertEqual(self.transitions[(ACTION, BASE)], "always")


class TransitionDecisionTests(unittest.TestCase):
    """直接驱动 `StateMachine._can_transition` 判定表。"""

    @staticmethod
    def _machine(current_state: str):
        from lib.script.mainpet.state import StateMachine

        machine = StateMachine.__new__(StateMachine)
        machine._current_state = current_state
        return machine

    def test_action_blocks_non_event_request_until_finished(self):
        machine = self._machine("wave")
        target = action_spec("happy")
        self.assertFalse(machine._can_transition(action_spec("wave"), target, by_event=False))

    def test_event_request_can_interrupt_interruptible_action(self):
        from dataclasses import replace

        machine = self._machine("wave")
        event_only = replace(action_spec("wave"), interrupt=INTERRUPT_EVENT)
        self.assertTrue(machine._can_transition(event_only, action_spec("happy"), by_event=True))

    def test_non_interruptible_action_rejects_every_request(self):
        from dataclasses import replace

        machine = self._machine("tape")
        strict = replace(action_spec("tape"), interrupt=INTERRUPT_NONE)
        self.assertFalse(machine._can_transition(strict, action_spec("happy"), by_event=True))
        self.assertFalse(machine._can_transition(strict, action_spec("happy"), by_event=False))

    def test_returning_to_base_is_never_blocked(self):
        """播完或被事件打断回到 idle 不受当前动作 interrupt 限制。"""
        from dataclasses import replace

        machine = self._machine("tape")
        strict = replace(action_spec("tape"), interrupt=INTERRUPT_NONE)
        self.assertTrue(machine._can_transition(strict, action_spec("idle"), by_event=True))
        self.assertTrue(machine._can_transition(strict, action_spec("idle"), by_event=False))

    def test_always_interrupt_action_yields_to_any_request(self):
        from dataclasses import replace

        machine = self._machine("wave")
        permissive = replace(action_spec("wave"), interrupt=INTERRUPT_ANY)
        self.assertTrue(machine._can_transition(permissive, action_spec("happy"), by_event=False))

    def test_moving_can_start_from_any_waiting_state(self):
        """待机态（idle 池里的任何动作）都能转入移动。"""
        for action_id in ("idle", "boring", "tape", "spinning", "play", "wave"):
            machine = self._machine(action_id)
            self.assertTrue(
                machine._can_transition(
                    action_spec(action_id), action_spec("moving"), by_event=False
                ),
                action_id,
            )

    def test_moving_cannot_start_while_already_moving(self):
        machine = self._machine("moving")
        self.assertTrue(
            machine._can_transition(action_spec("moving"), action_spec("moving"), by_event=True)
        )

    def test_idle_always_yields_plain_requests(self):
        """回归：移动运行时用 by_event=False 请求 moving，idle 必须放行。"""
        machine = self._machine("idle")
        self.assertTrue(machine._can_transition(action_spec("idle"), action_spec("moving"), by_event=False))
        self.assertTrue(machine._can_transition(action_spec("idle"), action_spec("wave"), by_event=False))

    def test_moving_does_not_yield_plain_requests(self):
        """moving 属于常驻层但不是等待态，仍按 event 级打断处理。"""
        machine = self._machine("moving")
        self.assertFalse(machine._can_transition(action_spec("moving"), action_spec("wave"), by_event=False))
        self.assertTrue(machine._can_transition(action_spec("moving"), action_spec("wave"), by_event=True))

    def test_moving_returns_to_idle_without_event(self):
        """回归：移动运行时用 by_event=False 结束移动，必须能回 idle。

        否则状态机停在 moving、移动控制器已停止，表现为原地播放移动动画。
        """
        machine = self._machine("moving")
        self.assertTrue(machine._can_transition(action_spec("moving"), action_spec("idle"), by_event=False))

    def test_action_layer_always_returns_to_base(self):
        for action_id in ("wave", "boring", "tape"):
            machine = self._machine(action_id)
            self.assertTrue(
                machine._can_transition(action_spec(action_id), action_spec("idle"), by_event=False),
                action_id,
            )


if __name__ == "__main__":
    unittest.main()
