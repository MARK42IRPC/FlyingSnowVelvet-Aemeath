import threading
import unittest
from unittest.mock import patch

from config.config import TIMEOUTS
from lib.script.chat.ollama_session import OllamaSessionMixin


class _Response:
    def __init__(self):
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        yield b'{"status":"success"}'

    def close(self):
        self.closed = True


class _Hub:
    def submit_latest(self, *_args, **_kwargs):
        return None


class _Session(OllamaSessionMixin):
    def __init__(self):
        self._is_running = True
        self._pulling_models = {'model'}
        self._pull_response_lock = threading.Lock()
        self._pull_response = None

    def _ping(self):
        return None


class OllamaPullLifecycleTests(unittest.TestCase):
    def test_pull_uses_finite_read_timeout_and_closes_response(self):
        response = _Response()
        session = _Session()
        with patch('lib.script.chat.ollama_session.requests.post', return_value=response) as post, patch(
            'lib.script.chat.ollama_session.get_compute_hub', return_value=_Hub()
        ):
            session._pull_model('model')

        self.assertEqual(post.call_args.kwargs['timeout'], (10, TIMEOUTS['ollama_pull_read']))
        self.assertTrue(response.closed)
        self.assertIsNone(session._pull_response)
        self.assertNotIn('model', session._pulling_models)


if __name__ == '__main__':
    unittest.main()
