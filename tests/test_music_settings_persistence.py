import unittest
from unittest.mock import patch

from lib.script.music.service import MusicService


class MusicSettingsPersistenceTests(unittest.TestCase):
    def test_provider_is_persisted_through_sparse_user_settings(self):
        with patch('lib.script.music.service.save_general_values') as save_values:
            result = MusicService._persist_provider_config('qq')

        self.assertTrue(result)
        save_values.assert_called_once_with({'CLOUD_MUSIC': {'provider': 'qq'}})

    def test_persistence_failure_is_reported(self):
        with patch(
            'lib.script.music.service.save_general_values',
            side_effect=OSError('read only'),
        ):
            self.assertFalse(MusicService._persist_provider_config('qq'))


if __name__ == '__main__':
    unittest.main()
