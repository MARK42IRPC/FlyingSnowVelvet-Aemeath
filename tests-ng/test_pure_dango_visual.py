"""Presentation regressions for the full-height selection and series result UI."""
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from lib.core.render.visuals.commands import ClipPop, ClipPush, EllipseCommand, RectCommand, SpriteCommand, TextCommand
from lib.core.render.visuals.resources import RasterFrame
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P, roster as R, constants as C
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase, RoundResult


class DangoPolishTests(unittest.TestCase):
    def test_new_fighters_keep_identity_and_distinct_sun_and_frost_skills(self):
        entries = [R.find_entry(character_id) for character_id in ("xigelika", "feixue")]
        self.assertTrue(all(entry is not None for entry in entries))
        for entry, name, tag in zip(entries, ("西格莉卡", "绯雪"), ("日灵团子", "霜渐团子")):
            expected_hp = {"xigelika": 185, "feixue": 165}[entry.character_id]
            self.assertEqual((entry.name, entry.tag, entry.max_hp), (name, tag, expected_hp))
            self.assertEqual((entry.meters, entry.reactions, entry.collision_rules), ((), (), ()))
        self.assertEqual(entries[0].sun_spirit.slots, 4)
        self.assertIsNone(entries[0].frost_slash)
        self.assertTrue(entries[0].skill)
        self.assertIsNone(entries[1].sun_spirit)
        self.assertEqual((entries[1].frost_slash.energy_slots, entries[1].frost_slash.interval,
                          entries[1].frost_slash.damage), (4, 3, 30))
        self.assertTrue(entries[1].skill)
        characters = tuple(P.CharacterView(side, entry.character_id, entry.name, hp=entry.max_hp,
            max_hp=entry.max_hp, meters=entry.meters) for side, entry in zip(("left", "right"), entries))
        for phase in (MatchPhase.SELECT, MatchPhase.FIGHT):
            snapshot = P.ScreenSnapshot(phase=phase, left_id="xigelika", right_id="feixue", characters=characters)
            batch, _ = P.paint_snapshot(snapshot, 1080, 720)
            texts = [c.text for c in batch.commands if isinstance(c, TextCommand)]
            for expected in ("西格莉卡", "绯雪", "日灵团子", "霜渐团子"):
                self.assertIn(expected, texts)
            joined = "".join(texts)
            self.assertIn("185", joined)
            self.assertIn("165", joined)
            for fake in ("技能描述", "星能", "星爆", "虚空之手", "触发概率", "碰撞技能"):
                self.assertNotIn(fake, joined)
            if phase is MatchPhase.SELECT:
                self.assertIn(entries[1].skill.replace(" ", ""), joined.replace(" ", ""))
        fight, _ = P.paint_snapshot(P.ScreenSnapshot(phase=MatchPhase.FIGHT, characters=characters), 1080, 720)
        texts = [c.text for c in fight.commands if isinstance(c, TextCommand)]
        for label in ("日灵能量", "霜渐能量", "环斩冷却", "3.0秒",
                      "3秒环斩30 · 命中减速50%", "落空+1能量 · 碰撞耗1增伤2"):
            self.assertIn(label, texts)
        for character in characters[1:]:
            commands = P._character_card(P.Layout().left_panel, character, picked=False,
                accent=C.C_SIDE_A, layout=P.Layout())
            self.assertTrue(any(getattr(c, "fill", None) == C.C_BG_TOP.with_alpha(135) for c in commands),
                            "Feixue's designed frost skill must include its rule panel")

    def test_selection_has_one_primary_action_and_fills_the_lower_canvas(self):
        for width, height in ((1080, 720), (810, 540)):
            snapshot = P.ScreenSnapshot(left_id="daniya", right_id="amis", picked_side="left", bet_side="left", bet_amount=5, can_start=True)
            batch, hit = P.paint_snapshot(snapshot, width, height)
            starts = [button for button in hit.buttons if button.action == P.ACTION_FIGHT and not button.value]
            self.assertEqual(len(starts), 1)
            cards = [button.rect for button in hit.buttons if button.value in ("left", "right")]
            self.assertEqual(len(cards), 2)
            self.assertGreater(cards[0].width, width * .25)
            left_metrics = P._fighter_plate_metrics(cards[0])
            self.assertGreater(left_metrics["head"].width, width * .09)
            geometry = P.roster_grid_layout(width, height)
            self.assertGreater(geometry.grid.height, height * .30)
            self.assertEqual((geometry.columns, geometry.rows, geometry.capacity), (12, 3, 36))
            self.assertLess(cards[0].bottom, geometry.bay.y)
            lower_text = [command for command in batch.commands if isinstance(command, TextCommand) and command.rect.y > height * .8]
            self.assertTrue(lower_text, "the roster and selection status must use the lower canvas")
            targets = [c for c in batch.commands if isinstance(c, TextCommand) and c.text == "达妮娅"]
            self.assertTrue(any(starts[0].rect.x <= c.rect.x < starts[0].rect.right for c in targets))

    def test_series_countdown_updates_caption_footer_and_progress_together(self):
        result = RoundResult(winner="left", bet_side="left", bet_amount=5, bet_won=True)
        widths = []
        for remaining, caption in ((3.0, "下一轮 3 秒"), (1.2, "下一轮 2 秒"), (.2, "下一轮 1 秒")):
            snapshot = P.ScreenSnapshot(phase=MatchPhase.RESULT, result=result, series_label="三局两胜", auto_advance_seconds=remaining)
            batch, hit = P.paint_snapshot(snapshot, 1080, 720)
            texts = [c.text for c in batch.commands if isinstance(c, TextCommand)]
            self.assertIn(caption, texts)
            self.assertTrue(any(f"{caption[-3]} 秒后自动进入下一轮" in text for text in texts))
            progress = [c.rect for c in batch.commands if isinstance(c, RectCommand) and c.z == 22 and c.fill == C.C_ACCENT and c.rect.height <= 4.01]
            self.assertEqual(len(progress), 1)
            widths.append(progress[0].width)
            self.assertEqual(len([b for b in hit.buttons if b.action == P.ACTION_NEXT]), 1)
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_completed_series_keeps_return_action_and_has_no_running_progress(self):
        snapshot = P.ScreenSnapshot(phase=MatchPhase.RESULT, result=RoundResult(), series_over=True)
        batch, _ = P.paint_snapshot(snapshot, 1080, 720)
        texts = [c.text for c in batch.commands if isinstance(c, TextCommand)]
        self.assertIn("返回选人", texts)
        self.assertFalse(any(text.startswith("下一轮 ") for text in texts))
        self.assertFalse(any(isinstance(c, RectCommand) and c.z == 22 and c.fill == C.C_ACCENT
                             and 3.9 < c.rect.height < 4.1 for c in batch.commands))

    def test_series_title_uses_total_winner_even_when_final_round_is_a_draw_or_loss(self):
        for final_winner, draw, series_left, series_right, title in (
            ("right", False, 2, 1, "爱弥斯 赢得系列赛"),
            ("", True, 1, 0, "爱弥斯 赢得系列赛"),
            ("", True, 1, 1, "系列赛平局"),
        ):
            result = RoundResult(winner=final_winner, draw=draw)
            snapshot = P.ScreenSnapshot(phase=MatchPhase.RESULT, result=result,
                left_id="amis", right_id="daniya", series_label="死战三局", series_over=True,
                series_left=series_left, series_right=series_right)
            batch, _ = P.paint_snapshot(snapshot, 1080, 720)
            texts = [c.text for c in batch.commands if isinstance(c, TextCommand)]
            self.assertIn(title, texts)
            self.assertTrue(any("本轮平局" in text if draw else "本轮 达妮娅 获胜" in text for text in texts))

    def test_skill_summary_uses_roster_meters_and_collision_rules(self):
        custom = R.ChargeSpec("starlight", "自定义能量", 2, damage=17, fill_meter="starburst")
        views = (P.CharacterView("left", "amis", "爱弥斯", meters=(custom,)), P.CharacterView("right", "daniya", "达妮娅"))
        batch, _ = P.paint_snapshot(P.ScreenSnapshot(phase=MatchPhase.FIGHT, characters=views), 1080, 720)
        texts = [c.text for c in batch.commands if isinstance(c, TextCommand)]
        self.assertIn("2 自定义能量 → 激光 17 伤害", texts)
        self.assertIn("虚空之手", texts)
        self.assertIn("触发概率", texts)
        self.assertIn("1~4", texts)

    def test_collision_chance_keeps_fractional_percent_without_changing_card_geometry(self):
        entry = R.find_entry("daniya")
        views = (P.CharacterView("left", "daniya", "达妮娅"),
                 P.CharacterView("right", "feixue", "绯雪"))
        snapshot = P.ScreenSnapshot(phase=MatchPhase.FIGHT, left_id="daniya", right_id="feixue", characters=views)
        for width, height in ((1080, 720), (810, 540)):
            previous = None
            for chance, label in ((.5, "50%"), (.505, "50.5%"), (.5025, "50.25%"), (0, "0%"), (1, "100%")):
                rules = (replace(entry.collision_rules[0], chance=chance),)
                entries = tuple(replace(item, collision_rules=rules) if item.character_id == "daniya" else item
                                for item in R.ROSTER)
                with self.subTest(size=(width, height), chance=chance), patch.object(R, "ROSTER", entries):
                    batch, _ = P.paint_snapshot(snapshot, width, height)
                    texts = [command for command in batch.commands if isinstance(command, TextCommand)]
                    probability = next(command for command in texts if command.text == label)
                    self.assertIn("触发概率", [command.text for command in texts])
                    self.assertEqual([command.text for command in texts if command.text.endswith("%")
                                      and command.text[:-1].replace(".", "", 1).isdigit()], [label])
                    if previous is not None:
                        self.assertEqual(probability.rect, previous.rect)
                        self.assertEqual(probability.font, previous.font)
                    previous = probability

    def test_every_roster_entry_is_reachable_once_across_pages(self):
        for count in (2, 24, 48, 97):
            entries = tuple(replace(R.ROSTER[index % len(R.ROSTER)], character_id=f"fighter_{index}", name=f"团子{index + 1:02}") for index in range(count))
            with patch.object(R, "ROSTER", entries):
                for width, height in ((1080, 720), (810, 540)):
                    pages = P.roster_page_count(width=width, height=height)
                    found = []
                    for page in range(pages):
                        snapshot = P.ScreenSnapshot(left_id=entries[0].character_id, right_id=entries[1].character_id, picked_side="right", roster_page=page)
                        _, hit = P.paint_snapshot(snapshot, width, height)
                        picks = [b for b in hit.buttons if b.value.startswith("pick:")]
                        found.extend(b.value.split(":", 2)[2] for b in picks)
                        self.assertTrue(all(b.value.startswith("pick:right:") for b in picks))
                        self.assertLessEqual(len(picks), 36)
                        for button in picks:
                            self.assertAlmostEqual(button.rect.width, button.rect.height)
                            self.assertLessEqual(button.rect.right, width - 20.0)
                            self.assertLessEqual(button.rect.bottom, height - 20.0)
                        nav = {b.value for b in hit.buttons if b.value.startswith("roster_page:")}
                        expected = {f"roster_page:{page - 1}"} if page else set()
                        if page + 1 < pages:
                            expected.add(f"roster_page:{page + 1}")
                        self.assertEqual(nav, expected)
                    self.assertEqual(found, [entry.character_id for entry in entries])

    def test_watermark_is_larger_and_has_no_circular_backing(self):
        frame = RasterFrame(8, 8, bytes((255, 255, 255, 100)) * 64, 100)
        source = P.FrameSource("watermark-test", 1, frame)
        arena = P.Layout().arena.rect
        commands = []
        P._paint_watermark(commands, arena, source)
        self.assertFalse(any(isinstance(c, EllipseCommand) for c in commands))
        self.assertIsInstance(commands[0], ClipPush)
        self.assertIsInstance(commands[-1], ClipPop)
        sprite = next(c for c in commands if isinstance(c, SpriteCommand))
        self.assertAlmostEqual(sprite.target_size.width / arena.width, .86)
        self.assertEqual(sprite.alpha, C.ARENA_WATERMARK_ALPHA)
