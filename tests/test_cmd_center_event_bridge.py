import subprocess
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from lib.core.cmd_center import CmdCenter
from lib.core.event.center import Event, EventCenter, EventType
from tests.timing_fakes import FakePump


class _FakeComputeHub:
    def __init__(self):
        self.calls = []

    def submit_io(self, callback, *args):
        self.calls.append((callback, args))

    def submit_interactive_io(self, callback, *args):
        self.calls.append((callback, args))
        future = Future()
        try:
            future.set_result(callback(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


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

    def test_backend_hash_command_persists_directx_and_requests_restart(self):
        saved = []
        self.center._backend_saver = saved.append
        information = []
        quits = []
        self.event_center.subscribe(
            EventType.INFORMATION,
            lambda event: information.append(event.data),
        )
        self.event_center.subscribe(
            EventType.APP_QUIT,
            lambda event: quits.append(event.data),
        )

        with patch.dict('config.config.UI', {'render_backend': 'qt'}):
            self.event_center.publish(Event(EventType.INPUT_HASH, {
                'text': '后端 dx',
            }))

        self.assertEqual(saved, ['directx'])
        self.assertEqual(quits, [{
            'restart': True,
            'source': 'hash_backend_command',
            'render_backend': 'directx',
        }])
        self.assertIn('DX', information[-1]['text'])

    def test_backend_hash_command_rejects_invalid_target_without_restart(self):
        information = []
        quits = []
        self.event_center.subscribe(
            EventType.INFORMATION,
            lambda event: information.append(event.data),
        )
        self.event_center.subscribe(
            EventType.APP_QUIT,
            lambda event: quits.append(event.data),
        )

        self.event_center.publish(Event(EventType.INPUT_HASH, {
            'text': '后端 vulkan',
        }))

        self.assertEqual(quits, [])
        self.assertEqual(self.compute_hub.calls, [])
        self.assertEqual(information[-1]['text'], '用法：#后端 dx 或 #后端 qt')

    def test_backend_hash_command_does_not_restart_when_already_selected(self):
        information = []
        quits = []
        self.event_center.subscribe(
            EventType.INFORMATION,
            lambda event: information.append(event.data),
        )
        self.event_center.subscribe(
            EventType.APP_QUIT,
            lambda event: quits.append(event.data),
        )

        with patch.dict('config.config.UI', {'render_backend': 'directx'}):
            self.event_center.publish(Event(EventType.INPUT_HASH, {
                'text': '后端 dx',
            }))

        self.assertEqual(quits, [])
        self.assertEqual(self.compute_hub.calls, [])
        self.assertEqual(information[-1]['text'], '当前已是 DX 后端')

    def test_backend_hash_command_accepts_directx_alias_and_whitespace(self):
        saved = []
        self.center._backend_saver = saved.append
        quits = []
        self.event_center.subscribe(
            EventType.APP_QUIT,
            lambda event: quits.append(event.data),
        )

        with patch.dict('config.config.UI', {'render_backend': 'qt'}):
            self.event_center.publish(Event(EventType.INPUT_HASH, {
                'text': '后端\t directx',
            }))

        self.assertEqual(saved, ['directx'])
        self.assertEqual(quits[-1]['render_backend'], 'directx')

    def test_backend_hash_command_keeps_instance_on_save_failure(self):
        def fail_save(_backend_id):
            raise OSError('disk full')

        self.center._backend_saver = fail_save
        information = []
        quits = []
        self.event_center.subscribe(
            EventType.INFORMATION,
            lambda event: information.append(event.data),
        )
        self.event_center.subscribe(
            EventType.APP_QUIT,
            lambda event: quits.append(event.data),
        )

        with patch.dict('config.config.UI', {'render_backend': 'qt'}):
            self.event_center.publish(Event(EventType.INPUT_HASH, {
                'text': '后端 dx',
            }))

        self.assertEqual(quits, [])
        self.assertIn('切换绘制后端失败', information[-1]['text'])


if __name__ == "__main__":
    unittest.main()
