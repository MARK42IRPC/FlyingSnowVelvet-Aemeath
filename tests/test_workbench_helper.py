from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.app import workbench_helper


class _Process:
    def __init__(self) -> None:
        self.return_code = None

    def poll(self):
        return self.return_code


class WorkbenchHelperLauncherTests(unittest.TestCase):
    def setUp(self):
        self.previous_process = workbench_helper._helper_process
        workbench_helper._helper_process = None

    def tearDown(self):
        workbench_helper._helper_process = self.previous_process

    def test_live_helper_is_reused_and_receives_latest_page_request(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            workbench_helper,
            "_helper_request_path",
            return_value=Path(tmpdir) / "request.json",
        ), patch.object(
            workbench_helper.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            self.assertTrue(workbench_helper.launch_workbench_helper("office"))
            first_request = workbench_helper.read_workbench_helper_request()
            self.assertEqual(first_request["page_id"], "office")

            self.assertTrue(workbench_helper.launch_workbench_helper("overview"))
            second_request = workbench_helper.read_workbench_helper_request()

        self.assertEqual(popen.call_count, 1)
        self.assertNotEqual(first_request["request_id"], second_request["request_id"])
        self.assertEqual(second_request["page_id"], "overview")
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["--initial-page", "office"])

    def test_exited_helper_is_replaced_and_invalid_page_falls_back(self):
        first = _Process()
        second = _Process()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            workbench_helper,
            "_helper_request_path",
            return_value=Path(tmpdir) / "request.json",
        ), patch.object(
            workbench_helper.subprocess,
            "Popen",
            side_effect=[first, second],
        ) as popen:
            self.assertTrue(workbench_helper.launch_workbench_helper("bad/page"))
            first.return_code = 0
            self.assertTrue(workbench_helper.launch_workbench_helper("office"))

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args_list[0].args[0][-1], "overview")
        self.assertIs(workbench_helper._helper_process, second)

    def test_game_request_is_normalized_and_published_for_live_helper(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            workbench_helper,
            "_helper_request_path",
            return_value=Path(tmpdir) / "request.json",
        ), patch.object(
            workbench_helper.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            self.assertTrue(workbench_helper.launch_workbench_helper(
                "game_manager",
                game_id="lahai_tetris",
                game_action="open",
            ))
            first = workbench_helper.read_workbench_helper_request()
            self.assertTrue(workbench_helper.launch_workbench_helper(
                "game_manager",
                game_action="close_manager",
            ))
            second = workbench_helper.read_workbench_helper_request()

        self.assertEqual(popen.call_count, 1)
        self.assertEqual(first["game_id"], "lahai_tetris")
        self.assertEqual(first["game_action"], "open")
        self.assertEqual(second["game_id"], "")
        self.assertEqual(second["game_action"], "close_manager")
        self.assertNotEqual(first["request_id"], second["request_id"])

    def test_invalid_game_fields_and_old_protocol_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            workbench_helper,
            "_helper_request_path",
            return_value=Path(tmpdir) / "request.json",
        ):
            payload = workbench_helper._publish_helper_request(
                "game_manager",
                game_id="../escape",
                game_action="launch",
            )
            self.assertEqual(payload["game_id"], "")
            self.assertEqual(payload["game_action"], "")

            path = workbench_helper._helper_request_path()
            path.write_text(
                '{"version":1,"request_id":"old","page_id":"office"}',
                encoding="utf-8",
            )
            self.assertEqual(workbench_helper.read_workbench_helper_request(), {})


if __name__ == "__main__":
    unittest.main()
