import unittest
from unittest.mock import Mock, patch

import config.ollama_config as oc
from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin
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
            wait_mock.assert_called_once_with('127.0.0.1', 18765)
        finally:
            oc.YUANBAO_FREE_API_LOCAL.clear()
            oc.YUANBAO_FREE_API_LOCAL.update(original)
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


if __name__ == '__main__':
    unittest.main()
