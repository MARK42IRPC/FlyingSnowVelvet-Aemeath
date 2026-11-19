"""Widget BGM phase requests and audible feedback for real UI input."""

from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QTest

from lib.core.event.center import EventType
from lib.core.render.visuals.commands import TextCommand
from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import roster as R
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget import DangoShowdownWidget

PACKAGE = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown"
SELECT_BGM = ("团子准备中", "鸣潮先约电台")
FIGHT_BGM = ("团子出击", "鸣潮先约电台")
SELECT_REF = "netease:3442263555"
FIGHT_REF = "netease:3442262625"


class DangoAudioInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config

        init_font_config()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dango-audio-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8")))
        self.widget = DangoShowdownWidget(GameContext(manifest, PACKAGE, root / "data", root / "cache"))
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget.shutdown)
        self.widget.resize(810, 540)
        self.widget.show()
        self.widget._timer.stop()
        self.widget._event_center = Mock()
        self.real_sfx = self.widget._sfx
        self.widget._sfx = Mock()
        self.bgm = Mock()
        self.widget.set_bgm_callback(self.bgm)
        self.match = self.widget._match

    def ui_keys(self):
        return [call.args[0] for call in self.widget._sfx.play.call_args_list
                if call.args and str(call.args[0]).startswith("ui_")]

    def click_rect(self, rect):
        x, y = self.widget._transform().to_canvas(rect.x + rect.width / 2, rect.y + rect.height / 2)
        QTest.mouseClick(self.widget, Qt.LeftButton, pos=QPoint(round(x), round(y)))

    def click_value(self, value):
        self.widget.grab()
        button = next((hit for hit in self.widget._hit_map.buttons if hit.value == value), None)
        self.assertIsNotNone(button, value)
        self.click_rect(button.rect)

    def click_next(self):
        self.widget.grab()
        button = next(hit for hit in self.widget._hit_map.buttons if hit.action == "next")
        self.click_rect(button.rect)

    def start(self, format_id="bo3"):
        self.assertTrue(self.match.choose_format(format_id))
        self.click_value("left")
        self.click_value("amount:5")
        QTest.keyClick(self.widget, Qt.Key_Return)
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)

    def finish(self, winner="left"):
        self.match.countdown = 0.0
        owner = self.match.dango_by_side(winner)
        target = self.match.right if winner == "left" else self.match.left
        self.match._skill_damage(owner, target, target.hp)
        self.match.update(1 / 60)
        self.assertEqual(self.match.phase, MatchPhase.RESULT)

    def test_phase_bgm_is_requested_once_and_kept_through_the_series(self):
        self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)
        for _ in range(4):
            self.widget._on_tick()
            self.widget._snapshot()
            self.widget.grab()
        self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)
        self.start()
        self.widget._on_tick()
        self.assertEqual([call.args for call in self.bgm.call_args_list], [SELECT_BGM, FIGHT_BGM])
        self.finish()
        self.match.result_reveal = 0.0
        self.widget._on_tick()
        self.click_next()
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.widget._on_tick()
        self.assertEqual([call.args for call in self.bgm.call_args_list], [SELECT_BGM, FIGHT_BGM])
        self.finish()
        self.assertTrue(self.match.series_over)
        self.match.result_reveal = 0.0
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_R)
        self.widget._on_tick()
        self.assertEqual(self.match.phase, MatchPhase.SELECT)
        self.assertEqual(self.ui_keys()[before:], ["ui_back"])
        self.assertEqual([call.args for call in self.bgm.call_args_list], [SELECT_BGM, FIGHT_BGM, SELECT_BGM])
        self.assertEqual([call.kwargs["track_ref"] for call in self.bgm.call_args_list], [SELECT_REF, FIGHT_REF, SELECT_REF])

    def test_random_and_repeated_valid_choices_do_not_restart_selection_bgm(self):
        for value in ("left", "left", "amount:5", "amount:5"):
            before = len(self.ui_keys())
            self.click_value(value)
            self.assertEqual(self.ui_keys()[before:], ["ui_click"])
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_R)
        self.assertEqual(self.ui_keys()[before:], ["ui_click"])
        self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)

    def test_deactivate_shutdown_and_close_detach_the_bgm_callback(self):
        for stop in (self.widget.deactivate, self.widget.shutdown, self.widget.close):
            with self.subTest(stop=stop.__name__):
                self.widget.reset_game(start_running=False)
                self.widget._timer.stop()
                self.bgm.reset_mock()
                self.widget.set_bgm_callback(self.bgm)
                self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)
                stop()
                self.match.randomize()
                self.widget._start_match()
                self.widget._on_tick()
                self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)
                self.assertFalse(self.widget._timer.isActive())

    def test_mouse_buttons_and_keyboard_actions_have_one_ui_cue_each(self):
        future = tuple(R.DangoEntry(f"audio_{index}", f"试听团子{index}") for index in range(40))
        with patch.object(R, "ROSTER", (*R.ROSTER, *future)):
            for value in ("left", "amount:5", "format:bo3", "roster_page:1", "pick:left:audio_39"):
                before = len(self.ui_keys())
                self.click_value(value)
                self.assertEqual(self.ui_keys()[before:], ["ui_click"], value)
            before = len(self.ui_keys())
            QTest.keyClick(self.widget, Qt.Key_PageUp)
            self.assertEqual(self.ui_keys()[before:], ["ui_click"])
        self.widget.set_fullscreen_callback(Mock())
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_F11)
        self.assertEqual(self.ui_keys()[before:], ["ui_click"])
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_Return)
        self.assertEqual(self.ui_keys()[before:], ["ui_confirm"])
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_P)
        self.assertEqual(self.ui_keys()[before:], ["ui_click"])
        closed = Mock()
        self.widget.set_close_callback(closed)
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_Escape)
        closed.assert_called_once_with()
        self.assertEqual(self.ui_keys()[before:], ["ui_back"])

    def test_disabled_blank_boundary_and_auto_repeat_inputs_are_silent(self):
        batch, _ = P.paint_snapshot(self.widget._snapshot(), 1080, 720)
        disabled = next(command for command in batch.commands
                        if isinstance(command, TextCommand) and command.text == "先选押注方")
        self.click_rect(disabled.rect)
        QTest.mouseClick(self.widget, Qt.LeftButton, pos=QPoint(1, self.widget.height() - 1))
        QTest.keyClick(self.widget, Qt.Key_Return)
        QTest.keyClick(self.widget, Qt.Key_PageUp)
        for key in (Qt.Key_1, Qt.Key_4, Qt.Key_F2, Qt.Key_R, Qt.Key_Escape):
            self.app.sendEvent(self.widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, "", True, 1))
        self.assertEqual(self.ui_keys(), [])
        self.assertFalse(self.match.can_start)
        self.bgm.assert_called_once_with(*SELECT_BGM, track_ref=SELECT_REF)

    def test_manual_next_confirms_but_automatic_next_does_not(self):
        self.start("bo5")
        self.finish()
        before = len(self.ui_keys())
        QTest.keyClick(self.widget, Qt.Key_R)
        self.assertEqual(self.match.phase, MatchPhase.RESULT)
        self.assertEqual(self.ui_keys()[before:], [])
        self.match.result_reveal = 0.0
        self.click_next()
        self.assertEqual(self.ui_keys()[before:], ["ui_confirm"])
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.finish("right")
        self.match.result_reveal = 0.0
        before = len(self.ui_keys())
        self.widget._auto_advance_tick(3.0)
        self.assertEqual(self.match.phase, MatchPhase.FIGHT)
        self.assertEqual(self.ui_keys()[before:], [])
        self.assertEqual([call.args for call in self.bgm.call_args_list], [SELECT_BGM, FIGHT_BGM])

    def test_real_click_sound_uses_shared_game_sfx_request_and_valid_pcm(self):
        self.widget._sfx = self.real_sfx
        self.click_value("left")
        requests = [call.args[0] for call in self.widget._event_center.publish.call_args_list
                    if call.args[0].type == EventType.SOUND_REQUEST]
        self.assertEqual(len(requests), 1)
        data = requests[0].data
        self.assertEqual(data["audio_type"], "game_sfx")
        self.assertTrue(data["interruptible"])
        self.assertEqual(Path(data["source"]).name, "lahai_move.wav")
        self.assertEqual(Path(data["source"]).parent, Path(self.temp.name) / "cache")
        self.assertEqual(data["volume_gain"], 0.24)
        self.assertFalse(list((Path(self.temp.name) / "cache").glob("dango_*.wav")))
        with wave.open(data["source"], "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
            self.assertGreater(wav.getnframes(), 0)
            self.assertLess(wav.getnframes() / wav.getframerate(), 0.3)
            self.assertTrue(any(wav.readframes(wav.getnframes())))


if __name__ == "__main__":
    unittest.main()
