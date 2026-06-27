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


if __name__ == '__main__':
    unittest.main()
