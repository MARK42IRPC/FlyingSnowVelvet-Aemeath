import unittest
from unittest.mock import patch

import config.ollama_config as oc
from lib.script.yuanbao_free_api.service import YuanbaoFreeApiService


class YuanbaoFreeApiServicePortFallbackTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
