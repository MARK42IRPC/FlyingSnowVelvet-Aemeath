import unittest
from unittest.mock import Mock

from lib.script.ui.cmd_window import CmdWindow


class CmdWindowProcessLifecycleTests(unittest.TestCase):
    def test_terminate_process_requests_graceful_exit_first(self):
        process = Mock()
        process.poll.return_value = None

        CmdWindow._terminate_process(process)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1.0)
        process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
