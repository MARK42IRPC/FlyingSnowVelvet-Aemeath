import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_TEST_HOME = tempfile.mkdtemp(prefix='workbench-lazy-test-')
os.environ['AEMEATH_DESK_PET_HOME'] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', os.path.join(_QT_ROOT, 'Qt5', 'plugins', 'platforms'))
os.environ.setdefault('QT_PLUGIN_PATH', os.path.join(_QT_ROOT, 'Qt5', 'plugins'))

from PyQt5.QtWidgets import QApplication

from lib.script.ui import ai_settings_panel as panel_module
from lib.script.ui.ai_settings_panel import AISettingsPanel
from lib.script.ui.announcement_dialog import AnnouncementPreferences


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
        self.assertEqual(theme_fields, [])

        page.deleteLater()
        panel.deleteLater()
        self.app.processEvents()

    def test_border_tick_subscription_follows_standalone_visibility(self):
        with patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)

        self.assertFalse(panel._tick_subscribed)
        panel._visible = True
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel._tick_subscribed)

        panel.hide()
        self.app.processEvents()
        self.assertFalse(panel._tick_subscribed)

        panel.deleteLater()
        self.app.processEvents()

    def test_ui_page_announcement_checkbox_reflects_and_saves_forever_state(self):
        with patch.object(
            panel_module,
            "load_announcement_preferences",
            return_value=AnnouncementPreferences(True, ""),
        ), patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)
            page = panel.create_workbench_page('ui_anim')

        fields = [
            field
            for field in panel._config_tab_meta['ui_anim']['fields']
            if field.get('kind') == 'external_announcement_suppression'
        ]
        self.assertEqual(len(fields), 1)
        checkbox = fields[0]['editor']
        self.assertEqual(checkbox.objectName(), 'AnnouncementSuppressionCheckbox')
        self.assertTrue(checkbox.isChecked())
        self.assertIn(
            '不显示公告',
            [label.text() for label in page.findChildren(panel_module.QLabel)],
        )

        checkbox.setChecked(False)
        panel._emit_info = Mock()
        with patch.object(panel_module, '_save_general_config'), patch.object(
            panel_module, '_apply_general_runtime'
        ), patch.object(
            panel_module, 'set_announcement_forever_suppressed'
        ) as save_suppression:
            self.assertTrue(panel._on_save_config_category('ui_anim'))

        save_suppression.assert_called_once_with(False)

        page.deleteLater()
        panel.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
