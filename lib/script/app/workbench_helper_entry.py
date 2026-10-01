"""Qt-only workbench helper process entry."""
from __future__ import annotations

import sys
from pathlib import Path


def _report_missing_qt() -> None:
    """无 PyQt5 时给出可恢复的提示，而不是一个裸 traceback。

    DX 主进程不引入 Qt，控制面板是独立 helper 进程；这个进程才是唯一需要
    PyQt5 的地方。用户看到的应该是“去装依赖”，而不是 ModuleNotFoundError 堆栈。
    """
    message = (
        "无法打开控制面板：缺少 PyQt5。\n\n"
        "桌宠主程序本身不需要 Qt，但控制面板是独立的 Qt 进程。\n"
        "请运行安装依赖（“安装依赖.bat”）后重试。"
    )
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "飞行雪绒 控制面板", 0x30)
    except Exception:
        pass


def run_workbench_helper(initial_page: str = "overview") -> int:
    """跑一个独立的 Qt 控制面板进程。缺 PyQt5 时给可恢复提示而不泄露堆栈。"""
    try:
        return _run_workbench_helper(initial_page)
    except ModuleNotFoundError:
        _report_missing_qt()
        return 1


def _run_workbench_helper(initial_page: str = "overview") -> int:
    from PyQt5.QtGui import QIcon
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    from lib.core.render.backends.qt.windows_app_id import set_windows_app_user_model_id

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
    from lib.core.render.backends.qt.music_player import QtMusicPlayer
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
