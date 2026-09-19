"""The update page's uninstall entry: button, confirmation and quit hand-off."""

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="update-uninstall-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

from lib.core.event.center import EventType
from lib.script.ui import ai_settings_panel as panel_module
from lib.script.ui.ai_settings_panel import AISettingsPanel

_PAGE_ID = "desktop_pet_update"


class UpdatePageUninstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(AISettingsPanel, "_refresh_hardware_watermark_async", lambda self: None):
            self.panel = AISettingsPanel(lazy_workbench_pages=True)
        self.page = self.panel.create_workbench_page(_PAGE_ID)

    def tearDown(self):
        self.page.deleteLater()
        self.panel.deleteLater()
        self.app.processEvents()

    def _uninstall_button(self) -> QPushButton:
        button = self.page.findChild(QPushButton, "uninstallPetButton")
        self.assertIsNotNone(button, "the update page must expose the uninstall button")
        return button

    def test_update_page_exposes_the_uninstall_button(self):
        button = self._uninstall_button()

        self.assertEqual(button.text(), "卸载桌宠")
        self.assertTrue(button.property("danger"), "the uninstall entry keeps the danger accent")
        meta = self.panel._config_tab_meta[_PAGE_ID]
        self.assertIn(button, meta["buttons"])
        self.assertEqual(len(meta["section_title_labels"]), 4)
        self.assertEqual(len(meta["section_hint_labels"]), 4)

    def test_uninstall_without_a_packaged_install_explains_the_manual_step(self):
        with patch.object(panel_module, "resolve_uninstaller", return_value=None), patch.object(
            self.panel, "_show_info_message"
        ) as info, patch.object(panel_module, "launch_uninstaller") as launcher:
            self.panel._on_uninstall_pet()

        launcher.assert_not_called()
        message = info.call_args[0][0]
        self.assertIn("源码工作区", message)
        self.assertIn(str(panel_module.get_shared_root_dir()), message)

    def test_uninstall_confirmation_launches_the_installer_and_quits(self):
        target = Path(_TEST_HOME) / "卸载飞行雪绒.exe"
        event_center = Mock()
        self.panel._ec = event_center
        with patch.object(panel_module, "resolve_uninstaller", return_value=target), patch.object(
            panel_module, "launch_uninstaller"
        ) as launcher, patch(
            "PyQt5.QtWidgets.QMessageBox.warning",
            return_value=QMessageBox.Yes,
        ), patch.object(
            panel_module.QTimer,
            "singleShot",
            lambda _delay, callback: callback(),
        ):
            self.panel._on_uninstall_pet()

        launcher.assert_called_once_with(target)
        published = [call.args[0] for call in event_center.publish.call_args_list]
        self.assertIn(
            (EventType.APP_QUIT, {"exit_code": 0}),
            [(event.type, event.data) for event in published],
        )

    def test_uninstall_can_be_cancelled(self):
        target = Path(_TEST_HOME) / "卸载飞行雪绒.exe"
        with patch.object(panel_module, "resolve_uninstaller", return_value=target), patch.object(
            panel_module, "launch_uninstaller"
        ) as launcher, patch(
            "PyQt5.QtWidgets.QMessageBox.warning",
            return_value=QMessageBox.No,
        ):
            self.panel._on_uninstall_pet()

        launcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
