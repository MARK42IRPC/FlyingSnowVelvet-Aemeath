"""Qt-only workbench helper process entry."""
from __future__ import annotations

import sys
from pathlib import Path


def run_workbench_helper(initial_page: str = "overview") -> int:
    from PyQt5.QtGui import QIcon
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    from lib.core.qt_bridge.windows_app_id import set_windows_app_user_model_id

    set_windows_app_user_model_id()
    _icon_path = Path(__file__).resolve().parents[3] / "resc" / "icon.ico"
    if _icon_path.is_file():
        app.setWindowIcon(QIcon(str(_icon_path)))
    from lib.script.ui.ai_settings_panel import AISettingsPanel
    from lib.script.ui.workbench_window import WorkbenchWindow
    from lib.script.app.workbench_helper import (
        normalize_workbench_page,
        read_workbench_helper_request,
    )
    from lib.core.qt_bridge.music_player import QtMusicPlayer
    from lib.core.voice.core import cleanup_voice_core, get_voice_core
    from lib.script.gemes import cleanup_game_runtime
    from lib.script.music import cleanup_music_service
    from lib.script.music.service import configure_music_player_factory
    from lib.script.ui.office_approval_controller import (
        OfficeApprovalController,
    )
    from lib.script.workbench.builtin_pages import builtin_tool_page_specs

    configure_music_player_factory(QtMusicPlayer)
    get_voice_core()

    page_id = normalize_workbench_page(initial_page)
    panel = AISettingsPanel(lazy_workbench_pages=True)
    window = WorkbenchWindow(
        lambda: panel,
        extra_page_specs=list(builtin_tool_page_specs()),
    )
    approval_controller = OfficeApprovalController(parent=window)
    approval_controller.start()

    current_request = read_workbench_helper_request()
    last_request_id = [str(current_request.get("request_id") or "")]

    def apply_request(request: dict) -> None:
        requested_page = normalize_workbench_page(request.get("page_id"))
        game_id = str(request.get("game_id") or "")
        game_action = str(request.get("game_action") or "")
        if game_action == "open_manager":
            window.show_page("game_manager")
            return
        if game_action == "close_manager":
            window.hide()
            return
        if game_action not in {"open", "close"} or not game_id:
            window.show_page(requested_page)
            return
        from lib.script.gemes.MAIN.runtime import get_game_runtime

        runtime = get_game_runtime()
        if game_action == "open":
            runtime.open_game(game_id)
        else:
            runtime.close_game(game_id)

    def poll_open_request() -> None:
        request = read_workbench_helper_request()
        request_id = str(request.get("request_id") or "")
        if not request_id or request_id == last_request_id[0]:
            return
        last_request_id[0] = request_id
        apply_request(request)

    request_timer = QTimer(window)
    request_timer.setInterval(200)
    request_timer.timeout.connect(poll_open_request)
    request_timer.start()
    app.aboutToQuit.connect(approval_controller.cleanup)

    if current_request:
        apply_request(current_request)
    else:
        window.show_page(page_id)
    try:
        return int(app.exec_())
    finally:
        request_timer.stop()
        approval_controller.cleanup()
        cleanup_game_runtime()
        cleanup_music_service()
        cleanup_voice_core()
