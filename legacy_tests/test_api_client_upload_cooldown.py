import threading
import unittest

from lib.script.chat.api_client_openai import (
    _ApiClientOpenAIMixin,
)
from lib.script.chat.api_client_common import (
    UPLOAD_COOLDOWN_SECONDS,
    _UploadCooldownQueue,
    multimodal_cooldown,
)


class ApiClientUploadCooldownTests(unittest.TestCase):
    def test_builtin_cooldown_is_at_least_five_seconds(self):
        self.assertGreaterEqual(UPLOAD_COOLDOWN_SECONDS, 5.0)

    def test_requests_are_kept_in_fifo_queue_and_run_once(self):
        queue = _UploadCooldownQueue(cooldown_seconds=0.0)
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []
        results = []

        def first_operation():
            calls.append("first")
            first_started.set()
            release_first.wait(timeout=2.0)
            return "first-result"

        def second_operation():
            calls.append("second")
            return "second-result"

        first = threading.Thread(target=lambda: results.append(queue.submit(first_operation)))
        first.start()
        self.assertTrue(first_started.wait(timeout=2.0))

        second = threading.Thread(target=lambda: results.append(queue.submit(second_operation)))
        second.start()
        release_first.set()
        first.join(timeout=2.0)
        second.join(timeout=2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(sorted(results), ["first-result", "second-result"])

    def test_failed_request_releases_next_queued_request(self):
        queue = _UploadCooldownQueue(cooldown_seconds=0.0)
        calls = []

        def failed_operation():
            calls.append("failed")
            raise RuntimeError("upload failed")

        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            queue.submit(failed_operation)

        self.assertEqual(queue.submit(lambda: calls.append("next") or "ok"), "ok")
        self.assertEqual(calls, ["failed", "next"])

    def test_decorator_skips_text_and_queues_all_multimodal_providers(self):
        calls = []

        class Client:
            @multimodal_cooldown
            def request(self, *, images=None):
                calls.append(images)
                return len(calls)

        client = Client()
        self.assertEqual(client.request(images=None), 1)
        self.assertEqual(client.request(images=[b"image"]), 2)
        self.assertEqual(calls, [None, [b"image"]])
        self.assertTrue(hasattr(_ApiClientOpenAIMixin._openai_chat_api, "__wrapped__"))


if __name__ == "__main__":
    unittest.main()
