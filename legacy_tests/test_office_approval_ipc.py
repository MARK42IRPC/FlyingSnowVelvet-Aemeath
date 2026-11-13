import tempfile
import unittest
from pathlib import Path

from lib.script.office.approval import TaskApprovalPolicy
from lib.script.office.ipc import OfficeFileIpc


class OfficeApprovalIpcTests(unittest.TestCase):
    def test_allow_task_auto_allows_only_same_task(self):
        policy = TaskApprovalPolicy()
        self.assertEqual(policy.resolve("one", "allow_task"), "allowed-once")
        self.assertTrue(policy.should_auto_allow("one"))
        self.assertFalse(policy.should_auto_allow("two"))
        self.assertEqual(policy.resolve("two", "reject"), "rejected")

    def test_atomic_state_and_command_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir))
            ipc.publish({"mode": "office"}, [{"id": "task-1"}])
            command_path = ipc.submit("followup", task_id="task-1", text="继续")

            self.assertEqual(ipc.read_state()["mode"], "office")
            self.assertEqual(ipc.read_tasks()[0]["id"], "task-1")
            commands = ipc.consume()

            self.assertFalse(command_path.exists())
            self.assertEqual(commands[0]["command"], "followup")
            self.assertEqual(commands[0]["data"]["text"], "继续")
            self.assertEqual(ipc.consume(), [])


if __name__ == "__main__":
    unittest.main()
