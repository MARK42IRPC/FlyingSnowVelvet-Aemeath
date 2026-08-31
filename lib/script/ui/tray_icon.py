"""系统托盘图标模块"""
import os
import sys
import uuid
from pathlib import Path
from PyQt5.QtWidgets import (
    QSystemTrayIcon,
    QAction,
    QApplication,
    QStyle,
)
from PyQt5.QtGui import QIcon, QCursor, QGuiApplication
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QPoint

from lib.core.logger import get_logger
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.tray_host import TrayCommand, TrayMenuState
from lib.script.ui.tray_menu import TrayContextMenu
from config.tooltip_config import TOOLTIPS
from lib.script.app.game_mode_service import get_game_mode_service

_logger = get_logger(__name__)

# 托盘图标唯一标识符（用于 Windows 持久化设置）
TRAY_ICON_GUID = uuid.UUID('{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}')


def _default_tray_icon_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'resc' / 'icon.ico'


class TrayIcon(QObject):
    """系统托盘图标管理器"""

    RETRY_INTERVAL_MS = 1500
    MAX_RETRY_COUNT = 40
    _ICON_TEST_SIZES = ((16, 16), (20, 20), (24, 24), (32, 32))

    # 退出信号
    quit_requested = pyqtSignal()
    announcement_requested = pyqtSignal()
    command_requested = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._event_center = get_event_center()
        self._event_center.subscribe(
            EventType.UI_TRAY_MENU_REQUEST,
            self._on_tray_menu_request,
        )
        self._tray_icon = None
        self._menu = None
        self._autostart_action = None
        self._clickthrough_action = None
        self._game_mode_action = None
        self._clickthrough_enabled = False
        self._game_mode_enabled = False
        self._autostart_enabled = False
        self._menu_state_initialized = False
        self._clickthrough_status_subscribed = False
        self._game_mode_status_subscribed = False
        self._ai_settings_panel = None
        self._workbench_window = None
        self._icon = None
        self._icon_path = None
        self._initialized = False
        self._retry_count = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(self.RETRY_INTERVAL_MS)
        self._retry_timer.timeout.connect(self._on_retry_timeout)

    def initialize(self, icon_path: str = None) -> bool:
        """
        初始化系统托盘图标

        Args:
            icon_path: 图标文件路径，如果为 None 则使用默认路径

        Returns:
            是否初始化成功
        """
        self._subscribe_clickthrough_events()
        self._subscribe_game_mode_events()

        if self._initialized:
            return True

        app = QApplication.instance()
        if app is None:
            _logger.error('QApplication 实例不存在，无法创建托盘图标')
            return False

        if not self._menu_state_initialized:
            try:
                from lib.script.app.tray_actions import prepare_autostart_state

                self._autostart_enabled = prepare_autostart_state()
            except Exception:
                self._autostart_enabled = False
            self._game_mode_enabled = bool(get_game_mode_service().is_enabled())
            self._menu_state_initialized = True

        if icon_path is None:
            icon_path = self._resolve_default_icon_path()
        self._icon_path = icon_path

        if self._try_create_tray_icon():
            return True

        self._start_retry()
        return False

    def _resolve_default_icon_path(self) -> str:
        """获取默认托盘图标路径"""
        return str(_default_tray_icon_path())

    def _start_retry(self):
        """开始后台重试创建托盘图标"""
        if self._retry_timer.isActive():
            return
        self._retry_count = 0
        self._retry_timer.start()
        _logger.warning('系统托盘暂不可用，已启动后台重试')

    def _stop_retry(self):
        """停止后台重试"""
        if self._retry_timer.isActive():
            self._retry_timer.stop()
        self._retry_count = 0

    def _on_retry_timeout(self):
        """重试定时器回调"""
        if self._initialized:
            self._stop_retry()
            return

        self._retry_count += 1
        if self._retry_count > self.MAX_RETRY_COUNT:
            _logger.error('系统托盘创建重试超时，已放弃（%s 次）', self.MAX_RETRY_COUNT)
            self._stop_retry()
            return

        if self._try_create_tray_icon():
            _logger.info('系统托盘在第 %s 次重试后创建成功', self._retry_count)
            self._stop_retry()

    def _try_create_tray_icon(self) -> bool:
        """执行一次托盘图标创建尝试"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False

        self._teardown_tray_icon()

        # 创建托盘对象
        self._tray_icon = QSystemTrayIcon(self)
        self._create_menu()
        self._tray_icon.setContextMenu(None)

        # 加载图标
        icon = self._load_icon(self._icon_path)
        if not self._is_icon_renderable(icon):
            if icon is not None and not icon.isNull():
                _logger.warning('自定义托盘图标不可渲染（常见于 ICO 解码异常），将回退默认图标')
            icon = self._get_default_icon()
        if not self._is_icon_renderable(icon):
            _logger.warning('托盘图标加载失败，无法创建系统托盘')
            self._teardown_tray_icon()
            return False

        self._icon = icon
        self._tray_icon.setIcon(self._icon)
        self._tray_icon.setToolTip('飞行雪绒')

        try:
            self._tray_icon.messageClicked.connect(self._on_message_clicked)
        except TypeError:
            # 已连接时忽略，避免重复连接报错
            pass
        try:
            self._tray_icon.activated.connect(self._on_tray_activated)
        except TypeError:
            # 已连接时忽略，避免重复连接报错
            pass

        # 不强依赖 isVisible() 立即返回值，某些系统上 show 后可见状态存在延迟
        self._tray_icon.show()

        self._initialized = True
        _logger.info('系统托盘图标已创建并显示')
        return True

    def _teardown_tray_icon(self):
        """销毁旧托盘对象（用于重建或 cleanup）"""
        if self._tray_icon is not None:
            try:
                self._tray_icon.messageClicked.disconnect(self._on_message_clicked)
            except (TypeError, RuntimeError):
                pass
            try:
                self._tray_icon.activated.disconnect(self._on_tray_activated)
            except (TypeError, RuntimeError):
                pass
            self._tray_icon.hide()
            self._tray_icon.setContextMenu(None)
            self._tray_icon.deleteLater()
            self._tray_icon = None

    def _load_icon(self, icon_path: str) -> QIcon:
        """加载图标文件"""
        if not icon_path:
            return None

        if not os.path.exists(icon_path):
            _logger.warning('图标文件不存在: %s', icon_path)
            return None

        icon = QIcon(icon_path)
        return icon

    def _is_icon_renderable(self, icon: QIcon) -> bool:
        """
        检查图标是否可渲染为托盘常用小尺寸。

        仅检查 `icon.isNull()` 不足以覆盖所有环境：部分机器会出现
        ICO 对象不为空，但 16x16/20x20 像素图取不到的情况。
        """
        if icon is None or icon.isNull():
            return False
        for w, h in self._ICON_TEST_SIZES:
            pixmap = icon.pixmap(w, h)
            if pixmap is not None and not pixmap.isNull():
                return True
        return False

    def _get_default_icon(self) -> QIcon:
        """获取系统默认图标"""
        # 尝试使用应用程序图标
        app = QApplication.instance()
        if app and hasattr(app, 'windowIcon'):
            icon = app.windowIcon()
            if not icon.isNull():
                return icon

        # 使用 Qt 内置图标
        app = QApplication.instance()
        if app:
            return app.style().standardIcon(QStyle.SP_ComputerIcon)

        return None

    def _create_menu(self):
        """创建托盘菜单"""
        if self._menu is not None:
            self._menu.clear()
            self._menu.deleteLater()

        self._menu = TrayContextMenu()

        # ── 常用入口 ───────────────────────────────────────────────
        # 控制面板动作
        ai_settings_action = QAction('控制面板', self._menu)
        ai_settings_action.setToolTip(TOOLTIPS['tray_ai_settings'])
        ai_settings_action.setStatusTip(TOOLTIPS['tray_ai_settings'])
        ai_settings_action.triggered.connect(
            lambda _checked=False: self._emit_command(TrayCommand.OPEN_SETTINGS)
        )
        self._menu.addAction(ai_settings_action)

        announcement_action = QAction('桌宠公告', self._menu)
        announcement_action.setToolTip(TOOLTIPS['tray_announcement'])
        announcement_action.setStatusTip(TOOLTIPS['tray_announcement'])
        announcement_action.triggered.connect(
            lambda _checked=False: self.announcement_requested.emit()
        )
        self._menu.addAction(announcement_action)

        bug_tracker_action = QAction('bug跟踪', self._menu)
        bug_tracker_action.setToolTip(TOOLTIPS['tray_bug_tracker'])
        bug_tracker_action.setStatusTip(TOOLTIPS['tray_bug_tracker'])
        bug_tracker_action.triggered.connect(self._on_bug_tracker)
        self._menu.addAction(bug_tracker_action)

        # CMD窗口动作
        cmd_window_action = QAction('CMD终端', self._menu)
        cmd_window_action.setToolTip(TOOLTIPS['tray_cmd_window'])
        cmd_window_action.setStatusTip(TOOLTIPS['tray_cmd_window'])
        cmd_window_action.triggered.connect(self._on_cmd_window)
        self._menu.addAction(cmd_window_action)

        self._game_mode_action = QAction('游戏模式', self._menu)
        self._game_mode_action.setCheckable(True)
        self._set_game_mode_action_checked(self._game_mode_enabled)
        self._game_mode_action.setToolTip(TOOLTIPS['tray_game_mode'])
        self._game_mode_action.setStatusTip(TOOLTIPS['tray_game_mode'])
        self._game_mode_action.triggered.connect(self._on_toggle_game_mode)
        self._menu.addAction(self._game_mode_action)

        # 鼠标穿透动作（可勾选）
        self._clickthrough_action = QAction('鼠标穿透', self._menu)
        self._clickthrough_action.setCheckable(True)
        self._set_clickthrough_action_checked(self._clickthrough_enabled)
        self._clickthrough_action.setToolTip(TOOLTIPS['tray_clickthrough'])
        self._clickthrough_action.setStatusTip(TOOLTIPS['tray_clickthrough'])
        self._clickthrough_action.triggered.connect(self._on_toggle_clickthrough)
        self._menu.addAction(self._clickthrough_action)

        self._menu.addSeparator()

        # ── 启动配置 ───────────────────────────────────────────────
        # 开机启动动作
        self._autostart_action = QAction('开机启动', self._menu)
        self._autostart_action.setCheckable(True)
        self._set_autostart_action_checked(self._autostart_enabled)
        self._autostart_action.setToolTip(TOOLTIPS['tray_autostart'])
        self._autostart_action.setStatusTip(TOOLTIPS['tray_autostart'])
        self._autostart_action.triggered.connect(self._on_toggle_autostart)
        self._menu.addAction(self._autostart_action)
        self._publish_autostart_status(self._autostart_enabled, source='tray_init')

        self._menu.addSeparator()

        # ── 清理维护 ───────────────────────────────────────────────
        # 清理桌面动作
        cleanup_action = QAction('清理桌面', self._menu)
        cleanup_action.setToolTip(TOOLTIPS['tray_cleanup_desktop'])
        cleanup_action.setStatusTip(TOOLTIPS['tray_cleanup_desktop'])
        cleanup_action.triggered.connect(self._on_cleanup_desktop)
        self._menu.addAction(cleanup_action)

        # 清理缓存动作
        cleanup_cache_action = QAction('清理缓存', self._menu)
        cleanup_cache_action.setToolTip(TOOLTIPS['tray_cleanup_cache'])
        cleanup_cache_action.setStatusTip(TOOLTIPS['tray_cleanup_cache'])
        cleanup_cache_action.triggered.connect(self._on_cleanup_cache)
        self._menu.addAction(cleanup_cache_action)

        # 清理历史动作
        cleanup_history_action = QAction('清理历史', self._menu)
        cleanup_history_action.setToolTip(TOOLTIPS['tray_cleanup_history'])
        cleanup_history_action.setStatusTip(TOOLTIPS['tray_cleanup_history'])
        cleanup_history_action.triggered.connect(self._on_cleanup_history)
        self._menu.addAction(cleanup_history_action)

        self._menu.addSeparator()

        # ── 其它 ───────────────────────────────────────────────────
        # 关注作者动作
        follow_author_action = QAction('关注作者', self._menu)
        follow_author_action.setToolTip(TOOLTIPS['tray_follow_author'])
        follow_author_action.setStatusTip(TOOLTIPS['tray_follow_author'])
        follow_author_action.triggered.connect(self._on_follow_author)
        self._menu.addAction(follow_author_action)

        # 分隔线
        self._menu.addSeparator()

        # 退出动作
        quit_action = QAction('退出程序', self._menu)
        quit_action.setToolTip(TOOLTIPS['tray_quit'])
        quit_action.setStatusTip(TOOLTIPS['tray_quit'])
        quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(quit_action)

    def _on_message_clicked(self):
        """消息点击回调"""
        _logger.debug('托盘消息被点击')

    def _on_tray_activated(self, reason):
        """处理托盘图标激活事件。"""
        if reason == QSystemTrayIcon.Context:
            self._show_menu_above_cursor()
            return
        if reason == QSystemTrayIcon.Trigger:
            self._on_ai_settings()

    def _show_menu_above_cursor(self):
        """在鼠标位置上方弹出托盘菜单，避免向下被屏幕遮挡。"""
        if self._menu is None:
            return

        self._menu.ensurePolished()
        hint = self._menu.sizeHint()
        menu_w = max(1, hint.width())
        menu_h = max(1, hint.height())

        cursor_pos = QCursor.pos()
        x = cursor_pos.x()
        y = cursor_pos.y() - menu_h

        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left(), min(x, geo.right() - menu_w + 1))
            y = max(geo.top(), min(y, geo.bottom() - menu_h + 1))

        self._menu.popup(QPoint(x, y))

    def show_context_menu(self):
        """主动弹出系统托盘右键菜单。"""
        if not self._initialized:
            self.initialize(self._icon_path)
        if self._menu is None:
            return
        self._show_menu_above_cursor()

    def _on_tray_menu_request(self, event: Event) -> None:
        del event
        self.show_context_menu()

    def _on_quit(self):
        """处理退出动作"""
        _logger.info('用户通过托盘菜单请求退出')
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': '正在退出程序',
            'min': 0,
            'max': 60,
        }))
        QTimer.singleShot(120, self.quit_requested.emit)

    def begin_shutdown(self):
        """立即隐藏托盘相关 UI，完整释放仍由 cleanup() 负责。"""
        self._stop_retry()
        if self._menu is not None:
            try:
                self._menu.hide()
            except Exception:
                pass
        if self._ai_settings_panel is not None:
            try:
                self._ai_settings_panel.hide()
            except Exception:
                pass
        if self._workbench_window is not None:
            try:
                self._workbench_window.hide_immediately()
            except Exception:
                pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.hide()
            except Exception:
                pass

    def _on_cleanup_desktop(self):
        """处理清理桌面动作"""
        self._emit_command(TrayCommand.CLEANUP_DESKTOP)

    def _on_cleanup_history(self):
        """处理清理历史动作：清空所有平台历史与登录数据，不清理缓存。"""
        self._emit_command(TrayCommand.CLEANUP_HISTORY)

    def _on_cleanup_cache(self):
        """处理清理缓存动作：仅清理音乐缓存目录，不影响历史与登录数据。"""
        self._emit_command(TrayCommand.CLEANUP_CACHE)

    def preload_ai_settings_panel(self):
        from lib.script.ui.ai_settings_panel import AISettingsPanel

        if self._ai_settings_panel is None:
            self._ai_settings_panel = AISettingsPanel(lazy_workbench_pages=True)
        return self._ai_settings_panel

    def preload_workbench(self):
        from lib.script.ui.workbench_window import WorkbenchWindow
        from lib.script.workbench.builtin_pages import builtin_tool_page_specs

        if self._workbench_window is None:
            self._workbench_window = WorkbenchWindow(
                self.preload_ai_settings_panel,
                extra_page_specs=list(builtin_tool_page_specs()),
            )
        return self._workbench_window

    def _on_ai_settings(self):
        """处理控制面板动作：打开统一工作台总览。"""
        try:
            self.preload_workbench().show_page('overview')
        except Exception as e:
            _logger.error('打开控制面板失败: %s', e)
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'打开控制面板失败: {e}',
                'min': 12,
                'max': 120,
            }))

    def open_settings(self):
        self._on_ai_settings()

    def _on_bug_tracker(self):
        try:
            self.preload_workbench().show_page('bug_tracker')
        except Exception as exc:
            _logger.error('打开 bug 跟踪器失败: %s', exc)
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'打开 bug 跟踪器失败: {exc}',
                'min': 12,
                'max': 120,
            }))

    def _on_cmd_window(self):
        """处理CMD窗口动作：打开CMD终端窗口。"""
        self._emit_command(TrayCommand.OPEN_CMD)

    def _on_toggle_game_mode(self, checked: bool):
        """处理游戏模式切换动作。"""
        target = bool(checked)
        self._game_mode_enabled = target
        self._set_game_mode_action_checked(target)
        self._emit_command(TrayCommand.TOGGLE_GAME_MODE, target)

    def _subscribe_game_mode_events(self):
        """订阅游戏模式状态事件，用于同步托盘动作。"""
        if self._game_mode_status_subscribed:
            return
        self._event_center.subscribe(
            EventType.GAME_MODE_STATUS_CHANGE,
            self._on_game_mode_status_change,
        )
        self._game_mode_status_subscribed = True

    def _unsubscribe_game_mode_events(self):
        """取消订阅游戏模式状态事件。"""
        if not self._game_mode_status_subscribed:
            return
        self._event_center.unsubscribe(
            EventType.GAME_MODE_STATUS_CHANGE,
            self._on_game_mode_status_change,
        )
        self._game_mode_status_subscribed = False

    def _on_game_mode_status_change(self, event: Event):
        """接收游戏模式状态变化并同步托盘动作。"""
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get('enabled', False))
        self._game_mode_enabled = enabled
        self._set_game_mode_action_checked(enabled)

    def _set_game_mode_action_checked(self, enabled: bool):
        """同步托盘游戏模式动作的勾选状态。"""
        if self._game_mode_action is None:
            return
        target = bool(enabled)
        if self._game_mode_action.isChecked() == target:
            return
        blocked = self._game_mode_action.blockSignals(True)
        try:
            self._game_mode_action.setChecked(target)
        finally:
            self._game_mode_action.blockSignals(blocked)

    def _on_toggle_clickthrough(self, checked: bool):
        """处理鼠标穿透开关动作。"""
        target = bool(checked)
        self._clickthrough_enabled = target
        self._set_clickthrough_action_checked(target)
        self._emit_command(TrayCommand.TOGGLE_CLICKTHROUGH, target)

    def _subscribe_clickthrough_events(self):
        """订阅鼠标穿透状态事件，用于同步托盘勾选状态。"""
        if self._clickthrough_status_subscribed:
            return
        self._event_center.subscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE,
            self._on_clickthrough_status_change,
        )
        self._clickthrough_status_subscribed = True

    def _unsubscribe_clickthrough_events(self):
        """取消订阅鼠标穿透状态事件。"""
        if not self._clickthrough_status_subscribed:
            return
        self._event_center.unsubscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE,
            self._on_clickthrough_status_change,
        )
        self._clickthrough_status_subscribed = False

    def _on_clickthrough_status_change(self, event: Event):
        """接收鼠标穿透状态变化并同步托盘动作。"""
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get('enabled', False))
        self._clickthrough_enabled = enabled
        self._set_clickthrough_action_checked(enabled)

    def _set_clickthrough_action_checked(self, enabled: bool):
        """同步托盘鼠标穿透动作的勾选状态，避免重复触发信号。"""
        if self._clickthrough_action is None:
            return
        target = bool(enabled)
        if self._clickthrough_action.isChecked() == target:
            return
        blocked = self._clickthrough_action.blockSignals(True)
        try:
            self._clickthrough_action.setChecked(target)
        finally:
            self._clickthrough_action.blockSignals(blocked)

    def _on_follow_author(self):
        """处理关注作者动作"""
        self._emit_command(TrayCommand.OPEN_AUTHOR_PAGE)

    def _is_autostart_enabled(self) -> bool:
        """检查开机启动是否已启用"""
        try:
            from lib.script.app.autostart import is_autostart_enabled

            return bool(is_autostart_enabled())
        except Exception as e:
            _logger.warning('检查开机启动状态失败: %s', e)
            return False

    def _set_autostart_action_checked(self, enabled: bool):
        """同步托盘开机启动动作的勾选状态，避免重复触发信号。"""
        if self._autostart_action is None:
            return
        target = bool(enabled)
        if self._autostart_action.isChecked() == target:
            return
        blocked = self._autostart_action.blockSignals(True)
        try:
            self._autostart_action.setChecked(target)
        finally:
            self._autostart_action.blockSignals(blocked)

    def _publish_autostart_status(self, enabled: bool, source: str = 'unknown'):
        """广播开机启动状态，供控制面板与其他 UI 同步。"""
        self._event_center.publish(Event(EventType.AUTOSTART_STATUS_CHANGE, {
            'enabled': bool(enabled),
            'source': str(source or 'unknown'),
        }))

    def _on_toggle_autostart(self, checked: bool, source: str = 'tray_menu'):
        """切换开机启动状态"""
        target = bool(checked)
        self._autostart_enabled = target
        self._set_autostart_action_checked(target)
        self._emit_command(TrayCommand.TOGGLE_AUTOSTART, target)

    def _emit_command(self, command: TrayCommand, checked: bool | None = None) -> None:
        self.command_requested.emit(command, checked)

    def set_menu_state(self, state: TrayMenuState) -> None:
        """Apply coordinator-owned check states to an existing or future menu."""
        if not isinstance(state, TrayMenuState):
            raise TypeError('tray menu state must be TrayMenuState')
        self._game_mode_enabled = bool(state.game_mode_enabled)
        self._clickthrough_enabled = bool(state.clickthrough_enabled)
        self._autostart_enabled = bool(state.autostart_enabled)
        self._menu_state_initialized = True
        self._set_game_mode_action_checked(self._game_mode_enabled)
        self._set_clickthrough_action_checked(self._clickthrough_enabled)
        self._set_autostart_action_checked(self._autostart_enabled)

    def show_message(self, title: str, message: str,
                     icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information,
                     msecs: int = 3000):
        """
        显示托盘消息

        Args:
            title: 消息标题
            message: 消息内容
            icon: 消息图标类型
            msecs: 消息显示时间(毫秒)
        """
        if self._tray_icon and self._tray_icon.isVisible():
            self._tray_icon.showMessage(title, message, icon, msecs)

    def is_visible(self) -> bool:
        """检查托盘图标是否可见"""
        return self._tray_icon is not None and self._tray_icon.isVisible()

    def set_icon(self, icon_path: str) -> bool:
        """
        动态更换托盘图标

        Args:
            icon_path: 新图标路径

        Returns:
            是否更换成功
        """
        if not self._tray_icon:
            return False

        icon = self._load_icon(icon_path)
        if icon and not icon.isNull():
            self._tray_icon.setIcon(icon)
            return True
        return False

    def cleanup(self):
        """清理托盘图标资源"""
        self._event_center.unsubscribe(
            EventType.UI_TRAY_MENU_REQUEST,
            self._on_tray_menu_request,
        )
        self._unsubscribe_clickthrough_events()
        self._unsubscribe_game_mode_events()
        self._stop_retry()
        self._teardown_tray_icon()
        if self._menu:
            self._menu.clear()
            self._menu.deleteLater()
            self._menu = None
        if self._ai_settings_panel is not None:
            if self._workbench_window is not None:
                self._workbench_window.hide_immediately()
                self._workbench_window.deleteLater()
                self._workbench_window = None
            self._ai_settings_panel.hide()
            self._ai_settings_panel.deleteLater()
            self._ai_settings_panel = None
        self._icon = None
        self._icon_path = None
        self._autostart_action = None
        self._clickthrough_action = None
        self._game_mode_action = None
        self._clickthrough_enabled = False
        self._game_mode_enabled = False
        self._autostart_enabled = False
        self._menu_state_initialized = False
        self._initialized = False
        _logger.info('系统托盘图标已清理')


# 全局单例实例
_tray_icon_instance: TrayIcon = None


def get_tray_icon() -> TrayIcon:
    """获取托盘图标单例"""
    global _tray_icon_instance
    if _tray_icon_instance is None:
        app = QApplication.instance()
        _tray_icon_instance = TrayIcon(app)
    return _tray_icon_instance


def cleanup_tray_icon():
    """清理托盘图标单例"""
    global _tray_icon_instance
    if _tray_icon_instance is not None:
        _tray_icon_instance.cleanup()
        _tray_icon_instance = None
