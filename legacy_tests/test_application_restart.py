import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.app.restart import (
    _is_process_running,
    build_restart_command,
    launch_current_application,
    run_restart_helper,
)


class ApplicationRestartTests(unittest.TestCase):
    def test_process_probe_is_read_only_and_reports_liveness(self):
        self.assertTrue(_is_process_running(os.getpid()))
        self.assertFalse(_is_process_running(2147483647))

    def test_source_restart_uses_stable_desktop_pet_entry(self):
        command = build_restart_command(
            ["ignored-entry.py", "--example"],
            executable="python-test",
            frozen=False,
        )

        self.assertEqual(command[0], "python-test")
        self.assertEqual(Path(command[1]).as_posix().split("/")[-3:], ["lib", "core", "qt_desktop_pet.py"])
        self.assertEqual(command[2:], ["--example"])

    def test_frozen_restart_reuses_executable_and_arguments(self):
        self.assertEqual(
            build_restart_command(
                ["pet.exe", "--example"],
                executable="C:/release/pet.exe",
                frozen=True,
            ),
            ["C:/release/pet.exe", "--example"],
        )

    @patch("lib.script.app.restart._write_restart_trace")
    @patch("lib.script.app.restart.subprocess.Popen")
    def test_launch_is_detached_from_current_process(self, popen, _trace):
        launch_current_application()

        command, kwargs = popen.call_args
        command = command[0]
        self.assertIn("--fsv-restart-helper", command)
        restarted = json.loads(command[-1])
        self.assertEqual(restarted[1].replace('\\', '/').split('/')[-3:], ["lib", "core", "qt_desktop_pet.py"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["close_fds"])
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])

    @patch("lib.script.app.restart._write_restart_trace")
    @patch("lib.script.app.restart.subprocess.Popen")
    def test_frozen_launch_uses_executable_as_helper_entry(self, popen, _trace):
        with (
            patch("lib.script.app.restart.sys.frozen", True, create=True),
            patch("lib.script.app.restart.sys.executable", "C:/release/pet.exe"),
        ):
            launch_current_application()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "C:/release/pet.exe")
        self.assertEqual(command[1], "--fsv-restart-helper")
        self.assertNotIn("qt_desktop_pet.py", command[:3])

    @patch("lib.script.app.restart._write_restart_trace")
    @patch("lib.script.app.restart.subprocess.Popen")
    @patch("lib.script.app.restart.time.sleep")
    @patch("lib.script.app.restart._is_process_running", side_effect=[True, False])
    def test_helper_waits_for_parent_before_launching(self, _running, sleep, popen, _trace):
        popen.return_value.wait.side_effect = subprocess.TimeoutExpired("app", 3)
        self.assertEqual(run_restart_helper(123, ["python", "app.py"], max_wait=1), 0)
        sleep.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["python", "app.py"])

    def test_restart_event_uses_normal_exit_request(self):
        from lib.script.main import ApplicationState

        state = ApplicationState.__new__(ApplicationState)
        state._restart_requested = False
        state._restart_helper_started = False
        exit_codes = []
        state.request_exit = exit_codes.append
        event = Event(EventType.APP_QUIT, {"restart": True})

        with patch("lib.script.main._launch_current_application") as launch:
            state._on_app_quit(event)

        self.assertTrue(state.restart_requested)
        launch.assert_called_once_with()
        self.assertEqual(exit_codes, [0])
        self.assertTrue(event.handled)

    def test_restart_helper_failure_keeps_current_instance_running(self):
        from lib.script.main import ApplicationState

        state = ApplicationState.__new__(ApplicationState)
        state._restart_requested = False
        state._restart_helper_started = False
        state.request_exit = Mock()

        with patch("lib.script.main._launch_current_application", side_effect=OSError("blocked")):
            self.assertFalse(state.request_restart())

        self.assertFalse(state.restart_requested)
        state.request_exit.assert_not_called()

    def test_main_releases_single_instance_lock_without_duplicate_launch(self):
        from lib.script import main as app_main

        calls = []
        state = Mock()
        state.run_event_loop.return_value = 0
        state.finalize_after_event_loop.return_value = 0

        with (
            patch.object(app_main, "_new_acquire_single_instance_lock", return_value=True),
            patch.object(app_main, "ApplicationState", return_value=state),
            patch.object(app_main, "_new_release_single_instance_lock", side_effect=lambda: calls.append("release")),
            patch.object(app_main, "_launch_current_application") as launch,
        ):
            with self.assertRaises(SystemExit) as raised:
                app_main.main()

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(calls, ["release"])
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
