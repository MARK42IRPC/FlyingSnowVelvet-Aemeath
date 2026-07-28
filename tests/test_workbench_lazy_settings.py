import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_TEST_HOME = tempfile.mkdtemp(prefix='workbench-lazy-test-')
os.environ['AEMEATH_DESK_PET_HOME'] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', os.path.join(_QT_ROOT, 'Qt5', 'plugins', 'platforms'))
os.environ.setdefault('QT_PLUGIN_PATH', os.path.join(_QT_ROOT, 'Qt5', 'plugins'))

from PyQt5.QtWidgets import QApplication

from lib.script.ui.ai_settings_panel import AISettingsPanel


class WorkbenchLazySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_lazy_panel_builds_only_requested_general_page(self):
        with patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)

        self.assertEqual(panel._config_tab_meta, {})
        page = panel.create_workbench_page('ui_anim')
        self.assertIs(page, panel._workbench_pages['ui_anim'])
        self.assertEqual(set(panel._config_tab_meta), {'ui_anim'})
        theme_fields = [
            field
            for field in panel._config_tab_meta['ui_anim']['fields']
            if field.get('dict_name') == 'UI' and field.get('key') == 'workbench_light_theme'
        ]
        self.assertEqual(len(theme_fields), 1)
        self.assertEqual(theme_fields[0]['editor'].isChecked(), False)

        page.deleteLater()
        panel.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
