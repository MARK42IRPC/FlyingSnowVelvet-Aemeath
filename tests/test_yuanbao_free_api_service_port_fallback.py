import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import config.ollama_config as oc
from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin
from lib.script.yuanbao_free_api import service as yuanbao_service
from lib.script.yuanbao_free_api.service import YuanbaoFreeApiService


class YuanbaoFreeApiServicePortFallbackTests(unittest.TestCase):
    def test_chat_request_uses_runtime_port_after_service_fallback(self):
        stale_config = {
            'base_url': 'http://127.0.0.1:8000/v1',
            'api_key': 'sk-yuanbao-local',
            'model': 'deepseek-v3',
            'key_source': 'yuanbao_local',
            'provider_options': {
                'yuanbao_free_api': {
                    'enabled': True,
                    'base_url': 'http://127.0.0.1:8000/v1',
                },
            },
        }
        response = Mock()
        response.ok = True
        response.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"ready"}}]}',
            b'data: [DONE]',
        ]
        client = _ApiClientOpenAIMixin()
        client._active_config = stale_config
        client._request_with_proxy_fallback = Mock(return_value=response)

        original = dict(oc.YUANBAO_FREE_API_LOCAL)
        try:
            oc.YUANBAO_FREE_API_LOCAL['base_url'] = 'http://127.0.0.1:18765/v1'

            result = client._openai_chat_api('hello', 'persona')

            self.assertEqual(result, 'ready')
            requested_url = client._request_with_proxy_fallback.call_args.args[1]
            self.assertEqual(
                requested_url,
                'http://127.0.0.1:18765/v1/chat/completions',
            )
            self.assertEqual(stale_config['base_url'], 'http://127.0.0.1:8000/v1')
            self.assertEqual(client._active_config['base_url'], 'http://127.0.0.1:18765/v1')
            self.assertEqual(
                client._active_config['provider_options']['yuanbao_free_api']['base_url'],
                'http://127.0.0.1:18765/v1',
            )
            response.close.assert_called_once_with()
        finally:
            oc.YUANBAO_FREE_API_LOCAL.clear()
            oc.YUANBAO_FREE_API_LOCAL.update(original)

    def test_switch_to_random_local_port_updates_runtime_base_url(self):
        service = YuanbaoFreeApiService()
        original = dict(oc.YUANBAO_FREE_API_LOCAL)
        try:
            with patch('lib.script.yuanbao_free_api.service._find_free_local_port', return_value=18765):
                switched = service._switch_to_random_local_port('127.0.0.1', 8000)

            self.assertEqual(switched, ('127.0.0.1', 18765))
            self.assertEqual(oc.YUANBAO_FREE_API_LOCAL['base_url'], 'http://127.0.0.1:18765/v1')
        finally:
            oc.YUANBAO_FREE_API_LOCAL.clear()
            oc.YUANBAO_FREE_API_LOCAL.update(original)
            service.cleanup()

    def test_ensure_status_endpoint_switches_when_non_yuanbao_service_occupies_port(self):
        service = YuanbaoFreeApiService()
        original = dict(oc.YUANBAO_FREE_API_LOCAL)
        try:
            with patch('lib.script.yuanbao_free_api.service._probe_status_endpoint', return_value=('error', None)), patch(
                'lib.script.yuanbao_free_api.service._find_free_local_port', return_value=18765
            ), patch.object(service, '_start_service_process', return_value=True) as start_mock, patch.object(
                service, '_wait_for_status_endpoint', return_value={'logged_in': False}
            ) as wait_mock:
                status, host, port = service._ensure_status_endpoint('127.0.0.1', 11434)

            self.assertEqual(status, {'logged_in': False})
            self.assertEqual((host, port), ('127.0.0.1', 18765))
            self.assertEqual(oc.YUANBAO_FREE_API_LOCAL['base_url'], 'http://127.0.0.1:18765/v1')
            start_mock.assert_called_once_with('127.0.0.1', 18765)
            wait_mock.assert_called_once_with('127.0.0.1', 18765, expected_instance_id='')
        finally:
            oc.YUANBAO_FREE_API_LOCAL.clear()
            oc.YUANBAO_FREE_API_LOCAL.update(original)
            service.cleanup()

    def test_status_probe_rejects_foreign_json_service(self):
        class _Headers:
            @staticmethod
            def get_content_charset():
                return 'utf-8'

        class _Response:
            headers = _Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return json.dumps({'logged_in': True}).encode('utf-8')

        with patch.object(yuanbao_service, '_can_connect', return_value=True), patch.object(
            yuanbao_service, 'urlopen', return_value=_Response()
        ):
            state, status = yuanbao_service._probe_status_endpoint('127.0.0.1', 18000)

        self.assertEqual(state, 'foreign')
        self.assertIsNone(status)

    def test_status_probe_accepts_matching_service_identity(self):
        payload = {
            'service_id': yuanbao_service._SERVICE_ID,
            'protocol_version': yuanbao_service._SERVICE_PROTOCOL_VERSION,
            'instance_id': 'instance-1',
            'logged_in': False,
        }

        class _Headers:
            @staticmethod
            def get_content_charset():
                return 'utf-8'

        response = Mock()
        response.headers = _Headers()
        response.read.return_value = json.dumps(payload).encode('utf-8')
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch.object(yuanbao_service, '_can_connect', return_value=True), patch.object(
            yuanbao_service, 'urlopen', return_value=response
        ):
            state, status = yuanbao_service._probe_status_endpoint(
                '127.0.0.1',
                18000,
                expected_instance_id='instance-1',
            )

        self.assertEqual(state, 'ok')
        self.assertEqual(status, payload)

    def test_service_startup_lifecycle_is_serialized(self):
        service = YuanbaoFreeApiService()
        state_lock = threading.Lock()
        active = 0
        maximum = 0

        def locked_worker(host, port):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with state_lock:
                active -= 1
            return {'logged_in': False}, host, port

        try:
            with patch.object(service, '_ensure_status_endpoint_locked', side_effect=locked_worker):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(service._ensure_status_endpoint, '127.0.0.1', 18000 + index)
                        for index in range(2)
                    ]
                    results = [future.result(timeout=2) for future in futures]

            self.assertEqual(maximum, 1)
            self.assertEqual(len(results), 2)
        finally:
            service.cleanup()

    def test_running_managed_target_is_reused_instead_of_starting_second_process(self):
        service = YuanbaoFreeApiService()
        proc = Mock()
        proc.poll.return_value = None
        service._set_managed_process(proc, ('127.0.0.1', 18000), 'managed-1')
        try:
            with patch.object(yuanbao_service, '_probe_status_endpoint', return_value=('offline', None)), patch.object(
                service,
                '_wait_for_status_endpoint',
                return_value={'logged_in': False},
            ) as wait_mock, patch.object(service, '_update_runtime_target', return_value=True) as update_mock, patch.object(
                service, '_start_service_process'
            ) as start_mock:
                status, host, port = service._ensure_status_endpoint('127.0.0.1', 19000)

            self.assertEqual(status, {'logged_in': False})
            self.assertEqual((host, port), ('127.0.0.1', 18000))
            wait_mock.assert_called_once_with(
                '127.0.0.1',
                18000,
                expected_instance_id='managed-1',
            )
            update_mock.assert_called_once_with('127.0.0.1', 18000)
            start_mock.assert_not_called()
        finally:
            service._take_managed_process()
            service.cleanup()

    def test_cancel_login_monitor_signals_worker_and_cancels_future(self):
        service = YuanbaoFreeApiService()
        cancel_event = threading.Event()
        future = Mock()
        service._login_monitor_cancel = cancel_event
        service._login_monitor_thread = future
        service._login_monitor_target = ('127.0.0.1', 18000)
        try:
            service._cancel_login_monitor()

            self.assertTrue(cancel_event.is_set())
            future.cancel.assert_called_once_with()
            self.assertIsNone(service._login_monitor_thread)
            self.assertIsNone(service._login_monitor_target)
        finally:
            service.cleanup()

    def test_peek_service_status_never_starts_or_reconfigures_service(self):
        service = YuanbaoFreeApiService()
        try:
            with patch('lib.script.yuanbao_free_api.service._parse_local_target', return_value=('127.0.0.1', 18765)), patch(
                'lib.script.yuanbao_free_api.service._fetch_service_status', return_value={'logged_in': True}
            ) as fetch_status, patch.object(service, '_ensure_status_endpoint') as ensure_status:
                status = service.peek_service_status()

            self.assertEqual(status, {'logged_in': True})
            fetch_status.assert_called_once_with('127.0.0.1', 18765, timeout=1.0)
            ensure_status.assert_not_called()
        finally:
            service.cleanup()

    def test_cleanup_without_managed_process_does_not_touch_external_service(self):
        service = YuanbaoFreeApiService()
        with patch.object(yuanbao_service, '_probe_status_endpoint') as probe_status, patch.object(
            yuanbao_service, '_request_service_logout'
        ) as request_logout, patch.object(service, '_terminate_process_tree') as terminate_process:
            service.cleanup()

        probe_status.assert_not_called()
        request_logout.assert_not_called()
        terminate_process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
