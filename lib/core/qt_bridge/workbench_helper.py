"""Qt-only workbench helper process entry."""
from __future__ import annotations

import sys


def run_workbench_helper() -> int:
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from lib.script.ui.ai_settings_panel import AISettingsPanel
    from lib.script.ui.workbench_window import WorkbenchWindow
    from lib.script.workbench.builtin_pages import builtin_tool_page_specs

    panel = AISettingsPanel(lazy_workbench_pages=True)
    window = WorkbenchWindow(
        lambda: panel,
        extra_page_specs=list(builtin_tool_page_specs()),
    )
    window.show_page('overview')
    return int(app.exec_())
