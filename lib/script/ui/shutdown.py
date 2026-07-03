"""UI 关闭与清理辅助。"""

from lib.core.logger import get_logger

logger = get_logger(__name__)


def _safe_flush_hide_and_close(widget) -> None:
    if widget is None:
        return
    try:
        if widget.isVisible():
            try:
                widget.setWindowOpacity(0.0)
            except Exception:
                pass
            try:
                widget.update()
                widget.repaint()
            except Exception:
                pass
        widget.hide()
    except Exception:
        pass
    try:
        widget.close()
    except Exception:
        pass


def hide_all_runtime_ui() -> None:
    """立即隐藏并关闭当前已创建的全局 UI。"""
    from lib.script.ui.tooltip_panel import get_tooltip_panel
    from lib.script.ui.playlist_panel import get_playlist_panel
    from lib.script.ui.progress_panel import get_progress_panel
    from lib.script.ui.speaker_search_dialog import get_speaker_search_dialog
    from lib.script.ui.cloudmusic_login_dialog import get_cloudmusic_login_dialog
    from lib.script.ui.yuanbao_login_dialog import get_yuanbao_login_dialog
    from lib.script.ui.cmd_window import get_cmd_window

    getters = [
        get_tooltip_panel,
        get_playlist_panel,
        get_progress_panel,
        get_speaker_search_dialog,
        get_cloudmusic_login_dialog,
        get_yuanbao_login_dialog,
        get_cmd_window,
    ]

    for getter in getters:
        try:
            _safe_flush_hide_and_close(getter())
        except Exception as exc:
            logger.debug('[ui.shutdown] hide failed for %s: %s', getattr(getter, '__name__', getter), exc)


def cleanup_all_runtime_ui() -> None:
    """释放全局 UI 单例资源。"""
    from lib.script.ui.tooltip_panel import cleanup_tooltip_panel
    from lib.script.ui.playlist_panel import cleanup_playlist_panel
    from lib.script.ui.progress_panel import cleanup_progress_panel
    from lib.script.ui.speaker_search_dialog import cleanup_speaker_search_dialog
    from lib.script.ui.cloudmusic_login_dialog import cleanup_cloudmusic_login_dialog
    from lib.script.ui.yuanbao_login_dialog import cleanup_yuanbao_login_dialog
    from lib.script.ui.cmd_window import cleanup_cmd_window

    cleanup_funcs = [
        cleanup_tooltip_panel,
        cleanup_playlist_panel,
        cleanup_progress_panel,
        cleanup_speaker_search_dialog,
        cleanup_cloudmusic_login_dialog,
        cleanup_yuanbao_login_dialog,
        cleanup_cmd_window,
    ]

    for cleanup in cleanup_funcs:
        try:
            cleanup()
        except Exception as exc:
            logger.debug('[ui.shutdown] cleanup failed for %s: %s', getattr(cleanup, '__name__', cleanup), exc)
