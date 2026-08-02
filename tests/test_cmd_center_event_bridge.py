import subprocess
import unittest
from unittest.mock import patch

from lib.core.cmd_center import CmdCenter
from lib.core.event.center import Event, EventCenter, EventType
from tests.timing_fakes import FakePump


class _FakeComputeHub:
    def __init__(self):
        self.calls = []

    def submit_io(self, callback, *args):
        self.calls.append((callback, args))


class CmdCenterEventBridgeTests(unittest.TestCase):
    def setUp(self):
        self.event_center = EventCenter(
            pump_factory=lambda callback: FakePump(callback),
        )
        self.compute_hub = _FakeComputeHub()
        self.center = CmdCenter(
            event_center=self.event_center,
            compute_hub=self.compute_hub,
        )
        self.addCleanup(self.center.cleanup)

    def test_input_command_is_submitted_to_shared_compute_hub(self):
        self.event_center.publish(Event(EventType.INPUT_COMMAND, {
            "text": "  echo hello  ",
        }))

        self.assertEqual(len(self.compute_hub.calls), 1)
        callback, args = self.compute_hub.calls[0]
        self.assertEqual(callback, self.center._run_command)
        self.assertEqual(args, ("echo hello",))

    def test_background_result_uses_event_center_without_qt_signal(self):
        information = []
        self.event_center.subscribe(
            EventType.INFORMATION,
            lambda event: information.append(event.data),
        )
        completed = subprocess.CompletedProcess(
            args="echo hello",
            returncode=0,
            stdout="命令完成".encode("gbk"),
            stderr=b"",
        )

        with patch("lib.core.cmd_center.subprocess.run", return_value=completed):
            self.center._run_command("echo hello")

        self.assertEqual(len(information), 1)
        self.assertEqual(information[0]["text"], "命令完成")
        self.assertEqual(information[0]["align"], "left")

    def test_cleanup_is_idempotent_and_unsubscribes(self):
        self.center.cleanup()
        self.center.cleanup()

        self.event_center.publish(Event(EventType.INPUT_COMMAND, {
            "text": "echo ignored",
        }))

        self.assertEqual(self.compute_hub.calls, [])


if __name__ == "__main__":
    unittest.main()
